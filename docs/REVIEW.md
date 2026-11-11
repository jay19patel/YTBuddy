# YTStudio → YT Autopilot: Phase R1 review

Reviewed 2026-11-11 at commit `9f05e75` (the only commit). No code was changed in this phase.

## 1. Snapshot

**Stack**

| Part | What is used | Version (from `uv.lock`) |
| --- | --- | --- |
| Python | CPython, `.python-version` = 3.11 | 3.11.13 |
| Packaging | uv, `uv.lock` with sha256 hashes; `pyproject.toml` uses `>=` ranges | uv |
| Web | FastAPI, all routes are sync `def` (run in the threadpool) | fastapi 0.162.4, starlette 1.10.0 |
| ORM / DB | SQLModel on SQLAlchemy 2, sync sessions, SQLite file `ytstudio.db` | sqlmodel 0.0.49, sqlalchemy 2.1.8 |
| Migrations | none: `create_all` plus a hand-written `DROP TABLE IF EXISTS jobrun` | n/a |
| Jobs | APScheduler `BackgroundScheduler`, in-process, in-memory job store | apscheduler 3.11.3 |
| Google | google-api-python-client, google-auth-oauthlib `Flow` | 2.202.0 / 1.5.0 |
| Frontend | one Jinja template (`templates/index.html`, 638 lines), inline CSS and vanilla JS, no build step | jinja2 3.1.6 |
| Tests, lint, types, CI, Docker, `.env` | none | n/a |

**How to run:** `uv run main.py` (uvicorn on `localhost:8000`, `reload=True`). Needs `YTStudioCred.json` (a Google OAuth web client) in the repo root.

**What I tested (on a copy of `ytstudio.db`, background jobs switched off so nothing hit your quota):**

| Check | Result |
| --- | --- |
| `uv` install and app startup | OK. Scheduler starts and adds 2 jobs |
| `GET /`, `/analytics`, `/analytics?days=7`, `/settings` | all 200 |
| `GET /auth` | 307 to Google with `state`, `code_challenge` (PKCE S256), `access_type=offline`, `prompt=select_account consent` |
| `GET /oauth2callback?state=bogus` | rejected with "Login session expired" (but HTTP 200) |
| Cross-site `POST /settings` with `Origin: https://evil.example` | **accepted (303) and changed the settings row** |
| Delete every channel row, then start the app again | **the channel came back** (re-imported from `tokens/`) |
| Full Google login, upload, live | not run: they need a browser sign-in and would write to your real channel |

**What runs today:** multi-channel connect, sidebar stats with day-over-day change, an analytics page (7/28/90/365 days, top videos, revenue), a settings page with job intervals, "run now", and manual video/Short upload and live-stream creation.

**What does not exist yet:** everything in Part B beyond OAuth, stats and analytics (see section 4).

## 2. Architecture map

```
templates/index.html ◄── main.py ──► jobs.py ──► youtube_api.py ──► db.py
   (one page,              │  routes, OAuth,   APScheduler,      Google creds,       SQLModel tables,
    every view)            │  upload, live,    sync_stats,       Data + Analytics    engine, settings
                           │  filters, errors  sync_analytics    calls, error text
                           └──────────────────────────────────────────────────────────► db.py
                           └─► googleapiclient directly (OAuth callback, upload, live)
```

- There are 4 modules with no layers. Every module imports `db` and opens sessions itself.
- **Business logic mixed with framework or API code:**
  - `main.py` holds OAuth handling (`auth`, `oauth2callback`), the YouTube upload and live calls, a token-file import, the stats-change maths (`changes`), and settings validation, all inside route functions.
  - `youtube_api.py` mixes credential persistence (it writes the refreshed token to the DB in `load_creds`), Google calls, the meaning of "monetized", and user-facing error text.
  - `jobs.py` mixes scheduling, mapping Google responses onto ORM rows, and run status, which lives in module globals.
- **Hidden coupling:**
  - `jobs.status()[0]` and `[1]` are used by position in `main.py:112` and `main.py:272`.
  - The scheduler, the OAuth `pending_flows` and `last_runs` are process globals, so the app only works as a single process.

