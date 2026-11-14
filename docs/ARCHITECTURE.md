# Architecture

Ports and adapters. Business rules never import Google, LiteLLM, Celery, Redis, the ORM or FastAPI.

```
interfaces/   web (FastAPI routers + Jinja)        worker.py (Celery app + thin tasks)
    │                     │                                   │
    ▼                     ▼                                   ▼
container.py   ── the only place that picks concrete adapters ──────────────
    │
application/  use_cases.py   pipeline.py (engine + registry)   steps_daily.py   schemas.py
    │  depends only on ↓
ports.py      Protocols: VideoSource, Analytics, TrendProvider, LLM, PromptRepository,
              TokenVault, Cache, DistributedLock, RateLimiter,
              QuotaBudget, Notifier, TaskDispatcher, scorers, repositories + UnitOfWork
domain/       models.py  errors.py  events.py  schedule.py (DST)  metrics.py     (pure, no I/O)
    ▲
adapters/     youtube.py (facade: OAuth + Data + Analytics + quota + error mapping)   trends.py
              llm.py (LiteLLM Router)   prompts.py   vendor_skills.py
              storage.py (local disk)   notify.py   redis_kv.py   token_vault.py   celery_dispatcher.py
              persistence/ (tables, repos + unit of work, read-model queries, web sessions)
```

`mypy` is strict on `domain`, `application`, `ports` and `config`; adapters and interfaces are type-checked with a gradual baseline.

## Request and job flow

- **Web:** a router checks the session and CSRF, calls one use case through the container, and renders a template. Page reads use `adapters/persistence/queries.py`. Every query there is scoped to the signed-in user.
- **Daily run:** `beat.tick` (every 5 min) → `Scheduling.dispatch_due` claims due schedule rows with `FOR UPDATE SKIP LOCKED`, moves `next_run_utc` forward (missed runs catch up once), and calls `PipelineRunner.start`.
- **`PipelineRunner.start`:** creates the run row under its idempotency key `channel:pipeline:local-date` (unique). If that key already finished, nothing happens; if it failed, the run resumes. It then takes a Redis lock `channel:pipeline` with the run id as owner, and hands a step plan to the `TaskDispatcher`.
- **`CeleryDispatcher`:** builds a chain of `pipeline.step` tasks, each routed to the queue its step declares, with task ids `<idem_key>:<step>`. A fan-out step (`pipeline.fan_out`) replaces itself at run time with a chord: one `pipeline.item` per item, then `pipeline.merge`.
- **Each step task:** loads the run's context from the database, skips itself if its output is already saved, runs, saves its output, and updates its status. Errors map to retries:
  - `TransientError` (network, 5xx, 429): exponential backoff with jitter, bounded per queue.
  - `QuotaExceeded`: the step waits and retries at the next Pacific-midnight reset.
  - Anything else: step and run marked failed, a dead-letter row is written, the lock is released, and `JobFailed` is published.
- **LLM retries:** the LiteLLM Router retries and falls back first. Only when it gives up does the adapter raise one `TransientError`, so Celery's retry is the single outer retry.

## Redis layout

DB 0 is the Celery broker and DB 1 the Celery results. DB 2 is the app's (`REDIS_URL`), with these prefixes:

| Prefix | What |
| --- | --- |
| `yt:cache:` | API response cache: searches 6 h, other channels' recent views 24 h |
| `yt:llm:` | LLM response cache, plus the once-a-day budget notice |
| `yt:lock:` | pipeline and sync-job locks |
| `yt:rl:` | LLM token buckets |
| `yt:quota:` | YouTube units per Pacific day, plus search caps |
| `yt:beat:` | Beat and worker heartbeats, sync-job last results |

## Patterns, and why they are here

- **Strategy:** trend providers, LLM model choice (aliases).
- **Registry + Template Method:** pipeline steps.
- **Repository + Unit of Work:** persistence.
- **Adapter:** Google, LiteLLM, Celery, the vendored tools.
- **Facade:** `YouTubeGateway` over the Data and Analytics APIs.
- **Domain events:** `BriefReady`, `JobFailed`, `ChannelNeedsReconnect`, `BudgetReached` and `BeatStale` are written to the log by `LogNotifier` (no in-app, email or Telegram messages). Failures are visible on the Jobs page; a channel needing reconnection shows a banner.

## Guide: add a trend provider

