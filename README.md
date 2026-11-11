# YTStudio

Small FastAPI + Jinja app: connect your YouTube channel, upload videos and Shorts, and create live streams.

## Setup (one time)

1. Go to https://console.cloud.google.com → create a project.
2. **APIs & Services → Library** → enable **YouTube Data API v3** and **YouTube Analytics API**.
3. **OAuth consent screen** → External → add **every** Gmail you want to connect under **Test users** (max 100 while in testing).
4. **Credentials → Create credentials → OAuth client ID** → type **Web application**.
   - Authorized redirect URI: `http://localhost:8000/oauth2callback`
5. Download the JSON, save it as `YTStudioCred.json` in this folder.

## Run

```bash
uv run main.py
```

Open http://localhost:8000 → **Add YouTube account** → sign in with Google → pick the channel.

## Multiple accounts

- Click **+** in the left rail to add another channel. Every sign-in shows Google's account picker.
- One Google account can own several channels (Brand Accounts). Google asks which channel to use, so sign in again and pick each one.
- Channels, their tokens and daily stats live in SQLite (`ytstudio.db`, models in `db.py`). Tokens refresh on their own. Click a channel in the sidebar to switch to it.
- The sidebar shows subscribers, views and videos, plus the change since the last day you opened the app. Stats refresh at most every 10 minutes.
- Never commit `YTStudioCred.json` or `ytstudio.db`; both hold secrets and are in `.gitignore`.

## Analytics

The **Analytics** tab shows subscribers, views, watch time, daily views, top videos and, for monetized channels, estimated revenue (USD or INR).

- Channels connected before analytics was added must be reconnected once (the page shows a button).
- On Google's sign-in screen, tick every box, including revenue, or revenue won't show.
- Revenue is YouTube's **estimate**. The money actually paid out is only visible in Google AdSense; there is no API for it.
- YouTube reports analytics with a 2 to 3 day delay.

## Background sync

Pages never call YouTube on load. A background scheduler (APScheduler, runs inside the app, no Redis or extra server) copies data into SQLite:

| Job | Default | What it fetches |
| --- | --- | --- |
| Channel stats | every 5 min | Subscribers, views, videos for the sidebar (1 quota unit per run for all channels) |
| Analytics | every 60 min | Views, watch time, top videos, revenue for 7/28/90/365 days |

Open **Sync & settings** (bottom of the sidebar) to change intervals, pause a job, pick the revenue currency, press **Run now**, and see what's running, what's queued, the next run time and the result of the last run (kept in memory only, nothing is logged to the database). After a restart, jobs continue their schedule instead of fetching again right away.

## Code layout

- `main.py`: web pages and routes
- `jobs.py`: scheduler and background jobs
- `youtube_api.py`: Google sign-in tokens, YouTube Data and Analytics calls
- `db.py`: SQLite tables (SQLModel)
- `templates/index.html`: the UI

## Notes

- **Shorts**: vertical video under 3 minutes. Choose the **Short** tab and `#Shorts` is added to the description.
- **Live**: your channel needs live streaming enabled (YouTube Studio → Go Live; first time takes ~24h). After creating, paste the server + stream key into OBS and start streaming. It goes live automatically.
- Unverified Google apps upload as **private** only until the app passes Google's audit.
- Quota: 10,000 units/day by default; one upload costs ~1,600.
