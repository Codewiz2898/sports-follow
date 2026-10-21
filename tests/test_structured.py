"""How one snapshot changes a player's card (structured._apply_snapshot), on recorded ESPN data, how a
lane polls all of a source's games together (structured.lane_round), what a poll's moments are found
against (structured.baseline), games the schedule finishes before the poller does, and how a refresh
reads a player's recent results."""

from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from sports_follow import bus, moments, structured
from sports_follow.adapters import ADAPTERS, KNOWN, AdapterError, Fixture, Line, PlayerRef, Snapshot, budget, espn_cricket, espn_soccer, lichess_chess
from sports_follow.adapters.sportmonks_cricket import SportmonksCricket
from sports_follow.adapters.sportmonks_football import SportmonksFootball
from sports_follow.models import Event, EventState, Player, PlayerCard, PlayerLine, SourceBinding

from .test_adapters import KOHLI, RONALDO, load
from .test_new_adapters import CARLSEN, LAZAVIK
from .test_new_adapters import load as recorded
from .test_pipeline import FakeDb, FakeRedis
from .test_predictions import board

NOW = datetime(2026, 9, 29, 13, 45, tzinfo=timezone.utc)


def card(**extra) -> dict:
    return {"live": {"is_live": False, "player_stats": []}, "upcoming": [], "recent_results": [], "freshness": {}, **extra}


def test_live_snapshot_fills_the_live_block_with_state_and_line():
    snap = espn_cricket.parse_summary(load("cricket-summary-live"))
    # poll_once sets the event's status from the snapshot before applying it.
    event = Event(id=7, title="India A v Australia A", notes="2nd unofficial Test", competition="Australia A tour of India", start_utc=NOW, status="live")
    pid, line = next(iter(snap.lines.items()))
    c = card(upcoming=[{"title": "India A v Australia A", "event_id": 7, "status": "armed"}])
    structured._apply_snapshot(c, event, snap, line, ADAPTERS["espn_cricket"], NOW)
    live = c["live"]
    assert live["is_live"] is True
    assert live["event_id"] == 7
    assert live["event"] == "India A v Australia A · 2nd unofficial Test"
    assert live["score"] == "AUS-A 282/7 (87.0 ov)"
    assert live["state"]["kind"] == "cricket"
    assert live["headline"] == line.headline
    assert live["source"] == "ESPNcricinfo"
    assert c["upcoming"][0]["status"] == "live"
    assert c["freshness"]["live"] == NOW.isoformat()


@pytest.mark.parametrize(("system", "live_note", "lag_note"), [
    ("liquipedia_esports", "Standings update from Liquipedia every few minutes as results are recorded.",
     "Lobby results appear as Liquipedia's editors record them, often several minutes after each match."),
    ("pubg_esports", "Standings update from PUBG after each match.", "PUBG lobbies update after each match ends."),
    ("espn_cricket", None, None),  # the app's own sentences
])
def test_the_live_block_carries_the_sources_own_notes(system, live_note, lag_note):
    # The app words a player page's notes from these, never by the source's name (BHA-249).
    snap = Snapshot(status="live", score_label="Lobby 3 of 6", clock_label="", state={"kind": "battle_royale"})
    event = Event(id=5, title="BGIS 2026 · Grand Finals", notes=None, competition="BGIS", start_utc=NOW, status="live")
    c = card()
    structured._apply_snapshot(c, event, snap, None, KNOWN[system], NOW)
    assert (c["live"]["live_note"], c["live"]["lag_note"]) == (live_note, lag_note)


def test_a_live_card_names_its_games_match_page():
    snap = Snapshot(status="live", score_label="Arsenal 1 – 0 Chelsea", clock_label="12'", state={"kind": "football"})
    event = Event(id=5, key="sportmonks_football:555", title="Arsenal vs Chelsea", notes=None, competition="Premier League", start_utc=NOW, status="live")
    c = card()
    structured._apply_snapshot(c, event, snap, None, KNOWN["sportmonks_football"], NOW)
    assert c["live"]["match"] == "sportmonks_football/555"  # /match/<source>/<its fixture id> (match.py)


