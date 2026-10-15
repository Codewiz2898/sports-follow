"""The pipeline's decisions (pipeline.py): which news makes an alert, when a failed build is tried
again, and which player a report lands on. The database and Redis are fakes."""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import Select
from sqlalchemy.sql.dml import Update

from sports_follow import bus, config, notify, pipeline, structured
from sports_follow.adapters import AdapterError
from sports_follow.agent import AgentError
from sports_follow.models import Player, PlayerCard, SourceBinding

NOW = datetime(2026, 10, 13, 7, 40, tzinfo=timezone.utc)


def _selected(stmt) -> str:
    """What a select reads: "PlayerCard" for whole rows, "PlayerIdentity.id" for one column."""
    d = stmt.column_descriptions[0]
    return d["entity"].__name__ if d["expr"] is d["entity"] else f"{d['entity'].__name__}.{d['name']}"


class Rows(list):
    def all(self):
        return list(self)


class FakeDb:
    """A session holding a few rows: get() by primary key, scalars() of a whole model, and scalar() and
    execute() answered from `answers` by what the query selects (a callable answers when asked), an
    UPDATE … RETURNING from answers["Update"]. Anything else finds nothing."""

    def __init__(self, *rows, answers=None):
        self.rows, self.answers, self.deleted = list(rows), answers or {}, []

    def _answer(self, key, default=None):
        value = self.answers.get(key, default)
        return value() if callable(value) else value

    def get(self, model, key):
        return next((r for r in self.rows if type(r) is model and (r.player_id if model is PlayerCard else r.id) == key), None)

    def scalar(self, stmt):
        return self._answer(_selected(stmt))

    def scalars(self, stmt):
        if isinstance(stmt, Update):
            return Rows(self._answer("Update", []))
        what = _selected(stmt)
        if "." in what:  # one column: the first of each answered row
            return Rows(r[0] if isinstance(r, tuple) else r for r in self._answer(what, []))
        return Rows(r for r in self.rows if type(r).__name__ == what)

    def execute(self, stmt):
        rows = self._answer(_selected(stmt), []) if isinstance(stmt, Select) else []
        return SimpleNamespace(all=lambda: list(rows))

    def add(self, row):
        self.rows.append(row)

    def delete(self, row):
        self.deleted.append(row)

    def flush(self):
        pass


class FakeRedis:
    """The calls bus.py makes. Keys don't expire: the tests move their own clock (or delete a key)."""

    def __init__(self):
        self.data, self.ttl = {}, {}

    def set(self, key, value, nx=False, ex=None, get=False):
        if nx and key in self.data:
            return None
        old = self.data.get(key)
        self.data[key], self.ttl[key] = value, ex
        return old if get else True

    def get(self, key):
        return self.data.get(key)

    def exists(self, key):
        return int(key in self.data)

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    def expire(self, key, seconds):
        self.ttl[key] = seconds
        return int(key in self.data)

    def eval(self, script, numkeys, key, token, *args):
        """bus.py's two scripts: delete, or set a new expiry, only if the key still holds the token."""
        if self.data.get(key) != token:
            return 0
        if script == bus._RELEASE:
            del self.data[key]
        else:
            self.ttl[key] = int(args[0])
        return 1

    def publish(self, channel, message):
        pass


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(bus, "_sync", fake)
    return fake


# ---------------------------------------------------------------- news alerts


def story(headline: str, kind: str | None, breaking: bool | None, hours_ago: int = 2, news_id: int = 1) -> dict:
    return {"headline": headline, "kind": kind, "breaking": breaking, "published": (NOW - timedelta(hours=hours_ago)).isoformat(), "news_id": news_id}


def test_a_story_about_a_retirement_is_not_news_of_one():
    # BUG-002: an opinion piece on Kohli's May 2025 retirement went to every follower as "Virat Kohli retires".
    items = [
        story("Wasim Jaffer says Virat Kohli retired from Test cricket out of bitterness", "retirement", False, news_id=29),
        story("Sanju Samson equals Virat Kohli's joint-fastest T20I fifty record", "milestone", False, news_id=31),
        story("Kohli ruled out of the third ODI", "injury", None, news_id=32),  # no word from the agent: no alert
    ]
    assert pipeline.news_moments("Virat Kohli", items, NOW) == []


