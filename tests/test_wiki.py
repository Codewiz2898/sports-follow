"""Player profiles from Wikidata and Wikipedia (wiki.py), and how a page gets them (pipeline.py): made-up
answers from both, a fake database and Redis, and never a model."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from sports_follow import bus, config, pipeline, sports, structured, wiki
from sports_follow.models import Player, PlayerCard
from tests.test_pipeline import FakeDb, FakeRedis

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
FOOTBALL, CRICKET, CHESS = sports.BY_KEY["football"], sports.BY_KEY["cricket"], sports.BY_KEY["chess"]


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(bus, "_sync", fake)
    return fake


def item(qid: str, rank: str = "normal", **qualifiers: Any) -> dict[str, Any]:
    """A statement naming an item, with qualifiers as Wikidata gives them: P580="+2008-01-01T00:00:00Z"."""
    quals = {p: [{"datavalue": {"value": {"time": t, "precision": 9}}} for t in (v if isinstance(v, list) else [v])] for p, v in qualifiers.items()}
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}, "rank": rank, "qualifiers": quals, "references": [{"snaks": "a source, kept out of the cache"}]}


def when(time: str, precision: int, rank: str = "normal") -> dict[str, Any]:
    return {"mainsnak": {"datavalue": {"value": {"time": time, "precision": precision}}}, "rank": rank}


# A made-up cricketer, as the Wikidata API answers for them.
CRICKETER = {
    "labels": {"en": {"value": "Asha Rao"}},
    "sitelinks": {"enwiki": {"site": "enwiki", "title": "Asha Rao"}},
    "claims": {
        "P569": [when("+1994-03-09T00:00:00Z", 11)],
        "P27": [item("Q668")],
        "P1532": [item("Q668")],
        "P413": [item("Q100"), item("Q101", rank="deprecated")],
        "P54": [
            item("Q200", P580="+2015-01-01T00:00:00Z"),  # India
            item("Q201", P580="+2018-01-01T00:00:00Z"),  # Mumbai Indians
            item("Q202", P580="+2012-01-01T00:00:00Z", P582="+2016-01-01T00:00:00Z"),  # a former club
            item("Q203", P580="+2011-01-01T00:00:00Z"),  # India Under-19: a youth side
        ],
        "P166": [
            item("Q3569447", P585="+2021-01-01T00:00:00Z"),  # Wisden Cricketer of the Year
            item("Q8027398", P585=["+2022-01-01T00:00:00Z", "+2023-01-01T00:00:00Z"]),  # Wisden Leading Cricketer, twice
            item("Q999", P585="+2020-01-01T00:00:00Z"),  # an award cricket doesn't name
            item("Q7526820", rank="deprecated"),
        ],
    },
}
LABELS = {
    "Q668": "India", "Q100": "batter", "Q101": "bowler", "Q200": "India national cricket team", "Q201": "Mumbai Indians",
    "Q202": "Old Club", "Q203": "India Under-19 cricket team", "Q3569447": "Wisden Cricketer of the Year",
    "Q8027398": "Wisden Leading Cricketer in the World",
}
SUMMARY = {
    "type": "standard", "title": "Asha Rao",
    "extract": "Asha Rao is an Indian international cricketer who bats right-handed.\n\nShe captains Mumbai Indians.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Asha_Rao"}},
}


# ---------------------------------------------------------------- reading


def test_a_cricketers_profile_reads_as_her_page_shows_it():
    found = wiki.parse("Q1", CRICKETER, LABELS, SUMMARY, CRICKET)
    assert found.facts() == {
        "qid": "Q1", "name": "Asha Rao", "born": "1994-03-09", "nationality": "India", "role": "Batter",
        # Her country first (cricket puts it first), then her newest current club; no former or youth side.
        "teams": ["India", "Mumbai Indians"],
        "honours": ["Wisden Leading Cricketer in the World ×2", "Wisden Cricketer of the Year"],
        "summary": "Asha Rao is an Indian international cricketer who bats right-handed. She captains Mumbai Indians.",
        "article": "https://en.wikipedia.org/wiki/Asha_Rao",
    }


def test_two_awards_of_one_name_are_counted_together_and_a_year_alone_is_a_year():
    entity = {"claims": {
        "P569": [when("+1987-00-00T00:00:00Z", 9)],
        "P1532": [], "P27": [item("Q30")],  # no sporting country: their citizenship
        "P166": [item("Q166177", P585=["+2009-01-01T00:00:00Z", "+2019-01-01T00:00:00Z"]), item("Q2291862", P585="+2010-01-01T00:00:00Z"), item("Q17355204")],
    }}
    found = wiki.parse("Q2", entity, {"Q30": "United States of America"}, None, FOOTBALL)
    assert (found.born, found.nationality, found.summary, found.article) == ("1987", "United States", None, None)
    assert found.honours == ["Ballon d'Or ×3", "World Cup Golden Ball"]


def test_a_chess_players_role_is_their_title_and_individual_sports_have_no_teams():
    entity = {"claims": {"P2962": [item("Q105269", rank="preferred"), item("Q752119")], "P54": [item("Q5")], "P413": [item("Q6")]}}
    found = wiki.parse("Q3", entity, {"Q105269": "Grandmaster", "Q752119": "International Master", "Q5": "A Club", "Q6": "forward"}, None, CHESS)
    assert (found.role, found.teams) == ("Grandmaster", [])  # the preferred title only


@pytest.mark.parametrize("summary", [{"type": "disambiguation", "extract": "Asha Rao may refer to:"}, {"type": "standard", "extract": "  "}, {}])
def test_no_about_from_a_disambiguation_page_or_an_empty_one(summary):
    assert wiki.about(summary) == (None, None)


def test_a_long_summary_is_cut_at_a_sentence():
    text, _ = wiki.about({"type": "standard", "extract": ("A sentence of the summary. " * 80).strip()})
    assert text and len(text) <= wiki.ABOUT_MAX and text.endswith("summary.")


class Wikimedia:
    """The Wikidata API and Wikipedia's REST API, answering from made-up documents; counts what's asked."""

    def __init__(self, entity: dict[str, Any] | None = None, summary: dict[str, Any] | None = None, status: int = 200):
        self.entity, self.summary, self.status, self.asked = entity, summary, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(str(request.url))
        if self.status != 200:
            return httpx.Response(self.status)
        params = request.url.params
        if request.url.host == "en.wikipedia.org":
            return httpx.Response(200, json=self.summary) if self.summary else httpx.Response(404, json={"type": "not_found"})
        if params.get("props") == "labels":
            return httpx.Response(200, json={"entities": {q: {"labels": {"en": {"value": LABELS[q]}}} for q in params["ids"].split("|") if q in LABELS}})
        found = self.entity if self.entity is not None else {"missing": ""}
        return httpx.Response(200, json={"entities": {params["ids"]: found}})


@pytest.fixture
def wikimedia(monkeypatch):
    def serve(**kwargs) -> Wikimedia:
        fake = Wikimedia(**kwargs)
        monkeypatch.setattr(wiki, "_client", httpx.Client(transport=httpx.MockTransport(fake)))
        return fake

    return serve


def test_a_profile_is_three_requests_kept_for_a_month(wikimedia, source_cache):
    served = wikimedia(entity=CRICKETER, summary=SUMMARY)
    found = wiki.read("Q1", "Cricket")
    assert found and found.summary and found.teams == ["India", "Mumbai Indians"]
    assert len(served.asked) == 3 and served.asked[-1] == "https://en.wikipedia.org/api/rest_v1/page/summary/Asha_Rao"
    assert wiki.read("Q1", "Cricket") == found and len(served.asked) == 3  # from the cache
    kept = [doc for doc in source_cache.data if doc.startswith("src:wikimedia:")]
    assert len(kept) == 3 and all(ex >= wiki.MAX_AGE for ex in source_cache.expiry.values())
    entity = wiki.entity("Q1", wiki.MAX_AGE)
    assert "references" not in str(entity) and set(entity["claims"]) <= set(wiki._properties())
    # The scheduler's monthly read takes a copy at most a day old: these are a moment old.
    assert wiki.read("Q1", "Cricket", wiki.REREAD) == found and len(served.asked) == 3


def test_a_player_without_an_article_or_a_person_has_no_about(wikimedia):
    served = wikimedia(entity={**CRICKETER, "sitelinks": {}})
    found = wiki.read("Q1", "Cricket")
    assert found and found.summary is None and found.born == "1994-03-09" and len(served.asked) == 2
    served = wikimedia(entity=CRICKETER)  # an article Wikipedia has no summary of (404)
    assert wiki.read("Q4", "Cricket").summary is None
    wikimedia(entity=None)
    assert wiki.read("Q5", "Cricket") is None  # an item that's gone
    assert wiki.read("pandascore:20401", "Valorant") is None  # an esports pro's source key: no request


def test_wikimedia_asking_us_to_wait_is_unavailable(wikimedia):
    wikimedia(status=429)
    with pytest.raises(wiki.Unavailable):
        wiki.read("Q1", "Cricket")


def test_an_error_the_api_answers_with_200_is_no_answer_and_isnt_kept(monkeypatch, source_cache):
    # The Wikidata API says "maxlag" or "ratelimited" in a 200: kept as an empty person, it would take the
    # page's About away for a month.
    answers = [httpx.Response(200, json={"error": {"code": "maxlag", "info": "Waiting for a database server"}})]
    monkeypatch.setattr(wiki, "_client", httpx.Client(transport=httpx.MockTransport(lambda request: answers[0])))
    with pytest.raises(wiki.Unavailable, match="maxlag"):
        wiki.read("Q1", "Cricket")
    assert not source_cache.data
    answers[0] = httpx.Response(200, json={"title": "no type"})
    with pytest.raises(wiki.Unavailable):
        wiki.summary("Asha Rao", wiki.MAX_AGE)


def test_teams_wikidata_no_longer_lists_leave_the_page(monkeypatch, redis):
    page = _page(monkeypatch, bound=False, qid="Q1", card={"status": "ready", "player": {"name": "Asha Rao"}}, status="ready", profile=PROFILE)
    pipeline.refresh_profile(7)
    assert page.player.teams == ["India", "Mumbai Indians"]
    monkeypatch.setattr(wiki, "read", lambda q, sport, ttl=wiki.MAX_AGE: wiki.Profile(qid="Q1", born="1994-03-09"))  # her stints all ended
    pipeline.refresh_profile(7)
    assert page.player.teams == [] and page.published[7]["player"]["teams"] == []


# ---------------------------------------------------------------- precedence (pipeline.merge_profile, keep_profile)


PROFILE = wiki.Profile(qid="Q1", name="Asha Rao", born="1994-03-09", nationality="India", role="Batter", teams=["India", "Mumbai Indians"],
                       honours=["Wisden Cricketer of the Year"], summary="Asha Rao is a cricketer.", article="https://en.wikipedia.org/wiki/Asha_Rao")


def test_wikidata_wins_over_the_agent_and_a_source_wins_over_wikidata():
    agents = {"name": "Asha Rao", "sport": "Cricket", "teams": ["India"], "nationality": "Indian", "role": "Opening batter", "summary": "The agent's words."}
    merged = pipeline.merge_profile(agents, PROFILE, bound=False)
    assert {k: merged[k] for k in ("nationality", "role", "teams", "summary", "born")} == {
        "nationality": "India", "role": "Batter", "teams": ["India", "Mumbai Indians"], "summary": "Asha Rao is a cricketer.", "born": "1994-03-09"}
    assert merged["about"] == {"source": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Asha_Rao", "licence": "CC BY-SA 4.0", "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/"}
    assert merged["profile_from"] == {"born": "wikidata", "nationality": "wikidata", "role": "wikidata", "honours": "wikidata", "teams": "wikidata", "summary": "wikipedia"}
    # A bound player's teams are their source's.
    sourced = pipeline.merge_profile({"name": "Asha Rao", "teams": ["Mumbai Indians"]}, PROFILE, bound=True)
    assert sourced["teams"] == ["Mumbai Indians"] and "teams" not in sourced["profile_from"]


def test_what_wikidata_no_longer_says_goes_and_the_agents_report_never_overwrites_it():
    before = pipeline.merge_profile({"name": "Asha Rao", "teams": []}, PROFILE, bound=False)
    after = pipeline.merge_profile(before, wiki.Profile(qid="Q1", born="1994-03-09"), bound=False)  # the article and award went
    assert "summary" not in after and "about" not in after and "honours" not in after and after["born"] == "1994-03-09"
    report = {"name": "Asha Rao", "sport": "Cricket", "role": "Bowler", "summary": "The agent's words.", "nationality": "Indian", "teams": ["India"]}
    kept = pipeline.keep_profile(before, report)
    assert (kept["role"], kept["summary"], kept["about"]["source"], kept["nationality"], kept["teams"]) == ("Batter", "Asha Rao is a cricketer.", "Wikipedia", "India", ["India", "Mumbai Indians"])
    assert pipeline.keep_profile({}, report) == {**report, "profile_from": {}}  # nothing from Wikidata: the agent's words stand


# ---------------------------------------------------------------- pages built without a model


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(pipeline, "llm", lambda: pytest.fail("the model was asked"))
    monkeypatch.setattr(pipeline, "follow_player", lambda *args: pytest.fail("the research agent ran"))


def _page(monkeypatch, *, bound: bool, qid: str | None, card: dict[str, Any], status: str, profile: wiki.Profile | Exception | None):
    player = Player(id=7, slug="asha-rao", name="Asha Rao", sport="Cricket", status="active", teams=["India"])
    row = PlayerCard(player_id=7, status=status, card=card, version=1, built_at=None)
    db = FakeDb(player, row, answers={"PlayerIdentity.external_id": qid, "PlayerIdentity.id": qid and 1, "Player.sport": "Cricket", "Follow.id": 1})
    published: dict[int, dict] = {}
    reads: list[tuple[str, float]] = []

    def read(q, sport, ttl=wiki.MAX_AGE):
        reads.append((q, ttl))
        if isinstance(profile, Exception):
            raise profile
        return profile

    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(pipeline, "_now", lambda: NOW)
    monkeypatch.setattr(wiki, "read", read)
    monkeypatch.setattr(structured, "binding", lambda db, player_id: SimpleNamespace(adapter="sportmonks_cricket") if bound else None)
    monkeypatch.setattr(bus, "store_card", lambda player_id, c: published.__setitem__(player_id, c))
    return SimpleNamespace(player=player, row=row, published=published, reads=reads)


@pytest.mark.parametrize("agent", [True, False])
def test_a_player_with_an_article_and_a_live_source_needs_no_model(monkeypatch, redis, no_model, agent):
    # BHA-323: the AI account ran out of credit and pages went without their profile. A page whose About is
    # Wikipedia's and whose games are a source's needs no model at all, with the agent on or off.
    monkeypatch.setattr(config, "AGENT", agent)
    source = {"status": "ready", "version": 1, "pending": ["profile"], "player": {"name": "Asha Rao", "teams": ["Mumbai Indians"]}, "provenance": {"upcoming": {}}}
    page = _page(monkeypatch, bound=True, qid="Q1", card=source, status="ready", profile=PROFILE)
    pipeline.build_card(7)
    card = page.published[7]
    assert page.reads == [("Q1", wiki.MAX_AGE)] and "pending" not in card
    assert card["player"]["summary"] == "Asha Rao is a cricketer." and card["player"]["about"]["source"] == "Wikipedia"
    assert card["player"]["teams"] == ["Mumbai Indians"] and card["freshness"]["profile"] == NOW.isoformat()
    assert (page.player.nationality, page.player.role) == ("India", "Batter")
    assert not pipeline.agent_due(7, card, NOW + timedelta(days=3))  # opening it never sends the agent either


def test_a_page_no_source_has_shows_its_profile_at_once_and_keeps_it_when_the_agent_fails(monkeypatch, redis):
    # A registry athlete no live source has: the agent still writes their games and news, but the profile
    # and About are on the page before it runs, and stay when the AI account is out of credit.
    monkeypatch.setattr(config, "AGENT", True)
    placeholder = pipeline.placeholder_card(Player(id=7, slug="asha-rao", name="Asha Rao", sport="Cricket", status="active", teams=[]))
    page = _page(monkeypatch, bound=False, qid="Q1", card=placeholder, status="building", profile=PROFILE)
    seen = []

    def out_of_credit(llm, name, known):
        seen.append(dict(page.row.card))
        raise pipeline.AgentError("Model call failed (server): openrouter: HTTP 402 Payment Required")
        yield

    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", out_of_credit)
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    pipeline.build_card(7)
    [during] = seen
    assert during["status"] == "ready" and during["pending"] == ["news"] and during["player"]["about"]["source"] == "Wikipedia"
    card = page.published[7]
    assert page.row.status == card["status"] == "ready" and "pending" not in card
    assert card["pending_error"] == "AI research isn't available right now. Try again later."
    assert card["player"]["summary"] == "Asha Rao is a cricketer." and card["live"] == {"is_live": False, "player_stats": [], "available": False}


def test_a_picked_players_page_without_an_article_gets_its_facts_and_the_agent_writes_its_background(monkeypatch, redis):
    monkeypatch.setattr(config, "AGENT", True)
    source = {"status": "ready", "version": 1, "pending": ["profile"], "player": {"name": "Asha Rao", "teams": ["Mumbai Indians"]}, "provenance": {}}
    page = _page(monkeypatch, bound=True, qid="Q1", card=source, status="ready", profile=wiki.Profile(qid="Q1", born="1994-03-09", teams=["India"]))
    ran = []
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", lambda llm, name, known: ran.append(name) or iter(()))
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    pipeline.build_card(7)
    card = page.published[7]
    assert card["player"]["born"] == "1994-03-09" and card["player"]["teams"] == ["Mumbai Indians"]  # the source's teams
    assert ran == ["Asha Rao"]  # no article: the background is the agent's


def test_wikimedia_not_answering_leaves_the_page_as_it_is_and_tries_again_later(monkeypatch, redis, no_model):
    monkeypatch.setattr(config, "AGENT", False)
    source = {"status": "ready", "version": 1, "pending": ["profile"], "player": {"name": "Asha Rao"}, "provenance": {}}
    page = _page(monkeypatch, bound=True, qid="Q1", card=source, status="ready", profile=wiki.Unavailable("HTTP 429"))
    pipeline.build_card(7)
    assert "pending" not in page.published[7] and "freshness" not in page.published[7]
    assert not pipeline.profile_due(page.row, True, NOW)  # an hour's wait (PROFILE_RETRY)
    redis.delete("lock:profileretry:7")
    assert pipeline.profile_due(page.row, True, NOW)


def test_a_page_with_no_article_and_no_source_is_researched_when_a_fan_opens_it(monkeypatch, redis):
    monkeypatch.setattr(config, "AGENT", True)
    monkeypatch.setattr(config, "CARD_MAX_AGE", 6 * 3600)
    page = _page(monkeypatch, bound=False, qid="Q1", card={"status": "ready", "player": {"name": "Asha Rao"}, "built_at": (NOW - timedelta(hours=7)).isoformat()},
                 status="ready", profile=wiki.Profile(qid="Q1", born="1994-03-09"))
    assert pipeline.agent_due(7, page.row.card, NOW)
    assert not pipeline.agent_due(7, {**page.row.card, "built_at": (NOW - timedelta(hours=1)).isoformat()}, NOW)  # researched an hour ago
    monkeypatch.setattr(config, "AGENT", False)
    assert not pipeline.agent_due(7, page.row.card, NOW)
    monkeypatch.setattr(config, "AGENT", True)
    ran = []
    monkeypatch.setattr(pipeline, "llm", lambda: None)
    monkeypatch.setattr(pipeline, "follow_player", lambda llm, name, known: ran.append(name) or iter(()))
    monkeypatch.setattr(structured, "describe", lambda player_id: None)
    pipeline.build_card(7)
    assert ran == ["Asha Rao"] and page.published[7]["player"]["born"] == "1994-03-09"


def test_profiles_are_read_again_monthly_and_never_for_a_player_with_no_person(redis):
    card = PlayerCard(player_id=7, status="ready", card={}, freshness={"profile": (NOW - timedelta(days=29)).isoformat()})
    assert not pipeline.profile_due(card, True, NOW)
    card.freshness = {"profile": (NOW - timedelta(days=31)).isoformat()}
    assert pipeline.profile_due(card, True, NOW) and not pipeline.profile_due(card, False, NOW)
    card.freshness = {}
    assert pipeline.profile_due(card, True, NOW)
    assert not pipeline.profile_due(PlayerCard(player_id=8, status="building", card={}, freshness={}), True, NOW)  # its build reads it


def test_an_esports_pro_is_read_by_the_person_their_row_was_matched_to():
    db = FakeDb(answers={"PlayerIdentity.external_id": "pandascore:20401", "Athlete.ids": {"wikidata": "Q113450352", "pandascore_esports": "20401"}})
    assert pipeline.player_qid(db, 1) == "Q113450352"
    db = FakeDb(answers={"PlayerIdentity.external_id": "pandascore:20402", "Athlete.ids": {"pandascore_esports": "20402"}})
    assert pipeline.player_qid(db, 1) is None  # not matched to anyone yet
    assert pipeline.player_qid(FakeDb(answers={"Player.wikidata_qid": "Q42"}), 1) == "Q42"


def test_a_player_with_no_person_is_read_once_a_month_not_every_minute(monkeypatch, redis):
    # A pro the registry hasn't matched has a "wikidata" identity that names no Wikidata person: their read
    # finds nothing and says when, so the scheduler's five a minute go to others.
    page = _page(monkeypatch, bound=True, qid="pandascore:20402", card={"status": "ready", "player": {"name": "TenZ"}}, status="ready", profile=None)
    pipeline.reread_profile(7)
    assert page.reads == [] and page.row.freshness["profile"] == NOW.isoformat()
    assert not pipeline.profile_due(page.row, True, NOW + timedelta(minutes=1))


def test_the_scheduler_reads_five_profiles_a_minute_and_never_a_building_page(monkeypatch, redis):
    players = [Player(id=i, slug=f"p{i}", name=f"P{i}", sport="Cricket", status="active", teams=[]) for i in range(1, 9)]
    cards = [PlayerCard(player_id=i, status="ready", card={"status": "ready"}, freshness={}) for i in range(1, 8)]
    cards.append(PlayerCard(player_id=8, status="building", card={"status": "building"}, freshness={}))
    db = FakeDb(*players, *cards, answers={"Player.id": [(p.id, p.sport) for p in players], "PlayerIdentity.player_id": [(i,) for i in range(1, 9)]})
    monkeypatch.setattr(pipeline, "session", lambda: nullcontext(db))
    monkeypatch.setattr(structured, "close_live", lambda db, ids: [])
    monkeypatch.setattr("sports_follow.matches.lapse", lambda db, now: None)
    monkeypatch.setattr("sports_follow.matches.settle", lambda db: None)
    monkeypatch.setattr("sports_follow.teams.due", lambda db, now: [])
    due = pipeline.due_work(NOW)
    assert due.profiles == [1, 2, 3, 4, 5] and due.builds == [8]
