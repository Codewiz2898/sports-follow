"""Logging for the API, the worker and the commands: one line per record on stdout, as JSON for a
server's log collector or as text to read, each carrying the id of the request or job it belongs to.

Every process calls setup() once it starts (and again if something replaces the logging config, as
arq's CLI does after importing the worker). Settings: SPORTS_FOLLOW_LOG_FORMAT and _LOG_LEVEL (config.py).

The id ("rid") and fields such as the fan, the job and the player live in a context the whole unit of
work shares: the request (server.RequestLog) or the job run (worker._logged) starts it with begin(),
anything that knows more adds to it with bind(), and a job queued from a request takes its id along
(carry()). asyncio.to_thread and Starlette's thread pool copy the context into their threads; a plain
ThreadPoolExecutor doesn't, so work handed to one goes through carried().
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
import re
import secrets
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from opentelemetry import propagate, trace

from .config import LOG_FORMAT, LOG_LEVEL

T = TypeVar("T")

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("sports_follow_log_context", default=None)

# What a proxy or load balancer in front may send as X-Request-ID for us to log under; anything else is
# replaced with an id of our own.
GIVEN_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

# A LogRecord's own attributes: everything else on a record came in `extra` and goes in the JSON.
_RECORD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "context"}


def new_id() -> str:
    return secrets.token_hex(8)


def given_id(value: str | None) -> str | None:
    return value if value and GIVEN_ID.fullmatch(value) else None


def begin(**fields: Any) -> dict[str, Any]:
    """Start a unit of work's context: the rest of this task, and the threads it hands work to, log with
    these fields. Not reset when the unit ends: it ends with its task, and a line the server or arq
    logs about the unit after it (an exception that escaped it) still carries its id."""
    context = {k: v for k, v in fields.items() if v is not None}
    _context.set(context)
    return context


def bind(**fields: Any) -> None:
    """Add fields to the current unit's context, for its lines from here on (and the request's own line)."""
    context = _context.get()
    if context is not None:
        context.update({k: v for k, v in fields.items() if v is not None})


def current() -> dict[str, Any]:
    return dict(_context.get() or {})


def carry() -> dict[str, Any]:
    """What a job queued now takes along so the worker logs it under this unit's id (worker._logged), and
    continues its trace (traceparent, when tracing.py is recording one)."""
    rid = (_context.get() or {}).get("rid")
    carried = {"rid": rid} if rid else {}
    propagate.inject(carried)
    return carried


def carried(fn: Callable[..., T]) -> Callable[..., T]:
    """fn, to run on another thread (a ThreadPoolExecutor's) in this unit's context."""
    return functools.partial(contextvars.copy_context().run, fn)


def fan(fan_id: str) -> str:
    """How a fan appears in logs: never their id (it's what their cookie or session stands for), but the
    same short hash on every line, which the database's fan ids can be matched against."""
    return hashlib.sha256(fan_id.encode()).hexdigest()[:12]


_make_record = logging.getLogRecordFactory()


def _record(*args: Any, **kwargs: Any) -> logging.LogRecord:
    """Every record, whichever handler takes it, with the context it was logged in (as it was then), and
    the trace and span it was logged in when tracing.py is on, so a line opens its trace."""
    record = _make_record(*args, **kwargs)
    context = dict(_context.get() or {})
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        context |= {"trace_id": format(span.trace_id, "032x"), "span_id": format(span.span_id, "016x")}
    record.context = context
    return record


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    """The record's context, then whatever it was logged with in `extra`."""
    return {**getattr(record, "context", {}), **{k: v for k, v in vars(record).items() if k not in _RECORD and not k.startswith("_")}}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **_fields(record),
        }
        if record.exc_info:
            line["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        elif record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """For reading in a terminal: the time, level, logger and message, then the context in brackets."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")

    def formatMessage(self, record: logging.LogRecord) -> str:
        context = getattr(record, "context", {})
        tail = " ".join(f"{k}={v}" for k, v in context.items())
        return f"{super().formatMessage(record)}  [{tail}]" if tail else super().formatMessage(record)


class _Stdout(logging.StreamHandler):
    """Whatever sys.stdout is when a line is written (a test's capture, a reloader's pipe), not when set up."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup() -> None:
    """Our lines at LOG_LEVEL, everyone else's warnings, and uvicorn's and arq's own lines, all through one
    handler on stdout. Safe to call again: it replaces the handler it added before."""
    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, _Stdout)]:
        root.removeHandler(old)
    handler = _Stdout()
    handler.setFormatter(JsonFormatter() if LOG_FORMAT == "json" else TextFormatter())
    root.addHandler(handler)
    logging.setLogRecordFactory(_record)
    root.setLevel(logging.WARNING)
    logging.getLogger("sports_follow").setLevel(LOG_LEVEL)
    # uvicorn's and arq's configs give their loggers handlers of their own: send theirs through ours.
    # arq's line for each job is replaced by worker._logged's (with the job's id and fields).
    for name, level in (("uvicorn", logging.INFO), ("arq", logging.WARNING)):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)
    # server.RequestLog logs each request, with its id and how long it took; uvicorn's line has neither.
    logging.getLogger("uvicorn.access").disabled = True