def test_a_contract_extension_is_not_transfer_news():
    # BUG-002: "Transfer news: Nikola Jokic", from a story about an extension he hasn't signed.
    items = [story("Jokic's Unsigned Extension Looms Over Everything for the Nuggets", "contract extension", True)]
    assert pipeline.news_moments("Nikola Jokic", items, NOW) == []


def test_breaking_news_says_no_more_than_the_story():
    items = [story("Salah out for six weeks with a hamstring tear, Liverpool confirm", "injury", True, news_id=7)]
    [f] = pipeline.news_moments("Mohamed Salah", items, NOW)
    assert (f.kind, f.level, f.priority, f.detail) == ("news", "key", 75, "n7")
    assert (f.title, f.body) == ("Mohamed Salah: injury news", "Salah out for six weeks with a hamstring tear, Liverpool confirm")
    [f] = pipeline.news_moments("Virat Kohli", [story("Virat Kohli announces his retirement from ODIs", "Retirement", True)], NOW)
    assert (f.title, f.level, f.priority) == ("Virat Kohli: retirement news", "key", 90)
    assert pipeline.news_moments("Mohamed Salah", [story("Salah out for six weeks", "injury", True, hours_ago=96)], NOW) == []


# ---------------------------------------------------------------- failed rebuilds


