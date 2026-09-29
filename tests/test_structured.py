"""How one snapshot changes a player's card (structured._apply_snapshot), on recorded ESPN data."""

from __future__ import annotations

from datetime import datetime, timezone

from sports_follow import structured
from sports_follow.adapters import ADAPTERS, espn_cricket, espn_soccer
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
