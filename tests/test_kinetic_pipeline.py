"""The kinetic typography pipeline: schema, timing, segmentation, markers, planner, ffmpeg commands, config."""

import json
import shutil
import subprocess
from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import codegen, markup, mux, plan, segment
from pipeline.cli import cut, fake_words, video_info
from pipeline.schema import Timeline, Word, frame_of
from pipeline.settings import AudioCfg, Settings, load
from pipeline.transcribe import TimedWord, align

ROOT = Path(__file__).resolve().parent.parent
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def tw(*items: tuple[str, float, float]) -> list[TimedWord]:
    return [TimedWord(word=w, start=a, end=b) for w, a, b in items]


def evenly(text: str, start: float = 0.0, step: float = 0.4) -> list[TimedWord]:
    return [
        TimedWord(word=w, start=start + i * step, end=start + i * step + step * 0.8) for i, w in enumerate(text.split())
    ]


# ---------------------------------------------------------------- schema


def test_demo_timeline_is_valid_and_matches_the_storyboard():
    t = Timeline.model_validate_json((ROOT / "demo" / "timeline.json").read_text())
    assert t.video.duration_frames == 22 * 30 and t.scenes[0].layout == "center_bracket"
    cfg = Settings()
    words = fake_words(plan.load_story(ROOT / "demo" / "story.yaml"), 90.6)
    fresh = cut(plan.plan(plan.load_story(ROOT / "demo" / "story.yaml"), words, video_info(cfg, "16x9"), 90.6), 22)
    assert fresh.model_dump() == t.model_dump()  # regenerate with: python -m pipeline demo --write-timeline


def test_schema_rejects_unknown_colours_fields_and_overlapping_scenes():
    with pytest.raises(ValidationError, match="unknown colour"):
        Word(text="a", start=0, end=1, color="purple")
    Word(text="a", start=0, end=1, color="#12ABef")
    data = json.loads((ROOT / "demo" / "timeline.json").read_text())
    bad = json.loads(json.dumps(data))
    bad["scenes"][0]["elements"][0]["props"]["wobble"] = 3  # an LLM inventing a field
    with pytest.raises(ValidationError):
        Timeline.model_validate(bad)
    bad = json.loads(json.dumps(data))
    bad["scenes"][1]["start"] = bad["scenes"][0]["start"]
    with pytest.raises(ValidationError, match="before scene"):
        Timeline.model_validate(bad)
    bad = json.loads(json.dumps(data))
    bad["scenes"][0]["elements"][0]["type"] = "hologram"
    with pytest.raises(ValidationError):
        Timeline.model_validate(bad)


def test_generated_typescript_is_up_to_date():
    for path, text in codegen.outputs().items():
        assert path.read_text() == text, f"{path.name} is stale: run python -m pipeline gen"
    assert "export type TextLineProps = {" in codegen.types_ts() and '"hero"' in codegen.types_ts()


# ---------------------------------------------------------------- timing


@pytest.mark.parametrize(
    ("sec", "fps", "frame"),
    [(0, 30, 0), (1.0, 30, 30), (0.016, 30, 0), (0.017, 30, 1), (2.5, 24, 60), (89.99, 30, 2700)],
)
def test_word_times_become_the_nearest_frame(sec, fps, frame):
    assert frame_of(sec, fps) == frame


def test_alignment_keeps_script_spelling_and_fills_words_the_asr_missed():
    heard = tw(("mera", 0.0, 0.3), ("vidio", 0.3, 0.6), ("editor", 0.6, 1.0), ("na", 1.5, 1.6), ("salary", 1.6, 2.0))
    out = align(["mera", "video", "editor", "—", "na", "~salary~"], heard)
    assert [w.word for w in out] == ["mera", "video", "editor", "—", "na", "~salary~"]
    assert out[1].start == 0.3 and out[2].start == 0.6  # misspelt word takes the recognised time
    assert 1.0 <= out[3].start <= 1.5  # "—" was never heard: placed in the gap
    assert out[5].start == 1.6 and all(a.start <= b.start for a, b in pairwise(out))


def test_alignment_handles_empty_asr():
    out = align(["a", "b"], [])
    assert len(out) == 2 and out[0].start == 0.0


# ---------------------------------------------------------------- segmentation


def test_scenes_start_on_sentences_and_last_three_to_eight_seconds():
    text = (
        "ek. " * 3
        + "yeh lambi line hai jo bahut der tak chalti rahti hai aur khatam hi nahi hoti kabhi bhi yaar sach mein."
    )
    words = evenly(text, step=0.5)
    scenes = segment.scenes(words, 3, 8)
    assert sorted(i for s in scenes for i in s) == list(range(len(words)))  # every word once, in order
    for s in scenes:
        span = words[s[-1]].end - words[s[0]].start
        assert span <= 8 + 1e-6
    assert all(words[s[0] - 1].word.endswith(".") or s[0] == 0 or len(s) > 1 for s in scenes)
    assert words[scenes[0][-1]].end - words[scenes[0][0]].start >= 1.0  # three short sentences merged