def test_a_failing_rebuild_waits_longer_each_time(monkeypatch, redis):
    # BUG-016, the worker log at 07:40: rebuilds failing on a model timeout were queued again every minute.
    stale = Player(id=1, slug="one", name="Player One", sport="Hurling", status="active", teams=[])
    from_source = Player(id=2, slug="two", name="Player Two", sport="Hurling", status="active", teams=[])
    cards = [
        PlayerCard(player_id=1, status="ready", card={"status": "ready"}, version=3, built_at=NOW - timedelta(hours=7)),
        PlayerCard(player_id=2, status="ready", card={"status": "ready", "pending": ["profile", "news"]}, version=1, built_at=None),
    ]
    db = FakeDb(stale, from_source, *cards)
    clock, runs = [NOW], {1: [], 2: []}

    def timing_out(llm, name, known):
        runs[1 if name == "Player One" else 2].append(int((clock[0] - NOW).total_seconds() // 60))
        raise AgentError("Model call failed (timeout): openrouter moonshotai/kimi-k2.6:nitro: timeout")
        yield

    monkeypatch.setattr(config, "CARD_MAX_AGE", 6 * 3600)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(pipeline, "_now", lambda: clock[0])
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", timing_out)
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    for minute in range(12 * 60):  # the scheduler's tick, once a minute for 12 hours
        clock[0] = NOW + timedelta(minutes=minute)
        for player_id in pipeline.due_work(clock[0]).builds:
            pipeline.build_card(player_id)
    # 30 minutes, then an hour, 2, 4, and never more than CARD_MAX_AGE (6 hours) apart.
    assert runs == {1: [0, 30, 90, 210, 450], 2: [0, 30, 90, 210, 450]}
    assert [c.status for c in cards] == ["ready", "ready"] and cards[1].card["pending_error"].startswith("The research agent")

    # A build still running is never queued twice, and one that succeeds starts the count again.
    later = NOW + timedelta(hours=20)
    assert bus.try_lock("build:1", 900) and not pipeline.rebuild_due(cards[0], later)
    bus.release("build:1")
    monkeypatch.setattr(pipeline, "follow_player", lambda llm, name, known: iter([{"type": "result", "data": {}}]))
    monkeypatch.setattr(pipeline, "apply_report", lambda player_id, report: player_id)
    monkeypatch.setattr(structured, "bind", lambda player_id: False)
    pipeline.build_card(1)
    assert pipeline.back_off(1, later) == later + timedelta(minutes=30)


def test_a_failed_first_build_waits_for_a_fan():
    card = PlayerCard(player_id=1, status="failed", card={}, built_at=None)
    assert not pipeline.rebuild_due(card, NOW)


@pytest.mark.parametrize("raw", ["openrouter: HTTP 402 Payment Required", "This request requires more credits, or fewer max_tokens"])
def test_running_out_of_credit_is_not_told_to_fans(raw):
    # The card's error is public (GET /api/players/{id}/card): the provider's bill stays in the worker log.
    said = pipeline.fan_message(raw)
    assert said == "AI research isn't available right now. Try again later."
    assert "credit" not in said.lower()


# ---------------------------------------------------------------- without the research agent


def test_without_the_agent_a_sources_page_is_whole_and_nothing_waits_for_a_model(monkeypatch, redis):
    # SPORTS_FOLLOW_AGENT=0 (no OpenRouter key): a page from a source stops waiting for the profile and news,
    # a typed name a source knows still gets its page from it, a page the agent built stays as it is, and
    # a name no source has fails, saying so. Nothing asks the model, and each is settled once.
    picked = Player(id=1, slug="tenz", name="TenZ", sport="Valorant", status="active", teams=["Sentinels"])
    typed = Player(id=2, slug="aspas", name="aspas", sport=None, status="active", teams=[])
    unknown = Player(id=3, slug="someone", name="Someone", sport=None, status="active", teams=[])
    researched = Player(id=4, slug="hurler", name="A Hurler", sport="Hurling", status="active", teams=[])
    cards = [
        # Picked in search while the agent was on: the source's page, waiting for the profile and news.
        PlayerCard(player_id=1, status="ready", card={"status": "ready", "pending": ["profile", "news"], "upcoming": [{"title": "Sentinels v G2"}]}, version=4, built_at=None),
        PlayerCard(player_id=2, status="building", card={"status": "building"}, version=0, built_at=None),  # typed, a source knows it
        PlayerCard(player_id=3, status="building", card={"status": "building"}, version=0, built_at=None),  # typed, no source has it
        PlayerCard(player_id=4, status="ready", card={"status": "ready", "news": [{"headline": "…"}]}, version=7, built_at=NOW - timedelta(days=3)),
    ]
    db = FakeDb(picked, typed, unknown, researched, *cards)
    published, looked_up = {}, []

    def from_source(player_id, adapter, ref):
        cards[1].status, cards[1].card, cards[1].version = "ready", {"status": "ready"}, 1  # as _start_from_source publishes it
        return player_id

    monkeypatch.setattr(config, "AGENT", False)
    monkeypatch.setattr(config, "CARD_MAX_AGE", 6 * 3600)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(pipeline, "llm", lambda: pytest.fail("the model was asked"))
    monkeypatch.setattr(structured, "binding", lambda db, player_id: SimpleNamespace(adapter="pandascore_esports") if player_id == 1 or cards[1].status == "ready" and player_id == 2 else None)
    monkeypatch.setattr(structured, "identify", lambda name: looked_up.append(name) or ((None, SimpleNamespace(name="aspas")) if name == "aspas" else None))
    monkeypatch.setattr(pipeline, "_start_from_source", from_source)
    monkeypatch.setattr(bus, "store_card", lambda player_id, card: published.__setitem__(player_id, card))
    for minute in range(0, 24 * 60, 30):  # a day of the scheduler's ticks
        for player_id in pipeline.due_work(NOW + timedelta(minutes=minute)).builds:
            pipeline.build_card(player_id)
    assert published[1] == {"status": "ready", "version": 5, "upcoming": [{"title": "Sentinels v G2"}]}  # nothing pending
    assert sorted(looked_up) == ["Someone", "aspas"]  # each typed name looked up once, in the sources
    assert [(c.status, c.version) for c in cards] == [("ready", 5), ("ready", 1), ("failed", 0), ("ready", 7)]
    assert cards[2].card["error"] == "No live source has this player, so there's no page to show yet."


@pytest.mark.parametrize("agent, pending", [(True, ["profile", "news"]), (False, None)])
def test_a_pick_waits_for_the_profile_and_news_only_with_the_agent(monkeypatch, agent, pending):
    db = FakeDb()
    monkeypatch.setattr(config, "AGENT", agent)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "record", lambda player_id, ref: None)
    monkeypatch.setattr(bus, "store_card", lambda player_id, card: None)
    ref = SimpleNamespace(system="pandascore_esports", athlete_id="8742", name="TenZ", team_ids=["1"], team_names=["Sentinels"], profile_url=None, game="Valorant")
    _, created = pipeline.start_from_pick(SimpleNamespace(sport="esports"), ref)
    [card] = [r for r in db.rows if isinstance(r, PlayerCard)]
    assert created and card.status == "ready" and card.card.get("pending") == pending


# ---------------------------------------------------------------- one build at a time


def test_a_build_that_outlives_its_lock_never_frees_the_next_ones(monkeypatch, redis):
    # BUG-017 (H6 exp2): a build ran past its lock's 900 s, the scheduler started a second, and the
    # first one's release then deleted the second's lock, so a third could start beside it.
    player = Player(id=1, slug="one", name="Player One", sport="Hurling", status="active", teams=[])
    card = PlayerCard(player_id=1, status="ready", card={"status": "ready"}, version=3, built_at=NOW - timedelta(hours=7))
    held, extended, second = [], [], []

    def outlived(llm, name, known):
        held.append(redis.ttl["lock:build:1"])
        redis.ttl["lock:build:1"] = 5  # minutes of research later
        yield {"type": "progress", "message": "Searching the web…"}
        extended.append(redis.ttl["lock:build:1"])  # each step of the agent renews the lock
        del redis.data["lock:build:1"]  # the lock lapsed anyway (a stalled thread)…
        second.append(bus.try_lock("build:1", 1200))  # …and the scheduler started another build
        yield {"type": "progress", "message": "Thinking…"}
        raise AgentError("Model call failed (timeout)")

    monkeypatch.setattr(config, "CARD_MAX_AGE", 6 * 3600)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(FakeDb(player, card)))
    monkeypatch.setattr(pipeline, "_now", lambda: NOW)
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", outlived)
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    pipeline.build_card(1)
    assert second[0] and bus.is_locked("build:1")  # the second build's lock: the first didn't release it…
    assert redis.ttl["lock:build:1"] == 1200  # …or renew it
    assert held == extended == [pipeline.BUILD_LOCK]  # its own lock lasted, and each step renewed it


