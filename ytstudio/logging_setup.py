"""Structured JSON logging with secret redaction."""

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Bare Google access/refresh tokens and API keys, and anything written as key=value / "key": "value".
BARE = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]+|sk-[\w-]{8,}|AIza[\w-]{20,}|AQ\.[\w.-]{20,})")
KEYED = re.compile(
    r"(?i)(\b(?:access_token|refresh_token|client_secret|api_key|password|code|token|key)[\"']?\s*[:=]\s*[\"']?)"
    r"[^\s\"'&,}]+"
)


def redact(text: str) -> str:
    return KEYED.sub(r"\1[REDACTED]", BARE.sub("[REDACTED]", text))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if rid := run_id_var.get():
            out["run_id"] = rid
        if qid := request_id_var.get():
            out["request_id"] = qid
        for key in ("channel_id", "step", "task_id", "event"):
            if key in record.__dict__:
                out[key] = record.__dict__[key]
        if record.exc_info:
            out["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if as_json else RedactingFormatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("googleapiclient.discovery_cache").setLevel("ERROR")
    logging.getLogger("LiteLLM").setLevel("WARNING")
