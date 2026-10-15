"""What goes wrong in the web app, in the log beside the API's and the worker's own errors (BHA-196).

The app (web/src/errors.ts) posts each uncaught error, failed render and failed API call to /api/errors,
and each becomes one line on the logger sports_follow.web, grouped as the server's exceptions are
(logs.py): `error` is its name ("TypeError", or "HTTP 502" for a call), and `place` is where in the app's
source it was thrown ("web/src/pages/PlayerPage.tsx"), or which call failed ("GET /api/players/{id}/card").
The line also has the page, the app's version (its build) and the failed call's request id, which finds
that request's own line and trace. Query strings are cut from every address in it (a search's words).

The build is minified. Its source maps (vite.config.ts keeps them in web/sourcemaps, which isn't served)
turn the stack back into the app's own files, lines and columns. A stack from a build this server doesn't
have (a tab left open since an earlier deploy) stays as it came.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import threading
from array import array
from bisect import bisect_right
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from . import logs

log = logging.getLogger("sports_follow.web")

MAPS = Path(__file__).resolve().parent.parent / "web" / "sourcemaps"
# Reports taken in an hour, from one fan, from one address, and from everyone together; the rest are dropped.
PER_FAN, PER_ADDRESS, PER_HOUR = 30, 60, 600
STACK_LINES, LINE_CHARS = 60, 1000


class Report(BaseModel):
    """One error, as the app sends it."""

    kind: Literal["error", "rejection", "render", "api"]
    name: str = Field("Error", max_length=120)
    message: str = Field("", max_length=2000)
    stack: str = Field("", max_length=16000)
    page: str = Field("", max_length=1000)
    version: str = Field("", max_length=80)
    call: str | None = Field(None, max_length=1000)  # a failed API call: "GET /api/players/7/card"
    status: int | None = Field(None, ge=0, le=999)
    request_id: str | None = Field(None, max_length=200)


URL = re.compile(r"https?://[^\s()'\"<>]+")
POSITION = re.compile(r"(:\d+){1,2}$")
# A frame in the build ("https://stann.in/assets/index-B3kd8.js:1:2345") or, in development, in the app's
# own files ("http://localhost:5173/src/pages/PlayerPage.tsx:120:14").
FRAME = re.compile(r"(?:https?://[^\s()/]+)?/(?P<file>assets/[\w.-]+\.js|src/[^\s():]+):(?P<line>\d+):(?P<column>\d+)")
ID = re.compile(r"/\d+(?=/|$)")


def _without_queries(text: str) -> str:
    """Every address in the text without its query string or fragment, keeping a stack frame's :line:col."""

    def bare(match: re.Match[str]) -> str:
        url = match.group(0)
        position = POSITION.search(url)
        tail = position.group(0) if position else ""
        return re.split(r"[?#]", url[: len(url) - len(tail)], maxsplit=1)[0] + tail

    return URL.sub(bare, text)


def _path(page: str) -> str:
    """An address's path alone: "/search?q=x" -> "/search"."""
    try:
        path = urlsplit(page).path
    except ValueError:  # not an address urllib can read ("//[")
        path = re.split(r"[?#]", page, maxsplit=1)[0]
    return path[:300] or "/"


def _call(call: str | None) -> str | None:
    """"GET /api/players/7/card?x=1" -> "GET /api/players/7/card"."""
    if not call:
        return None
    method, _, address = call.strip().partition(" ")
    return f"{method.upper()[:10]} {_path(address)}" if address else None


class _Map(NamedTuple):
    sources: list[str]
    # For each line of the build: its mapped columns, and each one's source, line and column there.
    lines: list[tuple[array, array, array, array]]


_DIGITS = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")}


def _vlq(segment: str) -> list[int]:
    """A source map segment's numbers (base64 VLQ)."""
    values, value, shift = [], 0, 0
    for char in segment:
        digit = _DIGITS[char]
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
        else:
            values.append(-(value >> 1) if value & 1 else value >> 1)
            value = shift = 0
    return values