def test_a_lock_outlasts_its_job_and_the_job_outlasts_the_agent():
    # BUG-017: the lock (900 s) lapsed exactly when arq gave up on the job (900 s), whose thread kept
    # running, and an agent run could take 16 turns of up to 300 s each.
    from sports_follow import agent, worker

    assert pipeline.BUILD_LOCK > worker.WorkerSettings.job_timeout > agent.RUN_LIMIT
    assert structured.REFRESH_LOCK > worker.WorkerSettings.job_timeout  # a Lichess refresh reads for minutes
    assert agent.LIVE_LIMIT < 300  # refresh_live's lock


def test_an_agent_run_stops_at_its_time_limit(monkeypatch):
    # BUG-017: 16 turns of up to 300 s each (and a retry) let one run outlast its build's lock.
    from sports_follow import agent

    clock = [0.0]

    class SlowModel:
        """Takes 200 s a turn and never submits."""

        def __init__(self):
            self.timeouts = []

        def complete(self, **request):
            self.timeouts.append(request["timeout_ms"])
            clock[0] += min(200, request["timeout_ms"] / 1000)
            return SimpleNamespace(finish="stop", message={"role": "assistant", "content": "Still looking."}, tool_calls=[])

    model = SlowModel()
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    with pytest.raises(AgentError, match="time limit"):
        list(agent.follow_player(model, "Player One"))
    assert clock[0] <= agent.RUN_LIMIT
    assert model.timeouts == [300_000, 300_000, 200_000]  # the last call gets only the time that's left


# ---------------------------------------------------------------- source refreshes


