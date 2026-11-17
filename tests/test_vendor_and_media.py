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
from ytstudio.domain.errors import PermanentError
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


def test_chatterbox_runs_its_own_python_or_its_server_and_never_falls_back_to_another_voice(tmp_path, monkeypatch):
    import wave

    from ytstudio.adapters import narrator

    fake = tmp_path / "fake_python.py"  # stands in for the Chatterbox venv: writes 0.5 s of silence per sentence
    fake.write_text(
        "#!/usr/bin/env python3\nimport json,sys,wave\na=sys.argv\nlines=json.load(open(a[a.index('--lines')+1]))\n"
        "out=a[a.index('--out')+1]\nfor i,_ in enumerate(lines):\n"
        "    w=wave.open(f'{out}/{i}.wav','wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)\n"
        "    w.writeframes(b'\\0\\0'*12000);w.close()\n"
    )
    fake.chmod(0o755)
    cb = narrator.ChatterboxNarrator(str(fake))
    monkeypatch.setattr(narrator, "CHATTERBOX_SCRIPT", fake)  # the "script" is the fake itself; run via its shebang
    out = tmp_path / "v.wav"
    t = cb.speak([("One.", 0.5), ("Two.", 0.0)], "en", out)
    assert [(round(x["start"], 2), round(x["end"], 2)) for x in t] == [(0.0, 0.5), (1.0, 1.5)]
    with wave.open(str(out)) as w:
        assert w.getnframes() == 36000
    assert t[0]["words"] == []

    import base64
    import io
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    asked: list[dict] = []

    class Server(BaseHTTPRequestHandler):  # stands in for tools/chatterbox_server.py
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            asked.append(body)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * 24000)
            raw = json.dumps({"wavs": [base64.b64encode(buf.getvalue()).decode()] * len(body["lines"])}).encode()
            self.send_response(200), self.send_header("Content-Length", str(len(raw))), self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Server)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        served = narrator.ChatterboxNarrator(url=f"http://127.0.0.1:{srv.server_port}/")
        t = served.speak([("एक।", 0.25), ("दो।", 0.0)], "hi", tmp_path / "s.wav")
        assert [(x["start"], x["end"]) for x in t] == [(0.0, 1.0), (1.25, 2.25)]
        assert asked == [{"lines": ["एक।", "दो।"], "lang": "hi", "take": 0}]
    finally:
        srv.shutdown()
    down = narrator.ChatterboxNarrator(url="http://127.0.0.1:9")  # nothing listens there
    with pytest.raises(PermanentError, match="Start it on the Mac"):
        down.speak([("One.", 0.0)], "en", tmp_path / "d.wav")

    class Edge:
        def speak(self, lines, voice, out):
            raise AssertionError("a Chatterbox voice must never fall back to another voice")

    texts = [("Hi.", 0.1)]
    with pytest.raises(PermanentError, match="not set up"):
        narrator.VoiceNarrator(Edge(), None, None).speak(texts, "chatterbox-en", tmp_path / "x.wav")  # type: ignore[arg-type]

    class Box:
        def speak(self, lines, language, out, on_progress=None):
            self.language = language
            return []

    box = Box()
    v = narrator.VoiceNarrator(Edge(), None, box)  # type: ignore[arg-type]
    assert v.speak(texts, "chatterbox-hinglish", tmp_path / "x.wav")[1] == "Chatterbox (open source)"
    assert box.language == "hi"  # Hinglish is read by the Hindi model (from its Devanagari copy)


def test_words_of_a_voice_without_word_times_land_on_the_speech_that_was_heard():
    from ytstudio.adapters.kinetic import on_speech

    units = [
        {"text": "Aaj FastAPI seekhenge.", "start": 0.0, "end": 4.0},  # read from Devanagari: 5 heard words for 3
        {"text": "Bas itna.", "start": 4.3, "end": 6.0},
        {"text": "Silent here.", "start": 6.3, "end": 7.3},
    ]
    heard = [(0.5, 0.8), (1.0, 1.2), (1.2, 1.4), (1.4, 1.6), (2.5, 3.0), (4.4, 4.8), (5.0, 5.5)]
    w = on_speech(units, heard)
    assert [(x.text, x.start, x.end) for x in w[:5]] == [
        ("Aaj", 0.5, 0.8),
        ("FastAPI", 1.0, 1.4),  # the speech, not the pause before it: the text keeps in sync
        ("seekhenge.", 1.4, 3.0),
        ("Bas", 4.4, 4.8),
        ("itna.", 5.0, 5.5),
    ]
    assert [x.unit for x in w[5:]] == [2, 2] and w[5].start == 6.3  # heard nothing: shared by length


