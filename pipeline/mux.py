"""The final file: Remotion's silent H.264 video + voice-over + music ducked under the voice + optional SFX, loudness
normalised (EBU R128) once, video stream copied (no second encode), +faststart for streaming."""

from collections import defaultdict
from pathlib import Path

from pipeline.schema import Timeline
from pipeline.settings import AudioCfg

STEREO = "aformat=sample_rates=48000:channel_layouts=stereo"
SFX_FOR = {"sticker": "pop", "card": "whoosh", "button_state": "click", "typing": "typing", "ui_window": "pop"}


def sfx_cues(t: Timeline, sfx_dir: Path) -> list[tuple[Path, float]]:
    """A sound per pop, whoosh, click and typing burst, from your own files in assets/sfx (missing ones skipped)."""
    cues: list[tuple[Path, float]] = []
    for s in t.scenes:
        if s.transition_in != "cut" and (f := sfx_dir / "whoosh.wav").exists():
            cues.append((f, s.start))
        for e in s.elements:
            if (name := SFX_FOR.get(e.type)) and (f := sfx_dir / f"{name}.wav").exists():
                cues.append((f, e.start))
    return cues


def mux_cmd(
    video: Path,
    voice: Path,
    out: Path,
    duration: float,
    cfg: AudioCfg,
    music: Path | None = None,
    sfx: list[tuple[Path, float]] | None = None,
) -> list[str]:
    """`duration` is the video's length: the voice is padded with silence to it, so an outro after the voice stays."""
    inputs = ["-i", str(video), "-i", str(voice)]
    duck = bool(music and cfg.duck)
    graph = [f"[1:a]{STEREO},apad=whole_dur={duration:.3f}{',asplit=2' if duck else ''}[vo]{'[key]' if duck else ''}"]
    mix = ["[vo]"]
    n = 2
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        graph.append(f"[{n}:a]{STEREO},atrim=0:{duration:.3f},volume={cfg.music_db}dB[bed]")
        if duck:
            graph.append("[bed][key]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[music]")
            mix.append("[music]")
        else:
            mix.append("[bed]")
        n += 1
    by_file: dict[Path, list[float]] = defaultdict(list)
    for f, at in sfx or []:
        by_file[f].append(at)
    for f, times in sorted(by_file.items()):
        inputs += ["-i", str(f)]
        labels = [f"s{n}_{k}" for k in range(len(times))]
        graph.append(f"[{n}:a]{STEREO},volume={cfg.sfx_db}dB,asplit={len(times)}" + "".join(f"[{x}]" for x in labels))
        for x, at in zip(labels, times, strict=True):
            ms = max(0, round(at * 1000))
            graph.append(f"[{x}]adelay={ms}|{ms}[{x}d]")
            mix.append(f"[{x}d]")
        n += 1
    graph.append(
        f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,"
        f"loudnorm=I={cfg.lufs}:TP={cfg.true_peak}:LRA=11,aresample=48000,{STEREO}[a]"
    )
    return [*inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-shortest", str(out)]  # fmt: skip
