"""Predictions with friends (BHA-317): before a game, or a map of a series, fans call it in a tap; the app settles it
from the board its poller reads anyway, and friends see who called it best in small leagues joined by a link.
Bragging rights only: no fee, no prize of any value (India's online gaming law, 2025), no odds anywhere.

What's asked is each sport's (Sport.asks, read off a game's board): a chess game's result, a football or cricket
match's winner, each map of a team esports series. A question belongs to the game, whichever of its fixtures shows
it (a chess board is one of each player's: Sport.board tells the game apart), and is made the first time its board
is seen before the start: a poll of a followed game (structured.apply_poll), a look at its match page (match.view),
or the worker's read of a fan's game, or one of Today's big games, coming up (prepare, made); a game with no start
time is asked nothing till it has one. It closes as the game, or its map, starts (a first question's start time
closes it too, should a poll be late: sweep), and a pick after that is refused.

A fan picks without an account: picks are kept under the fan id, as follows are, and move into the account they
sign in to (merge). A league needs one. A game with a pick waiting on it is polled as a followed game is (watched)
until its questions settle, armed by its start: a followed player's or team's refresh moves that, as for any game
they're in, and a read of the game moves it for one nobody follows anyone in (made), read every PREPARE_EVERY in the
day before it, and a big game also when the league's read says it moved (prepare). Each poll settles what its board
answers (polled): a right pick scores 100 less the share (%) of fans who picked the same, at least FLOOR, so a call
few made is worth more; a wrong one 0. A question its game won't answer (a map the series didn't need) is voided, its
picks unscored; one still unanswered STALE past its start is voided too (sweep). A result the source takes back
settles again.

Fans see how everyone split once they've picked, or once the question's closed; friends' picks in a league once
it's closed. A league's table is its members' points on the questions settled since it was made.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, delete, exists, func, literal, or_, select, union, update
from sqlalchemy.dialects.postgresql import array, insert
from sqlalchemy.orm import Session, aliased

from . import bus, sports
from . import league as big_games  # Today's big games (league.py): `league` here is a friends' league's page
from .adapters import ADAPTERS
from .config import APP_NAME
from .db import session
from .models import Event, EventPlayer, EventState, EventTeam, Follow, League, LeagueMember, MatchFollow, Pick, Question, TeamFollow

FLOOR = 10  # a right pick scores at least this: one everyone made is still worth something
STALE = timedelta(days=3)  # a question unanswered this long past its game's start (or its making, with none) is voided
SPLIT_FROM = 20  # picks before an open question's split shows: with fewer, its moves would tell a fan a friend's pick
FRESH = timedelta(minutes=3)  # a game under way takes a later map's pick only on a board this fresh: never after it's begun
POSTPONED_KEEP = timedelta(days=30)  # a postponed game's questions wait this long for its new date before the sweep voids them
OPEN, CLOSED, SETTLED, VOIDED = "open", "closed", "settled", "void"
UNSETTLED = (OPEN, CLOSED)
PRE_START = ("scheduled", "armed")  # a game whose questions can still be made: a postponed one waits for its date
TO_COME = (*PRE_START, "postponed")  # a game that hasn't started here, whose start a read can still move
AHEAD = timedelta(hours=26)  # prepare reads a fan's game coming up this far ahead, to ask in time
PREPARE_EVERY = 2 * 3600  # and each game at most this often till its board asks (a chess round's pairings come late)
PREPARE_MOST = 10  # games read a run
RECENT = timedelta(hours=36)  # Today lists a fan's picks settled since
OPEN_MOST = 60  # and this many questions still open at most: their games' and Today's big games'
LEAGUES = 20  # leagues a fan is in at most
LEAGUE_CALLS = 20  # a league's page's calls still to settle at most, and as many settled lately
MEMBERS = 50  # fans in a league at most
NAME = 40  # a member's name, in characters
LEAGUE_NAME = 60
CODE = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # an invite code's letters: none mistaken for another (0 and O, 1 and I)
CODE_LEN = 6
OPENED_ONCE = 86400  # an invite opened again by the same fan within this many seconds counts once
SHUT = "Picks for this one are closed: it's started."
STALE_BOARD = "This one's game is under way and its latest score hasn't come in: open the game and try again in a minute."
NO_START = "This one's game has no start time yet: open the game and try again in a minute."
OFF = "Picks for this sport are off for now."
NO_POLL = f"Picks aren't open for this game: {APP_NAME} can't follow it live."
SIGN_IN = "Sign in to make or join a league."


class Refused(Exception):
    """What can't be done: its HTTP status, and what the fan is told."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def points(right: int, total: int) -> int:
    """What a right pick scores: 100 less the share (%) of all picks that were right, at least FLOOR."""
    return max(FLOOR, 100 - round(100 * right / total)) if total else 0


def shares(counts: dict[str, int], keys: list[str]) -> dict[str, int]:
    """Each answer's share of the picks in whole percents that add up to 100 (the largest remainders round up);
    all 0 with no picks."""
    total = sum(counts.get(k, 0) for k in keys)
    if not total:
        return {k: 0 for k in keys}
    exact = {k: 100 * counts.get(k, 0) / total for k in keys}
    out = {k: int(v) for k, v in exact.items()}
    for k in sorted(keys, key=lambda k: exact[k] - out[k], reverse=True)[: 100 - sum(out.values())]:
        out[k] += 1
    return out


# ---------------------------------------------------------------- making questions


def game_key(system: str, fixture: str, board: str) -> str:
    """The game a question belongs to: its source, and the id its sport tells the game apart by (Sport.board: a chess
    board's, the same under each player's fixture), else the fixture's own."""
    return f"{system}:{board or fixture}"[:100]


