# YTStudio (YT Autopilot)

A self-hosted research and scripting assistant for your YouTube channels. Each channel is its own world: its own ideas, scripts, analysis, trends, voice, schedule and job history. Every day, for each channel, it:

1. syncs your uploads and reads your last 90 days against your own median,
2. finds outlier videos in your niche (views ÷ that channel's own median),
3. writes 5 ranked ideas, then for the top one a scored hook, a full script, 3 linted title + thumbnail pairs, and SEO,
Nothing is recorded, rendered or generated as video or audio: you make the video. You can also type your own idea and have it turned into a script, and the Publish page fills in the title, description and tags for you and can schedule the upload.

**Nothing is ever published.** Everything lands in a review queue as "needs review". You record, edit, design and upload yourself. Numbers shown are real numbers from the APIs; nothing predicts views.

## Run it

Everything runs with Docker Compose: website, workers, scheduler, Postgres, Redis.

1. Install and open Docker Desktop.
2. Put `YTStudioCred.json` (Google OAuth client, see below) in this folder.
3. `cp .env.example .env` and fill in: `TOKEN_ENCRYPTION_KEY`, `ALLOWED_EMAILS` (your Gmail), and the passwords (`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `FLOWER_PASSWORD`; letters and digits only).
4. Start everything:

```bash
docker compose up -d --build     # first time builds the image (a few minutes)
docker compose ps                # all services "healthy" / "running"
docker compose logs -f api       # follow a service's log
docker compose down              # stop (data stays in Docker volumes)
```

5. Open <http://localhost:8000>; it sends you to Google sign-in (only a Gmail in `ALLOWED_EMAILS` gets in). Then add your channel (Google asks for YouTube access), confirm Settings, then press **Run today's brief now** on Today.

Services: `web` (Caddy, the only published port, 8000), `api` (runs migrations on start), `worker-io`, `worker-llm`, `beat`, `flower` (<http://127.0.0.1:5555>, password), `postgres`, `redis` (password, not published). 

## Using it from another device

Your history (briefs, ideas, scripts, runs) is stored on the server under your Gmail, so signing in with the same Gmail on any device shows the same data. The only requirement is that the device can open the site and that Google accepts the redirect address:

- `http://localhost:8000` works only on this computer.
- Google refuses plain `http://` for any other address (for example `http://192.168.1.5:8000`). For a phone or another computer you need an `https://` address, such as a domain on a server (`SITE_ADDRESS=your.domain`, `BASE_URL=https://your.domain`, ports 80 and 443 open), or a tunnel to this computer (Tailscale or Cloudflare Tunnel).
- Whichever address you use, add `<address>/oauth2callback` to **Authorized redirect URIs** in the Google Cloud OAuth client, set `BASE_URL` to it, and add the Gmail under **Test users**.

## Upgrading from the old single-file app

Docker starts with a fresh Postgres; the old `ytstudio.db` is not needed. Add your channel again after signing in: the old sidebar stats and analytics are fetched again automatically. (To carry the old file over anyway: `DATABASE_URL=sqlite:///ytstudio.db uv run python -m ytstudio.cli migrate`, then `copy-sqlite` into Postgres. It encrypts the old tokens and needs `TOKEN_ENCRYPTION_KEY`.) Delete `tokens/` once you no longer need the old app.

## Google Cloud setup (once)

1. <https://console.cloud.google.com> → create a project.
2. **APIs & Services → Library** → enable **YouTube Data API v3** and **YouTube Analytics API**.
3. **OAuth consent screen** → External → add every Gmail you will use under **Test users**.
4. **Credentials → Create credentials → OAuth client ID → Web application.** Authorized redirect URI: `{BASE_URL}/oauth2callback` (for example `http://localhost:8000/oauth2callback`).
5. **Download JSON** and save it as `YTStudioCred.json` next to `main.py`. That file is the only place the client id and secret are read from (it is in `.gitignore`; never commit it).

Scopes requested when you add a channel: YouTube read-only, YouTube Analytics read-only, `youtube.force-ssl` (lets the optional Publish page upload when you press its button), and Analytics monetary read-only (revenue). Channels connected before this version have the old full `youtube` scope; reconnect them once to move to the new scopes.

Quota: 10,000 units/day per project. Search costs 100, list calls 1. A daily run for one channel uses roughly 20 to 450 units depending on how many niche searches it makes (capped by `SEARCHES_PER_CHANNEL_PER_DAY`; searches are cached 6 hours).

## LLM keys

**The LLM page** (link at the bottom of the sidebar, under Sign out) is where you paste your API keys and choose the models. There are no AI keys in `.env`. Keys are saved encrypted and never shown again. For each of the three aliases (fast, smart, long) you pick a main model and up to two fallbacks, from a list that "Refresh model lists" loads from your providers, or by typing `provider/model`. "Test fast / smart / long" sends a tiny real request, so a wrong key shows up right away. Each logged-in account has its own models and keys. Models you leave empty use the defaults below.

Aliases live in `config/llm.toml` as defaults: `fast` (classification, niche detection), `smart` (ideas, scripts, packages), `long` (long inputs), each with a primary model and fallbacks (Google Gemini first, Mistral as fallback). Choose your own on the LLM page. Prices for cost tracking are in the same file; check them against the providers' pricing pages now and then. Each user has a daily budget (Settings); at the budget, non-critical steps stop (shown on the Jobs page).

## Pages

Today (the daily brief: analysis, trends, 5 ideas, a script) · Scripts (every script, daily and requested) · Ideas (backlog, plus a form for your own idea) · Insights (analytics, retention charts) · Trends · Publish (manual upload; fills title, description and tags from a script and can schedule it) · Jobs (runs, retries, dead letters, LLM cost, quota, health for the selected channel) · Settings (per channel: schedule, language, region, topics, voice; plus budget, sync, read-only LLM aliases).

## Development

```bash
uv run pytest            # Redis-backed and Postgres tests run when Redis is up / TEST_POSTGRES_URL is set
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/OPERATIONS.md](docs/OPERATIONS.md), [docs/REVIEW.md](docs/REVIEW.md), [CHANGELOG.md](CHANGELOG.md).

Includes [youtube-agent-skill](https://github.com/Jakeschincariol/youtube-agent-skill) (MIT), vendored unmodified in `vendor/`.