def test_a_result_and_a_game_to_come_name_their_match_pages():
    source = KNOWN["sportmonks_football"]
    ref = PlayerRef("sportmonks_football", "7", "Bukayo Saka", ["19"], ["Arsenal"], None)
    final = Fixture(source_id="555", title="Arsenal vs Chelsea", competition="Premier League", start_utc=NOW, venue=None, status="final", team_ids=["19", "18"], locator={})
    event = Event(id=5, key="sportmonks_football:555", title="Arsenal vs Chelsea", start_utc=NOW, status="final")
    assert structured.result_item(source, final, None, event, ref)["match"] == "sportmonks_football/555"
    assert structured.result_item(source, final, None, None, ref)["match"] is None
    ahead = Event(id=6, key="sportmonks_football:556", title="Chelsea vs Arsenal", start_utc=NOW + timedelta(days=7), status="scheduled")
    assert structured._upcoming_item(replace(final, source_id="556", status="scheduled"), ahead, ref)["match"] == "sportmonks_football/556"
    # The poller seeing it finish: the result it adds names its page too.
    c = card(live={"is_live": True, "event_id": 5, "player_stats": []})
    structured._apply_snapshot(c, event, Snapshot(status="final", score_label="Arsenal 2 – 1 Chelsea", clock_label="FT", state={"kind": "football"}), None, source, NOW)
    assert c["recent_results"][0]["match"] == "sportmonks_football/555"
    assert structured.match_path("0123abcd" * 8) is None  # an event the research agent wrote up: no source has it


def test_a_chess_board_is_one_game_under_each_players_fixture():
    # A board is a fixture of each player's, so an event of each: their cards say the one game it is, so Today
    # shows it once to a fan who follows both (BHA-372), as its questions are one set (predictions.game_key).
    source = KNOWN["lichess_chess"]
    sides = [(CARLSEN, Event(id=11, key=f"lichess_chess:ohv5F74c-{CARLSEN}", title="Magnus Carlsen vs Denis Lazavik", start_utc=NOW, status="live")),
             (LAZAVIK, Event(id=12, key=f"lichess_chess:ohv5F74c-{LAZAVIK}", title="Magnus Carlsen vs Denis Lazavik", start_utc=NOW, status="live"))]
    lives, finals, refreshed = [], [], []
    for fide, event in sides:
        on = board(status="*", ongoing=True, fide=fide)
        c = card()
        structured._apply_snapshot(c, event, on, on.lines.get(fide), source, NOW)
        lives.append(c["live"])
        over = board(0, fide=fide)
        structured._apply_snapshot(c, event, over, over.lines.get(fide), source, NOW)
        finals.append(c["recent_results"][0])
        ref = PlayerRef("lichess_chess", fide, "", [fide], [], None)
        fixture = Fixture(source_id=event.key.partition(":")[2], title=event.title, competition="", start_utc=NOW, venue=None, status="final", team_ids=[fide], locator={})
        refreshed.append(structured.result_item(source, fixture, over, event, ref))
    assert [x["event_id"] for x in lives] == [11, 12]
    assert {x["game_key"] for x in [*lives, *finals, *refreshed]} == {"lichess_chess:mJnv1YBU"}
    # A refresh that couldn't read the scorecard keeps the key an earlier one gave the result.
    assert structured.merged_result(refreshed[0], {**refreshed[0], "game_key": None}, scorecard=False)["game_key"] == "lichess_chess:mJnv1YBU"
    # Where the fixture is the game (a football match, one for both sides), its event says it: no key of its own.
    event = Event(id=5, key="sportmonks_football:555", title="Arsenal vs Chelsea", notes=None, competition="Premier League", start_utc=NOW, status="live")
    c = card()
    structured._apply_snapshot(c, event, Snapshot(status="live", score_label="Arsenal 1 – 0 Chelsea", clock_label="12'", state={"kind": "football"}), None, KNOWN["sportmonks_football"], NOW)
    assert c["live"]["event_id"] == 5 and c["live"]["game_key"] is None
    # Nor before the pairing's out, a round with no board yet.
    assert structured.game_key(f"lichess_chess:ohv5F74c-{CARLSEN}", {"kind": "chess", "round": "Grand Final"}) is None