def _rules(sport: str | None, state: dict[str, Any]) -> sports.Sport:
    """The rules a game's questions are asked and answered by: its sport's (an esports game's), else its board's."""
    return sports.get(sport) or sports.of_state(state)


def ensure(db: Session, system: str, fixture: str, sport: str | None, status: str | None, state: dict[str, Any], start: datetime | None,
           title: str, keys: list[str], event_id: int | None = None, now: datetime | None = None) -> tuple[str, list[Question]]:
    """A game's questions: those its sport asks of its board, made now if it hasn't started and its start is known
    (each one closed by now left out), and those made before, by their place in it; each knowing every event key
    it's shown under and, where one's given, the event it's polled by. A game to come whose start moved has its
    first question's close move with it while it's open; one closed stays closed (friends' picks may have shown).
    Returns the game's key and its questions."""
    now = now or _now()
    rules = _rules(sport, state)
    game = game_key(system, fixture, rules.question_board(state))
    asks = {a.key: a for a in rules.asks(state)} if status in PRE_START else {}
    rows = list(db.scalars(select(Question).where(Question.game == game).order_by(Question.position, Question.id)))
    # Not one closed already: by its board, or a first one by its start time (a source late to say it began). Nor
    # any of a game with no start time: nothing would close it as it begins (its event isn't polled till it has
    # one), so it would take picks after its result was known. Its questions come once a read gives it a start.
    new = [a for a in asks.values() if start is not None and a.key not in {q.ask for q in rows} and not rules.closed(a.key, status, state) and not (a.first and start <= now)]
    for ask in new:
        db.execute(insert(Question).values(
            game=game, ask=ask.key, keys=keys, event_id=event_id, sport=rules.key, match=f"{system}/{fixture}"[:100], title=ask.title[:200],
            game_title=(title or "")[:300], options=[{"key": k, "label": label} for k, label in ask.options], position=ask.order, status=OPEN,
            starts_at=start, locks_at=start if ask.first else None,
        ).on_conflict_do_nothing(index_elements=["game", "ask"]))
    if new:
        rows = list(db.scalars(select(Question).where(Question.game == game).order_by(Question.position, Question.id)))
    for q in rows:
        if any(k not in (q.keys or []) for k in keys):
            q.keys = list(dict.fromkeys([*(q.keys or []), *keys]))
        if q.event_id is None and event_id is not None:
            q.event_id = event_id
        if start is not None and q.status in UNSETTLED and q.starts_at != start:
            q.starts_at = start
        ask = asks.get(q.ask)
        if ask is not None and ask.first and start is not None and start > now and q.status == OPEN and q.locks_at != start:
            q.locks_at, q.reminded_at = start, None  # moved: reminded again before its new start
    return game, rows


def polled(db: Session, event: Event, status: str, state: dict[str, Any], now: datetime) -> list[int]:
    """A poll of a game (structured.apply_poll): its questions made, if it's still to start, and what its board
    answers settled. Returns the questions settled now. Its last read is from Redis (match.last_read), which the
    poll's own copy is kept in too."""
    from . import match  # the game's last read, for its other addresses (match reads Live now, which reads search)

    rules = _rules(event.sport, state)
    if not rules.predicts:
        return []
    system, _, fixture = (event.key or "").partition(":")
    # Every address the game has, as its last read listed them (a chess board's other player's fixture), so a fan of
    # either player finds its questions before that fixture's own poll.
    listed = ((match.last_read(system, fixture) or {}).get("game") or {}).get("fixtures") or []
    keys = list(dict.fromkeys([event.key, *(f"{system}:{f}" for f in listed)]))
    _, rows = ensure(db, system, fixture, event.sport, status, state, event.start_utc, event.title, keys, event.id, now)
    return settle(db, rows, rules, status, state, now) if rows else []


def _read(db: Session, system: str, fixture: str, game: dict[str, Any], keys: list[str], polled_by: int | None, now: datetime) -> list[Question]:
    """A game's questions as a read of it says (its match page's, or the worker's): made if it's to come, settled from
    its board if it's under way or over, so a result its source corrects after the last poll still settles again."""
    state = (game.get("live") or {}).get("state") or {}
    start = datetime.fromisoformat(game["start_utc"]) if game.get("start_utc") else None
    _, rows = ensure(db, system, fixture, game.get("sport"), game.get("status"), state, start, game.get("title") or "", keys, polled_by, now)
    if rows and game.get("status") not in PRE_START:
        settle(db, rows, _rules(game.get("sport"), state), game.get("status"), state, now)
    return rows


