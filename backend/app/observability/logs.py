"""Structured logging: one JSON object per line on stderr, carrying the request ID and the active trace.

Log with fields as `extra`; they become top-level keys:

    log.info("answer.finished", extra={"repo_id": 4, "latency_ms": 6120})

-> {"ts": "...", "level": "INFO", "logger": "codedoc.answers", "msg": "answer.finished",
    "request_id": "9f2c...", "trace_id": "4bf9...", "span_id": "00f0...", "repo_id": 4, "latency_ms": 6120}

`LOG_FORMAT=text` prints the same fields as a readable line, for local development.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from opentelemetry import trace

from app.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

log = logging.getLogger("codedoc.http")

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RECORD_FIELDS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Libraries that log every request at INFO; their lines add nothing to ours.
_QUIET = {name: logging.WARNING for name in ("httpx", "httpx2", "httpcore", "mcp", "uvicorn.access")}


def _context_fields() -> dict:
    fields: dict = {}
    if request_id := request_id_var.get():
        fields["request_id"] = request_id
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        fields["trace_id"] = format(span_context.trace_id, "032x")
        fields["span_id"] = format(span_context.span_id, "016x")
    return fields


def _extra_fields(record: logging.LogRecord) -> dict:
    return {k: v for k, v in vars(record).items() if k not in _RECORD_FIELDS and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_context_fields(),
            **_extra_fields(record),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {**_context_fields(), **_extra_fields(record)}
        fields.pop("span_id", None)
        line = (f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} "
                f"{record.name}: {record.getMessage()}")
        if fields:
            line += "  " + " ".join(f"{k}={v}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(settings: Settings) -> None:
    """Send every logger (ours, uvicorn's, arq's) through one stderr handler. Idempotent."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "arq", "arq.worker", "arq.jobs"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    for name, level in _QUIET.items():
        logging.getLogger(name).setLevel(level)


class RequestLogMiddleware:
    """Gives each HTTP request an ID (the caller's X-Request-ID, or a new one), returns it as X-Request-ID,
    and logs one `http.request` line when the response ends. Plain ASGI, so streamed responses (chat SSE)
    pass through untouched and their duration covers the whole stream."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"x-request-id", request_id.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            log.log(logging.DEBUG if path == "/health" else logging.INFO, "http.request", extra={
                "method": scope.get("method"), "path": path, "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            request_id_var.reset(token)
