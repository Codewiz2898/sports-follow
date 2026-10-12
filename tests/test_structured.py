"""How one snapshot changes a player's card (structured._apply_snapshot), on recorded ESPN data, and how a
lane polls all of a source's games together (structured.lane_round)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sports_follow import structured
from sports_follow.adapters import ADAPTERS, AdapterError, Snapshot, espn_cricket, espn_soccer
from sports_follow.adapters.sportmonks_cricket import SportmonksCricket
from sports_follow.adapters.sportmonks_football import SportmonksFootball
from sports_follow.models import Event

from .test_adapters import KOHLI, RONALDO, load

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
    assert structured.batched("sportmonks_football")
    assert not structured.batched("sportmonks_cricket") and not structured.batched("espn_soccer")
    assert not structured.batched(None) and not structured.batched("not_running")
