"""Daily pipeline steps. Each step: one class, registered by name, listed in config/pipelines.toml."""

from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from ytstudio.application.pipeline import Context, Step, register
from ytstudio.application.schemas import HooksOut, IdeasOut, PackageOut, PerformanceOut, ScriptOut, SeoOut
from ytstudio.domain.events import BriefReady
from ytstudio.domain.metrics import age_days, median_views, retention_cliffs, top_and_bottom, velocity
from ytstudio.domain.models import AssetStatus, Queue, Video
from ytstudio.ports import LLMRequest, T, TrendQuery

LANG_NAMES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi in Latin script mixed with English)",
}


def channel_vars(ctx: Context) -> dict[str, Any]:
    s = ctx.schedule
    return {
        "channel": ctx.channel.title,
        "niche": s.niche or "not set",
        "language": LANG_NAMES.get(s.language, s.language),
        "region": s.region,
        "target_minutes": s.target_minutes,
        "voice": ctx.user.voice_md.strip(),
    }


class LLMStep(Step):
    queue = Queue.LLM

    def ask(self, ctx: Context, alias: str, template: str, variables: dict[str, Any], schema: type[T]) -> T:
        req = LLMRequest(
            alias=alias,
            template=template,
            variables=channel_vars(ctx) | variables,
            user_id=ctx.user.id,
            run_id=ctx.run.id,
            step=self.name,
            critical=self.critical,
            regenerate=ctx.regenerate,
        )
        return self.deps.llm.generate(req, schema)


def video_row(v: Video, now: Any, multiple: float | None = None) -> dict[str, Any]:
    return {
        "video_id": v.id,
        "title": v.title,
        "views": v.views,
        "published_at": v.published_at.isoformat(),
        "channel_id": v.channel_id,
        "channel_title": v.channel_title,
        "duration_s": v.duration_s,
        "age_days": round(age_days(v.published_at, now), 1),
        "velocity": round(velocity(v.views, v.published_at, now), 1),
        **({"multiple": multiple} if multiple is not None else {}),
    }


def dump(m: BaseModel) -> dict[str, Any]:
    return m.model_dump(mode="json")


@register
class SyncChannel(Step):
    name = "sync_channel"
    queue = Queue.IO

    def run(self, ctx: Context) -> dict[str, Any]:
        now = self.deps.clock.now()
        since = ctx.channel.videos_synced_at - timedelta(days=2) if ctx.channel.videos_synced_at else None
        vids = self.deps.videos.uploads(ctx.channel.id, since=since, limit=200 if since else 400)
        with self.deps.uow() as uow:
            uow.videos.upsert_many(vids)
            uow.channels.mark_videos_synced(ctx.channel.id, now)
            uow.commit()
        return {"synced": len(vids), "new_ids": [v.id for v in vids]}


@register
class FetchPerformance(Step):
    """Top and bottom performers of the last 90 days against the channel's own median, plus retention."""

    name = "fetch_performance"
    queue = Queue.IO
    needs = ("sync_channel",)

    def run(self, ctx: Context) -> dict[str, Any]:
        now = self.deps.clock.now()
        with self.deps.uow() as uow:
            vids = uow.videos.recent(ctx.channel.id, now - timedelta(days=90))
        longs = [v for v in vids if not v.is_short] or vids
        top, bottom = top_and_bottom(longs)
        retention: dict[str, Any] = {}
        for v, _ in top[:3] + bottom[:3]:
            points = self.deps.analytics.retention(ctx.channel.id, v.id)
            with self.deps.uow() as uow:
                uow.videos.save_retention(v.id, points)
                uow.commit()
            retention[v.id] = {
                "cliffs": retention_cliffs([(p.ratio, p.watch_ratio) for p in points]),
                "analysis": self.deps.retention.analyze(points, v.duration_s, None),
            }
        return {
            "median_views": median_views(longs),
            "count": len(longs),
            "top": [video_row(v, now, m) for v, m in top],
            "bottom": [video_row(v, now, m) for v, m in bottom],
            "retention": retention,
        }


@register
class AnalyzePerformance(LLMStep):
    name = "analyze_performance"
    needs = ("fetch_performance",)
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        perf = ctx.out("fetch_performance")
        if not perf["count"]:
            return {
                "summary": "No uploads in the last 90 days to compare.",
                "working": [],
                "not_working": [],
                "retention_notes": [],
            }
        return dump(self.ask(ctx, "smart", "analyze_performance", {"perf": perf}, PerformanceOut))


