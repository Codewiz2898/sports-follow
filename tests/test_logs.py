"""Logging (logs.py): the API's own lines reach the log under uvicorn (BHA-191), as JSON on a server, and
every line carries the id and fields of the request or job it belongs to (BHA-192)."""

from __future__ import annotations

import contextvars
import io
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sports_follow import logs

ROOT = Path(__file__).resolve().parent.parent

# What uvicorn does before it imports the app, then lines from the app and from uvicorn itself.
UNDER_UVICORN = """
import logging, logging.config
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
import sports_follow.server
logging.getLogger("sports_follow.search").info("search: a source left out")
logging.getLogger("sports_follow").warning("follow espn_soccer:1: unreachable")
logging.getLogger("uvicorn.error").info("Application startup complete.")
logging.getLogger("uvicorn.access").info('127.0.0.1:5000 - "GET /api/sports HTTP/1.1" 200')
logging.getLogger("httpx").info("HTTP Request: GET https://example.com/?api_token=secret")
"""


def test_the_api_logs_its_own_lines_under_uvicorn_as_json():
    env = {**os.environ, "SPORTS_FOLLOW_LOG_FORMAT": "json", "SPORTS_FOLLOW_LOG_LEVEL": "INFO"}
    out = subprocess.run([sys.executable, "-c", UNDER_UVICORN], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True)
    lines = [json.loads(line) for line in out.stdout.splitlines()]  # every line is JSON
    assert [(line["level"], line["logger"], line["msg"]) for line in lines] == [
        ("INFO", "sports_follow.search", "search: a source left out"),  # dropped before
        ("WARNING", "sports_follow", "follow espn_soccer:1: unreachable"),  # bare text before
        ("INFO", "uvicorn.error", "Application startup complete."),
    ]  # uvicorn's access line is replaced by the API's own; other libraries' info lines (URLs with keys) stay out
    assert all(line["ts"].endswith("Z") for line in lines)
    assert "Traceback" not in out.stderr and "secret" not in out.stdout + out.stderr


# What arq's CLI does: import the worker, replace the logging config with its own, start the worker; then a
# job runs, queued by a request.
UNDER_ARQ = """
import asyncio, logging, logging.config
from arq.logs import default_log_config
from sports_follow import worker
logging.config.dictConfig(default_log_config(verbose=False))
asyncio.run(worker.WorkerSettings.on_startup({}))
logging.getLogger("arq.worker").info("  0.01s -> build_card(7)")
logging.getLogger("arq.worker").warning("job x expired")

async def build_card(ctx, player_id):
    logging.getLogger("sports_follow.pipeline").warning("build %s failed: no answer", player_id)

asyncio.run(worker._logged(build_card)({"job_id": "j1", "job_try": 1}, 7, trace={"rid": "ab12cd34ef56ab78"}))
"""


def test_the_worker_logs_as_json_after_arqs_own_config():
    env = {**os.environ, "SPORTS_FOLLOW_LOG_FORMAT": "json", "SPORTS_FOLLOW_LOG_LEVEL": "INFO"}
    out = subprocess.run([sys.executable, "-c", UNDER_ARQ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True)
    lines = [json.loads(line) for line in out.stdout.splitlines()]
    assert [(line["logger"], line["msg"]) for line in lines] == [
        ("arq.worker", "job x expired"),  # arq's warnings through ours; its line per job replaced by _logged's
        ("sports_follow.pipeline", "build 7 failed: no answer"),
        ("sports_follow.worker", lines[-1]["msg"]),
    ]
    assert [line.get("rid") for line in lines] == [None, "ab12cd34ef56ab78", "ab12cd34ef56ab78"]
    assert lines[-1]["msg"].startswith("build_card done in ") and lines[-1]["player_id"] == 7 and lines[-1]["job_id"] == "j1"
    assert "Traceback" not in out.stderr


def test_setting_up_again_replaces_the_handler():
    logs.setup()
    logs.setup()
    assert sum(isinstance(h, logs._Stdout) for h in logging.getLogger().handlers) == 1
    assert logging.getLogger("sports_follow").getEffectiveLevel() == logging.INFO


def _formatted(formatter: logging.Formatter, log: Callable[[logging.Logger], None]) -> str:
    """What `log` writes through `formatter`, in a context of its own (begin() lasts as long as its task)."""
    logs.setup()
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(formatter)
    logger = logging.getLogger("sports_follow.test")
    logger.addHandler(handler)
    try:
        contextvars.copy_context().run(log, logger)
    finally:
        logger.removeHandler(handler)
    return out.getvalue()


def test_a_json_line_has_the_context_the_extras_and_the_traceback():
    def log(logger):
        logs.begin(rid="ab12cd34ef56ab78", job="build_card", player_id=7)
        logs.bind(fan=logs.fan("fan-a"))
        try:
            raise ValueError("no such athlete")
        except ValueError:
            logger.warning("build %s failed", 7, exc_info=True, extra={"status": 502, "source": object()})

    line = json.loads(_formatted(logs.JsonFormatter(), log))
    assert line["msg"] == "build 7 failed" and line["level"] == "WARNING" and line["logger"] == "sports_follow.test"
    assert line["rid"] == "ab12cd34ef56ab78" and line["job"] == "build_card" and line["player_id"] == 7
    assert line["fan"] == logs.fan("fan-a") != "fan-a" and len(line["fan"]) == 12
    assert line["status"] == 502 and line["source"].startswith("<object")  # what JSON can't hold, as text
    assert line["exc"].startswith("Traceback") and "ValueError: no such athlete" in line["exc"]


def test_a_text_line_ends_with_its_context():
    def log(logger):
        logs.begin(rid="ab12cd34ef56ab78", job="poll_event", event_id=5)
        logger.info("poll %s: 1-0", 5)

    assert _formatted(logs.TextFormatter(), log).rstrip().endswith("sports_follow.test: poll 5: 1-0  [rid=ab12cd34ef56ab78 job=poll_event event_id=5]")


def test_work_handed_to_a_thread_pool_keeps_its_context():
    def handed_over():
        logs.begin(rid="ab12cd34ef56ab78")
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(logs.current).result(), pool.submit(logs.carried(logs.current)).result()

    bare, carried = contextvars.copy_context().run(handed_over)
    assert bare == {} and carried == {"rid": "ab12cd34ef56ab78"}


def test_only_a_well_formed_request_id_is_taken():
    assert logs.given_id("0f8e2a6c-1b2d-4c3e-9f00-112233445566") == "0f8e2a6c-1b2d-4c3e-9f00-112233445566"
    for bad in (None, "", "short", "has space in it", "x" * 65, 'quote"injection'):
        assert logs.given_id(bad) is None
