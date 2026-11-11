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