class RefusingSource:
    """A source whose fixtures read fails (a team its plan doesn't cover), counting its requests."""

    system = "fake_source"
    sport = "hurling"
    live_cadence = 15

    def __init__(self, room: bool = True):
        self.room, self.requests, self.failing = room, [], True

    def spare(self):
        return self.room

    def fixtures(self, ref):
        self.requests.append(ref.athlete_id)
        if self.failing:
            raise AdapterError("Sportmonks returned HTTP 403 for /football/fixtures/between: not in your plan")
        return []

    def stats(self, ref, recent):
        return [], ""


def bound_player(monkeypatch, source, clock) -> tuple[FakeDb, SourceBinding, PlayerCard]:
    """Player 1, followed and bound to source, whose card's fixtures were read an hour before clock[0]."""
    player = Player(id=1, slug="one", name="Player One", sport="Hurling", status="active", teams=[])
    row = SourceBinding(id=1, player_id=1, purpose="fixtures", adapter=source.system, priority=10, health={},
                        locator={"system": source.system, "athlete_id": "A1", "name": "Player One", "team_ids": ["T"], "team_names": ["Team"]})
    read = (clock[0] - timedelta(hours=1)).isoformat()
    card = PlayerCard(player_id=1, status="ready", card={"status": "ready", "live": {"is_live": False}, "freshness": {"fixtures": read}}, version=1, built_at=clock[0], freshness={"fixtures": read})
    db = FakeDb(player, row, card, answers={"SourceBinding": row, "Player.sport": "Hurling", "SourceBinding.player_id": lambda: [(1, source.system, row.health)]})
    monkeypatch.setitem(structured.ADAPTERS, source.system, source)
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "_now", lambda: clock[0])
    return db, row, card


def test_a_failing_refresh_waits_longer_each_time(monkeypatch, redis):
    # BUG-020 (H6 exp9, the worker log): "refresh 17 failed: PandaScore unreachable" every tick; one
    # player whose team the plan doesn't cover cost a source request a minute.
    clock = [NOW]
    source = RefusingSource()
    _, row, card = bound_player(monkeypatch, source, clock)
    asked = []
    for minute in range(10 * 60):  # the scheduler's tick, once a minute for 10 hours
        clock[0] = NOW + timedelta(minutes=minute)
        for player_id in pipeline.due_work(clock[0]).refreshes:
            asked.append(minute)
            structured.refresh(player_id)
    # 2 minutes, then 4, 8, … and never more than two hours apart.
    assert asked == [0, 2, 6, 14, 30, 62, 126, 246, 366, 486] and len(source.requests) == len(asked)
    assert row.health["failures"] == 10 and "not in your plan" in row.health["error"]

    # Once the source answers, the count starts again and fixtures are read every FIXTURES_MAX_AGE.
    source.failing = False
    clock[0] = NOW + timedelta(minutes=606)
    assert pipeline.due_work(clock[0]).refreshes == [1]
    structured.refresh(1)
    assert row.health["failures"] == 0 and card.freshness["fixtures"] == clock[0].isoformat()
    assert pipeline.due_work(clock[0] + timedelta(minutes=1)).refreshes == []


def test_refreshes_leave_a_sources_live_reserve_alone(monkeypatch, redis):
    # BUG-020: refreshes ignored spare(), so on Sportmonks they ate the hour's reserve for live matches,
    # and live scores then stopped on 429s.
    clock = [NOW]
    source = RefusingSource(room=False)
    bound_player(monkeypatch, source, clock)
    assert pipeline.due_work(NOW).refreshes == []
    structured.refresh(1)  # queued another way (a fan's refresh button)
    assert source.requests == []
    source.room = True  # the hour turned
    assert pipeline.due_work(NOW).refreshes == [1]


# ---------------------------------------------------------------- games nobody saw finish


