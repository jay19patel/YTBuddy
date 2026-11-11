"""YouTube gateway: one facade over Google OAuth, the Data API and the Analytics API.

Every Data API call is charged to the quota budget *before* it is made. Errors are translated into
domain errors (transient / quota / reconnect / permanent) so callers never see googleapiclient types.
"""

import logging
import os
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httplib2
import httpx
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from ytstudio.domain.errors import (
    PermanentError,
    QuotaExceeded,
    ReconnectNeeded,
    TransientError,
)
from ytstudio.domain.models import Cue, OAuthToken, RetentionPoint, Video
from ytstudio.domain.schedule import next_quota_reset
from ytstudio.logging_setup import redact
from ytstudio.ports import Cache, ChannelInfo, Clock, OAuthResult, OAuthStart, QuotaBudget, TokenVault

log = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105  (a URL, not a secret)
READONLY = "https://www.googleapis.com/auth/youtube.readonly"
FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"  # captions of own videos; also upload/live
ANALYTICS = "https://www.googleapis.com/auth/yt-analytics.readonly"
MONETARY = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
CHANNEL_SCOPES = [READONLY, ANALYTICS, FORCE_SSL, MONETARY]
LEGACY_FULL = "https://www.googleapis.com/auth/youtube"

# Data API unit costs (https://developers.google.com/youtube/v3/determine_quota_cost)
COST = {
    "search": 100,
    "list": 1,
    "captions.list": 50,
    "captions.download": 200,
    "channels.update": 50,
    "videos.insert": 1600,
    "live.insert": 50,
    "live.bind": 50,
}
SEARCH_TTL, STATS_TTL = 6 * 3600, 24 * 3600
PERIODS = (7, 28, 90, 365)


def chunks(items: list[str], n: int = 50) -> Iterator[list[str]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def parse_duration(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        stamp = next((ln for ln in lines if "-->" in ln), None)
        if not stamp:
            continue
        a, b = (_ts(x) for x in stamp.split("-->"))
        body = " ".join(ln for ln in lines[lines.index(stamp) + 1 :] if ln.strip())
        if body:
            cues.append(Cue(a, b, body))
    return cues


def _ts(s: str) -> float:
    h, m, rest = s.strip().replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    names = [h["name"] for h in report["columnHeaders"]]
    return [dict(zip(names, r, strict=False)) for r in report.get("rows", [])]


def friendly_error(exc: Exception) -> str:
    text = redact(str(exc))  # Google errors echo the request URL, which can carry an API key
    if isinstance(exc, HttpError) and "accessNotConfigured" in text:
        api = "YouTube Analytics API" if "youtubeanalytics" in text else "YouTube Data API v3"
        link = re.search(r"visiting (\S+)", text)
        return f"{api} is turned off in your Google Cloud project. Turn it on here, wait 2-5 minutes, then reload: " + (
            link[1] if link else "https://console.cloud.google.com/apis/library"
        )
    if isinstance(exc, QuotaExceeded) or "quotaExceeded" in text:
        return "Daily YouTube API quota used up. It resets at midnight Pacific time. Sync less often in Settings."
    if isinstance(exc, ReconnectNeeded) or "invalid_grant" in text:
        return "Google access for this channel expired or was revoked. Reconnect the channel."
    return text[:500]


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "token_uri": TOKEN_URI,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "redirect_uris": [redirect_uri],
            }
        }
        self.client_id, self.redirect_uri = client_id, redirect_uri
        if redirect_uri.startswith("http://localhost") or redirect_uri.startswith("http://127.0.0.1"):
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # Google allows plain http only for localhost
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # noqa: S105  # granular consent: the user may untick optional scopes

    def _flow(self, scopes: list[str], state: str | None = None) -> Flow:
        return Flow.from_client_config(self.config, scopes, redirect_uri=self.redirect_uri, state=state)

    def start(self, scopes: list[str], login_hint: str | None = None) -> OAuthStart:
        flow = self._flow(scopes)
        extra = {"login_hint": login_hint} if login_hint else {}
        if "openid" in scopes:
            url, state = flow.authorization_url(prompt="select_account", **extra)
        else:
            url, state = flow.authorization_url(access_type="offline", prompt="select_account consent", **extra)
        return OAuthStart(url=url, state=state, code_verifier=flow.code_verifier or "")

    def finish(self, scopes: list[str], callback_url: str, state: str, code_verifier: str) -> OAuthResult:
        flow = self._flow(scopes, state)
        flow.code_verifier = code_verifier
        try:
            flow.fetch_token(authorization_response=callback_url)
        except Exception as e:  # oauthlib raises a zoo of types; none should reach the user as a 500
            raise PermanentError(f"Google sign-in failed: {type(e).__name__}") from e
        c = flow.credentials
        email = None
        if c.id_token:
            info = id_token.verify_oauth2_token(c.id_token, GoogleRequest(), self.client_id)
            email = info.get("email") if info.get("email_verified") else None
        return OAuthResult(
            OAuthToken(
                refresh_token=c.refresh_token or "",
                access_token=c.token,
                expiry=c.expiry.replace(tzinfo=UTC) if c.expiry else None,
                scopes=list(c.granted_scopes or c.scopes or scopes),
            ),
            email,
        )

    def revoke(self, token: OAuthToken) -> None:
        tok = token.refresh_token or token.access_token
        if tok:
            # Best effort: a token Google already dropped answers 400, which is fine.
            httpx.post("https://oauth2.googleapis.com/revoke", data={"token": tok}, timeout=10)