1. In `ytstudio/adapters/trends.py`, write a class with `name = "my_source"` and `fetch(self, q: TrendQuery) -> list[Video]`. If it calls the Data API, go through `YouTubeGateway.execute(...)` so quota is counted. Cache anything expensive.
2. Register it in `Container.trend_providers` (`available["my_source"] = lambda: ...`).
3. Add `"my_source"` to `[trends] providers` in `config/pipelines.toml`.
4. Add a contract test with recorded responses (see `tests/test_youtube_gateway.py`).

It then runs in parallel with the other providers (chord), is deduped, and is ranked by outlier multiple.

## Guide: add a pipeline step

```python
# ytstudio/application/steps_daily.py (or a new module imported in container.py)
@register
class SummariseComments(LLMStep):  # LLMStep: queue = llm, plus self.ask(...)
    name = "summarise_comments"
    needs = ("sync_channel",)  # outputs it reads
    critical = False  # stops when the daily LLM budget is reached

    def run(self, ctx: Context) -> dict[str, Any]:
        out = self.ask(ctx, "fast", "summarise_comments", {"x": ctx.out("sync_channel")}, MySchema)
        return out.model_dump(mode="json")  # saved; a re-run skips this step
```

Then add `"summarise_comments"` to a pipeline in `config/pipelines.toml`, add `prompts/summarise_comments.md` (with a `version:` front matter), and write a unit test with `FakeLLM`. To remove a step, delete its name from the config. For parallel work, set `fan_out = True` and implement `items`, `run_item` and `merge`.

## Guide: add an LLM provider or model

1. Paste the key on the LLM page (sidebar, under Sign out). Keys are stored encrypted in the database, never in `.env`. A new provider needs one line in `PROVIDERS` in `ytstudio/domain/llm.py` and a branch in `ytstudio/adapters/llm_models.py`. A service that speaks the OpenAI API at its own address (like BazaarLink) is one line in `COMPATIBLE` instead: LiteLLM reaches it through its `openai/` route with that base URL.
2. In `config/llm.toml`, add the model to an alias's `models` list (`"provider/model"`), in fallback order. Add a `[prices."provider/model"]` entry if LiteLLM's price map lacks it, and a `[rate_limits.provider]` bucket.
3. Restart the workers. No code changes: code only names the aliases `fast`, `smart` and `long`.
4. To add a new alias, add `[aliases.new]` and use it in a step's `self.ask(ctx, "new", ...)`.

## Prompts

`prompts/<name>.md` holds Jinja templates with a `version:` front matter. They include `_rules.md` (no invented numbers, no view predictions), `skill("yt-…")` (a vendored SKILL.md) and `voice` (your `voice.md`). Each LLM call records `template` and `template_version` (`<version>.<content hash>+<vendor commit>`). Bump `VERSION` on a schema in `application/schemas.py` when its shape changes; it is part of the cache key.

## Vendored tools

`vendor/youtube-agent-skill` is MIT and never modified; its commit is recorded in `VENDORED_COMMIT`. `hookscore.py` and `title.py` are imported directly. `swipe.py` and `retention.py` run as `python -I` subprocesses with `--json`. Their output appears in the UI as a "hook panel score", lint score and similar, always labelled as heuristics.

# Kinetic video pipeline

`python -m pipeline make` turns a voice-over into a kinetic typography explainer. It sits beside the web app and reuses
its FFmpeg runner, audio decoder and logging; the web app's "Generate video" step still uses the ASS renderer
(`ytstudio/adapters/kinetic.py`), which is also the CLI's `--simple` fallback.

```
vo.wav ─► transcribe ─► words.json ─► plan ─► timeline.json ─► Remotion render ─► silent.mp4 ─► mux ─► final.mp4
script/storyboard ─┘  (align)          ▲   (pydantic)                (renderer/)      (h264 yuv420p)   (FFmpeg)
                                segment (auto mode) / LLM (validated)                         voice + ducked music + SFX
                                                                                              loudnorm -14 LUFS, +faststart
```

