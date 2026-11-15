"""The steps every caller shares (the CLI and the web app's "Generate video"): plan a storyboard when there is none,
then render the timeline with Remotion and mux the voice into the final mp4."""

import json
import logging
from pathlib import Path

from pipeline import markup, mux, plan, render
from pipeline.schema import Brand, Timeline, VideoInfo
from pipeline.settings import Settings
from pipeline.theme import PALETTE
from pipeline.transcribe import TimedWord

log = logging.getLogger(__name__)


def video_info(cfg: Settings, fmt: str) -> VideoInfo:
    w, h = cfg.size(fmt)
    brand = Brand(
        handle=cfg.brand.handle, avatar=cfg.brand.avatar, character=cfg.brand.character, palette=dict(PALETTE)
    )
    return VideoInfo(width=w, height=h, fps=cfg.fps, duration_frames=1, brand=brand)


def auto_story(cfg: Settings, tokens: list[markup.Token], words: list[TimedWord], llm: bool = False) -> plan.Story:
    """The LLM's plan when asked for and valid, otherwise the rule-based one."""
    if llm:
        try:
            names = sorted(p.stem for p in (cfg.assets / "stickers").glob("*.png"))
            return plan.finish(plan.llm_story(tokens, words, cfg.plan.llm_model, names))
        except Exception as e:  # any bad reply falls back to the rules; the reason is logged
            log.warning("LLM plan rejected (%s); using the rule-based plan", e)
    return plan.auto_story(tokens, words, cfg.plan.min_scene_s, cfg.plan.max_scene_s, bool(cfg.brand.character))


def render_video(
    cfg: Settings,
    timeline: Timeline,
    audio: Path,
    out: Path,
    work: Path,
    music: Path | None = None,
    sfx: bool = False,
) -> None:
    """timeline → silent H.264 (Remotion) → `out` with the voice, music and SFX, -14 LUFS. `work` is a temp dir."""
    from ytstudio.adapters.media import _ffmpeg

    render.preflight(cfg.renderer)
    if missing := plan.missing_assets(timeline, cfg.assets):
        raise FileNotFoundError(f"missing in {cfg.assets}: {', '.join(missing)}")
    props = work / "props.json"
    props.write_text(json.dumps({"timeline": timeline.model_dump(mode="json")}), encoding="utf-8")
    silent = work / "silent.mp4"
    render.run(render.render_cmd("Main", props, silent, cfg.video.crf), cfg.renderer, cfg.video.timeout_s)
    cues = mux.sfx_cues(timeline, cfg.assets / "sfx") if sfx else []
    if sfx and not cues:
        log.warning("--sfx: no pop/whoosh/click/typing .wav files in %s", cfg.assets / "sfx")
    log.info(
        "muxing voice%s%s, loudness %s LUFS",
        " + music" if music else "",
        f" + {len(cues)} sfx" if cues else "",
        cfg.audio.lufs,
    )
    duration = timeline.video.duration_frames / timeline.video.fps
    _ffmpeg(*mux.mux_cmd(silent, audio, out, duration, cfg.audio, music, cues), timeout=cfg.video.timeout_s)
