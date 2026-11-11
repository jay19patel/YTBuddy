# YTStudio (YT Autopilot)

A self-hosted research and content-prep assistant for your YouTube channels. Every day, for each channel, it:

1. syncs your uploads and reads your last 90 days against your own median,
2. finds outlier videos in your niche (views ÷ that channel's own median),
3. writes 5 ranked ideas, then for the top one a scored hook, a full script, 3 linted title + thumbnail pairs, and SEO,
4. cuts Shorts from your master render (captions or Whisper, 9:16, karaoke captions, −14 LUFS), with covers and titles.

**Nothing is ever published.** Everything lands in a review queue as "needs review". You record, edit, design and upload yourself. Numbers shown are real numbers from the APIs; nothing predicts views.

## Run it locally (no Docker)

Needs Python 3.12, [uv](https://docs.astral.sh/uv/), Redis, and FFmpeg (`brew install redis ffmpeg`).

```bash
cp .env.example .env          # fill in: TOKEN_ENCRYPTION_KEY, ALLOWED_EMAILS, GEMINI_API_KEY, MISTRAL_API_KEY
uv sync
uv run python -m ytstudio.cli migrate                                   # creates/upgrades the database
uv run main.py                                                          # web app on http://localhost:8000
uv run celery -A ytstudio.interfaces.worker worker -Q io,llm,media -c 4 # jobs
uv run celery -A ytstudio.interfaces.worker beat                        # schedule ticks (run exactly one)
```

The default database is SQLite (`ytstudio.db`), fine for one person on one machine. Master files, rendered Shorts and covers are stored on local disk in `media/`. There are no email or Telegram notifications: failures show on the Jobs page, and a channel that needs reconnecting shows a banner. For Whisper (used when a video has no captions): `uv sync --extra media`.

## Run it with Docker

```bash
cp .env.example .env   # fill it in; set SITE_ADDRESS to your domain for automatic HTTPS
# YTStudioCred.json must sit next to docker-compose.yml (mounted read-only)
docker compose up -d --build
```

Services: `web` (Caddy, the only published port), `api`, `worker-io`, `worker-llm`, `worker-media`, `beat`, `flower` (127.0.0.1:5555, password), `postgres`, `redis` (password, not published). Masters and Shorts are stored on the local `media` volume, shared by api and workers. Moving an existing SQLite database into Postgres: `python -m ytstudio.cli copy-sqlite ytstudio.db` with `DATABASE_URL` pointing at the Postgres database.

## Upgrading from the old single-file app

`python -m ytstudio.cli migrate` recognises the old database, keeps every row, encrypts the stored Google tokens with `TOKEN_ENCRYPTION_KEY`, and imports `tokens/*.json` once. Then delete `tokens/`. Sign in with an email from `ALLOWED_EMAILS`: the first user to sign in owns the channels that were connected before logins existed.

## Google Cloud setup (once)

1. <https://console.cloud.google.com> → create a project.
2. **APIs & Services → Library** → enable **YouTube Data API v3** and **YouTube Analytics API**.
3. **OAuth consent screen** → External → add every Gmail you will use under **Test users**.
4. **Credentials → Create credentials → OAuth client ID → Web application.** Authorized redirect URI: `{BASE_URL}/oauth2callback` (for example `http://localhost:8000/oauth2callback`).
5. **Download JSON** and save it as `YTStudioCred.json` next to `main.py`. That file is the only place the client id and secret are read from (it is in `.gitignore`; never commit it).
6. Optional: **Create credentials → API key**, restricted to YouTube Data API v3, as `GOOGLE_API_KEY`. Public reads (trends, other channels' stats) then don't use your channel logins.

Scopes requested when you add a channel: YouTube read-only, YouTube Analytics read-only, `youtube.force-ssl` (captions of your own videos; also lets the optional Publish page upload when you press its button), and Analytics monetary read-only (revenue). Channels connected before this version have the old full `youtube` scope; reconnect them once to move to the new scopes.

Quota: 10,000 units/day per project. Search costs 100, list calls 1. A daily run for one channel uses roughly 20 to 450 units depending on how many niche searches it makes (capped by `SEARCHES_PER_CHANNEL_PER_DAY`; searches are cached 6 hours).

## LLM keys

Aliases live in `config/llm.toml`: `fast` (classification, niche detection), `smart` (ideas, scripts, packages), `long` (transcripts). Each has a primary model and fallbacks. The models are Google Gemini (primary) and Mistral (fallback), so set `GEMINI_API_KEY` ([AI Studio](https://aistudio.google.com/apikey)) and `MISTRAL_API_KEY` ([Mistral console](https://console.mistral.ai/api-keys)). Prices for cost tracking are in the same file; check them against the providers' pricing pages now and then. To change models, edit the lists ([LiteLLM provider names](https://docs.litellm.ai/docs/providers)) and set their keys. `LITELLM_PROXY_URL` routes everything through a LiteLLM proxy. Each user has a daily budget (Settings); at the budget, non-critical steps stop (shown on the Jobs page).

## Pages

Today (the brief) · Ideas · Shorts (master upload, review, trim, download) · Insights (analytics, retention charts) · Trends · Publish (optional, manual) · Jobs (runs, retries, dead letters, LLM cost, quota, health) · Settings (schedule, language, topics, voice.md, budget, sync, read-only LLM aliases and flags).

## Development

```bash
uv run pytest            # Redis-backed and Postgres tests run when Redis is up / TEST_POSTGRES_URL is set
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/OPERATIONS.md](docs/OPERATIONS.md), [docs/REVIEW.md](docs/REVIEW.md), [CHANGELOG.md](CHANGELOG.md).

Includes [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill) (MIT), vendored unmodified in `vendor/`.