@register
class DiscoverTrends(Step):
    """One task per provider (a chord in Celery); results deduped by video id."""

    name = "discover_trends"
    queue = Queue.IO
    fan_out = True

    def items(self, ctx: Context) -> list[Any]:
        enabled = self.deps.settings.get("trend_providers") or [p.name for p in self.deps.trend_providers]
        return [p.name for p in self.deps.trend_providers if p.name in enabled]

    def run_item(self, ctx: Context, item: Any) -> Any:
        s = ctx.schedule
        provider = next(p for p in self.deps.trend_providers if p.name == item)
        q = TrendQuery(
            channel_id=ctx.channel.id,
            region=s.region,
            language="hi" if s.language == "hinglish" else s.language,
            niche=s.niche,
            topics=s.custom_topics,
        )
        now = self.deps.clock.now()
        return [video_row(v, now) | {"provider": item} for v in provider.fetch(q)]

    def merge(self, ctx: Context, results: list[Any]) -> dict[str, Any]:
        seen: dict[str, dict[str, Any]] = {}
        for rows in results:
            for r in rows:
                if r["channel_id"] != ctx.channel.id:  # own videos are not trends
                    seen.setdefault(r["video_id"], r)
        return {"candidates": list(seen.values())}


@register
class RankTrends(Step):
    """Outlier multiple = views / the source channel's own recent median (vendored swipe.py)."""

    name = "rank_trends"
    queue = Queue.IO
    needs = ("discover_trends",)

    def run(self, ctx: Context) -> dict[str, Any]:
        cands = ctx.out("discover_trends")["candidates"]
        channels = sorted({c["channel_id"] for c in cands})
        recent = self.deps.videos.recent_views(channels, using=ctx.channel.id) if channels else {}
        rows: list[dict[str, Any]] = []
        for c in cands:
            rows.append({"channel": c["channel_id"], "title": c["title"], "views": c["views"], "url": c["video_id"]})
            rows += [
                {"channel": c["channel_id"], "title": "", "views": v, "url": ""}
                for v in recent.get(c["channel_id"], [])
            ]
        min_multiple = float(self.deps.settings.get("outlier_min_multiple", 2.0))
        ranked = self.deps.outliers.rank(rows, min_multiple) if rows else {"outliers": []}
        by_id = {c["video_id"]: c for c in cands}
        outliers = [
            by_id[r["url"]] | {"multiple": r["multiple"], "channel_median": r["median"], "formula": r["formula"]}
            for r in ranked["outliers"]
            if r["url"] in by_id
        ]
        outliers.sort(key=lambda r: (-r["multiple"], -r["velocity"]))
        with self.deps.uow() as uow:
            uow.trends.save(ctx.channel.id, self.deps.clock.now().date(), outliers)
            uow.commit()
        return {"outliers": outliers[:30], "considered": len(cands), "min_multiple": min_multiple}


@register
class GenerateIdeas(LLMStep):
    name = "generate_ideas"
    needs = ("rank_trends", "fetch_performance")

    def run(self, ctx: Context) -> dict[str, Any]:
        perf, outliers = ctx.out("fetch_performance"), ctx.out("rank_trends")["outliers"]
        analysis = ctx.outputs.get("analyze_performance", {})
        out = self.ask(ctx, "smart", "ideas", {"outliers": outliers, "perf": perf, "analysis": analysis}, IdeasOut)
        known = {o["video_id"] for o in outliers}
        own = {v["video_id"] for v in perf["top"] + perf["bottom"]}
        ideas = []
        for i, idea in enumerate(out.ideas):
            d = dump(idea) | {"rank": i + 1}
            # Keep only references that exist: the brief must never cite a video we did not see.
            d["outlier_refs"] = [r for r in d["outlier_refs"] if r in known]
            d["builds_on"] = [r for r in d["builds_on"] if r in own]
            ideas.append(d)
        return {"ideas": ideas}


def chosen_idea(ctx: Context) -> dict[str, Any]:
    return dict(ctx.inputs.get("idea") or ctx.out("generate_ideas")["ideas"][0])


