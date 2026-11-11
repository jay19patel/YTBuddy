# Operations

## Processes

| Service | Command | Notes |
| --- | --- | --- |
| api | `uvicorn main:app` (runs `ytstudio.cli migrate` first in Docker) | stateless; scale freely, but run migrations once |
| worker-io | `celery … worker -Q io -c 6` | YouTube API; concurrency 4 to 8 |
| worker-llm | `celery … worker -Q llm -c 3` | concurrency 2 to 4; bounded by provider rate limits (`config/llm.toml`) |
| worker-media | `celery … worker -Q media -c 1 --prefetch-multiplier 1` | FFmpeg and Whisper; image built with `--extra media` |
| beat | `celery … beat` | **exactly one**. It only sends ticks; schedules live in the `schedule` table |
| flower | `celery … flower --basic-auth=…` | bound to 127.0.0.1; reach it over `ssh -L 5555:localhost:5555` |

**Time limits (soft/hard, seconds):** io 300/360, llm 600/720, media 3600/3900. The Redis visibility timeout is 3 h, longer than the longest task. Change the limits with `QUEUE_LIMITS` in config.

**Shutdown:** `docker compose stop` sends SIGTERM. Workers finish their current task (warm shutdown, 2-minute grace period). Because of `acks_late` and `reject_on_worker_lost`, a killed task is redelivered, and steps skip work they already saved. Media output is written to a temp file and renamed into storage, so a half-written file is never visible.

## Scaling workers

- More channels means more `worker-io` replicas (`docker compose up -d --scale worker-io=2`). The YouTube quota, not CPU, is usually the limit.
- Renders queue up: add `worker-media` replicas on machines with CPU to spare. Keep concurrency at 1 per container and prefetch at 1.
- LLM 429s mean the provider is the limit, not workers. Lower `rate_limits` in `config/llm.toml` or add a fallback model.

## Health and alerts

- `GET /health` returns Postgres, Redis, Beat (last tick), and per-queue worker heartbeats. `GET /health/<part>` returns 503 when that part is down. Docker healthchecks use `python -m ytstudio.cli health <part>`.
- Beat ticks every 5 minutes and writes `yt:beat:tick`. If there is no tick for `BEAT_STALE_MINUTES` (default 15), the api logs a `BeatStale` warning once, and `/health/beat` returns 503 (the Docker healthcheck marks `beat` unhealthy).
- The Jobs page shows run and step status, step errors, dead letters, LLM cost (today, 7 days, by alias and step), today's YouTube quota use, and health.

## Dead letters

A step that fails for a non-retryable reason (bad input, schema still invalid after one repair, an exhausted budget, reconnect needed, a time limit) writes a `dead_letter` row and fails its run. Fix the cause, then press **Retry** on the Jobs page. The run resumes from the failed step; finished steps keep their saved output. Resolved dead letters are deleted after 30 days.

## Quota

Data API units are counted in Redis per Pacific day before each call. Non-critical calls (searches, other channels' stats) stop at `QUOTA_NONCRITICAL_PCT`. If Google answers `quotaExceeded`, the counter is set to the limit and the affected step waits for the reset. To see today's use: the Jobs page, or `redis-cli -n 2 GET yt:quota:<YYYY-MM-DD>`.

## Key rotation

**Token encryption key:**
1. Generate a new key and set `TOKEN_ENCRYPTION_KEY=<new>,<old>`. The first key encrypts; all keys decrypt.
2. Restart, then run `python -m ytstudio.cli rotate-keys` to re-encrypt every token with the new key.
3. Remove the old key and restart.

If a key is lost, the tokens can't be decrypted. Affected channels show "reconnect needed": reconnect each one.

**Google client secret:** rotate it in Cloud Console, download the new JSON over `YTStudioCred.json`, restart. Stored tokens don't contain the secret, so nothing else changes.

**LLM keys (Gemini, Mistral), Flower, Redis, Postgres passwords:** update `.env` and restart the affected services. For Redis and Postgres, change the password in the service first.

## Backups

Back up Postgres (`pg_dump`), the `media` volume or `media/` folder (master files, rendered Shorts, covers), and `YTStudioCred.json`. Redis holds only caches, locks and counters; losing it is safe, though in-flight tasks are redelivered or need a retry.

## Upgrading

`git pull && docker compose up -d --build`. The api runs migrations on start. With more than one api replica, run `docker compose run --rm api python -m ytstudio.cli migrate` first.