def _lines(mappings: str) -> list[tuple[array, array, array, array]]:
    lines = []
    source = line = column = 0  # carried from segment to segment, across lines too
    for text in mappings.split(";"):
        generated = 0
        mapped = (array("l"), array("l"), array("l"), array("l"))
        for segment in text.split(","):
            if not segment:
                continue
            values = _vlq(segment)
            generated += values[0]
            if len(values) >= 4:
                source, line, column = source + values[1], line + values[2], column + values[3]
                for into, value in zip(mapped, (generated, source, line, column)):
                    into.append(value)
        lines.append(mapped)
    return lines


_reading = threading.Lock()


def _map(file: str) -> _Map | None:
    """The source map of one file of the build ("index-B3kd8.js"), if this server has it. A name it
    doesn't have costs a look in the folder; one it has is read once (by one report at a time)."""
    if not (MAPS / f"{file}.map").is_file():
        return None
    with _reading:
        return _read(MAPS / f"{file}.map")


@lru_cache(maxsize=4)
def _read(path: Path) -> _Map | None:
    try:
        raw = json.loads(path.read_text())
        root = raw.get("sourceRoot") or ""
        # Its sources are relative to where the build wrote it, web/dist/assets.
        sources = [posixpath.normpath(posixpath.join("web/dist/assets", root, s)) for s in raw["sources"]]
        return _Map(sources, _lines(raw["mappings"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _original(file: str, line: int, column: int) -> str | None:
    """Where a place in the build ("assets/index-B3kd8.js", 1-based line and column) came from: "web/src/main.tsx:12:5"."""
    found = _map(file.removeprefix("assets/"))
    if found is None or not 0 < line <= len(found.lines):
        return None
    columns, sources, lines, source_columns = found.lines[line - 1]
    i = bisect_right(columns, column - 1) - 1
    if i < 0:
        return None
    return f"{found.sources[sources[i]]}:{lines[i] + 1}:{source_columns[i] + 1}"


def resolve(stack: str) -> tuple[str, str | None]:
    """The stack with each frame at its place in the app's source, and the file of its innermost frame
    in the app's own code (web/src), else of its innermost frame."""
    frames, ours, first = [], None, None
    for text in stack.splitlines()[:STACK_LINES]:
        text = text[:LINE_CHARS]
        match = FRAME.search(text)
        if match:
            file, line, column = match["file"], int(match["line"]), int(match["column"])
            where = (f"web/{file}:{line}:{column}" if file.startswith("src/") else _original(file, line, column)) or f"{file}:{line}:{column}"
            text = text[: match.start()] + where + text[match.end():]
            source = POSITION.sub("", where)
            first = first or source
            if ours is None and source.startswith("web/src/"):
                ours = source
        frames.append(text)
    return "\n".join(frames), ours or first


def record(report: Report, route: Callable[[str, str], str | None] = lambda method, path: None) -> None:
    """Log one report (the request's own context names the fan, by a hash). `route` names the API route
    a failed call went to ("/api/athletes/{system}/{source_id}"), which groups it; else its numbers are ids."""
    page, call = _path(report.page), _call(report.call)
    stack, where = resolve(_without_queries(report.stack)) if report.stack else ("", None)
    if report.kind == "api":
        error = f"HTTP {report.status}" if report.status else "no answer"
        if call:
            method, _, path = call.partition(" ")
            place = f"{method} {route(method, path) or ID.sub('/{id}', path)}"
        else:
            place = "an API call"
    else:
        error = report.name.strip() or "Error"
        place = where or f"page {ID.sub('/{id}', page)}"
    message = _without_queries(report.message)
    fields = {
        "error": error, "place": place, "kind": report.kind, "page": page, "version": report.version or None,
        "call": call, "call_status": report.status, "call_rid": logs.given_id(report.request_id), "exc": stack or None,
    }
    log.warning("web %s: %s: %s", report.kind, error, message[:300], extra={k: v for k, v in fields.items() if v is not None})