def test_chatterbox_sentences_are_cut_where_their_last_word_ends_and_babble_is_said_again(tmp_path):
    import math
    import struct

    from ytstudio.adapters import narrator

    # what Whisper heard in real Chatterbox takes: one clean, one running on with made-up words
    clean = [("यह", 0.0, 0.28), ("न्युमेडिकर", 0.28, 0.86), ("डेटा", 0.86, 1.1), ("के", 1.1, 1.26), ("लिए", 1.26, 1.38)]
    clean += [("बना", 1.38, 1.6), ("हैं", 1.6, 1.86)]
    assert narrator.sentence_end("यह न्यूमेरिकल डेटा के लिए बना है।", clean)[0] == 6
    babble = [("सिर्फ", 0.0, 0.3), ("नंबर", 0.3, 0.6), ("सीहा", 0.6, 0.88), ("जाँते", 0.88, 1.2), ("हैं", 1.2, 1.78)]
    babble += [("अन", 1.78, 1.96), ("दीवन", 1.96, 2.24), ("एक", 2.24, 2.44), ("उच", 2.44, 2.58), ("कर", 2.58, 2.74)]
    babble += [("नामा", 2.74, 3.06), ("सिर्फ", 3.88, 4.44), ("नंबर", 4.44, 4.62)]
    k, said, extra = narrator.sentence_end("सिर्फ नंबर्स यहाँ चलते हैं।", babble)
    assert k == 4 and said > 0.6 and extra > 0.5
    # the English words of a Hinglish line come back in Roman letters: still the whole line, nothing extra
    roman = [("Manual", 0.0, 0.4), ("trading", 0.4, 0.8), ("छोड़", 0.8, 1.0), ("दो", 1.0, 1.2)]
    k, said, extra = narrator.sentence_end("मैन्युअल ट्रेडिंग छोड़ दो।", roman)
    assert k == 3 and said > 0.8 and extra == 0

    rate = narrator.CHATTERBOX_RATE  # 1 s of voice, 1 s of silence, 0.5 s of murmur

    def tone(seconds: float, amp: int) -> bytes:
        return b"".join(struct.pack("<h", int(amp * math.sin(i / 7))) for i in range(int(seconds * rate)))

    take = tone(1, 12000) + tone(1, 0) + tone(0.5, 300)
    cut, lead = narrator.trimmed(take, rate, [("a", 0.0, 0.5), ("b", 0.5, 0.95)], 1)
    assert lead == 0 and 1.0 <= len(cut) / 2 / rate <= 1.15

    calls = []

    def listen(wav, lang):  # the first take babbles, the second is clean
        calls.append(lang)
        return babble if len(calls) == 1 else babble[:5]

    cb = narrator.ChatterboxNarrator(url="http://x", listen=listen)
    takes = []
    cb._say = lambda texts, language, work, take, *_: (
        takes.append(take) or [tone(1, 12000) + tone(2, 9000)] * len(texts)
    )  # type: ignore[method-assign]
    timings = cb.speak([("सिर्फ नंबर्स यहाँ चलते हैं।", 0.3)], "hi", tmp_path / "v.wav")
    assert calls == ["hi", "hi"] and takes == [0, 1]  # the retake samples a new seed
    assert timings[0]["end"] < 2.3 and len(timings[0]["heard"]) == 5  # cut after "हैं" (1.78 s) and its fading tail


def test_chatterbox_server_gets_a_few_sentences_at_a_time_and_progress_is_told(tmp_path):
    import wave

    from ytstudio.adapters import narrator

    asked: list[tuple[list[str], int]] = []
    told: list[tuple[int, int]] = []

    def server(texts, language, work, take, first=0):
        asked.append((texts, first))
        for i, _ in enumerate(texts, start=first):
            with wave.open(str(work / f"{i}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(narrator.CHATTERBOX_RATE)
                w.writeframes(bytes([i]) * 4)

    cb = narrator.ChatterboxNarrator(url="http://127.0.0.1:8765")
    cb._ask_server = server  # type: ignore[method-assign]
    texts = [f"line {i}" for i in range(9)]
    clips = cb._say(texts, "en", tmp_path, 0, lambda done, total: told.append((done, total)))
    assert [first for _, first in asked] == [0, 4, 8] and [len(t) for t, _ in asked] == [4, 4, 1]
    assert told == [(4, 9), (8, 9), (9, 9)] and [c[0] for c in clips] == list(range(9))  # in order