class YouTubeGateway:
    def __init__(
        self,
        vault: TokenVault,
        client_id: str,
        client_secret: str,
        quota: QuotaBudget,
        cache: Cache,
        clock: Clock,
        on_reconnect: Callable[[str], None],
    ):
        self.vault, self.client_id, self.client_secret = vault, client_id, client_secret
        self.quota, self.cache, self.clock, self.on_reconnect = quota, cache, clock, on_reconnect
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ----- plumbing -----

    def _lock(self, channel_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(channel_id, threading.Lock())

    def creds(self, channel_id: str) -> Credentials:
        with self._lock(channel_id):  # one refresh at a time per channel
            try:
                tok = self.vault.load(channel_id)
            except ReconnectNeeded:  # undecryptable or missing token: same fix for the user, reconnect
                self.on_reconnect(channel_id)
                raise
            c = self._to_creds(tok)
            if not c.valid:
                try:
                    c.refresh(GoogleRequest())
                except RefreshError as e:  # invalid_grant: revoked, expired, or password changed
                    self.on_reconnect(channel_id)
                    raise ReconnectNeeded(channel_id) from e
                except TransportError as e:
                    raise TransientError(str(e)) from e
                self.vault.save(
                    channel_id,
                    OAuthToken(
                        refresh_token=c.refresh_token or tok.refresh_token,
                        access_token=c.token,
                        scopes=tok.scopes,
                        expiry=c.expiry.replace(tzinfo=UTC) if c.expiry else None,
                    ),
                )
            return c

    def _to_creds(self, tok: OAuthToken) -> Credentials:
        expiry = tok.expiry.astimezone(UTC).replace(tzinfo=None) if tok.expiry else None  # google-auth wants naive UTC
        return Credentials(
            token=tok.access_token,
            refresh_token=tok.refresh_token,
            token_uri=TOKEN_URI,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=tok.scopes or None,
            expiry=expiry,
        )

    def service(self, name: str, version: str, channel_id: str | None) -> Any:
        if channel_id is None:  # every call, public data included, goes through a connected channel's login
            raise PermanentError("Connect a YouTube channel first.")
        return build(name, version, credentials=self.creds(channel_id), cache_discovery=False)

    def execute(self, request: Any, cost: int, critical: bool = True) -> Any:
        if cost:
            self.quota.spend(cost, critical)
        try:
            return request.execute(num_retries=0)
        except HttpError as e:
            status, text = e.resp.status, str(e)
            if status == 403 and ("quotaExceeded" in text or "dailyLimitExceeded" in text):
                self.quota.exhausted()
                raise QuotaExceeded(next_quota_reset(self.clock.now())) from e
            if status == 429 or status >= 500:
                raise TransientError(f"YouTube {status}") from e
            raise PermanentError(friendly_error(e)) from e
        except (OSError, TimeoutError, httplib2.HttpLib2Error, TransportError) as e:
            raise TransientError(f"network: {e}") from e

    # ----- OAuth-time -----

    def my_channel(self, token: OAuthToken) -> ChannelInfo | None:
        yt = build("youtube", "v3", credentials=self._to_creds(token), cache_discovery=False)
        items = self.execute(
            yt.channels().list(part="snippet,contentDetails,statistics,brandingSettings", mine=True), COST["list"]
        )
        return self._info(items["items"][0]) if items.get("items") else None

    @staticmethod
    def _info(i: dict[str, Any]) -> ChannelInfo:
        sn, st = i.get("snippet", {}), i.get("statistics", {})
        brand = i.get("brandingSettings", {}).get("channel", {})
        return ChannelInfo(
            id=i["id"],
            title=sn.get("title", ""),
            thumb=sn.get("thumbnails", {}).get("default", {}).get("url", ""),
            uploads_playlist=i.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""),
            description=brand.get("description", sn.get("description", "")),
            keywords=brand.get("keywords", ""),
            country=sn.get("country", ""),
            default_language=sn.get("defaultLanguage", ""),
            subscribers=int(st.get("subscriberCount", 0)),
            views=int(st.get("viewCount", 0)),
            videos=int(st.get("videoCount", 0)),
        )

    # ----- Data API -----

    def channel_infos(self, channel_ids: list[str], using: str | None) -> dict[str, ChannelInfo]:
        yt = self.service("youtube", "v3", using)
        out: dict[str, ChannelInfo] = {}
        for batch in chunks(channel_ids):
            resp = self.execute(
                yt.channels().list(
                    part="snippet,statistics,contentDetails,brandingSettings", id=",".join(batch), maxResults=50
                ),
                COST["list"],
            )
            out |= {i["id"]: self._info(i) for i in resp.get("items", [])}
        return out

    def update_about(self, channel_id: str, description: str, keywords: str) -> None:
        """channels.update replaces the whole brandingSettings part, so merge into the current one or the
        fields we don't send (trailer, default language, country) would be wiped."""
        yt = self.service("youtube", "v3", channel_id)
        cur = self.execute(yt.channels().list(part="brandingSettings", id=channel_id), COST["list"])
        branding = (cur.get("items") or [{}])[0].get("brandingSettings", {})
        branding.setdefault("channel", {}).update(description=description, keywords=keywords)
        self.execute(
            yt.channels().update(part="brandingSettings", body={"id": channel_id, "brandingSettings": branding}),
            COST["channels.update"],
        )

    def _video(self, i: dict[str, Any]) -> Video:
        sn, st = i["snippet"], i.get("statistics", {})
        return Video(
            id=i["id"],
            channel_id=sn["channelId"],
            channel_title=sn.get("channelTitle", ""),
            title=sn["title"],
            published_at=datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")),
            views=int(st.get("viewCount", 0)),
            likes=int(st.get("likeCount", 0)),
            comments=int(st.get("commentCount", 0)),
            duration_s=parse_duration(i.get("contentDetails", {}).get("duration", "")),
        )

    def videos(self, video_ids: list[str], using: str | None, critical: bool = True) -> list[Video]:
        yt = self.service("youtube", "v3", using)
        out = []
        for batch in chunks(video_ids):
            resp = self.execute(
                yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(batch), maxResults=50),
                COST["list"],
                critical,
            )
            out += [self._video(i) for i in resp.get("items", [])]
        return out

    def uploads(self, channel_id: str, since: datetime | None, limit: int = 200) -> list[Video]:
        """Uploads playlist -> playlist items -> video details; stops at `since` (incremental sync)."""
        info = self.channel_infos([channel_id], using=channel_id).get(channel_id)
        if not info or not info.uploads_playlist:
            return []
        yt = self.service("youtube", "v3", channel_id)
        ids: list[str] = []
        page: str | None = None
        while len(ids) < limit:
            resp = self.execute(
                yt.playlistItems().list(
                    part="contentDetails", playlistId=info.uploads_playlist, maxResults=50, pageToken=page
                ),
                COST["list"],
            )
            items = resp.get("items", [])
            done = False
            for it in items:
                published = it["contentDetails"].get("videoPublishedAt")
                if since and published and datetime.fromisoformat(published.replace("Z", "+00:00")) < since:
                    done = True
                    break
                ids.append(it["contentDetails"]["videoId"])
            page = resp.get("nextPageToken")
            if done or not page:
                break
        return self.videos(ids[:limit], using=channel_id)

    def recent_views(self, channel_ids: list[str], using: str | None) -> dict[str, list[int]]:
        """Views of each channel's ~15 latest uploads, the base for "outlier multiple". Cached 24 h."""
        out: dict[str, list[int]] = {}
        missing = []
        for cid in channel_ids:
            hit = self.cache.get(f"recent:{cid}")
            if hit is not None:
                out[cid] = hit
            else:
                missing.append(cid)
        if not missing:
            return out
        infos = self.channel_infos(missing, using)
        yt = self.service("youtube", "v3", using)
        for cid, info in infos.items():
            if not info.uploads_playlist:
                continue
            resp = self.execute(
                yt.playlistItems().list(part="contentDetails", playlistId=info.uploads_playlist, maxResults=15),
                COST["list"],
                critical=False,
            )
            vids = [it["contentDetails"]["videoId"] for it in resp.get("items", [])]
            views = [v.views for v in self.videos(vids, using, critical=False)] if vids else []
            self.cache.set(f"recent:{cid}", views, STATS_TTL)
            out[cid] = views
        return out

    def search(
        self, query: str, region: str, language: str, days: int, using: str | None, max_results: int = 25
    ) -> list[Video]:
        after = (self.clock.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
        key = f"search:{query.lower()}:{region}:{language}:{after[:10]}"
        if (hit := self.cache.get(key)) is not None:
            ids = hit
        else:
            yt = self.service("youtube", "v3", using)
            resp = self.execute(
                yt.search().list(
                    part="id",
                    q=query,
                    type="video",
                    order="viewCount",
                    publishedAfter=after,
                    regionCode=region,
                    relevanceLanguage=language,
                    maxResults=max_results,
                ),
                COST["search"],
                critical=False,
            )
            ids = [i["id"]["videoId"] for i in resp.get("items", [])]
            self.cache.set(key, ids, SEARCH_TTL)
        return self.videos(ids, using, critical=False) if ids else []

    def most_popular(self, region: str, category_id: str | None, using: str | None) -> list[Video]:
        key = f"popular:{region}:{category_id}"
        if (hit := self.cache.get(key)) is not None:
            return [Video(**{**v, "published_at": datetime.fromisoformat(v["published_at"])}) for v in hit]
        kw: dict[str, Any] = {
            "part": "snippet,statistics,contentDetails",
            "chart": "mostPopular",
            "regionCode": region,
            "maxResults": 50,
        }
        if category_id:
            kw["videoCategoryId"] = category_id
        resp = self.execute(self.service("youtube", "v3", using).videos().list(**kw), COST["list"], False)
        vids = [self._video(i) for i in resp.get("items", [])]
        self.cache.set(key, [v.__dict__ | {"published_at": v.published_at.isoformat()} for v in vids], SEARCH_TTL)
        return vids

    def captions(self, channel_id: str, video_id: str) -> list[Cue] | None:
        """Captions of your own video (force-ssl scope). None when there are none or access is refused."""
        yt = self.service("youtube", "v3", channel_id)
        try:
            tracks = self.execute(yt.captions().list(part="snippet", videoId=video_id), COST["captions.list"])
            items = tracks.get("items", [])
            if not items:
                return None
            items.sort(key=lambda t: t["snippet"].get("trackKind") == "asr")  # human captions first
            raw = self.execute(yt.captions().download(id=items[0]["id"], tfmt="srt"), COST["captions.download"])
        except PermanentError as e:
            log.info("captions unavailable for %s: %s", video_id, e)
            return None
        return parse_srt(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)

    # ----- Analytics API (separate quota, not counted in Data API units) -----

    def _ya(self, channel_id: str) -> Any:
        return self.service("youtubeAnalytics", "v2", channel_id)

    def _query(self, ya: Any, start: date, end: date, **kw: Any) -> list[dict[str, Any]]:
        return rows(
            self.execute(ya.reports().query(ids="channel==MINE", startDate=str(start), endDate=str(end), **kw), 0)
        )

    def channel_report(self, channel_id: str, days: int, currency: str) -> dict[str, Any]:
        tok = self.vault.load(channel_id)
        if ANALYTICS not in tok.scopes:
            return {"needs_reconnect": True}
        ya = self._ya(channel_id)
        end = self.clock.now().date()
        start = end - timedelta(days=days - 1)
        totals = self._query(
            ya,
            start,
            end,
            metrics="views,estimatedMinutesWatched,averageViewDuration,"
            "subscribersGained,subscribersLost,likes,comments,shares",
        )
        daily = self._query(ya, start, end, metrics="views,subscribersGained", dimensions="day", sort="day")
        top = self._query(
            ya,
            start,
            end,
            metrics="views,estimatedMinutesWatched,averageViewDuration,likes",
            dimensions="video",
            sort="-views",
            maxResults=10,
        )
        if top:
            titles = {v.id: v.title for v in self.videos([r["video"] for r in top], using=channel_id)}
            for r in top:
                r["title"] = titles.get(r["video"], r["video"])
        # Non-partner channels get a 403, a 500, or all-zero rows depending on the query,
        # so "monetized" means: revenue data came back and it isn't empty.
        revenue: list[dict[str, Any]] = []
        revenue_daily: list[dict[str, Any]] = []
        monetized = None  # None = revenue permission not granted
        if MONETARY in tok.scopes:
            try:
                revenue = self._query(
                    ya,
                    start,
                    end,
                    currency=currency,
                    metrics="estimatedRevenue,estimatedAdRevenue,estimatedRedPartnerRevenue,"
                    "grossRevenue,cpm,playbackBasedCpm,monetizedPlaybacks",
                )
                revenue_daily = self._query(
                    ya, start, end, metrics="estimatedRevenue", dimensions="day", sort="day", currency=currency
                )
            except (PermanentError, TransientError):
                pass
            monetized = bool(revenue and (revenue[0]["monetizedPlaybacks"] or revenue[0]["estimatedRevenue"]))
        return {
            "totals": totals[0] if totals else {},
            "daily": daily,
            "top": top,
            "monetized": monetized,
            "revenue": revenue[0] if revenue else {},
            "revenue_daily": revenue_daily,
            "currency": currency,
        }

    def video_metrics(
        self, channel_id: str, video_ids: list[str], start: date, end: date
    ) -> dict[str, dict[str, float]]:
        ya = self._ya(channel_id)
        out: dict[str, dict[str, float]] = {}
        for batch in chunks(video_ids, 200):
            filt = "video==" + ",".join(batch)
            for r in self._query(
                ya,
                start,
                end,
                metrics="views,averageViewDuration,averageViewPercentage",
                dimensions="video",
                filters=filt,
            ):
                out[r.pop("video")] = r
            try:  # reach metrics are not offered to every channel through the API
                for r in self._query(
                    ya,
                    start,
                    end,
                    dimensions="video",
                    filters=filt,
                    metrics="videoThumbnailImpressions,videoThumbnailImpressionsClickRate",
                ):
                    vid = r.pop("video")
                    out.setdefault(vid, {}).update(
                        impressions=r["videoThumbnailImpressions"],
                        impressionsClickThroughRate=r["videoThumbnailImpressionsClickRate"],
                    )
            except PermanentError:
                log.info("impressions/CTR not available from the Analytics API for %s", channel_id)
        return out

    def retention(self, channel_id: str, video_id: str) -> list[RetentionPoint]:
        ya = self._ya(channel_id)
        end = self.clock.now().date()
        data = self._query(
            ya,
            date(2010, 1, 1),
            end,
            dimensions="elapsedVideoTimeRatio",
            metrics="audienceWatchRatio,relativeRetentionPerformance",
            filters=f"video=={video_id}",
        )
        return [
            RetentionPoint(
                ratio=r["elapsedVideoTimeRatio"],
                watch_ratio=r["audienceWatchRatio"],
                relative=r.get("relativeRetentionPerformance"),
            )
            for r in data
        ]

    # ----- manual publishing (existing feature, behind ENABLE_MANUAL_PUBLISH) -----

    def upload(self, channel_id: str, path: str, body: dict[str, Any]) -> str:
        yt = self.service("youtube", "v3", channel_id)
        self.quota.spend(COST["videos.insert"], critical=True)
        req = yt.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True),
        )
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        return str(resp["id"])

    def create_live(
        self, channel_id: str, title: str, description: str, privacy: str, start: datetime
    ) -> dict[str, str]:
        yt = self.service("youtube", "v3", channel_id)
        broadcast = self.execute(
            yt.liveBroadcasts().insert(
                part="snippet,status,contentDetails",
                body={
                    "snippet": {"title": title, "description": description, "scheduledStartTime": start.isoformat()},
                    "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
                    # Goes live as soon as OBS starts sending, ends when OBS stops.
                    "contentDetails": {"enableAutoStart": True, "enableAutoStop": True},
                },
            ),
            COST["live.insert"],
        )
        stream = self.execute(
            yt.liveStreams().insert(
                part="snippet,cdn",
                body={
                    "snippet": {"title": title},
                    "cdn": {"ingestionType": "rtmp", "resolution": "variable", "frameRate": "variable"},
                },
            ),
            COST["live.insert"],
        )
        self.execute(
            yt.liveBroadcasts().bind(part="id,contentDetails", id=broadcast["id"], streamId=stream["id"]),
            COST["live.bind"],
        )
        ingest = stream["cdn"]["ingestionInfo"]
        return {"id": broadcast["id"], "server": ingest["ingestionAddress"], "key": ingest["streamName"]}