def test_a_chess_board_to_come_is_one_game_under_each_players_fixture(monkeypatch):
    # Before a board has started there's none to read, but a round within 12 hours of its start is read and paired:
    # each player's fixture of a board says the game it is, so Coming up shows it once to a fan who follows both
    # (BHA-379), by the key their live cards and results will have (BHA-372), its questions' too.
    data = recorded("lichess/round-final")
    now = datetime.now(timezone.utc)  # the adapter reads its rounds against the clock
    paired = {**data["round"], "finished": False, "ongoing": False, "startsAt": int((now + timedelta(hours=2)).timestamp() * 1000)}
    later = {"id": "Rnd2Late", "name": "Round 2", "finished": False, "ongoing": False, "startsAt": int((now + timedelta(days=2)).timestamp() * 1000)}
    answers = {f"{lichess_chess.API}broadcast/{data['tour']['id']}": {"rounds": [paired, later]},
               f"{lichess_chess.API}broadcast/-/-/{paired['id']}": {**data, "round": paired, "games": [{**data["games"][0], "status": "*"}]}}
    monkeypatch.setattr(lichess_chess, "get_json", lambda url, ttl, text=False: answers[url])
    monkeypatch.setattr(lichess_chess.LichessChess, "_tours", lambda self, ref, now: [data["tour"]])
    source = KNOWN["lichess_chess"]
    coming, unpaired, lives = [], [], []
    for event_id, (fide, name) in enumerate(((CARLSEN, "Magnus Carlsen"), (LAZAVIK, "Denis Lazavik")), start=11):
        ref = PlayerRef("lichess_chess", fide, name, [fide], [name], None)
        soon, late = structured.upcoming(source.fixtures(ref), now)
        assert (soon.title, soon.status, late.detail) == ("Magnus Carlsen vs Denis Lazavik", "scheduled", "Pairings not published yet")
        event = Event(id=event_id, key=f"lichess_chess:{soon.source_id}", title=soon.title, start_utc=soon.start_utc, status="scheduled")
        coming.append(structured._upcoming_item(soon, event, ref))
        unpaired.append(structured._upcoming_item(late, Event(id=event_id + 10, key=f"lichess_chess:{late.source_id}", title=late.title, status="scheduled"), ref))
        # And once it's on, the live block the same board gives that player's card.
        on = board(status="*", ongoing=True, fide=fide)
        c = card()
        structured._apply_snapshot(c, event, on, on.lines.get(fide), source, NOW)
        lives.append(c["live"]["game_key"])
    assert [u["event_id"] for u in coming] == [11, 12]  # an event of each player's
    assert [u["game_key"] for u in coming] == lives == ["lichess_chess:mJnv1YBU"] * 2
    assert [u["game_key"] for u in unpaired] == [None, None]
    # A knockout round's mini-match too: its fixture says the board parse_board shows, the last game once it's over.
    final = lichess_chess.fixture_for(data["tour"], data["round"], lichess_chess.games_of(data, CARLSEN), CARLSEN, NOW)
    assert structured.board_key(f"lichess_chess:{final.source_id}", final.board) == structured.game_key(f"lichess_chess:{final.source_id}", lichess_chess.parse_board(data, CARLSEN, NOW).state)
    # Where the fixture is the game (a football match, one for both sides), its event says it: no key of its own.
    ref = PlayerRef("sportmonks_football", "7", "Bukayo Saka", ["19"], ["Arsenal"], None)
    match = Fixture(source_id="556", title="Chelsea vs Arsenal", competition="Premier League", start_utc=NOW, venue=None, status="scheduled", team_ids=["18", "19"], locator={})
    assert structured._upcoming_item(match, Event(id=6, key="sportmonks_football:556", title=match.title, status="scheduled"), ref)["game_key"] is None


def test_final_snapshot_moves_the_game_from_upcoming_to_results():
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    event = Event(id=9, title="India v West Indies", notes="1st ODI (D/N)", competition="West Indies tour of India", start_utc=datetime(2026, 9, 27, 8, 30, tzinfo=timezone.utc))
    c = card(
        live={"is_live": True, "event_id": 9, "player_stats": []},
        upcoming=[{"title": "India v West Indies", "event_id": 9}, {"title": "India v West Indies", "event_id": 10}],
        recent_results=[{"title": "older", "event_id": 3}],
        freshness={"fixtures": "2026-09-29T13:00:00+00:00"},
    )
    structured._apply_snapshot(c, event, snap, snap.lines[KOHLI], ADAPTERS["espn_cricket"], NOW)
    assert c["live"] == {"is_live": False, "player_stats": []}
    assert [u["event_id"] for u in c["upcoming"]] == [10]
    first = c["recent_results"][0]
    assert first["result"] == "India won by 8 wkts (50b rem)"
    assert first["player_contribution"] == "139* (88)"
    assert first["date"] == "2026-09-27"
    assert [r["event_id"] for r in c["recent_results"]] == [9, 3]
    # The next scheduler tick re-reads fixtures and stats now that the game is done.
    assert c["freshness"]["fixtures"] is None


def test_unused_sub_in_a_final_football_match():
    snap = espn_soccer.parse_summary(load("soccer-summary-final"), "uefa.nations")
    event = Event(id=4, title="Norway vs Portugal", notes=None, competition="UEFA Nations League", start_utc=datetime(2026, 9, 27, 18, 45, tzinfo=timezone.utc))
    c = card()
    structured._apply_snapshot(c, event, snap, snap.lines.get(RONALDO), ADAPTERS["espn_soccer"], NOW)
    first = c["recent_results"][0]
    assert first["result"] == "Norway 1 – 2 Portugal"
    assert first["player_contribution"] == "On the bench"


