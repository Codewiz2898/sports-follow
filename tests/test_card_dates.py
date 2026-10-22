"""Dates on a card: news newest first (pipeline._card_from_report), agent times stored with a zone so the app
and the scheduler mean the same instant, and results carrying their start so the app can show the fan's
local day (results.py, structured.py)."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

from sports_follow import pipeline, structured, results
from sports_follow.adapters import ADAPTERS, Fixture, espn_cricket
from sports_follow.models import Event, Player, PlayerCard

from .test_adapters import KOHLI, load
from .test_pipeline import FakeDb, redis  # noqa: F401 — the fixture

NOW = datetime(2026, 10, 13, 7, 40, tzinfo=timezone.utc)
PLAYER = Player(id=5, slug="virat-kohli", name="Virat Kohli", sport="Cricket", status="active", teams=["India"])


def report(**sections) -> dict:
    return {
        "player": {"name": "Virat Kohli", "sport": "Cricket", "status": "active", "teams": ["India"], "summary": "s"},
        "live": {"is_live": False, "player_stats": []},
        "news": [], "upcoming": [], "recent_results": [], "season_stats": [], "sources": [],
        **sections,
    }


def test_a_cards_news_is_newest_first_and_undated_last():
    # BUG-003: Kohli's card read 13 Oct, 7 Oct, 12 Oct, 9 Oct, 8 Oct, 11 Oct.
    news = [
        ("a", "2026-10-13"), ("b", "2026-10-07"), ("undated", None), ("c", "2026-10-12T11:08:16+00:00"),
        ("d", "2026-10-09"), ("e", "2026-10-12T21:00:00Z"), ("not a date", "2 days ago"), ("f", "2026-10-11"),
    ]
    card = pipeline._card_from_report(PLAYER, report(news=[{"headline": h, "published": p} for h, p in news]), 2, NOW)
    assert [n["headline"] for n in card["news"]] == ["a", "e", "c", "f", "d", "b", "undated", "not a date"]
    # A day stays a day: the agent didn't say when in it.
    assert [n["published"] for n in card["news"]] == [
        "2026-10-13", "2026-10-12T21:00:00+00:00", "2026-10-12T11:08:16+00:00", "2026-10-11", "2026-10-09", "2026-10-07", None, None,
    ]


def test_agent_times_without_a_zone_are_stored_as_utc():
    # BUG-037: "2026-10-20T14:00:00" was 14:00 to the app in India and 19:30 IST to the scheduler.
    upcoming = [
        {"title": "Final", "competition": "Cup", "start_utc": "2026-10-20T14:00:00"},
        {"title": "Semi", "competition": "Cup", "start_utc": "2026-10-18T19:30:00+05:30"},
        {"title": "Round 6", "competition": "Open", "start_utc": "2026-10-21"},
        {"title": "Exhibition", "competition": "", "start_utc": "next Tuesday"},
        {"title": "TBC", "competition": "", "start_utc": None},
    ]
    card = pipeline._card_from_report(PLAYER, report(upcoming=upcoming, live={"is_live": True, "as_of": "2026-10-13T07:30:00", "player_stats": []}), 2, NOW)
    starts = [u["start_utc"] for u in card["upcoming"]]
    assert starts == ["2026-10-20T14:00:00+00:00", "2026-10-18T14:00:00+00:00", "2026-10-21", None, None]
    # The instant on the card is the one the scheduler arms the event for.
    assert pipeline._parse_time(starts[0]) == pipeline._parse_time(upcoming[0]["start_utc"])
    assert card["live"]["as_of"] == "2026-10-13T07:30:00+00:00"


def test_an_agent_result_with_a_time_carries_its_start():
    # BUG-028: the app picks the fan's local day from the instant; a bare day is shown as that day.
    played = [{"title": "Bulls at Nuggets", "result": "Won 118-110", "date": "2026-10-12T01:00:00"}, {"title": "C v D", "result": "Lost", "date": "2026-10-09"}]
    card = pipeline._card_from_report(PLAYER, report(recent_results=played), 2, NOW)
    assert card["recent_results"] == [
        {"title": "Bulls at Nuggets", "result": "Won 118-110", "date": "2026-10-12", "start_utc": "2026-10-12T01:00:00+00:00"},
        {"title": "C v D", "result": "Lost", "date": "2026-10-09"},
    ]


def test_a_live_check_stores_its_times_with_a_zone(monkeypatch, redis):  # noqa: F811
    card_row = PlayerCard(player_id=5, status="ready", card={"upcoming": [{"title": "Old", "start_utc": None}], "freshness": {}}, version=3)
    db = FakeDb(PLAYER, card_row)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(pipeline, "_write_live", lambda *args: None)
    monkeypatch.setattr(pipeline, "_upsert_event", lambda db, player, **event: None)
    nxt = {"title": "Final", "competition": "Cup", "start_utc": "2026-10-20T14:00:00"}
    pipeline.apply_live(5, {"live": {"is_live": False, "as_of": "2026-10-13T07:30:00Z", "player_stats": []}, "next_event": nxt})
    assert card_row.card["live"]["as_of"] == "2026-10-13T07:30:00+00:00"
    assert [u["start_utc"] for u in card_row.card["upcoming"]] == ["2026-10-20T14:00:00+00:00", None]


# ---------------------------------------------------------------- results from a source


def test_results_carry_their_start():
    # BUG-028: Bulls at Nuggets started 01:00 UTC on 12 Oct, 7 PM on 11 Oct in Denver; "date" alone said 12 Oct.
    start = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
    f = Fixture(source_id="401", title="Chicago Bulls at Denver Nuggets", competition="NBA", start_utc=start, venue=None, status="final", team_ids=["7"], locator={})
    adapter = SimpleNamespace(result_label=lambda fixture, team_ids: "Won 118-110", sport_of=lambda ref: "basketball")
    ref = SimpleNamespace(athlete_id="3112335", team_ids=["7"], team_names=["Denver Nuggets"], other_teams=[])
    item = structured.result_item(adapter, f, None, None, ref)
    assert (item["date"], item["start_utc"]) == ("2026-10-12", "2026-10-12T01:00:00+00:00")
    # Stored before start_utc was kept: the event's start fills it in.
    stored = {"title": "Chicago Bulls at Denver Nuggets", "result": "Won 118-110", "date": "2026-10-12"}
    assert results._item(Event(id=12, start_utc=start), stored)["start_utc"] == "2026-10-12T01:00:00+00:00"
    assert results._item(Event(id=13, start_utc=None), {"title": "x", "result": "Won", "date": "2026-10-09"})["start_utc"] is None


def test_a_game_the_poller_saw_finish_carries_its_start():
    snap = espn_cricket.parse_summary(load("cricket-summary-final"))
    start = datetime(2026, 9, 27, 8, 30, tzinfo=timezone.utc)
    event = Event(id=9, title="India v West Indies", notes="1st ODI (D/N)", competition="West Indies tour of India", start_utc=start)
    card = {"live": {"is_live": True, "event_id": 9, "player_stats": []}, "upcoming": [], "recent_results": [], "freshness": {}}
    structured._apply_snapshot(card, event, snap, snap.lines[KOHLI], ADAPTERS["espn_cricket"], NOW)
    assert (card["recent_results"][0]["date"], card["recent_results"][0]["start_utc"]) == ("2026-09-27", "2026-09-27T08:30:00+00:00")