def prepare(now: datetime | None = None) -> list[tuple[str, str]]:
    """The games coming up within AHEAD, in a sport that asks, with no questions yet: those of fans' players, teams
    and followed games, and Today's big games (league.coming), whoever follows them, so a fan who follows nobody can
    call those too. And the games only a read of says when they start, so their questions and the event they're
    polled by move with it (made; moved, for a refresh's): one a pick waits on that no followed refresh moves, while
    it's within AHEAD, and a big game whose start the league's read has moved since its questions were made, or
    since a pick gave them such an event (one brought forward from further off). The reads to ask for
    (worker.prepare_questions). A game is asked for at most every PREPARE_EVERY, PREPARE_MOST a run, soonest first."""
    from . import match  # the match page's reads (match reads Live now, which reads search, which reads the pipeline)

    now = now or _now()
    asked = exists().where(Question.keys.contains(func.jsonb_build_array(Event.key)))
    coming = [(datetime.fromisoformat(g["start_utc"]), g["key"], g["sport"]) for g in big_games.coming(now)]  # the league's last reads: no source asked
    big = [game for game in coming if game[0] <= now + AHEAD]
    with session() as db:
        theirs = db.execute(select(Event.start_utc, Event.key, Event.sport).where(Event.id.in_(_fans_events()), Event.status.in_(PRE_START), Event.start_utc.is_not(None),
                                                                                 Event.start_utc > now, Event.start_utc <= now + AHEAD, ~asked)).all()
        # A game a pick waits on that nothing refreshes (a game picked from its card or its match page): only a read
        # of it says it's moved, so it's read every PREPARE_EVERY in the AHEAD before it, a dozen reads at most.
        picked = db.execute(select(Event.start_utc, Event.key, Event.sport).where(Event.id.in_(watched()), Event.status.in_(TO_COME), Event.id.not_in(_refreshed()),
                                                                                 Event.start_utc > now, Event.start_utc <= now + AHEAD)).all()
        unrefreshed = and_(Event.id == Question.event_id, Event.status.in_(TO_COME), Event.id.not_in(_refreshed()))
        made = db.execute(select(Question.keys, Question.status, Question.starts_at, Question.event_id, Event.id, Event.start_utc).outerjoin(Event, unrefreshed)
                          .where(Question.keys.has_any(array([key for _, key, _ in big])))).all() if big else []
    # Each big game asked of: the starts held here that only a read of it moves (made). Its open questions' while
    # nothing polls them; once a pick gives them an event to be polled by (pick), that event's while it's still to
    # start and no followed player's or team's refresh moves it (structured.upsert_game), which is read anyway in
    # the AHEAD before that start (picked).
    held: dict[str, set[datetime | None]] = {}
    for keys, status, starts_at, event_id, ours, event_start in made:
        start = ([starts_at] if status == OPEN else []) if event_id is None else ([event_start] if ours is not None and status in UNSETTLED else [])
        for key in keys or []:
            held.setdefault(key, set()).update(start)
    # A start the league's read and the game's own disagree on (an older league read) is read again every
    # PREPARE_EVERY till the league's next read: a few reads a game at most, in the refresh lane.
    due = [game for game in big if game[1] not in held or held[game[1]] - {game[0]}]
    games = {key: (start, key, sport) for start, key, sport in [*theirs, *picked, *due]}  # a fan's game that's a big one, once
    out: list[tuple[str, str]] = []
    redis = bus.sync_redis()
    for _, key, sport in sorted(games.values()):
        system, _, fixture = key.partition(":")
        if not (sports.of(sport).predicts and sports.on(sport) and match.reader(system) and match.ID.fullmatch(fixture)):
            continue
        if redis.set(f"predictions:prepared:{key}", "1", nx=True, ex=PREPARE_EVERY):
            out.append((system, fixture))
        if len(out) >= PREPARE_MOST:
            break
    return out


def made(system: str, fixture: str) -> None:
    """After a game's read (worker.read_match): the questions its board asks, if it's still to start, under every
    fixture the read lists (a chess board's two); or, under way or over, what it answers of those made before. Its
    events here still to start that no followed player's or team's refresh brings up to date (a game picked from
    its card or its match page) start when the read says (_rescheduled), so they're armed, and their questions
    close, by the game's start now."""
    from . import match

    game = (match.last_read(system, fixture) or {}).get("game")
    if not game or not _rules(game.get("sport"), (game.get("live") or {}).get("state") or {}).predicts:
        return
    keys = list(dict.fromkeys(f"{system}:{f}" for f in [fixture, *(game.get("fixtures") or [])]))
    with session() as db:
        _rescheduled(db, keys, game)
        polled_by = db.scalar(select(Event.id).where(Event.key.in_(keys), Event.live_binding.is_not(None)).order_by(Event.id).limit(1))
        _read(db, system, fixture, game, keys, polled_by, _now())


def _rescheduled(db: Session, keys: list[str], game: dict[str, Any]) -> None:
    """A read's start and status, where it says the game's still to come, for its events here still to start that
    nothing else moves, as a schedule read sets them (structured.reschedule). A followed player's or team's refresh
    moves theirs (_refreshed): the two taking turns would move one back and forth, and remind its fans again each
    time (moved). A read that finds the game under way or over leaves its event to the poller, which a read calling
    it over too soon (a chess mini-match's next game) would stop."""
    from . import structured  # its polls make questions here

    status = game.get("status")
    start = datetime.fromisoformat(game["start_utc"]) if game.get("start_utc") else None
    if start is None or status not in TO_COME:
        return  # under way or over, the poller's; and a read with no start doesn't take away one known
    postponed = Event.status == "postponed"
    changed = or_(Event.start_utc.is_distinct_from(start), postponed if status == "scheduled" else ~postponed)  # a postponement often keeps its date
    for event in db.scalars(select(Event).where(Event.key.in_(keys), Event.status.in_(TO_COME), changed, Event.id.not_in(_refreshed()))):
        structured.reschedule(db, event, start, status)


# ---------------------------------------------------------------- closing and settling


