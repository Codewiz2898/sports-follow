"""Metrics (OpenTelemetry): the numbers that show trouble before fans see it, sent over OTLP beside the
traces and log lines (tracing.setup starts them where OTEL_METRICS_EXPORTER, otlp by default, has an
endpoint). Off, every call here does nothing.

Two kinds:
* Counted where they happen, in every process: API requests by route (server.RequestLog), job runs by
  job (worker._logged), pushes by push service and result and how long a moment waited for its push,
  moments found by kind (notify.py), card builds and refreshes by result (pipeline.py, structured.py).
  Source requests come from the httpx instrumentation: http.client.request.duration, by server.address.
* Read every minute by the worker alone (watch()): live games and how long since each was last polled,
  the job queue, cards by status and how old the live cards' data is, and each source's allowance:
  used, what it says is left, whether it's pausing us.
* The feed's, in the curator (BHA-326, feedstats.py): its funnel by sport and stream, why what was matched wasn't
  kept, the judge's verdicts, errors and time, moment searches; and each sport's coverage, worked out once a poll.

Labels are route, job_name, source, bucket, lane, sport, kind, status, result, and the feed's stream, reason,
about_player, sponsored, risk and stage: never a fan's, a player's or a channel's id. Not plain job, which Prometheus keeps for the process (sports-follow-api, -worker), dropping ours.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from opentelemetry import metrics as otel

log = logging.getLogger("sports_follow.metrics")

_meter = otel.get_meter("sports_follow")
_made: dict[tuple[int, str], Any] = {}
_watching = False

# Seconds: from a fast query to a ten-minute poll session (job durations, a moment's wait for its push).
SECONDS = (0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300, 900)
# A live game is behind when it hasn't been polled for three of its rounds, a round being its source's
# cadence or a minute (armed and quiet games are polled once a minute), and never sooner than this.
BEHIND = 180
# A moment found this long ago and still unsent: its batch's sender (notify.send) didn't claim it, nor
# did the tick's sweep a minute after (notify.SWEEP_AFTER).
UNSENT_AFTER = 180


def _instrument(kind: str, name: str, unit: str, description: str, **advice: Any) -> Any:
    """The instrument on the current meter, made once (tests swap the meter for one of their own)."""
    key = (id(_meter), name)
    if key not in _made:
        _made[key] = getattr(_meter, f"create_{kind}")(name, unit=unit, description=description, **advice)
    return _made[key]


# ---------------------------------------------------------------- counted where they happen


def request(method: str, route: str | None, status: int, seconds: float) -> None:
    """One API request answered (server.RequestLog): OpenTelemetry's own HTTP server metric, and a count of
    server errors of its own (no route: one series, which seed() starts at 0, for sf-api-errors)."""
    _instrument("histogram", "http.server.request.duration", "s", "API requests by route and status, and how long they took",
                explicit_bucket_boundaries_advisory=SECONDS).record(
        seconds, {"http.request.method": method, "http.route": route or "unmatched", "http.response.status_code": status})
    if status >= 500:
        _api_errors().add(1)


def _api_errors() -> Any:
    return _instrument("counter", "sports_follow.api.errors", "{request}", "API requests answered with a server error (5xx)")


def admin_sign_in(result: str) -> None:
    """One try at the admin panel's sign-in (server.admin_login): ok, or wrong (a wrong password or code).
    app-rules.json emails on every one that's ok, and on many wrong ones."""
    _admin_sign_ins().add(1, {"result": result})


def _admin_sign_ins() -> Any:
    return _instrument("counter", "sports_follow.admin.sign_ins", "{sign_in}", "Tries at the admin panel's sign-in, by result")


# The push services nearly every browser uses (Chrome and Edge, Safari, Firefox): seed() starts their counts at 0.
PUSH_SERVICES = ("fcm.googleapis.com", "web.push.apple.com", "updates.push.services.mozilla.com")


