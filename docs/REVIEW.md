# Video rendering review (kinetic typography)

Reviewed 2026-11-14 on branch `autopilot` at `d6e84c8`. Scope: everything that turns a script or voice into video —
`ytstudio/adapters/{kinetic,media,narrator}.py`, `ytstudio/application/steps_video.py`, `steps_shorts.py` (render parts),
and their tests. The web app, DB and YouTube parts were reviewed earlier (see [REVIEW-R1.md](REVIEW-R1.md)).

## How video is made today

```
script ─► spoken_lines() ─► EdgeNarrator (Microsoft TTS, word boundaries) ─► voice.wav ─► encode_audio (-16 LUFS mp3)
                               └─ fallback PiperNarrator (no word times)
units[{text,start,end,words}] ─► kinetic.build_ass() ─► subs.ass ─► ffmpeg color=…+ass= filter ─► mp4
Shorts: master video ─► words_timed() (cue time shared by word length) ─► one Pillow PNG per word ─► ffconcat ─► overlay
```

The text engine is **not** FFmpeg drawtext. It is an ASS subtitle script rendered by libass (`\move`, `\t`, `\fad`), plus a
Pillow-PNG overlay for Shorts captions. All FFmpeg calls already use `subprocess.run([...])` list args — no shell, no
injection risk.

What ran during review: full test suite (171 passed, 4 skipped). On this Mac, `ffmpeg 9.0.1` (Homebrew) is built
**without libass**, so `test_the_subtitle_video_has_the_right_shape…` skips and "Generate video" cannot run locally.

## Findings

| File | Issue | Severity | Fix |
| --- | --- | --- | --- |
| `adapters/media.py:183` `render_subtitle_video` | Needs ffmpeg's `ass` filter. Homebrew ffmpeg 9 has none, so the job fails with a raw ffmpeg stderr. Nothing checks for it up front; `has_ffmpeg()` only checks the binary exists. | high | New renderer draws text in Remotion (no libass). Keep ASS as `--simple` mode with a preflight that names the missing filter. |
| `adapters/media.py:FasterWhisperTranscriber` | Calls `transcribe()` without `word_timestamps=True`; only segment times come back, so per-word sync is impossible. | high | `pipeline/transcribe.py` asks for word timestamps. |
| `adapters/media.py:FasterWhisperTranscriber`, `steps_shorts.py:91` | Hinglish → `language=None` (auto-detect). Whisper usually detects `hi` and writes Devanagari; the spec needs Roman script. | high | Force `language="en"` for Hinglish, and when a script is given, align the script's words onto the ASR times (script spelling wins). |
| `adapters/media.py:words_timed`, `kinetic.timed_words` (fallback) | When word times are missing, a cue's time is shared out by character count — drifts up to ~0.5 s from speech within a long sentence. | med | Only a fallback now; the new path always has real word times (Whisper or Edge word boundaries). |
| `adapters/kinetic.py:timed_words` | If the TTS word count ≠ script token count (Edge drops "—", splits "60-second"), it silently switches to the TTS spelling and loses punctuation, so `cards()` stops splitting at sentence ends. | med | Sequence alignment (`difflib`) of script tokens onto timed words, in `pipeline/transcribe.align`. |
| `adapters/media.py:24-33` `FONTS` | Fixed system font paths; silently falls back to Pillow's bitmap default. Layout is measured with one font (Pillow) and drawn with another (libass resolves "Noto Sans" by name), so widths can disagree and text can overflow. | med | Fonts ship in `renderer/public/fonts` (OFL) and are measured in the same browser that draws them. |
| `adapters/media.py:196` `fontsdir` | Hard-coded `/usr/share/fonts/truetype/noto`; on macOS/Windows it is skipped and libass picks any font. | med | Same as above. |
| `adapters/kinetic.py:layout` | Wraps to 86 % width and shrinks a single over-wide word, but never checks total height; with a smaller font many rows can run off screen. | low | Renderer `fitText` shrinks until the block fits the 5 % safe area in both axes. |
| `adapters/media.py:_ffmpeg` | `subprocess.TimeoutExpired` is not caught, so a hung render surfaces as an untyped crash, not a `PermanentError`. `FileNotFoundError` when ffmpeg is missing likewise. | med | Catch both and raise a clear error (done in the shared helper; the new pipeline reuses it). |
| `adapters/media.py:render_short` | One PNG per spoken word, written and decoded every time — slow for 60 s clips (hundreds of frames). `ffconcat` lines quote paths with `'`, which breaks if a temp path ever contains `'`. | low | Out of scope for the new style; left as is. |
| `adapters/media.py:render_subtitle_video` | Audio is AAC-encoded without loudness normalisation (VO was set to -16 LUFS earlier); spec target is -14 LUFS with music + SFX. | med | `pipeline/mux.py` does loudnorm -14 LUFS / -1.5 dBTP on the final mix. |
| `adapters/media.py:esc` | Filter-option escaping covers `\ : '` but not `, ; [ ]`. Temp paths never contain them today. | low | Not needed in the new path (no file paths inside filtergraphs). |
| `adapters/kinetic.py` | Colours, sizes, timings (`HOLD`, `GAP`, `size=112/92`, `bar_w`, `+34px`) are module constants or inline literals; no theme. | low | `renderer/src/theme.ts` + `pipeline/theme.py`, one source of numbers. |
| `adapters/kinetic.py:stamp` | ASS times are centiseconds — up to 5 ms rounding. Fine at 30 fps (33 ms/frame). | low | New path converts `round(start * fps)` once, in one function, tested. |
| `application/steps_video.py:spoken_lines` | Strips `[...]` as stage directions. The new markup uses `[word]` for a highlight box, so a marked-up script must not go through this function unchanged. | med | New pipeline has its own marker parser that strips markers for TTS/alignment and keeps them for styling. |
| `adapters/narrator.py:PiperNarrator` | Offline voice returns no word timings, so it always falls back to length-based timing. | low | When Piper is used, run Whisper alignment on its output (the CLI does this automatically when no word times exist). |
| `adapters/media.py` | No logging of command, duration or progress; errors are trimmed to the last 500 chars of stderr. | low | Pipeline logs each stage with timing; render progress is streamed from Remotion. |
| `tests/test_vendor_and_media.py` | Render test skips silently without libass; nothing tests filter-graph strings or audio mixing. | med | New tests assert the exact ffmpeg argument lists and run a real mux on generated audio. |