def settle(db: Session, rows: list[Question], rules: sports.Sport, status: str | None, state: dict[str, Any], now: datetime) -> list[int]:
    """What a game's board says of its questions: those under way closed, those it answers settled and their picks
    scored (or voided), one whose answer the source changed settled again (a voided one too, once a winner it lacked
    comes). Returns the ones settled now."""
    done = []
    for q in rows:
        if q.status == VOIDED and q.answer != sports.VOID:
            continue  # voided by the sweep, long past its start: that's final
        answer = rules.answer(q.ask, status, state)
        if q.status == OPEN and (answer is not None or rules.closed(q.ask, status, state) or (q.locks_at is not None and q.locks_at <= now)):
            q.status, q.closed_at = CLOSED, now
        if answer is None or answer == q.answer or (answer == sports.VOID and q.status == SETTLED):
            continue  # nothing new; or a board that lost an answer it gave doesn't take it back
        if answer != sports.VOID and answer not in {o["key"] for o in q.options or []}:
            continue  # an answer it didn't offer (a side changed after it was asked): it waits for the sweep
        score(db, q, answer, now)
        done.append(q.id)
    return done


def score(db: Session, q: Question, answer: str, now: datetime) -> None:
    """Settle a question with its answer: each pick scored by how many made it (points), or, voided, none."""
    q.answer, q.settled_at, q.closed_at = answer, now, q.closed_at or now
    q.told_at = None  # settled again (a void a winner came for, a corrected result): its fans are told again
    if answer == sports.VOID:
        q.status = VOIDED
        db.execute(update(Pick).where(Pick.question_id == q.id).values(points=None))
        return
    q.status = SETTLED
    counts: dict[str, int] = {option: n for option, n in db.execute(select(Pick.option, func.count()).where(Pick.question_id == q.id).group_by(Pick.option))}
    worth = points(counts.get(answer, 0), sum(counts.values()))
    db.execute(update(Pick).where(Pick.question_id == q.id).values(points=case((Pick.option == answer, worth), else_=0)))


def moved(db: Session, event: Event, start: datetime | None, now: datetime | None = None) -> None:
    """A schedule read moved a game's start (structured.upsert_game), with no poll or read to make its questions
    again (ensure): those still to settle start then, so a game put back weeks isn't voided by the sweep three days
    after its old date, and a first one still open closes then (at once, for one brought forward to a time that's
    passed). One closed stays closed (friends' picks may have shown)."""
    if start is None:
        return
    now = now or _now()
    theirs = or_(Question.event_id == event.id, Question.keys.contains(func.jsonb_build_array(event.key)))
    db.execute(update(Question).where(theirs, Question.status.in_(UNSETTLED), Question.starts_at.is_distinct_from(start)).values(starts_at=start))
    if start > now:
        db.execute(update(Question).where(theirs, Question.status == OPEN, Question.locks_at.is_not(None), Question.locks_at != start).values(locks_at=start, reminded_at=None))
    else:
        db.execute(update(Question).where(theirs, Question.status == OPEN, Question.locks_at > start).values(locks_at=start))


def sweep(db: Session, now: datetime) -> None:
    """The tick (pipeline.due_work): first questions whose start time has come close, though no poll has seen the
    game start; and a question still unanswered STALE past its game's start (or its making, with none) is voided, but
    a postponed game's wait POSTPONED_KEEP for their new date."""
    db.execute(update(Question).where(Question.status == OPEN, Question.locks_at <= now).values(status=CLOSED, closed_at=now))
    since = func.coalesce(Question.starts_at, Question.created_at)
    postponed = Question.event_id.in_(select(Event.id).where(Event.status == "postponed"))
    stale = (Question.status.in_(UNSETTLED), since < now - STALE, or_(~postponed, Question.event_id.is_(None), since < now - POSTPONED_KEEP))
    # Its answer left empty: a void of the sweep's is final, where one its board gave (VOID) can still be settled.
    db.execute(update(Question).where(*stale).values(status=VOIDED, answer=None, settled_at=now, closed_at=func.coalesce(Question.closed_at, now)))


def watched() -> Any:
    """The events questions still to settle are settled by, which the poller arms and polls as followed games
    (pipeline.due_work, structured._watching): a game a fan picked any question of, and one with a question only a
    poll can close (a later map: it has no start time of its own), so a map played is never left open to call."""
    other = aliased(Question)
    picked = exists().where(Pick.question_id == other.id, other.game == Question.game)
    return select(Question.event_id).where(Question.status.in_(UNSETTLED), Question.event_id.is_not(None), or_(picked, Question.locks_at.is_(None)))


def waiting(db: Session, event_id: int) -> bool:
    """Whether a question still to settle waits on this event's polls (watched)."""
    return db.scalar(watched().where(Question.event_id == event_id).limit(1)) is not None


# ---------------------------------------------------------------- picking


def is_open(q: Question, now: datetime) -> bool:
    return q.status == OPEN and not (q.locks_at is not None and q.locks_at <= now)


def pick(db: Session, fan: str, question_id: int, option: str, now: datetime | None = None) -> Question:
    """A fan's answer to a question, or a new one in place of theirs, while it's open. A question with no event to
    be settled by yet gets its game's (matches.event_for), so the poller follows it from now on. Raises Refused for
    no such question, one closed, an answer it doesn't offer, a game no running source can follow, or one with no
    start time that a fresh board doesn't say is still to come."""
    from . import matches  # the event a followed game's polled by (matches reads match, which reads Live now)

    now = now or _now()
    q = db.get(Question, question_id, with_for_update=True)  # a poll closing it waits for this pick, or this for it
    if q is None:
        raise Refused(404, "No such question.")
    if not is_open(q, now):
        raise Refused(409, SHUT)
    if not sports.on(q.sport):
        raise Refused(409, OFF)
    if option not in {o["key"] for o in q.options or []}:
        raise Refused(400, "That isn't one of its answers.")
    if q.event_id is None:
        system, _, fixture = q.match.partition("/")
        event = matches.event_for(db, system, fixture)
        if event is None:
            raise Refused(409, NO_POLL)
        db.execute(update(Question).where(Question.game == q.game, Question.event_id.is_(None)).values(event_id=event.id))
    elif (db.scalar(select(Event.live_binding).where(Event.id == q.event_id)) or {}).get("adapter") not in ADAPTERS:
        raise Refused(409, NO_POLL)  # its source is off for now: a pick would never be settled
    if q.locks_at is None and q.starts_at is not None and q.starts_at <= now and not _fresh(db, q, now):
        raise Refused(409, STALE_BOARD)  # a later map of a game under way: only a fresh board says it hasn't begun
    if q.starts_at is None and not (_fresh(db, q, now) and _to_come(db, q)):
        raise Refused(409, NO_START)  # asked before a start was needed: nothing closes it, so only a fresh board can
    db.execute(insert(Pick).values(question_id=q.id, fan_id=fan, option=option)
               .on_conflict_do_update(index_elements=["question_id", "fan_id"], set_={"option": option, "updated_at": func.now()}))
    return q