def seed() -> None:
    """Zero for each count the alert rules take increase() of (app-rules.json), sent at once. A series'
    first sample counts nothing in increase(), Grafana Cloud records no zero before it, and each
    process's series are new (its own service.instance.id): without these, the first failures after a
    deploy would go unseen. At startup, in the API and the worker."""
    _api_errors().add(0)
    for result in ("ok", "wrong"):
        _admin_sign_ins().add(0, {"result": result})
    for step in ("build", "refresh"):
        for result in ("ok", "failed"):
            card(step, result, by=0)
    counter = _instrument("counter", "sports_follow.pushes", "{push}", "Pushes to devices by push service and result")
    for service in PUSH_SERVICES:
        for result in ("delivered", "gone", "failed"):
            counter.add(0, {"push_service": service, "result": result})
    _flush()


def _flush() -> None:
    """Send what's recorded now, not at the next export."""
    flush = getattr(otel.get_meter_provider(), "force_flush", None)
    if flush is not None:
        try:
            flush(timeout_millis=5000)
        except Exception as exc:  # noqa: BLE001 — a slow export mustn't hold up a start
            log.warning("metrics: the first export failed: %s", exc)


def job(name: str, outcome: str, seconds: float, waited: float | None) -> None:
    """One worker job ended (worker._logged): how long it ran and how it ended; how long it waited to start."""
    _instrument("histogram", "sports_follow.job.duration", "s", "Worker jobs by job and outcome, and how long they ran",
                explicit_bucket_boundaries_advisory=SECONDS).record(seconds, {"job_name": name, "outcome": outcome})
    if waited is not None:
        _instrument("histogram", "sports_follow.job.wait", "s", "How long worker jobs waited in the queue to start",
                    explicit_bucket_boundaries_advisory=SECONDS).record(max(0.0, waited), {"job_name": name})


def pushes(results: Iterable[tuple[str | None, int | str | None]], waited: float | None = None) -> None:
    """A batch's pushes (notify.send, predictions_push): each device's push service and what it answered (None: took
    it), and how long the batch's oldest moment waited, from its poll or build finding it to its pushes (None for
    pushes that aren't moments')."""
    counted: Counter[tuple[str, str]] = Counter()
    for service, error in results:
        result = "delivered" if error is None else "gone" if error in (404, 410) else "failed"
        counted[(service or "unknown", result)] += 1
    counter = _instrument("counter", "sports_follow.pushes", "{push}", "Pushes to devices by push service and result")
    for (service, result), n in counted.items():
        counter.add(n, {"push_service": service, "result": result})
    if waited is not None:
        _instrument("histogram", "sports_follow.push.wait", "s", "How long a batch's oldest moment waited for its pushes",
                    explicit_bucket_boundaries_advisory=SECONDS).record(max(0.0, waited))


def moment(kind: str) -> None:
    """A moment found and stored (notify.record): a goal, a wicket, a result."""
    _instrument("counter", "sports_follow.moments", "{moment}", "Moments found, by kind").add(1, {"kind": kind})


def card(step: str, result: str, by: int = 1) -> None:
    """A card built (pipeline.build_card) or a player's source read again (structured.refresh): ok or failed."""
    _instrument("counter", "sports_follow.cards.updates", "{update}", "Card builds and refreshes, by result").add(by, {"step": step, "result": result})


def feed(result: str, by: int = 1) -> None:
    """Things the feed's fetch looked at (candidates.py): kept, refused by a hard filter, or dropped once gone."""
    if by:
        _instrument("counter", "sports_follow.feed.candidates", "{item}", "Feed candidates fetched, by result").add(by, {"result": result})


# ---------------------------------------------------------------- the feed's funnel, judge and coverage (BHA-326)

