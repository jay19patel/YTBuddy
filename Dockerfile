# One image for api, workers and beat. Pinned base; deps installed from uv.lock with hashes.
FROM python:3.12.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /usr/local/bin/uv
# The media worker draws kinetic typography videos with Remotion: Node plus the libraries its headless Chrome needs.
ARG RENDERER=""
RUN if [ -n "$RENDERER" ]; then apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libgbm1 \
    libasound2 libxrandr2 libxkbcommon0 libxfixes3 libxcomposite1 libxdamage1 libpango-1.0-0 libcairo2 libcups2 \
 && rm -rf /var/lib/apt/lists/*; fi

# PYTHONPATH: the project is not an installed package, and Celery puts /app on the path only while it starts, so code
# imported later inside a task (the video step imports `pipeline`) would not be found without it.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
ARG EXTRAS=""
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS
COPY . .
RUN uv sync --frozen --no-dev $EXTRAS && useradd --uid 10001 --create-home app && mkdir -p /app/media && chown app /app/media
RUN if [ -n "$RENDERER" ]; then cd renderer && npm ci && npx remotion browser ensure && chown -R app /app/renderer; fi
USER app

EXPOSE 8000
CMD ["sh", "-c", "python -m ytstudio.cli migrate && uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