def _to_come(db: Session, q: Question) -> bool:
    """Whether a game is still to start as far as it's known here: its event, its last poll's board and its match
    page's last read all say so (or say nothing)."""
    from . import match

    event_id = q.event_id
    status = db.scalar(select(Event.status).where(Event.id == event_id)) if event_id is not None else None
    polled = db.scalar(select(EventState.status).where(EventState.event_id == event_id).order_by(EventState.as_of.desc()).limit(1)) if event_id is not None else None
    system, _, fixture = q.match.partition("/")
    read = ((match.last_read(system, fixture) or {}).get("game") or {}).get("status")
    return all(s is None or s in TO_COME for s in (status, polled, read))



def _fresh(db: Session, q: Question, now: datetime) -> bool:
    """Whether a game's board is FRESH: its last poll's, or its match page's last read (match.read, every minute
    while the page is open)."""
    from . import match

    polled = db.scalar(select(func.max(EventState.as_of)).where(EventState.event_id == q.event_id)) if q.event_id is not None else None
    system, _, fixture = q.match.partition("/")
    read_at = (match.last_read(system, fixture) or {}).get("as_of")
    newest = max([t for t in (polled, datetime.fromisoformat(read_at) if read_at else None) if t is not None], default=None)
    return newest is not None and now - newest <= FRESH


def unpick(db: Session, fan: str, question_id: int, now: datetime | None = None) -> None:
    """Take a pick back while its question's open."""
    q = db.get(Question, question_id, with_for_update=True)
    if q is None or not sports.on(q.sport):
        raise Refused(404, "No such question.")
    if not is_open(q, now or _now()):
        raise Refused(409, SHUT)
    db.execute(delete(Pick).where(Pick.question_id == question_id, Pick.fan_id == fan))


# ---------------------------------------------------------------- what the app shows


def shown(db: Session, fan: str | None, rows: list[Question], now: datetime, public: bool = False) -> list[dict[str, Any]]:
    """Questions as the app shows them, of sports that are on: each answer with how fans split on it, once this fan
    has picked (and SPLIT_FROM have, so it can't tell them a friend's pick) or the question's closed (the crowd doesn't
    pick for them), as shares while it's open and with counts once closed;
    their own pick, what it scored or would score now, and the answer once it's settled. `public`: a link preview's,
    whose split shows to anyone once SPLIT_FROM have picked (the picker shared it)."""
    rows = [q for q in rows if sports.on(q.sport)]  # a sport turned off isn't offered
    ids = [q.id for q in rows]
    if not ids:
        return []
    counts: dict[int, dict[str, int]] = {}
    for qid, option, n in db.execute(select(Pick.question_id, Pick.option, func.count()).where(Pick.question_id.in_(ids)).group_by(Pick.question_id, Pick.option)):
        counts.setdefault(qid, {})[option] = n
    mine = {qid: (option, pts) for qid, option, pts in db.execute(select(Pick.question_id, Pick.option, Pick.points).where(Pick.question_id.in_(ids), Pick.fan_id == fan))} if fan else {}
    out = []
    for q in rows:
        keys = [o["key"] for o in q.options or []]
        c = counts.get(q.id, {})
        total = sum(c.get(k, 0) for k in keys)
        live = is_open(q, now)
        own = mine.get(q.id)
        split = shares(c, keys) if ((own is not None or public) and total >= SPLIT_FROM) or not live else None
        out.append({
            "id": q.id, "ask": q.ask, "title": q.title, "game": q.game_title, "sport": q.sport, "match": q.match,
            "status": q.status if q.status != OPEN or live else CLOSED, "locks_at": _iso(q.locks_at), "answer": q.answer,
            "picks": total,
            "split_from": SPLIT_FROM,
            # Shares only while it's open: exact counts moving as friends pick would tell their picks.
            "options": [{"key": o["key"], "label": o["label"], **({"share": split[o["key"]]} if split is not None else {}), **({"picks": c.get(o["key"], 0)} if not live else {})}
                        for o in q.options or []],
            "mine": own[0] if own else None,
            "points": own[1] if own else None,
            # What their pick would score were it right now, while it's still to be settled.
            "worth": points(c.get(own[0], 0), total) if own and q.status in UNSETTLED and split is not None else None,
        })
    return out