def test_a_game_closed_after_six_days_stops_showing_live(monkeypatch, redis):
    # BUG-019 (H6 exp5): the source stopped returning a match; the tick closed it after six days, and the
    # card kept saying LIVE "A 245/6 (43.2)" at the top of Today.
    player = Player(id=1, slug="one", name="Player One", sport="Cricket", status="active", teams=[])
    stuck = PlayerCard(player_id=1, status="ready", version=5, built_at=NOW, is_live=True, freshness={"fixtures": NOW.isoformat()}, card={
        "status": "ready", "version": 5, "live": {"is_live": True, "event_id": 10, "score": "A 245/6 (43.2)"},
        "upcoming": [{"event_id": 10}, {"event_id": 11}], "freshness": {"fixtures": NOW.isoformat()},
    })
    elsewhere = PlayerCard(player_id=2, status="ready", version=2, built_at=NOW, is_live=True, card={"status": "ready", "live": {"is_live": True, "event_id": 12}})
    db = FakeDb(player, stuck, elsewhere, answers={"Update": [10]})  # the six-day close returns event 10
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    pipeline.due_work(NOW)
    assert (stuck.is_live, stuck.version, stuck.card["live"]) == (False, 6, {"is_live": False, "player_stats": []})
    assert stuck.card["upcoming"] == [{"event_id": 11}] and stuck.freshness["fixtures"] is None
    assert json.loads(redis.data["card:1"])["live"]["is_live"] is False  # published to the app
    assert elsewhere.card["live"]["is_live"] and "card:2" not in redis.data


# ---------------------------------------------------------------- where a report lands


def test_a_namesake_picked_in_search_is_never_folded_into_the_star():
    # BUG-018 (H2): Al Ahly's Mohamed Salah, picked from the registry, was folded into Liverpool's by name.
    star = Player(id=1, slug="mohamed-salah", name="Mohamed Salah", sport="Football")
    namesake = Player(id=2, slug="mohamed-salah-football", name="Mohamed Salah", sport="Football")
    db = FakeDb(answers={"Player": star, "PlayerIdentity.id": 77})  # 77: the namesake's Wikidata identity
    assert pipeline._canonicalize(db, namesake, "Mohamed Salah") == (namesake, None)
    assert db.deleted == [] and namesake.slug == "mohamed-salah-football"


def test_a_typed_name_still_converges_on_the_player_who_has_it():
    kohli = Player(id=5, slug="virat-kohli", name="Virat Kohli", sport="Cricket")
    typed = Player(id=9, slug="kohli", name="Kohli", sport="")
    db = FakeDb(answers={"Player": kohli})
    assert pipeline._canonicalize(db, typed, "Virat Kohli") == (kohli, 9)
    assert db.deleted == [typed]


def test_a_report_folded_into_a_bound_player_keeps_the_sources_sections(monkeypatch, redis):
    # BUG-018 (H6): "Kohli" typed while Virat Kohli was batting; the report replaced his live score,
    # fixtures, results and stats with the agent's guesses.
    kohli = Player(id=5, slug="virat-kohli", name="Virat Kohli", sport="Cricket", status="active", teams=["India"])
    typed = Player(id=9, slug="kohli", name="Kohli", sport="", status="unknown", teams=[])
    source_card = {
        "status": "ready", "version": 40,
        "live": {"is_live": True, "score": "IND 187/2 (31.4)", "headline": "74* (61)"},
        "upcoming": [{"title": "India v NZ", "event_id": 140}],
        "recent_results": [{"title": "India v WI", "result": "India won by 7 wkts", "event_id": 50}],
        "season_stats": [{"label": "Runs", "value": "412"}],
        "provenance": {"upcoming": {"source": "ESPNcricinfo", "url": "https://espncricinfo.com/player/253802"}},
        "freshness": {"live": NOW.isoformat(), "fixtures": NOW.isoformat(), "stats": NOW.isoformat()},
    }
    card_row = PlayerCard(player_id=5, status="ready", card=source_card, version=40, built_at=NOW - timedelta(hours=7), is_live=True)
    db = FakeDb(kohli, typed, card_row)
    guessed = []
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "binding", lambda db, player_id: SimpleNamespace(adapter="espn_cricket") if player_id == 5 else None)
    monkeypatch.setattr(pipeline, "_canonicalize", lambda db, player, name: (kohli, player.id))  # the name is Kohli's
    monkeypatch.setattr(pipeline, "_upsert_event", lambda db, player, **event: guessed.append(event["title"]))
    monkeypatch.setattr(pipeline, "_write_news", lambda db, player, items: [])
    monkeypatch.setattr(notify, "record", lambda *args: [])
    monkeypatch.setattr(notify, "dispatch", lambda ids: None)
    report = {
        "player": {"name": "Virat Kohli", "sport": "Cricket", "status": "active", "nationality": "India", "role": "Batter", "teams": ["India", "RCB"], "summary": "s"},
        "live": {"is_live": False, "player_stats": []},
        "news": [{"headline": "Kohli in the nets"}],
        "upcoming": [{"title": "India v Australia (agent guess)", "competition": "ODI", "start_utc": "2026-10-16T08:00:00Z"}],
        "recent_results": [{"title": "agent's recollection", "result": "Won"}],
        "season_stats": [{"label": "Agent stat", "value": "?"}],
        "sources": ["https://example.org/kohli"],
    }
    assert pipeline.apply_report(9, report) == 5
    card = card_row.card
    assert guessed == []
    assert {k: card[k] for k in ("live", "upcoming", "recent_results", "season_stats", "provenance")} == {k: source_card[k] for k in ("live", "upcoming", "recent_results", "season_stats", "provenance")}
    assert card_row.is_live and card["news"] == [{"headline": "Kohli in the nets"}] and card["player"]["teams"] == ["India"]
    assert card["sources"] == ["https://espncricinfo.com/player/253802", "https://example.org/kohli"]