# Where a video came from: a trusted channel's uploads (candidates.poll), a moment's search (searches.run), a search
# for a player with nothing to watch this week (gap), for their month's most watched (month), or for their most
# watched ever (famous; all three sweeps.py). A
# publisher's story (news.py) is judged, passed and served as "news" too.
STREAMS = ("channels", "search", "gap", "month", "famous")
# Each step of the funnel, as sports_follow.feed.<step>: uploads read, those naming a followed player (one for each
# player named), kept (a row stored), judged, passed by the judge, and served (a row's score set, newly).
FEED_STEPS = {
    "uploads": "Uploads a feed source returned: a trusted channel's latest, or a moment search's results",
    "matched": "Uploads naming a followed player, one for each player named",
    "kept": "Candidates kept: a row stored for a player and video",
    "judged": "Candidates the judge gave a verdict on",
    "passed": "Candidates the judge lets be served: about the player, not paid for, no risk",
    "served": "Candidates newly served: their score set where it wasn't (again, after a hide or a channel's trust at 0 is taken back)",
}
# Why a matched upload isn't kept (feedstats.reason): the hard filters' words, and the two ways it was seen already.
REFUSALS = ("not_embeddable", "blocked", "too_long", "too_short", "live", "private", "gone", "refused_before", "already_stored")
# Why the judge couldn't answer (curate.CurateError.kind). "off" is the model turned off on purpose: no alert.
JUDGE_ERRORS = ("out_of_credit", "timeout", "invalid_output", "declined", "unavailable", "off")
_coverage: dict[str, tuple[int, int]] = {}  # sport: (followed players, those with a servable video this week)


def sport_label(name: str | None) -> str:
    """A sport as the feed's metrics label it: its key ("football" for "Soccer"), or "other" for one no module
    knows, so a stray name can't make a series of its own."""
    from . import sports

    sport = sports.get(name)
    return sport.key if sport else "other"


def feed_step(step: str, stream: str, sports_of: Iterable[str | None]) -> None:
    """One step of the feed's funnel (FEED_STEPS), counted by sport: one sport for each thing that took it."""
    counter = _instrument("counter", f"sports_follow.feed.{step}", "{item}", FEED_STEPS[step])
    for sport, n in Counter(sport_label(s) for s in sports_of).items():
        counter.add(n, {"sport": sport, "stream": stream})


def feed_refused(stream: str, lost: Iterable[tuple[str | None, str]]) -> None:
    """Matched uploads not kept, by sport and reason (REFUSALS): (sport, reason) for each player and video."""
    counter = _instrument("counter", "sports_follow.feed.refused", "{item}", "Matched uploads not kept, by sport and reason")
    for (sport, reason), n in Counter((sport_label(s), r) for s, r in lost).items():
        counter.add(n, {"sport": sport, "stream": stream, "reason": reason})


def feed_verdict(about: str, sponsored: bool, risky: bool, stage: str, seconds: float) -> None:
    """One verdict from the judge (curate.judge_many): who it says it's about, whether it's paid for or names a risk,
    which stage gave it; and how long it took."""
    _instrument("counter", "sports_follow.feed.verdicts", "{verdict}", "The judge's verdicts, by about_player, sponsored, risk and stage").add(
        1, {"about_player": about, "sponsored": str(sponsored).lower(), "risk": str(risky).lower(), "stage": stage})
    _judge_seconds().record(seconds, {"stage": stage})


def feed_judge_error(kind: str, seconds: float) -> None:
    """A candidate the judge couldn't answer for now (curate.CurateError), by kind (JUDGE_ERRORS)."""
    _judge_errors().add(1, {"kind": kind})
    _judge_seconds().record(seconds, {"stage": "failed"})


def _judge_errors() -> Any:
    return _instrument("counter", "sports_follow.feed.judge_errors", "{candidate}", "Candidates the judge couldn't answer for, by kind")


def _judge_seconds() -> Any:
    return _instrument("histogram", "sports_follow.feed.judge.duration", "s", "How long the judge took over a candidate, by stage (or failed)",
                       explicit_bucket_boundaries_advisory=SECONDS)


def feed_search(sport: str | None, units: int, stream: str = "search") -> None:
    """A search one source answered, a moment's (searches.run) or a sweep's (sweeps.py: its stream), and what it
    spent of the source's allowance."""
    labels = {"sport": sport_label(sport), "stream": stream}
    _instrument("counter", "sports_follow.feed.searches", "{search}", "Searches a source answered, by sport and stream").add(1, labels)
    _instrument("counter", "sports_follow.feed.search_units", "{unit}", "What searches spent of their sources' allowances, by sport and stream").add(units, labels)