## 3. Findings

Severity is judged for how the app runs **today** (bound to `localhost`, one person). Several findings become critical as soon as the app is reachable by anyone else, which the target requires. Those are marked ↑.

| ID | Area | File:line | Problem | Severity | Suggested fix |
| --- | --- | --- | --- | --- | --- |
| S1 | Security: tokens | `db.py:15`, `main.py:150`, `youtube_api.py:29` | Refresh tokens, access tokens **and the OAuth client secret** are stored in plain text in `channel.creds`. The secret is copied into every row. `ytstudio.db` is mode `644` (any local user can read it). | critical | Encrypted token vault (Fernet/AES-GCM, key from env). Store only the refresh token and scopes, and read the client id and secret from config. A data migration re-encrypts the existing rows. |
| S2 | Security: tokens | `tokens/UCYvy-…json`, `main.py:62-73` | Old plain-text token file is still on disk (mode `644`) and is re-imported on **every** startup. | critical | Run the import once inside a migration, encrypt during it, then delete `tokens/`. Remove `import_old_tokens`. |
| S3 | Security: secrets in repo | `.gitignore`, git history | Checked: `YTStudioCred.json`, `tokens/` and `ytstudio.db` are ignored and **not** in git history. No rotation needed for git reasons. | info | Add `.env`, `media/` and `*.db` patterns to `.gitignore`. Add a gitleaks pre-commit hook in R2. |
| S4 | Security: authN | whole app | The app has no login. Whoever can reach the port can use, upload to and remove every channel. It is safe today only because uvicorn binds to `localhost`. | high ↑ critical | App login (see question Q2), a session in an httpOnly, `SameSite=Lax`, `Secure` cookie, and a `users` table that owns channels. |
| S5 | Security: CSRF | `main.py:224` `/live`, `main.py:289` `/settings`, `main.py:314` `/settings/run`, `main.py:180` `/upload` | No CSRF protection. **Confirmed:** a cross-origin POST changed settings. Any website you open could create a live broadcast on your channel. | high | CSRF token (double-submit or synchronizer) on every POST, and an `Origin` check. |
| S6 | Security: CSRF + bug | `main.py:169` `/remove/{id}` | Destructive action on a **GET**. An `<img src=…/remove/ID>` on any page deletes the channel. It also does not revoke the Google token. | high | Make it POST + CSRF, revoke the token at Google, and mark the row disconnected. |
| S7 | Security: scoping | `main.py:180-235` | `upload` and `live` take `channel_id` from the form and use any connected channel without checking who owns it. | medium ↑ critical | Owner-scoped repository: `get_channel(user_id, channel_id)`. |
| S8 | Security: OAuth | `main.py:59, 124-138` | State and PKCE are present (verified). But pending flows sit in an in-memory dict: never expires, grows without bound, is lost on reload or restart, does not work with more than one process, and is **not bound to the browser** (login-CSRF: someone else's callback URL could attach their channel to your app). | medium | Store `state → code_verifier, user_id, expiry` server-side (Redis, 10 min TTL) and bind it to the session cookie. |
| S9 | Security: transport | `main.py:24` | `OAUTHLIB_INSECURE_TRANSPORT=1` is set unconditionally, so a non-localhost deploy would accept http callbacks. | medium | Set it only when `REDIRECT_URI` is `http://localhost…`. |
| S10 | Security: XSS | `templates/index.html:353` | `confirm('Remove {{ current.title }}…')`: an HTML-escaped value inside a JS string in an `onclick` attribute. A channel title containing `'` breaks out (the browser decodes `&#39;` before the JS runs). Self-XSS today. | low ↑ medium | Use `{{ current.title \| tojson }}` or a `data-` attribute. Goes away with S6. |
| S11 | Security: errors | `main.py:321-324`, `youtube_api.py:111` | The global handler shows raw `str(exc)` on the page. Google errors can include request URLs and internal detail. | low | Log the full error server-side (redacted) and show a friendly message plus an error id. |
| S12 | Security: scopes | `youtube_api.py:16` | Asks for full `youtube` (manage) scope. The target wants `youtube.readonly` + `yt-analytics.readonly` + `youtube.force-ssl`. The full scope is only needed by today's upload and live features. | medium | Depends on Q1. Least privilege, with incremental consent for upload if kept. |
| B1 | Bug | `main.py:62-73` | A removed channel is **resurrected** on restart from `tokens/` (confirmed). | high | Same fix as S2. |
| B2 | Bug: token refresh | `youtube_api.py:20-32` | A revoked or expired refresh token (`invalid_grant`) is not recorded. Every job keeps retrying and failing. There is no "reconnect needed" state and no notification. | high | Catch `RefreshError` → set `channel.status = reconnect_needed`, emit a domain event, skip the channel in jobs. |
| B3 | Bug: token refresh | `youtube_api.py:27-31` | Concurrent refreshes (scheduler thread and request thread) can each refresh and save, and the last write wins. Harmless with Google today, but it is a race. | low | Refresh under a per-channel lock, and only refresh when expired. |
| B4 | Bug | `jobs.py:26` | `sync_stats` reads every channel with `channels[0]`'s credentials. If that one channel is revoked, stats for **all** channels fail. | medium | Use a public API key for public stats, or fall back to the next healthy channel. |
| B5 | Bug | `jobs.py:29-31` | If a channel is removed while `sync_stats` runs, `db.get` returns `None` and raises `AttributeError`. | low | Skip `None`. |
| B6 | Bug: dates | `jobs.py:35`, `main.py:83`, `youtube_api.py:57` | `date.today()` uses the server's local timezone. YouTube days are Pacific time and channel days should be the channel's timezone. | low | Use explicit timezones (`zoneinfo`). |
| B7 | Bug | `main.py:136-137` | Bad or expired state returns HTTP 200 instead of 400. `fetch_token` errors (user pressed "deny", `error=access_denied`) end as a 500 page. | low | Handle the `error` param and return 400. |
| B8 | Bug: ops | `main.py:330`, `jobs.py` | `reload=True` is always on. With more than one uvicorn worker (or `--reload` restarts), the in-process scheduler runs every job **once per process**. | medium | The scheduler moves to Celery Beat (one instance). Turn reload on only in dev. |
| B9 | Bug: data | `db.py:50-53` | Schema changes are made by hand (`DROP TABLE jobrun` on every start). Adding a column to an existing table will not apply to existing DBs. | high | Alembic, with a baseline migration for the current schema. |
| Q1 | Quota | `jobs.py:22`, `youtube_api.py:50-70` | Usage today is small (~300 Data API units per day: stats every 5 min, plus `videos.list` per analytics period). But nothing counts or limits it, and nothing caches by cost. | medium | Quota budget service (Redis counter per Pacific day) in P2. |
| Q2 | Quota | `youtube_api.py:61` | `execute(num_retries=2)` retries analytics calls even on quota errors, and there is no backoff policy elsewhere. | low | Central retry policy: transient errors only. |
| A1 | Async / blocking | all routes | No blocking-in-async problem today: routes and the exception handler are sync `def`, so Starlette runs them in the threadpool. **But** `POST /upload` holds a worker thread for the whole YouTube upload (minutes), and 40 concurrent uploads would use up the threadpool. | medium | Keep sync routes. Long work goes to Celery (and the target removes upload from the web path anyway). |
| E1 | Error handling | `jobs.py:47` | `except Exception` turns every error, including bugs, into a stored message. Nothing is logged with a traceback. | medium | Separate transient, quota, auth and permanent errors. Use `log.exception`. |
| E2 | Error handling | `main.py:198-218` | No upload size limit. A temp file can fill the disk. | low | Size limit, streaming to storage. |
| L1 | Logging | `main.py:31` | `basicConfig`, plain text, no request or run ids. | medium | Structured JSON logging with a redaction filter for tokens. |
| D1 | SOLID / coupling | `main.py` | One module does routing, OAuth, Google calls, persistence and presentation (SRP). Google is called directly in 3 places (`main.py:140, 198, 235`) outside `youtube_api.py`. | medium | YouTube gateway facade + ports (P1/P2). |
| D2 | SOLID / coupling | `youtube_api.py:20-32` | `load_creds` reads **and writes** the DB, so the Google layer depends on persistence. | medium | Token vault port: the gateway asks the vault for credentials. |
| D3 | Coupling | `main.py:112, 272` | `jobs.status()[0]`, `[1]`: index-based coupling. | low | Look up by job id. |
| D4 | Duplication | `main.py:140`, `youtube_api.py:36,55-56` | `build(...)` is called in 4 places. | low | One gateway. |
| T1 | Types | all modules | Partial hints. Many return `dict` or no annotation (`youtube()`, `query()`, routes). No mypy. | low | mypy strict on new code. |
| T2 | Tests | repo | **No tests at all.** | high | Characterization tests in R2 (OAuth start/callback with a mocked Google, stats sync, analytics shape, settings, remove). |
| P1 | Performance | `main.py:100-116` | Every page render runs `accounts()` twice, `changes()` (scans up to 60 days of snapshots) and `jobs.status()`. Fine at this size. | low | Query once per request. Not urgent. |
| U1 | UI | `templates/index.html` | No dark mode. One template for every view will not scale to the ~9 target pages. | low | Base layout + one template per page (see section 5). |
| DOC1 | Docs | `README.md` | Accurate for today. No ARCHITECTURE, OPERATIONS or CHANGELOG. | low | Written during the phases. |

Nothing to report for CORS: no CORS middleware is set, so cross-origin `fetch` reads are blocked by default. The CSRF problem (S5) is form posts, which CORS does not stop.

## 4. Feature gap matrix (Part B)

**Legend:** ✅ done · 🟡 partial · ❌ missing · 🔴 broken

| Feature | State | Evidence |
| --- | --- | --- |
| OAuth web flow, state, PKCE, offline, forced consent | 🟡 | All present (`main.py:124-130`, verified in the redirect URL). State is in memory and not tied to the browser (S8). |
| Target scopes (readonly, analytics readonly, force-ssl) | 🟡 | Uses full `youtube` + analytics + monetary (`youtube_api.py:14-16`). |
| Encrypted refresh tokens | ❌ | Plain text (S1, S2) |
| Automatic access-token refresh | ✅ | `youtube_api.py:27-31` |
| Revoked token → reconnect needed + notify | 🔴 | Only an error string (B2) |
| Revoke on disconnect | ❌ | `main.py:169` |
| Multiple channels | ✅ | Channel table, `/use`, cookie |
| Multiple **users** and owner scoping | ❌ | No users or login (S4, S7) |
| Quota budget service, search/stats caching | ❌ | None. Today there is a DB cache for analytics (`AnalyticsCache`) and the stats columns. |
| Own-videos sync (uploads playlist → items → details, incremental) | ❌ | Only channel totals and top-10 analytics |
| Retention (audienceWatchRatio, relativeRetentionPerformance), CTR | ❌ | Analytics has views, watch time and AVD only (`youtube_api.py:63-65`) |
| Trend sources (most popular, search 14 days, custom topics) | ❌ | none |
| Ports and adapters, composition root, pipeline engine | ❌ | 4 flat modules |
| Postgres + Alembic | ❌ | SQLite, `create_all` |
| Celery, Redis, Beat, Flower, queues | ❌ | APScheduler in-process (`jobs.py`) |
| LiteLLM layer (aliases, cache, budget, cost) | ❌ | none |
| Prompt repository, voice.md | ❌ | none |
| Vendored youtube-agent-skill + scorer adapters | ❌ | Not in the repo. Upstream checked: MIT, 11 SKILL.md, 6 tools (`hookscore.py`, `title.py`, `deadair.py`, `chapters.py`, `retention.py`, `swipe.py`) + `templates/voice.md`, last push 2026-09-16. |
| Daily pipeline (sync → analyze → trends → ideas → script/package/SEO → brief) | ❌ | none |
| Shorts pipeline (transcript, candidates, FFmpeg, cover) | ❌ | none |
| Per-channel scheduler with DST | ❌ | Only global interval jobs (`db.py:41-47`) |
| Notifications (in-app, email, Telegram) | ❌ | none |
| Review queue ("needs review") | ❌ | none |
| Pages: Login/picker | 🟡 | Channel picker in the sidebar; no login |
| Pages: Today, Ideas, Script editor, Shorts review, Trends, Jobs | ❌ | none |
| Pages: Channel insights + retention chart | 🟡 | `/analytics` has totals, a daily bar chart and top videos; no retention |
| Pages: Settings | 🟡 | Sync intervals and currency only |
| Jobs page with own run tables | 🟡 | Settings shows the last run in memory; no run history (deliberately removed, `db.py:52`) |
| Health checks | ❌ | none |
| Dark mode | ❌ | light only. Mobile ✅ (`@media (max-width:760px)`) |
| Structured logging | ❌ | L1 |
| Tests, lint, mypy, pip-audit, CI | ❌ | none |
| docker-compose, `.env.example` | ❌ | none |
| **Not in spec but exists:** manual upload, Shorts upload, live-stream creation, revenue analytics, currency setting | ✅ | `main.py:180-261`, `youtube_api.py:75-85`. See Q1. |

## 5. Keep / refactor / replace

| Module or choice | Decision | Reason |
| --- | --- | --- |
| FastAPI | **Keep** | Matches the target default. Works. |
| uv + `uv.lock` | **Keep** | Already pins exact versions with hashes. Add `uv export --frozen --require-hashes` and pip-audit. |
| Python 3.11 | **Bump to 3.12** | Target says 3.12+. All current dependencies support it. Cost: one line in `.python-version` and `pyproject.toml`. |
| SQLModel (sync) | **Keep, add Alembic** | It is SQLAlchemy 2 underneath, so Alembic works. I recommend **staying sync**: every route is sync `def`, Celery tasks are sync, and async would add a rewrite with no benefit at this scale. The target's async SQLAlchemy is a default, not a requirement. |
| SQLite | **Replace with Postgres** (R2) | The target needs `SKIP LOCKED` scheduling, several worker processes writing at once and docker-compose. SQLite stays usable for unit tests. Data migration copies the existing channel, snapshot, analytics and settings rows. |
| APScheduler (`jobs.py`) | **Replace with Celery + Beat** (P1, strangler) | An in-process scheduler runs jobs once per process (B8), keeps state in memory, and has no queues, retries or dead letters. Its 2 jobs become Celery tasks first, then APScheduler is deleted. The interval settings map onto Beat ticks. |
| `youtube_api.py` | **Refactor** into the YouTube gateway facade + token vault adapter | The logic is good (monetized detection, friendly errors). It needs to stop touching the DB (D2) and gain quota accounting. |
| `main.py` | **Refactor**: split into routers (`auth`, `channels`, `analytics`, `settings`, `publish`) | It is 334 lines doing 6 jobs (D1). Move OAuth into an application use case. |
| `db.py` | **Refactor** into persistence adapters (repositories + unit of work), models under migrations | Needed for owner scoping (S7) and testing with fakes. |
| Jinja `index.html` | **Keep Jinja, split** into `base.html` + one template per page, add dark mode tokens, small vanilla JS (or htmx, ~14 KB, no build) for polling and SSE | Your stack wins unless there is a real problem. Server rendering gives httpOnly-cookie auth for free and has no second toolchain. Next.js would add a Node service, an API layer and CORS/CSRF work for one user. **Cost of switching later is low** if routes return data through use cases. See Q3. |
| Upload / live features | **Keep as-is, behind a feature flag** (pending Q1) | Not in the target spec, and they conflict with read-only scopes. |
| `tokens/` import | **Replace** with a one-time migration, then delete the folder | S2, B1 |

## 6. Migration plan (phases adjusted)

The phases in Part C mostly fit. Changes, and why:

- **Postgres + Alembic moves into R2.** docker-compose needs Postgres, and every later phase adds tables. Doing migrations first means no later phase hand-edits the schema.
- **App login + users table moves into R2** (from P2). S4–S7 are high now and critical once deployed, and owner scoping touches every query. It is cheaper to add `user_id` before more tables exist.
- **docker-compose grows over time.** R2 ships `api`, `postgres` and `redis`. Workers and beat come in P1, flower in P5, minio in P6. Each phase leaves a compose file that starts cleanly, instead of stub services.
- **Jobs page (run/step tables) moves to P1**, because the pipeline runner writes those tables anyway. P5 adds health checks and Flower.

**R2: Stabilize**
1. Characterization tests first, with a mocked Google: `/auth` redirect params, callback success, bad state and no-channel cases, `sync_stats`, `sync_analytics` (incl. `needs_reconnect`, error-for-all-periods), settings clamp, `remove`.
2. Alembic baseline for the current 4 tables, run against existing SQLite (no data change).
3. Token vault: `TOKEN_ENCRYPTION_KEY` env, encrypt `channel.creds` in a data migration (decrypt-on-read during the transition), drop the stored client secret, then a migration that imports `tokens/*.json` encrypted and tells you to delete the folder (S1, S2, B1).
4. App login (Q2) + `users` table + `channel.user_id` (backfill to you) + owner-scoped queries (S4, S7).
5. CSRF tokens + Origin check, `/remove` → POST + revoke at Google (S5, S6, S10).
6. OAuth state bound to the session, with TTL; `OAUTHLIB_INSECURE_TRANSPORT` only for localhost; handle `error=` (S8, S9, B7).
7. `invalid_grant` → `reconnect_needed` status, skip in jobs, banner (B2). Fix B4 and B5.
8. Tooling: ruff (lint + format), mypy (strict on new packages), pytest + coverage, pip-audit, gitleaks, GitHub Actions CI.
9. Postgres: migrations run on Postgres, a one-off copy script from SQLite, `Dockerfile`, docker-compose (`api`, `postgres`, `redis`), `.env.example`. Python 3.12.

**P1: Architecture.** Package layout `ytstudio/{domain,ports,application,adapters,interfaces,container.py}`. Move the existing logic behind ports: the YouTube gateway (wrapping the current `youtube_api.py`), the token vault, repositories. Pipeline engine + registry, run and step tables, Celery app with io/llm/media queues, a task dispatcher adapter, Redis cache/lock/rate-limiter adapters. Re-home `sync_stats` and `sync_analytics` as Celery tasks, then delete APScheduler. Jobs page v1. Compose: + `worker-io`, `worker-llm`, `worker-media`, `beat`.

**P2: YouTube depth.** Scope change (Q1), revoke on disconnect, quota budget service, search and stats caching, incremental own-videos sync, retention and CTR analytics, an insights page with a retention chart (extending `/analytics`).

**P3–P7:** as in Part C. P5 adds `flower` (behind auth), P6 adds `minio`.

## 7. Open questions

1. **Upload and live (and scopes).** Your app can upload videos and Shorts and create live streams. The spec says "upload stays manual" and asks for read-only scopes. Should I:
   - (a) keep upload and live, behind a feature flag, requesting `youtube.upload` incrementally only when you use it (my recommendation), or
   - (b) remove them?
   Also: keep the **revenue** analytics (needs the monetary scope, which the spec doesn't list)? I assume yes.
2. **App login.** Who logs in, and how? My recommendation is "Sign in with Google" (openid + email only, separate from the channel-connect consent) with an email allowlist in env. The alternative is local email + password (argon2). Is it only you for now, or other people too?
3. **Frontend.** Keep Jinja + vanilla JS/htmx (my recommendation, see section 5), or do you want the Next.js app from the target default? Next.js adds a Node service, a JSON API for every page and a second test toolchain.
4. **Sync vs async DB.** OK to stay on sync SQLModel sessions (my recommendation) rather than async SQLAlchemy?
5. **Hosting.** Will this stay on your machine, or go on a server? That decides the redirect URI, HTTPS, and whether `Secure` cookies are enforced in dev.
6. **Existing data.** Your `ytstudio.db` holds 1 channel, 1 snapshot and 4 analytics periods. OK to copy it into Postgres in R2 and keep the SQLite file as a backup until you delete it?
7. **Run history.** You removed the `jobrun` table on purpose (`db.py:52`). The target needs run and step tables for the Jobs page and resume. OK to bring run history back (with daily cleanup)?
8. **Notifications.** Email (which SMTP provider?), Telegram (a bot token), or both?
9. **LLM providers.** Which provider keys will you have for the `fast`, `smart` and `long` aliases? This only affects the default config and `.env.example`.

**Assumptions (labelled, non-blocking):**
- Single Google Cloud project, default 10,000 units per day.
- Your channel language and region will be auto-detected in P5 as the spec says.
- Commit style: there is one commit with an imperative sentence-case subject. I'll match that.

---

## 8. Status after implementation (2026-11-11, branch `autopilot`)

Your instruction was "do all things", so the open questions were settled with the recommended defaults: keep upload/live behind `ENABLE_MANUAL_PUBLISH`, Google login with an allowlist, keep Jinja, stay on sync SQLModel, local-first hosting, copy the data, bring run history back, both email and Telegram, Claude models as the defaults. Each of these can be changed.

| ID | Status | Where / how |
| --- | --- | --- |
| S1 | ✅ fixed | `adapters/token_vault.py`; migration `0002` re-encrypts and drops the stored client secret. Tested on a copy of the real DB. |
| S2 | ✅ fixed | One-time import in migration `0002`; `import_old_tokens` removed. **You still need to delete `tokens/` yourself.** |
| S3 | ✅ fixed | `.gitignore` extended (`.env`, `media/`, `*.db*`). gitleaks is **not** added: pip-audit runs in CI, but secret scanning should be enabled in GitHub settings. |
| S4 | ✅ fixed | Google login plus `ALLOWED_EMAILS`, server-side sessions (`web_sessions.py`), httpOnly/SameSite/Secure cookie. |
| S5 | ✅ fixed | `csrf` dependency on every POST plus Origin check (`interfaces/web/app.py`). Tested. |
| S6 | ✅ fixed | `POST /channels/{id}/disconnect`, revokes at Google. Tested. |
| S7 | ✅ fixed | Owner-scoped repositories and queries; other users get 404. Tested. |
| S8 | ✅ fixed | `oauthstate` table: single use, 10 minutes, bound to a nonce cookie. Tested. |
| S9 | ✅ fixed | Insecure transport only for localhost redirect URIs. |
| S10 | ✅ fixed | No inline JS; `data-confirm`; CSP forbids inline script. |
| S11 | ✅ fixed | Errors show a friendly message plus a request id; logs are JSON with secret redaction (which itself had a bug, caught and fixed by tests). |
| S12 | ✅ fixed | Scopes: readonly, analytics, force-ssl, monetary. Legacy full-scope tokens keep working until reconnected. |
| B1 | ✅ fixed | Same as S2. |
| B2 | ✅ fixed | `RefreshError` or an undecryptable token marks the channel `reconnect_needed` and notifies once; jobs skip it. |
| B3 | ✅ fixed | Per-channel refresh lock in the gateway. |
| B4 | ✅ fixed | `Sync.sync_stats` tries the next healthy login; `GOOGLE_API_KEY` is optional. |
| B5 | ✅ fixed | `save_stats` ignores channels removed mid-sync. |
| B6 | ✅ fixed | Quota days use Pacific time, schedules use the channel's time zone (DST tested), stored times are UTC. |
| B7 | ✅ fixed | `error=` handled; bad state returns 400. |
| B8 | ✅ fixed | APScheduler removed; exactly one Beat; reload only via `uv run main.py`. |
| B9 | ✅ fixed | Alembic `0001` baseline (legacy DBs are stamped), `0002`, `0003`. |
| Q1 | ✅ fixed | `RedisQuotaBudget` counts per Pacific day; searches cached 6 h, other channels' stats 24 h. |
| Q2 | ✅ fixed | `execute(num_retries=0)`; one retry policy in the worker. |
| A1 | ✅ fixed | Long work runs in Celery; master upload is chunked and resumable. The legacy manual Publish upload still holds a request thread (kept as it was). |
| E1 | ✅ fixed | Errors classified into transient / quota / permanent; dead letters; `log.exception`. |
| E2 | ✅ fixed | `MAX_MASTER_BYTES`, extension allowlist, chunk size cap. |
| L1 | ✅ fixed | `logging_setup.py`. |
| D1–D4 | ✅ fixed | Ports and adapters, `YouTubeGateway` facade, one composition root. |
| T1 | 🟡 partial | mypy strict on domain/application/ports/config; adapters and interfaces on a gradual baseline. |
| T2 | ✅ fixed | 102 tests; domain + application coverage 97%. |
| P1 | ✅ fixed | Page reads go through `queries.py`, about 3 queries per page. |
| U1 | ✅ fixed | Base layout plus per-page templates; dark mode via system preference. |
| DOC1 | ✅ fixed | README, ARCHITECTURE, OPERATIONS, CHANGELOG. |

### Verified, and how

- `pytest`: 100 pass locally. 2 Postgres tests are skipped by default and pass against a real Postgres 14 (`TEST_POSTGRES_URL`). The suite includes a Celery worker on real Redis (chain + chord, idempotent re-run, resume, lock contention, quota reschedule, transient retry) and a real FFmpeg render of a 10-second fixture: 1080×1920 H.264/AAC 48 kHz, loudness within ±2 LU of −14 LUFS, captions checked visually.
- `ruff`, `ruff format --check`, `mypy` and `pip-audit` (hashed lock) are clean.
- The migration was run on a **copy** of your `ytstudio.db`: all rows kept, the token encrypted and decryptable. That copy was then moved into Postgres with `cli copy-sqlite`.
- Smoke run on Postgres + Redis with api + worker + beat: every page returns 200; "Run now" went through Celery and failed safely (reconnect needed, dead letter, notification). That run found the missing reconnect flag for undecryptable tokens, now fixed.

### Not verified / deferred, with reasons

- **Docker images were not built or started**: the Docker daemon was not running. `docker compose config` validates all 10 services; the first `docker compose up --build` is untested.
- **Live Google flows** (real sign-in, Data/Analytics calls, captions download, CTR/impressions availability) need your browser sign-in. They are covered by recorded responses only. Reach metrics may be unavailable for your channel through the API; the UI says so.
- **Real LLM calls** were not made (no key in this environment). Covered by LiteLLM's `mock_response` tests: fallback, repair, cache, budget, cost.
- **Media worker load test** (P7): not run. One 8-second render takes about 1 s on this Mac; a 58-second Short is estimated at 5 to 15 s per render at concurrency 1.
- **Optional face detection** for the 9:16 crop: not implemented (centre crop). It fits as a new `MediaProcessor` behind the same port.
- **yt-dlp adapter**: only the flag exists (off, with a terms-of-service note). No implementation, by design for now.
- **Hindi (Devanagari) captions and covers**: the Docker image ships Noto Devanagari, but correct conjunct shaping depends on Pillow's raqm support in the build. Check one Hindi Short visually.
- **gitleaks / secret scanning**: not wired into CI. Turn on GitHub secret scanning or add a pre-commit hook.
- **Per-user LLM budget day** is the UTC day, not the user's time zone.
- **Commit granularity**: the work was built in one pass, so the commits are grouped by purpose. Only the final commit was verified as a whole; intermediate commits are not individually guaranteed runnable.

### Follow-up decisions (2026-11-11)

- **Google client:** read only from `YTStudioCred.json`, as before the rewrite. The env-var option was removed.
- **Storage:** local disk only (`MEDIA_DIR`). S3/MinIO and boto3 were removed.
- **LLM:** Gemini (`gemini-pro-latest` / `gemini-flash-latest`) with Mistral (`mistral-large-latest` / `mistral-small-latest`) as fallback. Prices in `config/llm.toml` are conservative estimates from third-party sources and need checking against the official pricing pages.
- **Notifications:** removed entirely (in-app, email, Telegram; migration `0004`). Events are logged. Job failures show on the Jobs page; reconnect-needed shows a banner; a stale Beat shows in `/health` and the Jobs page.
