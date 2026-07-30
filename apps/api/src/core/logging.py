"""Cook Mantra's correlated, privacy-safe structured logging."""

import json
import logging
import re
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time
from enum import Enum

_OWNED_LOGGERS = (
    "agents",
    "api",
    "core",
    "domain",
    "main",
    "orchestration",
    "repositories",
    "services",
)
_OWNED_HANDLER_MARKER = "_cook_mantra_json_handler"
_CONFIGURATION_LOCK = threading.RLock()
_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "cook_mantra_log_context",
    default=None,
)
_SENSITIVE_KEY = re.compile(r"(key|secret|token|image|base64)", re.IGNORECASE)
_SECRET_TEXT = re.compile(
    r"(?:api(?:[_\s-])?key|secret|token|password|authorization)"
    r"(?:\s|%20)*(?:[:=]|%3[ad])|bearer(?:\s|%20)+",
    re.IGNORECASE,
)
_DATA_IMAGE = re.compile(r"^\s*data:image/[^;,]+;base64,", re.IGNORECASE)
_BASE64_PAYLOAD = re.compile(r"^[A-Za-z0-9+/_-]{64,}={0,2}$")
_ASCII_WHITESPACE = str.maketrans("", "", " \t\r\n\f\v")
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord(
        name="",
        level=0,
        pathname="",
        lineno=0,
        msg="",
        args=(),
        exc_info=None,
    ).__dict__
)
_STANDARD_RECORD_FIELDS |= {
    "message",
    "asctime",
    "taskName",
}


@contextmanager
def log_context(
    request_id: str | None = None,
    session_id: str | None = None,
    job_id: str | None = None,
) -> Iterator[None]:
    """Temporarily augment correlation fields without losing outer context."""
    nested = dict(_CONTEXT.get() or {})
    for key, value in (
        ("request_id", request_id),
        ("session_id", session_id),
        ("job_id", job_id),
    ):
        if value is not None:
            nested[key] = value
    token = _CONTEXT.set(nested)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def configure_logging(level: str) -> None:
    """Install one current, idempotent JSON-lines handler on owned loggers."""
    resolved_level = logging._nameToLevel.get(level.upper())
    if not isinstance(resolved_level, int):
        raise ValueError(f"Unknown logging level: {level}")

    with _CONFIGURATION_LOCK:
        handler = _SafeJsonStreamHandler()
        handler.setLevel(resolved_level)
        handler.setFormatter(_JsonFormatter())
        setattr(handler, _OWNED_HANDLER_MARKER, True)

        for name in _OWNED_LOGGERS:
            owned_logger = logging.getLogger(name)
            for existing in tuple(owned_logger.handlers):
                if getattr(existing, _OWNED_HANDLER_MARKER, False):
                    owned_logger.removeHandler(existing)
            owned_logger.addHandler(handler)
            owned_logger.setLevel(resolved_level)
            owned_logger.propagate = False


class _SafeJsonStreamHandler(logging.StreamHandler):
    """Write JSON records without logging's unsafe diagnostic fallback."""

    def __init__(self) -> None:
        super().__init__(stream=sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record)
            self.stream.write(rendered + self.terminator)
            self.flush()
        except BaseException:
            return

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        """Suppress logging's record repr and traceback fallback."""


class _JsonFormatter(logging.Formatter):
    """Render a safe JSON object while ignoring prose and tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            event = record.__dict__.get("event")
            payload: dict[str, object] = {
                "event": _safe_value(event if event is not None else "log_event"),
            }
            for key, value in (_CONTEXT.get() or {}).items():
                payload[key] = _safe_value(value)
            for key, value in record.__dict__.items():
                if key in _STANDARD_RECORD_FIELDS or key == "event":
                    continue
                safe_key = _safe_mapping_key(key)
                payload[safe_key] = (
                    "[REDACTED]"
                    if _SENSITIVE_KEY.search(safe_key)
                    else _safe_value(value)
                )
            return json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except BaseException:
            return '{"event":"logging_failure"}'


def _safe_value(value: object, seen: set[int] | None = None) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        compact_value = value.translate(_ASCII_WHITESPACE)
        if (
            _DATA_IMAGE.match(value)
            or _BASE64_PAYLOAD.fullmatch(compact_value)
            or _SECRET_TEXT.search(value)
        ):
            return "[REDACTED]"
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[REDACTED]"
    if isinstance(value, Exception):
        return {"exception_type": type(value).__name__}
    if isinstance(value, Enum):
        return _safe_value(value.value, seen)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    active = seen if seen is not None else set()
    value_id = id(value)
    if value_id in active:
        return "[REDACTED]"
    active.add(value_id)
    try:
        if isinstance(value, Mapping):
            converted: dict[str, object] = {}
            for key, child in value.items():
                safe_key = _safe_mapping_key(key)
                converted[safe_key] = (
                    "[REDACTED]"
                    if _SENSITIVE_KEY.search(safe_key)
                    else _safe_value(child, active)
                )
            return converted
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_safe_value(child, active) for child in value]
        return f"<{type(value).__name__}>"
    except BaseException:
        return "[UNSERIALIZABLE]"
    finally:
        active.discard(value_id)


def _safe_mapping_key(key: object) -> str:
    return key if isinstance(key, str) else f"<{type(key).__name__}>"