def feed_coverage(by_sport: dict[str, tuple[int, int]]) -> None:
    """Each sport's followed players and those with a servable video this week (feedstats.coverage), worked out
    once a poll and reported at every export until the next: a sport no longer in it isn't reported at all."""
    global _coverage
    _coverage = dict(by_sport)
    for name, part, unit, description in (
        ("sports_follow.feed.followed", 0, "{player}", "Followed players, by sport"),
        ("sports_follow.feed.covered", 1, "{player}", "Followed players with a servable video published in the last 7 days, by sport"),
        ("sports_follow.feed.coverage", 2, "", "The share of a sport's followed players with a servable video published in the last 7 days"),
    ):
        def read(options: Any, part: int = part) -> Iterable[Any]:
            from opentelemetry.metrics import Observation

            return [Observation((followed, covered, covered / followed if followed else 0.0)[part], {"sport": sport})
                    for sport, (followed, covered) in _coverage.items()]

        _instrument("observable_gauge", name, unit, description, callbacks=[read])


def feed_seed() -> None:
    """Zero, at the curator's start, for each count app-rules.json takes increase() of (see seed()): candidates kept
    by each running sport and stream, while a video source runs, and the judge's errors by kind."""
    from . import sports
    from .adapters import videos

    if videos():
        for sport in sports.ENABLED:
            for stream in STREAMS:
                _instrument("counter", "sports_follow.feed.kept", "{item}", FEED_STEPS["kept"]).add(0, {"sport": sport.key, "stream": stream})
    for kind in JUDGE_ERRORS:
        _judge_errors().add(0, {"kind": kind})
    _flush()


# ---------------------------------------------------------------- read every minute, by the worker


Reading = list[tuple[float, dict[str, Any]]]


def behind_after(live_binding: dict[str, Any]) -> float:
    """Seconds without a poll after which a game is behind: three rounds of its source's cadence (or of a
    minute, for armed and quiet games), at least BEHIND. Liquipedia's 5-minute rounds make 15 minutes."""
    return max(BEHIND, 3 * max(60, int(live_binding.get("cadence") or 10)))


def live(now: float | None = None) -> dict[str, Reading]:
    """The armed and live games with a followed player, or followed by themselves (matches.py) unless held back
    for now (structured.holds_back: the poller waits on purpose, so they aren't late), and a live binding to a
    source this build runs (one it doesn't run never polls them, and they aren't late for it), by source and
    status; per
    source, how long since its least recently polled game was polled (structured._polled), and how
    many of its games weren't polled within behind_after (or ever)."""
    from sqlalchemy import select

    from . import bus, structured
    from .adapters import ADAPTERS
    from .db import session
    from .models import Event, EventPlayer, Follow

    now = now or time.time()
    with session() as db:
        theirs = select(EventPlayer.event_id).where(EventPlayer.player_id.in_(select(Follow.player_id)))
        games = [(event_id, status, dict(binding or {})) for event_id, status, binding, mine in db.execute(
            select(Event.id, Event.status, Event.live_binding, Event.id.in_(theirs))
            .where(Event.status.in_(("armed", "live")), Event.live_binding.is_not(None), Event.id.in_(structured.watched()))
        ).all() if (adapter := ADAPTERS.get(str((binding or {}).get("adapter")))) is not None and (mine or not structured.holds_back(adapter))]
    counts: Counter[tuple[str, str]] = Counter((binding["adapter"], status) for _, status, binding in games)
    redis: Any = bus.sync_redis()  # synchronous: its answers are values, whatever redis-py's types allow
    polled = redis.mget([f"polled_at:{event_id}" for event_id, _, _ in games]) if games else []
    oldest: dict[str, float] = {}
    # Every running source says how many of its games are behind, 0 too: the alert rule then has data
    # while the worker reports, and keeps its state when the worker goes quiet (sf-app-silent says so).
    behind: Counter[str] = Counter({name: 0 for name in ADAPTERS})
    for (_, _, binding), at in zip(games, polled):
        source = binding["adapter"]
        age = now - float(at) if at is not None else None
        if age is not None:
            oldest[source] = max(oldest.get(source, 0.0), age)
        if age is None or age > behind_after(binding):
            behind[source] += 1
    return {
        "games": [(n, {"source": source, "status": status}) for (source, status), n in counts.items()],
        "poll_age": [(age, {"source": source}) for source, age in oldest.items()],
        "behind": [(n, {"source": source}) for source, n in behind.items()],
    }