No critical issues: there is no shell string building, temp files use `TemporaryDirectory`, and outputs are written to a
`.part` file then `os.replace`d (readers never see half a file).

## What can be reused vs what must be replaced

| Reuse as is | Reuse, extended | Replace (kept only as `--simple` fallback) |
| --- | --- | --- |
| `media._ffmpeg` (list args, trimmed stderr, error type) | `FasterWhisperTranscriber` → word timestamps + Roman-script forcing | `kinetic.build_ass` / `layout` (ASS typography) |
| `FFmpegMediaProcessor.duration` (ffprobe) | `kinetic.timed_words` → becomes the fallback when no aligner output exists | `media.caption_frame` Pillow captions (Shorts only, untouched) |
| `decode_16k_mono` (avoids faster-whisper's PyAV bug) | `EdgeNarrator` word boundaries → written straight to `words.json`, no ASR needed | |
| `.part` + `os.replace` atomic output | Loudness chain from `encode_audio` → final mix at -14 LUFS | |
| `logging_setup.setup_logging` (JSON logs, redaction) | | |

New architecture: Python orchestrator (`pipeline/`) → `timeline.json` → Remotion (`renderer/`) → FFmpeg mux. See
[ARCHITECTURE.md](ARCHITECTURE.md#kinetic-video-pipeline).

## Status after the rebuild (2026-11-14)

| Finding | Status |
| --- | --- |
| libass required, no preflight | Fixed: the ASS renderer is gone; the web app's "Generate video" uses the new renderer. |
| No word timestamps / Devanagari output | Fixed in `pipeline/transcribe.py` (word timestamps, Hinglish → `en`, script alignment). The web app's Shorts transcription is unchanged. |
| Length-shared timing, TTS/script count mismatch | Fixed by `align()`; `kinetic.timed_words` is only the fallback for the offline voice. |
| Font mismatch / fixed font paths / overflow | Fixed: bundled OFL fonts, measured in the renderer, fit-to-safe-area tested with a 45-letter word in 9:16. |
| `_ffmpeg` timeout / missing binary | Fixed in `ytstudio/adapters/media.py` (clear `PermanentError`). |
| No loudness target on the final mix | Fixed: -14 LUFS (measured -14.1 LUFS on speech + ducked music). |
| Magic numbers | New code reads `THEME` / `config/kinetic.yaml`. |
| Missing tests | `tests/test_kinetic_pipeline.py` (30 tests) incl. a real ffmpeg mux. |
| `render_short` per-word PNGs, `spoken_lines` vs `[markers]` | Not changed: out of scope for this style; the new pipeline does not use them. |