def test_a_schedule_read_never_moves_a_game_backwards():
    # Egypt v South Sudan: the poller saw full time, then a 30-minute-old schedule still said "live".
    assert structured.next_status("final", "live") == "final"
    assert structured.next_status("live", "scheduled") == "live"
    assert structured.next_status("armed", "scheduled") == "armed"
    assert structured.next_status("scheduled", "live") == "live"
    assert structured.next_status("live", "final") == "final"
    assert structured.next_status("postponed", "scheduled") == "scheduled"  # rescheduled
    assert structured.next_status(None, "scheduled") == "scheduled"


class RacedSession:
    """A session in which a teammate's refresh adds the game between our select and our insert."""

    def __init__(self, theirs: Event):
        self.theirs, self.sql = theirs, []

    def scalar(self, stmt):
        return None  # not committed yet when we looked

    def execute(self, stmt):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))

    def scalars(self, stmt):
        return SimpleNamespace(one=lambda: self.theirs)

    def add(self, obj):
        raise AssertionError("a second Event with the same key")

    def flush(self):
        pass


def test_a_game_a_teammate_just_added_is_read_back_not_inserted_again():
    # Kohli's and Bumrah's refreshes in the same tick (2026-10-12): Bumrah's found no row for the match,
    # then Kohli's committed one, which the poller has since seen go live.
    theirs = Event(id=7, key="espn_cricket:1539432", sport="cricket", title="India v Australia", status="live")
    db = RacedSession(theirs)
    f = Fixture(source_id="1539432", title="India v Australia", competition="ODI", start_utc=NOW, venue=None, status="scheduled", team_ids=["6"], locator={})
    event = structured.upsert_event(db, ADAPTERS["espn_cricket"], Player(id=6, sport="cricket"), f)
    assert event is theirs and event.status == "live"  # a stale schedule doesn't move it back
    assert db.sql[0].startswith("INSERT INTO event") and "ON CONFLICT (key) DO NOTHING" in db.sql[0]


class FakeSource:
    """A source that fetches many events in one request, counting its requests."""

    system = "fake_source"
    live_cadence = 15

    def __init__(self, status: str = "live", error: Exception | None = None):
        self.status, self.error, self.requests = status, error, []

    def snapshots(self, locators):
        self.requests.append(dict(locators))
        if self.error:
            raise self.error
        return {key: Snapshot(status=self.status, score_label="1 – 0", clock_label="10'", state={}) for key, loc in locators.items() if loc["fixture"] != "gone"}


def lane(monkeypatch, source, events, changed=True, crash=()):
    """Point a lane at events (id -> locator, None for an event no followed player is in); returns
    the events each round applied."""
    applied = []
    monkeypatch.setattr(structured, "lane_events", lambda system: list(events))
    monkeypatch.setattr(structured, "_watching", lambda e: ({"adapter": source.system, "locator": events[e]}, source, [(e, "x")]) if events[e] else None)

    def apply_poll(event_id, live_binding, adapter, watchers, snap):
        if event_id in crash:
            raise RuntimeError("bad data")
        applied.append(event_id)
        return snap.status, 15, changed

    monkeypatch.setattr(structured, "apply_poll", apply_poll)
    return applied


def test_a_lane_polls_all_its_games_in_one_request(monkeypatch):
    source = FakeSource()
    applied = lane(monkeypatch, source, {1: {"fixture": "a"}, 2: {"fixture": "b"}, 3: None})
    assert structured.lane_round(source, {}, set()) == 15
    assert source.requests == [{1: {"fixture": "a"}, 2: {"fixture": "b"}}]
    assert applied == [1, 2]


def test_a_lane_slows_to_a_minute_when_nothing_is_moving(monkeypatch):
    source = FakeSource()
    lane(monkeypatch, source, {1: {"fixture": "a"}}, changed=False)
    moved = {1: time.monotonic() - 120}  # unchanged for two minutes: half-time, a delay
    assert structured.lane_round(source, moved, set()) == 60
    assert structured.lane_round(FakeSource(status="scheduled"), {}, set()) == 60  # not started yet


def test_a_lane_without_games_ends(monkeypatch):
    source = FakeSource()
    lane(monkeypatch, source, {1: None})
    assert structured.lane_round(source, {}, set()) is None
    assert source.requests == []


def test_a_failed_request_is_retried_in_thirty_seconds(monkeypatch):
    source = FakeSource(error=AdapterError("Sportmonks unreachable"))
    applied = lane(monkeypatch, source, {1: {"fixture": "a"}})
    assert structured.lane_round(source, {}, set()) == 30
    assert applied == []


