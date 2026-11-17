"""Every video made from a script is kept as a version: its voice, its planned timeline, a quick draft and, once you
ask for it, the same video in full quality. One version is shown (and uploaded); you can go back to an earlier one
without making it again. The script's `video_*` fields always describe the version shown."""

import hashlib
import json
from typing import Any

from ytstudio.domain.errors import PermanentError

MAX_VERSIONS = 5  # older ones are deleted with their files, never the one shown


def script_hash(data: dict[str, Any]) -> str:
    spoken = [s.get("spoken", "") for s in (data.get("script") or {}).get("sections", [])]
    return hashlib.sha1(json.dumps(spoken).encode(), usedforsecurity=False).hexdigest()[:12]


def versions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The versions, oldest first. A video made before versions existed is version 1, full quality, not redrawable."""
    if "videos" in data:
        return [dict(v) for v in data["videos"]]
    if not data.get("video_key"):
        return []
    return [
        {
            "n": 1,
            "made_at": data.get("video_made_at"),
            "voice": data.get("video_voice"),
            "engine": data.get("video_engine"),
            "seconds": data.get("video_seconds"),
            "audio_key": data.get("audio_key"),
            "timeline_key": None,
            "draft_key": None,
            "hd_key": data["video_key"],
            "hd_made_at": data.get("video_made_at"),
            "script_hash": None,
        }
    ]


def find(data: dict[str, Any], n: int) -> dict[str, Any]:
    v = next((v for v in versions(data) if v["n"] == n), None)
    if v is None:
        raise PermanentError(f"Version {n} of this video is gone. Pick another one, or make the video again.")
    return v


def showing(data: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
    """The script's fields for version `v` shown: its best file, its voice, and whether the script changed since."""
    outdated = v["script_hash"] != script_hash(data) if v.get("script_hash") else bool(data.get("video_outdated"))
    return {
        "video_n": v["n"],
        "video_key": v.get("hd_key") or v.get("draft_key"),
        "video_hd": bool(v.get("hd_key")),
        "audio_key": v.get("audio_key"),
        "video_seconds": v.get("seconds"),
        "video_voice": v.get("voice"),
        "video_engine": v.get("engine"),
        "video_made_at": v.get("hd_made_at") or v.get("made_at"),
        "video_outdated": outdated,
    }


def added(data: dict[str, Any], v: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The script's data with version `v` added and shown, and the files of the versions that no longer fit."""
    old = versions(data)
    v = v | {"n": max((x["n"] for x in old), default=0) + 1}
    keep = [*old, v][-MAX_VERSIONS:]
    gone = [k for x in old if x not in keep for k in files(x)]
    return data | {"videos": keep} | showing(data, v), gone


def updated(data: dict[str, Any], v: dict[str, Any]) -> dict[str, Any]:
    """The script's data with version `v` (same number) replaced and shown."""
    vs = [v if x["n"] == v["n"] else x for x in versions(data)]
    return data | {"videos": vs} | showing(data, v)


def files(v: dict[str, Any]) -> list[str]:
    return [k for k in (v.get("draft_key"), v.get("hd_key"), v.get("audio_key"), v.get("timeline_key")) if k]
