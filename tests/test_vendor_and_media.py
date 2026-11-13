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


def test_subtitle_words_are_grouped_and_timed_inside_each_spoken_line():
    from ytstudio.adapters.media import chunk_words, subtitle_words

    assert chunk_words([f"w{i}" for i in range(9)]) == [["w0", "w1", "w2", "w3", "w4"], ["w5", "w6", "w7", "w8"]]
    assert chunk_words([]) == []
    words = subtitle_words(
        [{"text": "one two three four", "start": 1.0, "end": 3.0}, {"text": "five", "start": 4.0, "end": 5.0}]
    )
    assert [w[3][w[2]] for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0][0] == 1.0 and abs(words[3][1] - 3.0) < 0.01 and words[4][0] == 4.0


@needs_ffmpeg
def test_subtitle_video_is_white_with_black_text_in_the_right_shape(fixture_video, tmp_path):
    from PIL import Image

    from ytstudio.adapters.media import FFmpegMediaProcessor, subtitle_frame

    m = FFmpegMediaProcessor()
    units = [
        {"text": "Stop overpaying rent today", "start": 0.3, "end": 2.5},
        {"text": "Ask for receipts", "start": 3.0, "end": 4.5},
    ]
    for vertical, size in ((True, (1080, 1920)), (False, (1920, 1080))):
        out = tmp_path / f"v{vertical}.mp4"
        m.render_subtitle_video(units, fixture_video, vertical, out)
        streams = probe(out)["streams"]
        video = next(s for s in streams if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == size and any(s["codec_type"] == "audio" for s in streams)
        assert abs(float(probe(out)["format"]["duration"]) - m.duration(fixture_video)) < 0.6
    frame = tmp_path / "f.png"
    subtitle_frame(["Stop", "overpaying", "rent"], 1, (1080, 1920), frame)
    img = Image.open(frame).convert("RGB")
    colours = {c for _, c in img.getcolors(maxcolors=100000)}
    assert img.getpixel((2, 2)) == (255, 255, 255) and (20, 20, 20) in colours  # white screen, black text
    assert any(c[0] > 200 and c[1] < 90 for c in colours)  # the word being said is red
