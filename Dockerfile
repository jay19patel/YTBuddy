# One image for api, workers and beat. Pinned base; deps installed from uv.lock with hashes.
FROM python:3.12.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8.19 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app
ARG EXTRAS=""
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $EXTRAS
COPY . .
RUN uv sync --frozen --no-dev $EXTRAS && useradd --uid 10001 --create-home app && mkdir -p /app/media && chown app /app/media
USER app

EXPOSE 8000
CMD ["sh", "-c", "python -m ytstudio.cli migrate && uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