def for_page(db: Session, fan: str | None, system: str, fixture: str, page: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    """A match page's questions (match.view's page, which carries its board as a read does): made from its board if
    the game's still to come, else those made before, settled as far as its board says; under every address the page
    knows it by, polled by one of its events where it has one. None while its source is off."""
    now = now or _now()
    state = ((page.get("live") or {}).get("state")) or {}
    if system not in ADAPTERS or not _rules(page.get("sport"), state).predicts:
        return []  # a sport that asks nothing, or a source that's off: nothing could be settled
    ids = page.get("event_ids") or []
    polled_by = db.scalar(select(Event.id).where(Event.id.in_(ids), Event.live_binding.is_not(None)).order_by(Event.id).limit(1)) if ids else None
    keys = list(dict.fromkeys(f"{system}:{f}" for f in [fixture, *(page.get("fixtures") or [])]))
    rows = _read(db, system, fixture, page, keys, polled_by, now)
    return shown(db, fan, rows, now)


def public(db: Session, question_id: int, now: datetime | None = None) -> dict[str, Any] | None:
    """A question as its link preview shows it to anyone (share.of_question): its split, once SPLIT_FROM have picked
    or it's closed. None for no such question, or one of a sport that's off."""
    q = db.get(Question, question_id)
    found = shown(db, None, [q], now or _now(), public=True) if q is not None else []
    return found[0] if found else None


def for_players(db: Session, fan: str | None, player_ids: list[int], now: datetime | None = None) -> list[dict[str, Any]]:
    """A campaign page's questions (campaigns.page): those of its players' games still open, soonest first, then
    those settled lately, newest first."""
    now = now or _now()
    if not player_ids:
        return []
    keys = [k for (k,) in db.execute(select(Event.key).join(EventPlayer, EventPlayer.event_id == Event.id).where(EventPlayer.player_id.in_(player_ids))).all()]
    if not keys:
        return []
    theirs = Question.keys.has_any(array(keys))
    coming = list(db.scalars(select(Question).where(theirs, Question.status == OPEN, or_(Question.locks_at.is_(None), Question.locks_at > now))
                             .order_by(Question.locks_at.asc().nulls_last(), Question.game, Question.position).limit(6)))
    settled = list(db.scalars(select(Question).where(theirs, Question.status == SETTLED, Question.settled_at >= now - RECENT).order_by(Question.settled_at.desc()).limit(3)))
    return shown(db, fan, [*coming, *settled], now)


def _refreshed() -> Any:
    """The events a followed player's or team's refresh brings up to date, their start too (structured.upsert_game):
    those a followed player or team is in, whether or not their sport or source runs just now."""
    players = select(EventPlayer.event_id).join(Follow, Follow.player_id == EventPlayer.player_id)
    teams = select(EventTeam.event_id).join(TeamFollow, TeamFollow.team_id == EventTeam.team_id)
    return union(players, teams)


def _fans_events(fan: str | None = None) -> Any:
    """The events of a fan's players, teams and followed games (every fan's, for None)."""
    players = select(EventPlayer.event_id).join(Follow, Follow.player_id == EventPlayer.player_id)
    teams = select(EventTeam.event_id).join(TeamFollow, TeamFollow.team_id == EventTeam.team_id)
    games = select(MatchFollow.event_id).where(MatchFollow.until.is_(None))
    if fan is not None:
        players, teams, games = players.where(Follow.fan_id == fan), teams.where(TeamFollow.fan_id == fan), games.where(MatchFollow.fan_id == fan)
    return union(players, teams, games)


def today(db: Session, fan: str, now: datetime | None = None) -> dict[str, Any]:
    """Today's (GET /api/me/predictions): the questions still open on the games of the fan's players, teams and
    followed games (a series under way has its maps to come), on Today's big games coming up (league.coming: whoever
    follows them, so a fan who follows nobody has them to call), and on any they've picked, soonest first, OPEN_MOST
    at most: every game's first question comes before any later map (which has no start time of its own), so the cut
    takes later maps first. And their picks settled in the last RECENT, newest first."""
    now = now or _now()
    keys = [k for (k,) in db.execute(select(Event.key).where(Event.id.in_(_fans_events(fan)), Event.status.in_((*PRE_START, "live")))).all()]
    keys += [g["key"] for g in big_games.coming(now)]
    picked = select(Pick.question_id).where(Pick.fan_id == fan)
    theirs = or_(Question.keys.has_any(array(keys)), Question.id.in_(picked)) if keys else Question.id.in_(picked)
    coming = list(db.scalars(select(Question).where(Question.status == OPEN, or_(Question.locks_at.is_(None), Question.locks_at > now), theirs)
                             .order_by(Question.locks_at.asc().nulls_last(), Question.game, Question.position).limit(OPEN_MOST)))
    settled = list(db.scalars(select(Question).where(Question.id.in_(picked), Question.status.in_((SETTLED, VOIDED)), Question.settled_at >= now - RECENT)
                              .order_by(Question.settled_at.desc()).limit(20)))
    return {"open": shown(db, fan, coming, now), "settled": shown(db, fan, settled, now)}


# ---------------------------------------------------------------- leagues


def _code() -> str:
    return "".join(secrets.choice(CODE) for _ in range(CODE_LEN))


def _name(raw: Any, most: int, what: str) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise Refused(400, f"Give {what}.")
    if len(name) > most:
        raise Refused(400, f"{what[0].upper()}{what[1:]} can be {most} characters at most.")
    return name


def _room(db: Session, fan: str) -> None:
    if (db.scalar(select(func.count()).select_from(LeagueMember).where(LeagueMember.fan_id == fan)) or 0) >= LEAGUES:
        raise Refused(429, f"You're in {LEAGUES} leagues already: leave one to join another.")


def create(db: Session, fan: str, name: Any, member: Any) -> League:
    """A new league, with its maker in it by the name they gave. Raises Refused for a name missing or too long,
    or a fan in LEAGUES already."""
    title, me = _name(name, LEAGUE_NAME, "the league a name"), _name(member, NAME, "your name")
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"leagues:{fan}", 0))))
    _room(db, fan)
    for _ in range(5):
        league_id = db.scalar(insert(League).values(code=_code(), name=title, owner=fan).on_conflict_do_nothing(index_elements=["code"]).returning(League.id))
        if league_id is not None:
            db.execute(insert(LeagueMember).values(league_id=league_id, fan_id=fan, name=me))
            return db.get_one(League, league_id)
    raise Refused(503, "Couldn't make the league just now. Try again.")


