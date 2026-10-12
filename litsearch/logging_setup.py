"""Structured logging for deployment (v0.9.13).

One line per event, JSON when a machine reads it and plain text when a human
does, on stdout by default so Docker can collect it without a log agent. A file
sink is optional and always rotates, because 40 GB of disk is not an excuse for
an unbounded log.

Redaction is a property of this module, not a convention: :func:`redact` strips
anything that looks like a credential before a record is formatted, so a token
cannot reach a log by accident. Health endpoints, logs and error payloads all
run through it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

from litsearch import config

#: Event field order for the text sink; JSON keeps its own keys.
FIELDS = ("component", "event", "job_id", "provider", "duration_ms")

SECRET_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)
#: Provider keys look like ``s2k-…``, ``sk-…``; DuckDNS tokens are UUIDs.
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:s2k|sk|hf|ghp|gho|glpat)[-_][A-Za-z0-9_\-]{16,}\b"
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)

_configured = False


def redact(value):
    """Return ``value`` with credentials removed, recursively for mappings."""
    if isinstance(value, dict):
        return {key: ("***" if SECRET_KEY_RE.search(str(key)) else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = SECRET_VALUE_RE.sub("***", value)
        return text if len(text) <= 400 else text[:400] + "…"
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with the deployment fields promoted."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "version": getattr(record, "version", None) or _version(),
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", record.getMessage()),
            "logger": record.name,
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None and field not in payload:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable single line for a console or ``docker logs``."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [self.formatTime(record, "%H:%M:%S"), record.levelname[0],
                 str(getattr(record, "component", record.name))]
        event = getattr(record, "event", None) or record.getMessage()
        parts.append(str(event))
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None and field != "event":
                parts.append(f"{field}={value}")
        if record.exc_info:
            parts.append(self.formatException(record.exc_info))
        return redact(" ".join(parts))


def _version() -> str:
    try:
        from litsearch.version import __version__
        return __version__
    except Exception:  # pragma: no cover - version module is always importable
        return "unknown"


def configure(level: str | None = None, *, force: bool = False) -> dict:
    """Install the root handler set and report where records go.

    Returns a small dict (``sink``/``log_file``/``level``) so ``/api/health``
    can state it without re-deriving the decision. Never raises: a bad
    ``LEEXTRACTOR_LOG_DIR`` falls back to stdout rather than killing startup.
    """
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return {"sink": "unchanged", "log_file": None, "level": logging.getLevelName(root.level)}
    for handler in list(root.handlers):
        root.removeHandler(handler)

    level_name = (level or os.environ.get("LEEXTRACTOR_LOG_LEVEL", "") or "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    json_sink = (os.environ.get("LEEXTRACTOR_LOG_FORMAT", "") or "text").lower() == "json"

    sink, log_file = "stdout", None
    directory = config.log_dir()
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
            log_file = os.path.join(directory, "leextractor.log")
            handler: logging.Handler = RotatingFileHandler(
                log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            sink = "file"
        except OSError:
            handler = logging.StreamHandler(sys.stdout)
            sink, log_file = "stdout", None
    else:
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(JsonFormatter() if json_sink else TextFormatter())
    root.addHandler(handler)
    _configured = True
    return {"sink": sink, "log_file": log_file, "level": level_name}


def log_event(logger: logging.Logger, event: str, *, component: str = "", level: int = logging.INFO,
              **fields) -> None:
    """Emit one structured record; unknown fields are dropped, secrets removed."""
    safe = {key: value for key, value in redact(fields).items() if key in FIELDS}
    logger.log(level, event, extra={"event": event, "component": component or logger.name, **safe})