def test_a_game_the_source_leaves_out_is_skipped_and_logged_once(monkeypatch, caplog):
    source = FakeSource()
    applied = lane(monkeypatch, source, {1: {"fixture": "gone"}, 2: {"fixture": "b"}})
    missing: set[int] = set()
    structured.lane_round(source, {}, missing)
    structured.lane_round(source, {}, missing)
    assert applied == [2, 2] and missing == {1}
    assert sum("no data for event 1" in r.message for r in caplog.records) == 1


def test_one_games_bad_data_doesnt_stop_the_others(monkeypatch):
    source = FakeSource()
    applied = lane(monkeypatch, source, {1: {"fixture": "a"}, 2: {"fixture": "b"}}, crash={1})
    assert structured.lane_round(source, {}, set()) == 15
    assert applied == [2]


def test_only_sources_that_fetch_many_games_at_once_get_a_lane(monkeypatch):
    monkeypatch.setitem(structured.ADAPTERS, "sportmonks_football", SportmonksFootball())
    monkeypatch.setitem(structured.ADAPTERS, "sportmonks_cricket", SportmonksCricket())
    assert structured.batched("sportmonks_football") and structured.batched("sportmonks_cricket")
    assert not structured.batched("espn_soccer") and not structured.batched("espn_cricket")
    assert not structured.batched(None) and not structured.batched("not_running")


# ---------------------------------------------------------------- binding by name


class BindSession:
    """The one player bind() reads, with no binding, identity or registry id yet."""

    def __init__(self, player: Player):
        self.player = player

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, model, key):
        return self.player

    def scalar(self, stmt):
        return None


class NameSource:
    """A source whose name search finds one athlete."""

    system = "espn_soccer"
    sport = "football"

    def __init__(self, ref):
        self.ref = ref

    def find_player(self, name):
        return self.ref


def bind_by_name(monkeypatch, player: Player, ref) -> list:
    recorded = []
    monkeypatch.setattr(structured, "session", lambda: BindSession(player))
    monkeypatch.setattr(structured, "for_sport", lambda sport: NameSource(ref))
    monkeypatch.setattr(structured, "record", lambda player_id, ref, confidence=1.0: recorded.append((ref.athlete_id, confidence)) or True)
    structured.bind(player.id)
    return recorded


def test_a_name_search_never_binds_a_namesake_from_another_team(monkeypatch):
    # The agent researched Al Ahly's Mohamed Salah; ESPN's search finds Liverpool's (BUG-032).
    liverpool = PlayerRef("espn_soccer", "173896", "Mohamed Salah", ["364", "2620"], ["Liverpool", "Egypt"])
    assert bind_by_name(monkeypatch, Player(id=1, name="Mohamed Salah", sport="Football", teams=["Al Ahly"]), liverpool) == []
    assert bind_by_name(monkeypatch, Player(id=1, name="Ahmed Salah", sport="Football", teams=[]), liverpool) == []
    # A team in common, or the same full name when the agent named no team, is certain.
    assert bind_by_name(monkeypatch, Player(id=1, name="Mohamed Salah", sport="Football", teams=["Liverpool FC"]), liverpool) == [("173896", 1.0)]
    assert bind_by_name(monkeypatch, Player(id=1, name="Mohamed Salah", sport="Football", teams=[]), liverpool) == [("173896", 1.0)]


def test_an_individual_athlete_binds_on_the_full_name(monkeypatch):
    # The source has no team for a tennis player, so the agent's "Serbia" can't overlap anything.
    novak = PlayerRef("espn_tennis", "296", "Novak Djokovic", ["296"], ["Novak Djokovic"], league="atp")
    assert bind_by_name(monkeypatch, Player(id=2, name="Novak Djokovic", sport="Tennis", teams=["Serbia"]), novak) == [("296", 1.0)]
    assert bind_by_name(monkeypatch, Player(id=2, name="Marko Djokovic", sport="Tennis", teams=[]), novak) == []


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(bus, "_sync", fake)
    return fake


# ---------------------------------------------------------------- what a poll's moments are found against


def football(status: str, clock: str, goals: int, score: str) -> tuple[Snapshot, moments.Seen]:
    """Mohamed Salah (athlete 9) in a football snapshot, and the Seen detect() compares it as."""
    stats = [{"label": "Goals", "value": str(goals)}] if goals else []
    line = Line(f"{goals} goals" if goals else "Started", stats)
    snap = Snapshot(status=status, score_label=score, clock_label=clock, state={"kind": "football"}, lines={"9": line})
    seen = moments.Seen(status, snap.state, {"headline": line.headline, "stats": stats}, score, clock, lineups=True)
    return snap, seen


def told(before: moments.Seen | None, seen: moments.Seen) -> list[str]:
    return [f.kind for f in moments.detect("Football", "Mohamed Salah", "9", before, seen)]