def find(db: Session, code: str) -> League | None:
    code = (code or "").strip().upper()
    return db.scalar(select(League).where(League.code == code)) if 0 < len(code) <= 16 else None


def join(db: Session, fan: str, code: str, member: Any) -> League:
    """Join a league by its invite code, by the name given (a member already in it changes their name). Raises
    Refused for no such league, a full one, or a fan in LEAGUES already."""
    me = _name(member, NAME, "your name")
    league = find(db, code)
    if league is None:
        raise Refused(404, "No league has that invite: ask for a new link.")
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"leagues:{fan}", 0))))
    db.execute(select(League.id).where(League.id == league.id).with_for_update())  # one join at a time: the count holds
    if db.get(LeagueMember, (league.id, fan)) is not None:
        db.execute(update(LeagueMember).where(LeagueMember.league_id == league.id, LeagueMember.fan_id == fan).values(name=me))
        return league
    _room(db, fan)
    if (db.scalar(select(func.count()).select_from(LeagueMember).where(LeagueMember.league_id == league.id)) or 0) >= MEMBERS:
        raise Refused(409, f"This league is full: {MEMBERS} is the most.")
    db.execute(insert(LeagueMember).values(league_id=league.id, fan_id=fan, name=me).on_conflict_do_nothing())
    return league


def leave(db: Session, fan: str, code: str) -> None:
    """Leave a league; the last one out takes it with them."""
    league = find(db, code)
    if league is None:
        return
    db.execute(delete(LeagueMember).where(LeagueMember.league_id == league.id, LeagueMember.fan_id == fan))
    if db.scalar(select(LeagueMember.fan_id).where(LeagueMember.league_id == league.id).limit(1)) is None:
        db.execute(delete(League).where(League.id == league.id))


def invite(db: Session, code: str, fan: str | None) -> dict[str, Any]:
    """What an invite link shows anyone (/j/<code>): the league's name, how many are in it, who made it (by their
    name in it) and whether this fan's in it already; counted as opened for a fan who isn't. Raises Refused for no
    such league. A fan's opening is counted once a day."""
    league = find(db, code)
    if league is None:
        raise Refused(404, "No league has that invite: ask for a new link.")
    names: dict[str, str] = {f: n for f, n in db.execute(select(LeagueMember.fan_id, LeagueMember.name).where(LeagueMember.league_id == league.id))}
    member = fan is not None and fan in names
    if not member and bus.sync_redis().set(f"league:opened:{league.id}:{fan}", "1", nx=True, ex=OPENED_ONCE):
        db.execute(update(League).where(League.id == league.id).values(opened=League.opened + 1))
    return {"code": league.code, "name": league.name, "members": len(names), "by": names.get(league.owner or ""), "member": member}


def was_shared(db: Session, fan: str, code: str) -> None:
    """A member shared the invite from the app: counted for the launch numbers."""
    league = find(db, code)
    if league is not None and db.get(LeagueMember, (league.id, fan)) is not None:
        db.execute(update(League).where(League.id == league.id).values(shared=League.shared + 1))


def mine(db: Session, fan: str) -> list[dict[str, Any]]:
    """The leagues a fan is in, newest joined first, each with its size and their place in it."""
    out = []
    for league, _ in db.execute(select(League, LeagueMember.joined_at).join(LeagueMember, LeagueMember.league_id == League.id)
                                .where(LeagueMember.fan_id == fan).order_by(LeagueMember.joined_at.desc())):
        rows = table(db, league)
        me = next((r for r in rows if r["fan"] == fan), None)
        out.append({"code": league.code, "name": league.name, "members": len(rows), "place": me["place"] if me else None, "points": me["points"] if me else 0})
    return out


def table(db: Session, league: League) -> list[dict[str, Any]]:
    """A league's table: each member's points on the questions settled since it was made, how many they called
    right of those they picked, best first; members level on points share a place."""
    members: dict[str, str] = {f: n for f, n in db.execute(select(LeagueMember.fan_id, LeagueMember.name).where(LeagueMember.league_id == league.id))}
    scored = {fan: (int(pts or 0), int(right or 0), int(played or 0)) for fan, pts, right, played in db.execute(
        select(Pick.fan_id, func.sum(Pick.points), func.count().filter(Pick.points > 0), func.count(Pick.points))
        .join(Question, Question.id == Pick.question_id)
        .where(Pick.fan_id.in_(list(members)), Question.status == SETTLED, Question.settled_at >= league.created_at)
        .group_by(Pick.fan_id))}
    rows = sorted(({"fan": f, "name": n, "points": scored.get(f, (0, 0, 0))[0], "right": scored.get(f, (0, 0, 0))[1], "played": scored.get(f, (0, 0, 0))[2]}
                   for f, n in members.items()), key=lambda r: (-r["points"], -r["right"], r["name"].lower()))
    for i, r in enumerate(rows):
        r["place"] = rows[i - 1]["place"] if i and rows[i - 1]["points"] == r["points"] else i + 1
    return rows