def unsent(now: datetime | None = None) -> dict[str, Reading]:
    """Moments found over UNSENT_AFTER seconds ago that no sender has claimed, still young enough to send
    (alerts.LATE_TTL): any for a few minutes, and pushes aren't going out."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from .alerts import LATE_TTL
    from .db import session
    from .models import Moment

    now = now or datetime.now(timezone.utc)
    with session() as db:
        waiting = db.scalar(select(func.count()).select_from(Moment).where(
            Moment.sent_at.is_(None), Moment.created_at <= now - timedelta(seconds=UNSENT_AFTER), Moment.created_at >= now - timedelta(seconds=LATE_TTL)))
    return {"unsent": [(waiting or 0, {})]}


def queue(now: float | None = None) -> dict[str, Reading]:
    """arq's queue: jobs ready to run but waiting for a slot, jobs set for later, jobs running, and how
    long the oldest waiting one has waited. arq leaves a job in its queue until it finishes (with the time
    it was due), so the running ones are taken out: a ten-minute poll session isn't waiting."""
    from arq.constants import default_queue_name, in_progress_key_prefix

    from . import bus

    now_ms = (now or time.time()) * 1000
    redis: Any = bus.sync_redis()  # synchronous: its answers are values, whatever redis-py's types allow
    running = {key.removeprefix(in_progress_key_prefix) for key in redis.scan_iter(match=f"{in_progress_key_prefix}*", count=500)}
    due = redis.zcount(default_queue_name, "-inf", now_ms)
    first = redis.zrangebyscore(default_queue_name, "-inf", now_ms, start=0, num=1000, withscores=True)  # oldest first
    waiting = [at for job, at in first if job not in running]
    ready = due - (len(first) - len(waiting))
    return {
        "jobs": [(ready, {"state": "ready"}), (redis.zcard(default_queue_name) - due, {"state": "deferred"}), (len(running), {"state": "running"})],
        "oldest_wait": [(max(0.0, (now_ms - waiting[0]) / 1000) if waiting else 0.0, {})],
    }


def cards(now: datetime | None = None) -> dict[str, Reading]:
    """Followed players' cards by status and sport, and how old the newest live score is on each sport's
    live cards at their worst (freshness["live"])."""
    from sqlalchemy import func, select

    from .db import session
    from .models import Follow, Player, PlayerCard

    now = now or datetime.now(timezone.utc)
    with session() as db:
        followed = select(Follow.player_id)
        statuses = db.execute(
            select(PlayerCard.status, Player.sport, func.count()).join(Player, Player.id == PlayerCard.player_id)
            .where(PlayerCard.player_id.in_(followed)).group_by(PlayerCard.status, Player.sport)
        ).all()
        live_rows = db.execute(
            select(Player.sport, PlayerCard.freshness).join(Player, Player.id == PlayerCard.player_id)
            .where(PlayerCard.is_live.is_(True), PlayerCard.player_id.in_(followed))
        ).all()
    worst: dict[str, float] = {}
    for sport, freshness in live_rows:
        stamp = (freshness or {}).get("live")
        try:
            at = datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            at = None
        if at is not None:
            worst[sport or "other"] = max(worst.get(sport or "other", 0.0), (now - at).total_seconds())
    return {
        "cards": [(n, {"status": status, "sport": sport or "other"}) for status, sport, n in statuses],
        "live_data_age": [(age, {"sport": sport}) for sport, age in worst.items()],
    }


def _running_budgets() -> dict[str, Any]:
    """The budgets of the sources this build runs, by name."""
    from .adapters import ADAPTERS, news, videos

    return {b.name: b for source in [*ADAPTERS.values(), *videos(), *news()] for b in source.budgets()}