@register
class WriteScript(LLMStep):
    """Five hooks off the 21 formulas, scored by the hook panel; the best one opens the full script."""

    name = "write_script"

    def run(self, ctx: Context) -> dict[str, Any]:
        idea = chosen_idea(ctx)
        hooks = self.ask(ctx, "smart", "hooks", {"idea": idea}, HooksOut).hooks
        scored = self.deps.hooks.score(hooks)
        best = scored[0]
        script = self.ask(ctx, "smart", "script", {"idea": idea, "hook": best["hook"]}, ScriptOut)
        return {"idea": idea, "hooks": scored, "hook": best, "script": dump(script)}


@register
class BuildPackage(LLMStep):
    name = "build_package"
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        idea = chosen_idea(ctx)
        script = ctx.outputs.get("write_script", {}).get("script")
        out = self.ask(ctx, "smart", "package", {"idea": idea, "script": script}, PackageOut)
        pairs = [dump(p) | {"lint": self.deps.linter.lint(p.title, p.thumbnail_text)} for p in out.pairs]
        return {"idea": idea, "pairs": pairs, "thumbnail_brief": out.thumbnail_brief}


@register
class WriteSeo(LLMStep):
    name = "write_seo"
    critical = False

    def run(self, ctx: Context) -> dict[str, Any]:
        idea = chosen_idea(ctx)
        script = ctx.outputs.get("write_script", {}).get("script")
        out = self.ask(ctx, "smart", "seo", {"idea": idea, "script": script}, SeoOut)
        return {"idea": idea, **dump(out), "chapters": "Added after recording, from the final transcript."}


SECTION_KIND = {"write_script": "script", "build_package": "package", "write_seo": "seo"}


@register
class SaveBrief(Step):
    """Everything lands in the review queue as "needs review". Nothing is ever published."""

    name = "save_brief"
    queue = Queue.IO

    def run(self, ctx: Context) -> dict[str, Any]:
        o = ctx.outputs
        brief_id, idea_ids = None, []
        with self.deps.uow() as uow:
            if "generate_ideas" in o:  # a daily run; "develop" runs only add sections under the picked idea
                brief = uow.assets.add(
                    ctx.asset(
                        kind="brief",
                        title=f"Brief {ctx.run.idem_key.rsplit(':', 1)[-1]}",
                        data={
                            "performance": o.get("fetch_performance"),
                            "analysis": o.get("analyze_performance"),
                            "trends": o.get("rank_trends"),
                        },
                    )
                )
                brief_id = brief.id
                for idea in o["generate_ideas"]["ideas"]:
                    a = uow.assets.add(ctx.asset(kind="idea", title=idea["title"], data=idea, parent_id=brief_id))
                    idea_ids.append(a.id)
            parent = ctx.inputs.get("idea_asset_id") or (idea_ids[0] if idea_ids else brief_id)
            for step, kind in SECTION_KIND.items():
                if step in o:
                    uow.assets.add(ctx.asset(kind=kind, title=o[step]["idea"]["title"], data=o[step], parent_id=parent))
            uow.commit()
        self.deps.publish(BriefReady(user_id=ctx.user.id, channel_title=ctx.channel.title, run_id=ctx.run.id))
        return {"brief_id": brief_id, "idea_ids": idea_ids}


@register
class SaveSection(Step):
    """Regenerate: replace one asset's content, keep the previous versions in its history."""

    name = "save_section"
    queue = Queue.IO

    def run(self, ctx: Context) -> dict[str, Any]:
        target = ctx.inputs["target_step"]
        with self.deps.uow() as uow:
            asset = uow.assets.get(ctx.inputs["asset_id"], ctx.user.id)
            history = [{k: v for k, v in asset.data.items() if k != "history"}, *asset.data.get("history", [])]
            new = ctx.out(target)
            if target == "generate_ideas":  # one idea asset regenerated: take the idea at the same rank
                new = new["ideas"][min(asset.data.get("rank", 1), len(new["ideas"])) - 1]
            if asset.kind == "brief":  # the brief's regenerable section is its analysis
                new = asset.data | {"analysis": new}
            asset.data = new | {"history": history[:5]}
            asset.title = new.get("title") or new.get("idea", {}).get("title", asset.title)
            asset.status = AssetStatus.NEEDS_REVIEW
            uow.assets.update(asset)
            uow.commit()
        return {"asset_id": asset.id}