def test_a_game_first_seen_well_past_kick_off_is_the_baseline():
    # BUG-053: the last state was from before kick-off and the next poll found the game at 61' with two
    # goals: "game has started", "Goal: Mohamed Salah" and "Goal (2)" all went out, loud, as new.
    pre = EventState(status="armed", score_label="LIV 0 – 0 CHE", clock_label="", state={"kind": "football"})
    snap, seen = football("live", "61'", 2, "LIV 2 – 0 CHE")
    assert structured.baseline(pre, None, snap) is None
    assert told(structured.baseline(pre, None, snap), seen) == []
    # Seen go from before the start to over: only the result is told.
    snap, seen = football("final", "FT", 2, "LIV 2 – 0 CHE")
    assert told(structured.baseline(pre, None, snap), seen) == ["final"]


def test_a_game_seen_kicking_off_still_says_so():
    pre = EventState(status="armed", score_label="LIV 0 – 0 CHE", clock_label="", state={"kind": "football"})
    snap, seen = football("live", "1'", 0, "LIV 0 – 0 CHE")
    assert told(structured.baseline(pre, None, snap), seen) == ["start"]


def test_a_gap_in_polling_is_the_baseline():
    # BUG-053: a worker restart, or an hour of ESPN 502s, then the next poll pushed an hour of goals as new.
    live = EventState(status="live", score_label="LIV 0 – 0 CHE", clock_label="20'", state={"kind": "football"})
    last = PlayerLine(stats={"headline": "Started", "stats": []})
    snap, seen = football("live", "75'", 2, "LIV 2 – 0 CHE")
    assert structured.baseline(live, last, snap, recent=False) is None
    assert told(structured.baseline(live, last, snap, recent=False), seen) == []
    assert told(structured.baseline(live, last, snap), seen) == ["goal", "goal"]  # polled all along: news


def test_a_poll_knows_whether_the_one_before_was_recent(redis):
    assert structured._polled(7, 15) is False  # nobody polled it within POLL_GAP
    assert structured._polled(7, 15) is True
    assert redis.ttl["polled:7"] == structured.POLL_GAP
    structured._polled(8, 300)  # Liquipedia reads every five minutes: three of its rounds
    assert redis.ttl["polled:8"] == 900


# ---------------------------------------------------------------- games the schedule finishes first


class FinishedSource:
    """A source whose schedule says the game is over; its final scorecard can be read, or not. A final read
    is served from the cache, which (as ESPN's does) still holds the poller's last live read."""

    system = "fake_source"
    sport = "hurling"
    live_cadence = 15

    def __init__(self, scorecard: bool):
        self.scorecard = scorecard

    def fixtures(self, ref):
        return [Fixture(source_id="1", title="A vs B", competition="L", start_utc=NOW - timedelta(hours=2), venue=None, status="final", team_ids=["T1", "T2"], locator={"id": 1}, team_names=["A", "B"])]

    def snapshot(self, locator, final=False):
        if not self.scorecard:
            raise AdapterError("Fake returned HTTP 502 for /summary")
        line = {"A1": Line("1 goal", [{"label": "Goals", "value": "1"}])}
        if final:
            return Snapshot(status="live", score_label="A 2 – 1 B", clock_label="90'+3", state={"kind": "football"}, lines=line)
        return Snapshot(status="final", score_label="A 2 – 1 B", clock_label="FT", state={"kind": "football"}, lines=line)

    def result_label(self, fixture, team_ids):
        return "Won 2–1"

    def stats(self, ref, recent):
        return [], ""


def finished(monkeypatch, scorecard: bool) -> tuple[Event, PlayerCard, list]:
    """Player 1's game (event 10) live on their card at 90'+3 when the schedule refresh finds it over;
    returns the event, the card and the final polls applied."""
    source = FinishedSource(scorecard)
    player = Player(id=1, slug="p1", name="P1", sport="Hurling", status="active", teams=["A"])
    row = SourceBinding(id=1, player_id=1, purpose="fixtures", adapter=source.system, priority=10, health={},
                        locator={"system": source.system, "athlete_id": "A1", "name": "P1", "team_ids": ["T1"], "team_names": ["A"]})
    event = Event(id=10, key="fake_source:1", sport="Hurling", title="A vs B", status="live", start_utc=NOW - timedelta(hours=2))
    live = {"is_live": True, "event_id": 10, "event": "A vs B", "score": "A 2 – 1 B", "clock": "90'+3"}
    card_row = PlayerCard(player_id=1, status="ready", version=5, is_live=True, card={**card(live=live), "status": "ready", "upcoming": [{"event_id": 10}]})
    db = FakeDb(player, row, event, card_row, answers={"SourceBinding": row, "Player.sport": "Hurling", "Event": event, "Event.id": [(10, "fake_source:1")]})
    polls = []
    monkeypatch.setitem(structured.ADAPTERS, source.system, source)
    monkeypatch.setattr(structured, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "_watching", lambda event_id: ({"adapter": source.system, "locator": {"id": 1}, "cadence": 15}, source, [(1, "A1")]))
    monkeypatch.setattr(structured, "apply_poll", lambda event_id, live_binding, adapter, watchers, snap: polls.append((event_id, snap.status, snap.score_label)))
    return event, card_row, polls