def test_a_pause_ends_a_sentence():
    words = tw(("a", 0, 0.2), ("b", 0.25, 0.4), ("c", 1.5, 1.7))
    assert segment.sentences(words) == [[0, 1], [2]]


# ---------------------------------------------------------------- markers


def test_emphasis_markers_become_word_styles():
    toks = markup.parse("na ~salary~ usi ne [edit|white] *60* {cheez|crimson} ab !!magic!! [best reels]")
    assert [(t.text, t.style, t.color) for t in toks] == [
        ("na", "normal", None),
        ("salary", "strike", None),
        ("usi", "normal", None),
        ("ne", "normal", None),
        ("edit", "highlight", "white"),
        ("60", "bold", None),
        ("cheez", "accent", "crimson"),
        ("ab", "normal", None),
        ("magic", "serif", None),
        ("best", "highlight", None),
        ("reels", "highlight", None),
    ]
    assert markup.plain("sirf [kaam ki cheez.]") == "sirf kaam ki cheez."


@pytest.mark.parametrize("bad", ["an [open box", "a ~strike", "a {x|purple}", "stray } here"])
def test_broken_markers_are_errors(bad):
    with pytest.raises(ValueError):
        markup.parse(bad)


def test_markers_round_trip_through_the_auto_storyboard():
    for t in markup.parse("[edit|white] ~na~ !!magic|gold!! *60*"):
        assert markup.parse(plan.marked(t)) == [t]


# ---------------------------------------------------------------- planner


def story(content: list[dict]) -> plan.Story:
    return plan.Story(scenes=[plan.StoryScene(bg="orange", content=content)])


def test_storyboard_words_appear_when_spoken_and_unspoken_entries_attach_to_the_next_line():
    s = story(
        [
            {"lead": "aur yeh reel bhi"},
            {"sticker": "film", "at": "@edit"},
            {"tag_pill": "step 01"},
            {"hero": "usi ne [edit|white]"},
        ]
    )
    words = evenly("aur yeh reel bhi usi ne edit", start=1.0)
    t = plan.plan(s, words, video_info(Settings(), "16x9"), 5.0)
    els = t.scenes[0].elements
    assert [e.type for e in els] == ["text_line", "sticker", "tag_pill", "text_line"]
    assert els[1].start == pytest.approx(words[6].start)  # @edit
    assert els[2].start == pytest.approx(words[4].start)  # attaches to the next spoken line
    hero = els[3].props.words
    assert [(w.text, w.style, w.color) for w in hero][-1] == ("edit", "highlight", "white") and hero[
        0
    ].start == pytest.approx(words[4].start)
    assert t.video.duration_frames == 150


def test_clear_ends_earlier_text_and_a_new_window_replaces_the_old_one():
    s = story(
        [
            {"hero": "one two"},
            {"ui_window": "app"},
            {"hero": "three", "clear": True},
            {"ui_window": "finder", "at": "@three"},
        ]
    )
    t = plan.plan(s, evenly("one two three"), video_info(Settings(), "16x9"), 3.0)
    first, win1, third, win2 = t.scenes[0].elements
    assert first.end == third.start and win1.end == win2.start and third.end is None


def test_scenes_cut_just_before_their_first_word_and_a_wordless_outro_extends_the_video():
    s = plan.Story(
        scenes=[
            plan.StoryScene(content=[{"hero": "a b"}]),
            plan.StoryScene(content=[{"hero": "c d"}]),
            plan.StoryScene(layout="outro", duration=4, content=[{"outro": "day 07"}]),
        ]
    )
    words = evenly("a b c d", step=1.0)
    t = plan.plan(s, words, video_info(Settings(), "16x9"), 4.0)
    assert t.scenes[0].start == 0 and t.scenes[1].start == pytest.approx(2 - plan.LEAD_IN)
    assert t.scenes[2].start == pytest.approx(words[-1].end + plan.HOLD)
    assert t.scenes[2].end == pytest.approx(t.scenes[2].start + 4) and t.video.duration_frames == round(
        t.scenes[2].end * 30
    )


def test_planner_explains_a_word_count_mismatch_and_a_missing_anchor():
    with pytest.raises(ValueError, match="spoken words"):
        plan.plan(story([{"hero": "a b c"}]), evenly("a b"), video_info(Settings(), "16x9"), 2)
    with pytest.raises(ValueError, match="no '@zzz'"):
        plan.plan(
            story([{"hero": "a b"}, {"sticker": "key", "at": "@zzz"}]), evenly("a b"), video_info(Settings(), "16x9"), 2
        )