def test_a_typed_name_a_source_knows_lands_on_the_player_holding_that_athlete(monkeypatch, redis):
    # BUG-031: the NBA's Nikola Jokić is held by id; a footballer of the same name has the plain alias.
    footballer = Player(id=1, slug="nikola-jokic", name="Nikola Jokić", sport="Football")
    nba = Player(id=2, slug="nikola-jokic-basketball", name="Nikola Jokić", sport="Basketball")
    typed = Player(id=3, slug="jokic-nikola", name="Jokic Nikola", sport="", teams=[])
    db = FakeDb(footballer, nba, typed, answers={"Player": footballer, "PlayerIdentity": SimpleNamespace(player_id=2)})
    ref = SimpleNamespace(system="espn_basketball", athlete_id="3112335", name="Nikola Jokić", team_ids=["7"], team_names=["Denver Nuggets"])
    adapter = SimpleNamespace(sport="basketball")
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    assert pipeline._start_from_source(3, adapter, ref) == 2
    assert db.deleted == [typed]


# ---------------------------------------------------------------- arming (BHA-43)


def test_a_football_match_is_armed_when_its_team_sheets_come_out(monkeypatch):
    # Line-ups are out 75 minutes before kick-off (the Premier League's rule since 2024-25); armed at 30
    # minutes, "starts" or "on the bench" reached fans 45 minutes late. Other sports keep the 30 minutes.
    monkeypatch.setattr(structured, "ARM_BEFORE_START", 30 * 60)
    games = [(1, "Football", NOW + timedelta(minutes=74), True), (2, "Football", NOW + timedelta(minutes=76), True),
             (3, "Chess", NOW + timedelta(minutes=74), True), (4, "Chess", NOW + timedelta(minutes=29), True), (5, None, NOW + timedelta(minutes=29), False),
             (6, "Football", NOW + timedelta(minutes=74), False)]  # no source polls it: agent live checks can't see a team sheet
    assert pipeline.to_arm(games, NOW) == [1, 4, 5]
    assert structured.arm_before_any() == timedelta(minutes=75)


def test_the_tick_arms_what_is_due(monkeypatch):
    soon = iter([[(7, "Football", NOW + timedelta(minutes=60), True), (8, "Cricket", NOW + timedelta(minutes=60), True)]])
    db = FakeDb(answers={"Event.id": lambda: next(soon, [])})  # the games starting soon; nothing armed or live yet
    armed = []
    real_execute = db.execute

    def execute(stmt):
        if isinstance(stmt, Update) and "event.id IN" in str(stmt):
            armed.extend(stmt.compile().params["id_1"])
        return real_execute(stmt)

    db.execute = execute
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    pipeline.due_work(NOW)
    assert armed == [7]