def league(db: Session, fan: str, code: str, now: datetime | None = None) -> dict[str, Any]:
    """A league's page, for its members: its table, and the questions its members have picked that are still to
    settle, soonest first, then those settled lately, newest first (LEAGUE_CALLS of each at most, of sports that are
    on), each with who picked what once it's closed (before, only who has picked). Raises Refused for no such
    league, or one this fan isn't in."""
    now = now or _now()
    found = find(db, code)
    if found is None or db.get(LeagueMember, (found.id, fan)) is None:
        raise Refused(404, "No such league, or you're not in it.")
    rows = table(db, found)
    names = {r["fan"]: r["name"] for r in rows}
    asked = select(Pick.question_id).where(Pick.fan_id.in_(list(names)))
    theirs = [Question.id.in_(asked), or_(Question.status.in_(UNSETTLED), Question.settled_at >= now - RECENT)]
    # Sports turned off are left out before the cut, and the calls still to settle come first: else members' calls
    # settled lately take every place, and the open ones (whose "still to pick" is the page's point) go.
    theirs.append(Question.sport.not_in([s for s in db.scalars(select(Question.sport).where(*theirs).distinct()) if not sports.on(s)]))
    coming = list(db.scalars(select(Question).where(*theirs, Question.status.in_(UNSETTLED))
                             .order_by(Question.locks_at.asc().nulls_last(), Question.game, Question.position).limit(LEAGUE_CALLS)))
    settled = list(db.scalars(select(Question).where(*theirs, Question.status.not_in(UNSETTLED))
                              .order_by(Question.settled_at.desc(), Question.game, Question.position).limit(LEAGUE_CALLS)))
    questions = {q.id: q for q in [*coming, *settled]}
    picks: dict[int, dict[str, str]] = {}
    for qid, who, option in db.execute(select(Pick.question_id, Pick.fan_id, Pick.option).where(Pick.question_id.in_(list(questions)), Pick.fan_id.in_(list(names)))):
        picks.setdefault(qid, {})[who] = option
    shown_ = shown(db, fan, list(questions.values()), now)
    for item in shown_:
        q = questions[item["id"]]
        made = picks.get(q.id, {})
        hidden = is_open(q, now)
        item["friends"] = [{"name": names[f], "you": f == fan, **({"option": made[f]} if f == fan or not hidden else {})} for f in names if f in made]
        item["waiting"] = [names[f] for f in names if f not in made] if hidden else []
    return {"code": found.code, "name": found.name, "created_at": _iso(found.created_at), "members": len(rows),
            "table": [{k: r[k] for k in ("place", "name", "points", "right", "played")} | {"you": r["fan"] == fan} for r in rows],
            "questions": shown_}


# ---------------------------------------------------------------- accounts


def merge(db: Session, anon: str, fan: str) -> None:
    """An anonymous fan's picks on questions still open into their account's (accounts.adopt): a question both picked
    keeps the account's. Those on games under way or over stay the device's, in the split but in no league's table:
    else a fan could call a game three ways on three browsers and sign in on the one that was right. An anonymous
    fan is in no league: leagues need an account."""
    still = select(Question.id).where(Question.status == OPEN, or_(Question.locks_at.is_(None), Question.locks_at > func.now()))
    theirs = (Pick.fan_id == anon, Pick.question_id.in_(still))
    moved = insert(Pick).from_select(["question_id", "fan_id", "option", "points", "created_at", "updated_at"],
                                     select(Pick.question_id, literal(fan), Pick.option, Pick.points, Pick.created_at, Pick.updated_at).where(*theirs))
    db.execute(moved.on_conflict_do_nothing())
    db.execute(delete(Pick).where(*theirs))


def forget(db: Session, fan: str) -> None:
    """Everything kept under a fan id here, as their account goes (accounts.delete_account): their picks and places
    in leagues. A league they made stays its other members'; one left empty goes."""
    db.execute(delete(Pick).where(Pick.fan_id == fan))
    left = list(db.scalars(delete(LeagueMember).where(LeagueMember.fan_id == fan).returning(LeagueMember.league_id)))
    db.execute(update(League).where(League.owner == fan).values(owner=None))
    if left:
        db.execute(delete(League).where(League.id.in_(left), ~exists().where(LeagueMember.league_id == League.id)))


# ---------------------------------------------------------------- numbers (/admin)


def numbers(db: Session) -> dict[str, int]:
    """How it's doing, for the admin panel: leagues and those of three or more, invites shared and opened, fans who
    picked and their picks, and those who picked on two game days or more (they came back for another game)."""
    sizes = select(LeagueMember.league_id, func.count().label("n")).group_by(LeagueMember.league_id).subquery()
    leagues, shared, opened = db.execute(select(func.count(), func.coalesce(func.sum(League.shared), 0), func.coalesce(func.sum(League.opened), 0)).select_from(League)).one()
    days = select(Pick.fan_id, func.count(func.distinct(func.date(func.coalesce(Question.locks_at, Question.closed_at, Pick.created_at)))).label("d")) \
        .join(Question, Question.id == Pick.question_id).group_by(Pick.fan_id).subquery()
    return {
        "leagues": int(leagues),
        "leagues_3": int(db.scalar(select(func.count()).select_from(sizes).where(sizes.c.n >= 3)) or 0),
        "invites_shared": int(shared),
        "invites_opened": int(opened),
        "pickers": int(db.scalar(select(func.count(func.distinct(Pick.fan_id)))) or 0),
        "picks": int(db.scalar(select(func.count()).select_from(Pick)) or 0),
        "game_day_returns": int(db.scalar(select(func.count()).select_from(days).where(days.c.d >= 2)) or 0),
    }
