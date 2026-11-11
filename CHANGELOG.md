# Changelog

## 0.3.0 (2026-11-11)
### Added
- AI models and keys on their own LLM page (sidebar, under Sign out): paste provider keys (Gemini, Mistral, OpenAI, Anthropic) and choose the main and fallback model for the fast, smart and long aliases, per account (one box, no global setting). Keys are stored encrypted and never shown again; "Test" and "Refresh model lists" buttons check them. BazaarLink (an OpenAI-compatible service) is a supported provider, models written `bazaarlink/<model>`. Migration `0010`. Removed from `.env` and the code: `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `GOOGLE_API_KEY` (public YouTube reads now always use your channel's login) and `LITELLM_PROXY_URL`/`KEY`. Nothing about AI is read from the environment any more.
- Delete ideas, scripts, packages and SEO (deleting an idea also removes what was written for it).
- The run panel on a page now survives a refresh: the latest run for the asset or channel shows, polls only while it is running, and a failed run offers Retry.
- Channel-first layout: the sidebar lists only channels; each channel opens on Insights, with tabs for Ideas, Shorts, Trends, Jobs and Settings.
- Channel About sync: Settings shows the YouTube description and keywords. Saving pushes them to YouTube (`channels.update`, 50 units); edits made in YouTube Studio come back with the channel stats sync. The ideas prompt uses them. Migration `0008`.
- Shorts are kept: the pipeline, master upload, Shorts page and media worker were removed and then restored in this release. Migration `0007` drops their tables and `0009` recreates them, so a database already at `0007` comes back too.
- Your own ideas: type a topic and details on the Ideas page, then save it or have a script written right away.
- Scripts page: every script for the channel (the daily one and the ones you asked for), with hook score and review actions.
- Publish page fills the title, description and tags from a script's package and SEO, and can schedule the upload (private until the time you pick, in the channel's time zone).
### Changed
- Everything is per channel: `voice.md` moved from the account to each channel (migration `0006`), the Jobs page shows only the selected channel's runs, opening an asset switches to its channel, and the channel name shows on every page.

## 0.2.2 (2026-11-11)
### Changed
- Sign-in is Google-only and strict: only Gmail addresses in `ALLOWED_EMAILS` get in (an empty list means nobody, with no first-user exception). A PIN was tried for a few hours and removed. Data belongs to the Gmail, so any device that signs in with it sees the same history.
- A bad Gemini/Mistral key now fails at once with a clear message instead of retrying; connecting a channel owned by another account now says so instead of showing "channel".
- Docker Compose is the one way to run it; the website is on port 8000.
### Fixed
- Sidebar was white with white text in dark mode; it now has its own dark colour in both themes.

## 0.2.1 (2026-11-11)
### Changed
- Google OAuth client is read only from `YTStudioCred.json` (no `GOOGLE_CLIENT_ID`/`SECRET` env vars).
- Storage is local disk only: S3/MinIO support and boto3 removed.
- LLM aliases use Google Gemini (primary) and Mistral (fallback); Anthropic removed. Cost tracking falls back to the alias's price when a `-latest` model comes back under its resolved name.
### Removed
- All notifications: in-app page, email and Telegram. Events are logged; failures show on the Jobs page. Migration `0004` drops the `notification` table and the user's notification columns.

## 0.2.0 (2026-11-11): YT Autopilot

### Security
- Google tokens are encrypted at rest (Fernet, rotatable keys). The duplicated OAuth client secret is no longer stored in each row. The migration re-encrypts existing tokens and keeps every row.
- App login with Google, limited to `ALLOWED_EMAILS`, using server-side sessions in an httpOnly cookie.
- CSRF token and same-origin check on every POST. Removing a channel is now a POST and revokes the Google token.
- OAuth state and PKCE verifier are stored server-side, single-use, expire after 10 minutes, and are bound to the browser that started the flow.
- Every query is scoped to the owning user. Strict CSP and other security headers. Secrets are redacted from logs.

### Fixed
- Removed channels no longer come back on restart from `tokens/`.
- A revoked or expired Google login now marks the channel "reconnect needed" (banner on every page), instead of failing every job.
- One broken channel no longer stops the sidebar stats for all channels.

### Added
- Daily brief per channel: performance vs your own median, retention cliffs, outlier trends, 5 ideas, scored hooks, a script, linted packages, SEO. Everything goes to review.
- Shorts from your master render: captions or Whisper, chapters and a dead-air edit list, 15–58 s picks, 9:16 render with karaoke captions at −14 LUFS, covers, titles, plus trim and re-render.
- Pages: Today, Ideas, script editor, Shorts, Insights with retention, Trends, Jobs, Notifications, and more Settings. Dark mode.
- Celery (io/llm/media queues), Beat, Flower; per-channel schedules with DST handling; resumable, idempotent pipeline runs; dead letters.
- LiteLLM aliases with fallbacks, cache, rate limits, cost tracking and a daily budget. Versioned prompts built on the vendored youtube-agent-skill.
- YouTube quota budget, response caching, incremental upload sync, video analytics and retention.
- Postgres + Alembic, docker-compose, CI (ruff, mypy, pytest with coverage, pip-audit).

### Changed
- Python 3.12. APScheduler is replaced by Celery Beat. The single template is split into per-page templates. The old `db.py`, `jobs.py` and `youtube_api.py` moved into `ytstudio/`.

## 0.1.0
- Original app: connect channels, sidebar stats, analytics, manual upload and live streams.