| Stage | Code | What it does |
| --- | --- | --- |
| Transcribe | `pipeline/transcribe.py` | faster-whisper with `word_timestamps=True`; Hinglish decoded as English so it is Roman script. `align()` lays the script's own words onto the recognised times (`difflib` opcodes: matches take ASR times, misspellings share the span, missed words fill the gap). |
| Segment | `pipeline/segment.py` | sentences end on `. ? ! । —` or a 0.45 s pause; scenes are 3–8 s on sentence boundaries; long sentences split at their longest pause. Used by auto mode. |
| Plan | `pipeline/plan.py` | storyboard YAML (manual) or `auto_story()` / `llm_story()` → `Timeline`. Spoken entries consume words in order; unspoken ones anchor to words (`@word`, `#index`), seconds or offsets. A scene cuts in 0.12 s before its first word. LLM output must pass `LlmPlan` and coverage checks or the rule-based plan is used. |
| Schema | `pipeline/schema.py` | pydantic models of `timeline.json` (`extra="forbid"`, palette-checked colours, ordered scenes). `python -m pipeline gen` writes `renderer/src/types.gen.ts`; a test fails when it is stale. |
| Theme | `pipeline/theme.py` | palette, type scale (fractions of the short side), motion presets in frames @30 fps, shadows, emoji→sticker map → `renderer/src/theme.gen.ts`. No other numbers live in components. |
| Render | `pipeline/render.py`, `renderer/` | `npx remotion render src/index.ts Main --props=…` (list args), progress logged every 10 %. Public dir is `assets/`. |
| Mux | `pipeline/mux.py` | video stream copied (no second encode); voice padded to the video length; music looped, trimmed, sidechain-ducked; SFX placed with `adelay`; one `loudnorm` pass to -14 LUFS / -1.5 dBTP; AAC 192k; `+faststart`. |

## Renderer

- `Video.tsx` mounts one `<Sequence>` per scene; a scene stays mounted under the next for that scene's transition
  (`cut`, `crossfade` 150 ms, `diagonal_wipe`, `split_slide`). Nothing renders until the local fonts are loaded,
  because text is measured.
- `Scene.tsx` paints the background (colour, split panel, diagonal panel), the outline keyword, brackets, deco column
  and watermark, then the elements. Text-like elements (`text_line`, `card`, `tag_pill`, `list_reveal`, `swatches`)
  flow in a column in the layout's text area; elements that overlap in time form a block whose lines keep their place
  (later words hold their space, so nothing reflows when a word is spoken). Everything else is positioned in the
  visual area.
- `layout.ts` gives text and visual areas per layout. In 9:16, side-by-side areas become top/bottom, so the design is
  re-laid, not cropped.
- `fit.ts` measures every word with the real font (`@remotion/layout-utils`) and picks the largest scale ≤ 1 at which
  no word is wider than the text area and the block is no taller than it. One scale per scene keeps hierarchy.
- Every word appears on `Math.round(start × fps)`, the same rule as `pipeline.schema.frame_of`.
- Components: AnimatedWord, TextBlock, HighlightBox, StrikeThrough, SerifReveal, OutlineBgWord, Brackets, Watermark,
  ColorCard, MacWindow (+ AppBody, FinderList, ChatInput, ToolsTable, NoteCard, FolderTree, DropZone), TypingText,
  TypingBox, KeyBadge, Sticker, ListReveal, CommentBubbles, FollowCard, HandCursor, ApproveReject, DraftCards,
  StoryboardRow, TimelineCut, Character, AvatarBadge, TagPill, DmCard, Swatches, Transitions, Outro, InstagramIcon,
  EmojiText, DecoColumn.

## Guide: add a component

1. Add `XProps` and `X(Base)` with `type: Literal["x"]` in `pipeline/schema.py`, and add it to `Element`.
2. `python -m pipeline gen`.
3. Write `renderer/src/components/X.tsx` taking `props` and `at` (scene-relative frame); use `useUnits()`,
   `useScene()`, `useRel()` and the presets in `motion.ts`/`THEME`.
4. Add a case in `Visual` (or `FLOW` + `FlowItem` for a text-column item) in `Scene.tsx`, and use it in
   `demo/story.yaml`.

## Assets and licences

`assets/fonts`: Inter Tight, Playfair Display, Bebas Neue (SIL OFL, licence files alongside). `assets/stickers`:
Microsoft Fluent Emoji 3D (MIT, `LICENSE-FluentEmoji.txt`). Inline emoji in on-screen text are drawn with these
stickers; unknown emoji are dropped, so the system (Apple) emoji font is never used. `assets/brand`: placeholders
drawn for this repo; replace them with your own avatar and character. `assets/sfx`, music: yours.