def test_a_game_the_schedule_finished_between_polls_gets_its_final_poll(monkeypatch, redis):
    # BUG-019 (H6 exp6): the schedule refresh found the game over in the poller's hand-over gap; the card
    # stayed LIVE at 90'+3, and nobody heard the result.
    event, card_row, polls = finished(monkeypatch, scorecard=True)
    structured.refresh(1)
    assert polls == [(10, "final", "A 2 – 1 B")]  # the poller's path: the result moment, the card's live block


def test_a_game_finished_without_a_scorecard_leaves_live(monkeypatch, redis):
    # BUG-019: with no final scorecard to apply, the card still mustn't say LIVE.
    event, card_row, polls = finished(monkeypatch, scorecard=False)
    structured.refresh(1)
    assert polls == [] and event.status == "final"
    assert card_row.card["live"] == {"is_live": False, "player_stats": []} and not card_row.is_live
    assert card_row.card["upcoming"] == []


# ---------------------------------------------------------------- a refresh's recent results


class FiveFinalsSource:
    """A source with five finished games of the player's (game 5 the latest). Each scorecard read waits till all
    five are under way, so reads one after another never get past the first; then the oldest answers last.
    Game 3's read fails."""

    system = "fake_source"
    sport = "hurling"
    live_cadence = 15

    def __init__(self):
        self.together = threading.Barrier(structured.RECENT_LIMIT, timeout=5)
        self.reads: list[tuple[int, bool, str]] = []  # (game, final, lane)
        self.stats_from: list[str] = []

    def sport_of(self, ref):
        return self.sport

    def fixtures(self, ref):
        return [Fixture(source_id=str(n), title=f"A vs B, game {n}", competition="L", start_utc=NOW - timedelta(days=10 - n), venue=None, status="final",
                        team_ids=["T1", "T2"], locator={"id": n}, team_names=["A", "B"]) for n in range(1, 6)]

    def snapshot(self, locator, final=False):
        n = locator["id"]
        self.reads.append((n, final, budget.lane()))
        self.together.wait()
        time.sleep(0.01 * (5 - n))
        if n == 3:
            raise AdapterError("Fake returned HTTP 502 for /summary")
        return Snapshot(status="final", score_label=f"A {n} – 0 B", clock_label="FT", state={"kind": "football"}, lines={"A1": Line(f"Scored in game {n}", [])})

    def result_label(self, fixture, team_ids):
        return f"Won game {fixture.source_id}"

    def stats(self, ref, recent):
        self.stats_from = [f.source_id for f, _ in recent]
        return [], ""


def five_finals(monkeypatch) -> tuple[FiveFinalsSource, PlayerCard, list]:
    """Player 1 (A1) bound to FiveFinalsSource, their card ready; game 5 still live here (the poller didn't see
    it finish). Returns the source, the card and the final polls applied."""
    source = FiveFinalsSource()
    player = Player(id=1, slug="p1", name="P1", sport="Hurling", status="active", teams=["A"])
    row = SourceBinding(id=1, player_id=1, purpose="fixtures", adapter=source.system, priority=10, health={},
                        locator={"system": source.system, "athlete_id": "A1", "name": "P1", "team_ids": ["T1"], "team_names": ["A"]})
    events = {str(n): Event(id=10 + n, key=f"fake_source:{n}", sport="Hurling", title=f"A vs B, game {n}", status="live" if n == 5 else "final") for n in range(1, 6)}
    card_row = PlayerCard(player_id=1, status="ready", version=5, is_live=False, card={**card(), "status": "ready"})
    db = FakeDb(player, row, card_row, answers={"SourceBinding": row, "Player.sport": "Hurling", "Event.id": [(15, "fake_source:5")]})
    polls = []
    monkeypatch.setitem(structured.ADAPTERS, source.system, source)
    monkeypatch.setattr(structured, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "upsert_event", lambda db, adapter, player, f: events[f.source_id])
    monkeypatch.setattr(structured, "_watching", lambda event_id: ({"adapter": source.system, "locator": {"id": 5}, "cadence": 15}, source, [(1, "A1")]))
    monkeypatch.setattr(structured, "apply_poll", lambda event_id, live_binding, adapter, watchers, snap: polls.append((event_id, snap.status, snap.score_label)))
    return source, card_row, polls