def sources(now: float | None = None) -> dict[str, Reading]:
    """Each running source's allowance (adapters/budget.py): requests so far this window, by bucket,
    against the window's allowance; what the source says is left; whether it's pausing us; and this
    hour's requests by lane, sent and refused."""
    from .adapters import budget

    now = now or time.time()
    running = _running_budgets()
    used: Reading = []
    allowance: Reading = []
    left: Reading = []
    paused: Reading = []
    for name, b in running.items():
        stand = budget.standing(b, now)
        for w in stand["windows"]:
            used.append((w["used"], {"source": name, "bucket": w["bucket"], "window": w["window"]}))
            if not w["bucket"]:
                allowance.append((w["allowance"], {"source": name, "window": w["window"]}))
        left += [(n, {"source": name, "bucket": bucket}) for bucket, n in stand["left"].items()]
        paused.append((1 if stand["paused"] else 0, {"source": name}))
    hourly = [(row[f"{what}_h"], {"source": row["source"], "lane": row["lane"], "result": what})
              for row in budget.usage(list(running.values()), now) for what in ("sent", "refused") if row[f"{what}_h"]]
    return {"used": used, "allowance": allowance, "left": left, "paused": paused, "hourly": hourly}


# Each reading the worker reports: (metric, unit, description, the reading, which of its parts).
READINGS: list[tuple[str, str, str, Callable[[], dict[str, Reading]], str]] = [
    ("sports_follow.live.games", "{game}", "Armed and live games with a followed player, by source and status", live, "games"),
    ("sports_follow.live.poll_age", "s", "Seconds since each source's least recently polled live game was polled", live, "poll_age"),
    ("sports_follow.live.games_behind", "{game}", "Live games not polled for three of their rounds (at least 3 minutes), by source", live, "behind"),
    ("sports_follow.moments.unsent", "{moment}", f"Moments found over {UNSENT_AFTER} seconds ago that no sender has claimed", unsent, "unsent"),
    ("sports_follow.jobs", "{job}", "arq jobs: ready and waiting for a slot, set for later, running", queue, "jobs"),
    ("sports_follow.jobs.oldest_wait", "s", "How long the oldest ready job has waited for a slot", queue, "oldest_wait"),
    ("sports_follow.cards", "{card}", "Followed players' cards by status and sport", cards, "cards"),
    ("sports_follow.cards.live_data_age", "s", "Age of the newest live score on a sport's live cards, at the worst", cards, "live_data_age"),
    ("sports_follow.source.used", "{request}", "Requests to a source so far in its current window, by bucket", sources, "used"),
    ("sports_follow.source.allowance", "{request}", "A source's allowance a window", sources, "allowance"),
    ("sports_follow.source.left", "{request}", "What a source says is left of its allowance, by bucket", sources, "left"),
    ("sports_follow.source.paused", "", "1 while a source has asked us to slow down", sources, "paused"),
    ("sports_follow.source.requests_this_hour", "{request}", "Requests to a source this hour, by lane, sent and refused", sources, "hourly"),
]


def watch() -> None:
    """Report READINGS every time metrics are exported: in the worker only, as the numbers are the same
    from any process. Readings share one run of each function per export."""
    from opentelemetry.metrics import CallbackOptions, Observation

    global _watching
    if _watching:
        return
    _watching = True
    cache: dict[Callable[[], dict[str, Reading]], tuple[float, dict[str, Reading]]] = {}

    def read(fn: Callable[[], dict[str, Reading]]) -> dict[str, Reading]:
        at, value = cache.get(fn, (0.0, {}))
        if time.monotonic() - at > 5:
            try:
                value = fn()
            except Exception as exc:  # noqa: BLE001 — a reading that fails is left out, and the rest go
                log.warning("metrics: %s failed: %s", fn.__name__, exc)
                value = {}
            cache[fn] = (time.monotonic(), value)
        return value

    for name, unit, description, fn, part in READINGS:
        def callback(options: CallbackOptions, fn: Callable[[], dict[str, Reading]] = fn, part: str = part) -> Iterable[Observation]:
            return [Observation(value, attributes) for value, attributes in read(fn).get(part, [])]

        _meter.create_observable_gauge(name, callbacks=[callback], unit=unit, description=description)
