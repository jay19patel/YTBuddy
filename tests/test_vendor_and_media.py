"""Contract tests for the vendored scorer adapters, and FFmpeg/Pillow on a generated 10-second fixture."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fakes import transcript
from ytstudio.adapters import vendor_skills as v
from ytstudio.adapters.media import FFmpegMediaProcessor, PillowImageRenderer, words_timed
from ytstudio.config import ROOT
from ytstudio.domain.models import Cue, RetentionPoint

VENDOR = ROOT / "vendor" / "youtube-agent-skill"


def test_vendor_is_unmodified_and_licensed():
    assert (VENDOR / "LICENSE").read_text().startswith("MIT")
    assert len(list((VENDOR / "skills").glob("*/SKILL.md"))) == 11
    assert len(list((VENDOR / "skills").glob("*/*.py"))) == 6
    assert len(json.loads((VENDOR / "skills/yt-script/hooks.json").read_text())["hooks"]) == 21


def test_hook_scorer_contract():
    out = v.VendorHookScorer(VENDOR).score(
        ["Hello and welcome back to my channel", "You are losing 30% of your salary every month before you see it."]
    )
    assert out[0]["panel_score"] >= out[1]["panel_score"]
    assert {"hook", "panel_score", "band", "formula", "properties", "weakest", "fix"} <= set(out[0])


def test_package_linter_contract():
    r = v.VendorPackageLinter(VENDOR).lint("The ULTIMATE AMAZING INSANE guide to everything you need", "ULTIMATE GUIDE")
    kinds = {i["kind"] for i in r["issues"]}
    assert {"shouting", "vague", "duplicate"} <= kinds and 0 <= r["score"] <= 100


def test_outlier_ranker_contract():
    rows = [{"channel": "a", "title": "x", "views": 100, "url": f"a{i}"} for i in range(4)]
    rows.append({"channel": "a", "title": "Why 3 people quit", "views": 1000, "url": "hit"})
    out = v.VendorOutlierRanker(VENDOR).rank(rows, 2.0)
    assert [o["url"] for o in out["outliers"]] == ["hit"] and out["outliers"][0]["multiple"] == 10.0


def test_chapters_edl_retention_contracts():
    cues = transcript(30)
    ch = v.VendorChapterBuilder(VENDOR).build(cues)
    assert ch["valid"] and ch["chapters"][0]["start"] == 0
    assert v.VendorChapterBuilder(VENDOR).build(cues[:3])["valid"] is False
    edl = v.VendorEditDecisionList(VENDOR).build([*cues, Cue(400, 401, "um")])
    assert any(c["kind"] == "FILLER" for c in edl["cuts"])
    points = [RetentionPoint(i / 20, 1 - i / 25) for i in range(21)]
    ret = v.VendorRetentionAnalyzer(VENDOR).analyze(points, 300, cues)
    assert ret["available"] and "hook_leak" in ret
    assert v.VendorRetentionAnalyzer(VENDOR).analyze(points[:3], 300, None)["available"] is False


def test_words_are_timed_inside_the_clip():
    timed = words_timed([Cue(0, 4, "one two three four"), Cue(10, 12, "outside")], 1.0, 5.0)
    assert [t[2] for t in timed] == [0, 1, 2, 3] and timed[0][0] == 0.0 and timed[-1][1] == pytest.approx(3.0)


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def fixture_video(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        check=True,
    )
    return out


def probe(path: Path) -> dict:
    return json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


@needs_ffmpeg
def test_ffmpeg_renders_vertical_short_with_captions(fixture_video, tmp_path):
    m = FFmpegMediaProcessor()
    assert m.duration(fixture_video) == pytest.approx(10, abs=0.1)
    out = tmp_path / "short.mp4"
    m.render_short(fixture_video, 1.0, 9.0, [Cue(1.5, 4.0, "Hello there friend"), Cue(5, 8, "second line")], out)
    info = probe(out)
    v_ = next(s for s in info["streams"] if s["codec_type"] == "video")
    a_ = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v_["codec_name"], v_["width"], v_["height"]) == ("h264", 1080, 1920)
    assert a_["codec_name"] == "aac" and a_["sample_rate"] == "48000"
    assert float(info["format"]["duration"]) == pytest.approx(8.0, abs=0.3)
    assert not list(tmp_path.glob(".*part*"))  # temp file was renamed into place


@needs_ffmpeg
def test_loudness_is_normalised_near_minus_14_lufs(fixture_video, tmp_path):
    out = tmp_path / "short.mp4"
    FFmpegMediaProcessor().render_short(fixture_video, 0, 10, [], out)
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(out), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    integrated = float(p.stderr.rsplit("I:", 1)[1].split("LUFS")[0])
    assert integrated == pytest.approx(-14, abs=2)


@needs_ffmpeg
def test_frame_and_cover(fixture_video, tmp_path):
    frame, cover = tmp_path / "f.jpg", tmp_path / "c.jpg"
    FFmpegMediaProcessor().frame(fixture_video, 1.0, frame)
    PillowImageRenderer().cover(frame, "Rent trick nobody tells you", cover)
    from PIL import Image

    assert Image.open(cover).size == (1080, 1920)


def test_bad_media_is_permanent_error(tmp_path):
    from ytstudio.domain.errors import PermanentError

    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"nope")
    with pytest.raises(PermanentError):
        FFmpegMediaProcessor().duration(bad)


@needs_ffmpeg
def test_audio_is_decoded_to_16k_mono_floats_for_whisper(fixture_video):
    pytest.importorskip("numpy")  # only installed with the media extra
    from ytstudio.adapters.media import decode_16k_mono

    samples = decode_16k_mono(fixture_video)
    assert str(samples.dtype) == "float32" and abs(len(samples) - 10 * 16000) < 2000 and abs(samples).max() > 0.1


def test_words_take_the_voices_own_timing_or_share_a_sentence_by_length():
    from ytstudio.adapters.kinetic import timed_words

    units = [
        {
            "text": "Stop overpaying rent today.",
            "start": 0.0,
            "end": 2.0,
            "words": [
                {"text": w, "start": i * 0.5, "end": i * 0.5 + 0.4}
                for i, w in enumerate(["Stop", "overpaying", "rent", "today"])
            ],
        },
        {"text": "Ask for receipts", "start": 3.0, "end": 4.5, "words": []},  # no word times: shared out by length
    ]
    words = timed_words(units)
    assert [w.text for w in words] == ["Stop", "overpaying", "rent", "today.", "Ask", "for", "receipts"]
    assert (
        words[1].start == 0.5
        and words[3].text == "today."
        and words[4].start == 3.0
        and abs(words[-1].end - 4.5) < 0.01
    )


@needs_ffmpeg
def test_the_script_video_is_kinetic_typography_timed_to_the_voice(fixture_video, tmp_path):
    import subprocess
    from pathlib import Path

    from PIL import Image

    from ytstudio.adapters.media import FFmpegMediaProcessor

    if not (Path(__file__).resolve().parent.parent / "renderer" / "node_modules" / "remotion").exists():
        pytest.skip("renderer not installed (cd renderer && npm ci)")
    m = FFmpegMediaProcessor()
    units = [  # like the Microsoft voice: punctuation in the script, none in the reported words
        {
            "text": "Stop overpaying [rent] today.",
            "start": 0.3,
            "end": 2.5,
            "words": [
                {"text": w, "start": 0.3 + i * 0.5, "end": 0.7 + i * 0.5}
                for i, w in enumerate(["Stop", "overpaying", "rent", "today"])
            ],
        },
        {"text": "Ask for receipts", "start": 3.0, "end": 4.5, "words": []},
    ]
    out = tmp_path / "short.mp4"
    m.render_script_video(units, fixture_video, True, out)
    streams = probe(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["pix_fmt"]) == (1080, 1920, "yuv420p")
    assert any(s["codec_type"] == "audio" for s in streams)
    assert abs(float(probe(out)["format"]["duration"]) - m.duration(fixture_video)) < 0.6
    shots = []
    for i, at in enumerate((0.05, 2.2)):
        png = tmp_path / f"f{i}.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(out), "-frames:v", "1", str(png)], check=True
        )
        shots.append(Image.open(png).convert("L"))
    bright = lambda img: sum(1 for px in img.getdata() if px > 200)  # noqa: E731
    assert bright(shots[1]) > bright(shots[0]) + 2000  # white words arrive as they are said


@needs_ffmpeg
def test_the_microsoft_voice_loses_the_silence_padded_around_each_clip(tmp_path, monkeypatch):
    import subprocess
    import sys
    import wave

    from ytstudio.adapters import narrator

    clip = tmp_path / "clip.mp3"  # 1 s silence, 1 s of voice, 1 s silence, like the service sends
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=300:d=1", "-af", "adelay=1000,apad=pad_dur=1",
         "-ar", "24000", "-ac", "1", str(clip)],
        check=True,
    )  # fmt: skip

    async def fake_all(self, edge_tts, lines, voice):
        return [(clip.read_bytes(), [(1.0, 0.5, "namaste"), (1.5, 0.5, "dosto")]) for _ in lines]

    monkeypatch.setitem(sys.modules, "edge_tts", object())
    monkeypatch.setattr(narrator.EdgeNarrator, "_all", fake_all)
    out = tmp_path / "vo.wav"
    timings = narrator.EdgeNarrator().speak(
        [("namaste dosto", 0.2), ("namaste dosto", 0.0)], "en-IN-PrabhatNeural", out
    )
    with wave.open(str(out)) as w:
        total = w.getnframes() / w.getframerate()
    keep = 1.0 + 2 * narrator.EDGE_EDGE
    assert abs(total - (2 * keep + 0.2)) < 0.05  # was 3 s per clip
    first, second = timings
    assert abs(first["words"][0]["start"] - narrator.EDGE_EDGE) < 0.01 and abs(first["end"] - keep) < 0.05
    assert abs(second["words"][0]["start"] - (keep + 0.2 + narrator.EDGE_EDGE)) < 0.05