def test_auto_storyboard_covers_every_word_and_builds_a_valid_timeline():
    text = (
        "pichhli video ke baad sabne ek hi cheez poochhi. toh aaj poora system khol ke rakh deta hoon. ab asli magic."
    )
    words = evenly(text)
    tokens = [markup.Token(w.word) for w in words]
    s = plan.auto_story(tokens, words, 3, 8, character=True)
    assert [t.text for t in plan.script_of(s)] == [t.text for t in tokens]  # every word, in order
    assert any(t.style == "highlight" for t in plan.script_of(s))  # the key word of a hero line is boxed
    for sc in s.scenes:
        assert sc.layout == "center_bracket" or any("sticker" in e for e in sc.content)
        assert sc.bg_word is None or len(sc.bg_word) <= 7  # short enough to read when huge
    stickers = [e["sticker"] for sc in s.scenes for e in sc.content if "sticker" in e]
    assert all(a != b for a, b in pairwise(stickers))  # never the same sticker twice in a row
    t = plan.plan(s, words, video_info(Settings(), "9x16"), words[-1].end + 1)
    assert (t.video.width, t.video.height) == (1080, 1920) and len(t.scenes) >= 2
    assert not plan.missing_assets(t, ROOT / "assets")


def test_missing_assets_are_reported():
    t = plan.plan(story([{"hero": "a"}, {"sticker": "unicorn"}]), evenly("a"), video_info(Settings(), "16x9"), 1)
    assert "stickers/unicorn.png" in plan.missing_assets(t, ROOT / "assets")


# ---------------------------------------------------------------- ffmpeg / remotion commands


def test_mux_command_is_a_list_with_ducking_sfx_loudness_and_faststart(tmp_path):
    pop = tmp_path / "pop.wav"
    cmd = mux.mux_cmd(
        Path("v.mp4"), Path("vo.wav"), Path("out.mp4"), 12.5, AudioCfg(), Path("m.mp3"), [(pop, 1.0), (pop, 2.5)]
    )
    assert cmd[:4] == ["-i", "v.mp4", "-i", "vo.wav"] and cmd[-1] == "out.mp4"
    assert cmd[cmd.index("-stream_loop") : cmd.index("-stream_loop") + 4] == ["-stream_loop", "-1", "-i", "m.mp3"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "apad=whole_dur=12.500" in graph and "sidechaincompress" in graph and "atrim=0:12.500" in graph
    assert "adelay=1000|1000" in graph and "adelay=2500|2500" in graph and "[3:a]" in graph  # pop is input 3
    assert "loudnorm=I=-14:TP=-1.5" in graph and "amix=inputs=4" in graph
    assert cmd[cmd.index("-c:v") : cmd.index("-c:v") + 2] == ["-c:v", "copy"] and "+faststart" in cmd


def test_mux_without_music_or_sfx_is_just_the_voice():
    graph = mux.mux_cmd(Path("v.mp4"), Path("vo.wav"), Path("o.mp4"), 3, AudioCfg())[5]
    assert "amix=inputs=1" in graph and "sidechain" not in graph


def test_remotion_commands():
    from pipeline import render

    cmd = render.render_cmd("Main", Path("/t/p.json"), Path("/t/o.mp4"), 18, 4)
    assert cmd[:5] == ["npx", "remotion", "render", "src/index.ts", "Main"]
    assert "--props=/t/p.json" in cmd and "--pixel-format=yuv420p" in cmd and "--concurrency=4" in cmd
    assert render.still_cmd("Demo", None, 30, Path("x.png"))[-1] == "--frame=30"


@needs_ffmpeg
def test_mux_runs_on_real_audio(tmp_path):
    from ytstudio.adapters.media import FFmpegMediaProcessor, _ffmpeg

    v, vo, m, out = tmp_path / "v.mp4", tmp_path / "vo.wav", tmp_path / "m.wav", tmp_path / "o.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v))
    _ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=2", str(vo))
    _ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=1", str(m))
    _ffmpeg(*mux.mux_cmd(v, vo, out, 3.0, AudioCfg(), m, [(m, 0.5)]))
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)],
            capture_output=True,
            text=True,
        ).stdout
    )
    kinds = {s["codec_type"]: s for s in probe["streams"]}
    assert kinds["video"]["pix_fmt"] == "yuv420p" and kinds["audio"]["codec_name"] == "aac"
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.2  # voice padded to the video, not cut to 2 s
    assert abs(FFmpegMediaProcessor().duration(out) - 3.0) < 0.2


# ---------------------------------------------------------------- config


def test_config_loads_and_rejects_typos(tmp_path):
    cfg = load()
    assert cfg.brand.handle.startswith("@") and cfg.size("9x16") == (1080, 1920) and cfg.audio.lufs == -14
    with pytest.raises(ValueError, match="unknown format"):
        cfg.size("4x3")
    bad = tmp_path / "k.yaml"
    bad.write_text("audio:\n  lufz: -14\n")
    with pytest.raises(ValidationError):
        load(bad)
    assert load(tmp_path / "missing.yaml").fps == 30