def test_a_refresh_reads_its_recent_results_together(monkeypatch, redis):
    # BHA-283: a new follower's first card waited on five scorecard reads one after another, about 1.2 s each
    # from the server. Now they're one round, each in the lane of the refresh that asked (counted against
    # its budget: a background one stops at the live reserve).
    source, card_row, polls = five_finals(monkeypatch)
    with budget.in_lane("history"):  # any lane but the default, to see it carried into the reads
        structured.refresh(1)
    assert sorted(source.reads) == [(1, True, "history"), (2, True, "history"), (3, True, "history"), (4, True, "history"),
                                    (5, False, "history")]  # game 5 read afresh: the cache may hold a live poll
    assert card_row.version == 6


def test_recent_results_keep_their_order_and_a_failed_read_only_its_scorecard(monkeypatch, redis):
    # BHA-283: answers coming back in any order still make the card newest first; a read that failed leaves
    # that game's result without the player's line, and the others stand.
    source, card_row, polls = five_finals(monkeypatch)
    card_row.card["recent_results"] = [{"event_id": 13, "game_key": "fake_source:board3"}, {"event_id": 12, "game_key": "fake_source:board2"}]
    structured.refresh(1)
    results = card_row.card["recent_results"]
    assert [r["result"] for r in results] == ["Won game 5", "Won game 4", "Won game 3", "Won game 2", "Won game 1"]
    assert [r["player_contribution"] for r in results] == ["Scored in game 5", "Scored in game 4", None, "Scored in game 2", "Scored in game 1"]
    # And it's still the game an earlier read said (a chess board under each player's fixture: BHA-372), so it
    # stays one card on Today; a read that worked says it afresh (this source's boards name no shared game).
    assert [r["game_key"] for r in results] == [None, None, "fake_source:board3", None, None]
    assert source.stats_from == ["1", "2", "4", "5"]
    assert polls == [(15, "final", "A 5 – 0 B")]  # game 5, which the poller didn't see finish, gets its final poll


class RenamingSource:
    """A source that now names A1 and B1 (its old ids) A2 and B2."""

    system = "fake_source"

    def rekey(self, ref):
        return replace(ref, athlete_id=ref.athlete_id[0] + "2") if ref.athlete_id.endswith("1") else None


def test_every_player_bound_to_a_source_that_renamed_them_moves_to_the_new_id(monkeypatch, redis, caplog):
    # BHA-216: followed or not, so no old id stays on a page in search beside the registry's row. A player
    # whose new id another holds (fans had followed both of a pro's rows) stays, named once a day to merge.
    def bound(player_id, athlete_id):
        return SourceBinding(player_id=player_id, purpose="fixtures", adapter="fake_source", confidence=0.8, locator={"system": "fake_source", "athlete_id": athlete_id, "name": "P", "team_ids": ["T"], "team_names": ["T"]})

    holders = iter([None, 9])  # nobody holds A2; player 9 holds B2
    db = FakeDb(bound(1, "A1"), bound(2, "B1"), bound(3, "C2"), answers={"PlayerIdentity.player_id": lambda: next(holders)})
    monkeypatch.setattr(structured, "session", lambda: nullcontext(db))
    recorded = []
    monkeypatch.setattr(structured, "record", lambda player_id, ref, confidence=1.0: recorded.append((player_id, ref.athlete_id, ref.name, confidence)) or True)
    assert structured.rekey_bound(RenamingSource()) == 1
    assert recorded == [(1, "A2", "P", 0.8)]
    assert "player 2 (B1) is player 9 (B2); merge them" in caplog.text
    caplog.clear()
    holders = iter([None, 9])
    structured.rekey_bound(RenamingSource())
    assert "merge them" not in caplog.text  # once a day
    assert structured.rekey_bound(SimpleNamespace(system="other_source")) == 0  # a source that never renamed anyone


def test_a_game_moved_later_is_disarmed_by_its_own_sports_lead(monkeypatch):
    # BHA-43: an armed football match is polled from 75 minutes out; moved a little later it stays armed,
    # moved hours later it isn't polled all that time.
    monkeypatch.setattr(structured, "ARM_BEFORE_START", 30 * 60)
    assert structured.arm_before("Football") == timedelta(minutes=75)
    assert structured.arm_before("Chess") == structured.arm_before(None) == timedelta(minutes=30)
    assert structured.arm_before("golf") == timedelta(minutes=30)  # a sport no module knows
