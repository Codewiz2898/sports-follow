"""Campaign pages (campaigns.py): a campaign is a row an operator sets with a command, never code. Its page offers
its players and its teams' rosters as rows a fan follows with one tap, a team's whole roster at once; a player
an operator removes leaves it, and a source that isn't running shows its teams without players. Its link
preview says its event, next game and who to follow (share.of_campaign).

The tests of the rows run against a throwaway database at alembic head, as tests/test_players.py's do, and are
skipped without one (SPORTS_FOLLOW_TEST_DATABASE_URL).
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import adapters, campaigns, players, registry, server, share, sports
from sports_follow.adapters import AdapterError, PlayerRef, SourceTeam
from sports_follow.adapters.pandascore_esports import parse_team
from sports_follow.config import APP_NAME
from sports_follow.models import Athlete, Campaign, Follow, Player, PlayerCard, PlayerIdentity

from .test_mobile_app_links import campaigns_take
from .test_web_refetch import NODE

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")
WEB = Path(__file__).resolve().parent.parent / "web" / "src"
APP_ROUTES = Path(__file__).resolve().parent.parent / "mobile" / "src" / "app"


# ---------------------------------------------------------------- addresses, times and previews


def test_no_campaign_can_take_an_address_the_web_app_has():
    routes = set(re.findall(r'<Route path="/([a-z]+)', (WEB / "main.tsx").read_text()))
    assert routes and routes <= campaigns.TAKEN, routes - campaigns.TAKEN
    assert campaigns.SLUG.fullmatch("gukesh") and campaigns.SLUG.fullmatch("ipl-2027")
    for bad in ("Gukesh", "-x", "x-", "sw.js", "a/b", "", "x" * 41):
        assert not campaigns.SLUG.fullmatch(bad), bad


def test_no_campaign_can_take_an_address_the_android_app_has():
    # Its routes are files (Expo Router), and stann.in links to them open the app (mobile/app.config.ts).
    routes = {re.sub(r"\.tsx$", "", p.name) for p in APP_ROUTES.iterdir()}
    pages = {r for r in routes if re.fullmatch(r"[a-z]+", r)} - {"index"}
    assert pages and pages <= campaigns.TAKEN, pages - campaigns.TAKEN
    assert "[slug]" in routes  # the campaign page itself
    # It claims every one-word address for campaigns, so a one-word page of the web app's that it doesn't have
    # would open its Not found: the admin panel is the one that may (README.md, "Android app"). A longer
    # address it has no folder for, such as /match/<source>/<game>, isn't claimed and stays in the browser.
    web = set(re.findall(r'<Route path="/([a-z]+)(?:/\*)?"', (WEB / "main.tsx").read_text()))
    assert web - pages == {"admin"}, web - pages


@pytest.mark.skipif(NODE is None, reason="needs Node")
def test_a_campaign_link_typed_with_capitals_opens_the_android_app():
    """BUG-191: Android matches a link's path as it's typed, so the campaign pages' patterns take capitals too (the
    server finds /Gukesh as /gukesh). It's still one word: /match/<source>/<game> isn't claimed by them. They're
    spelled out around the admin panel's /admin (mobile/links.js, BUG-157)."""
    for path in ("/gukesh", "/Gukesh", "/IPL-2027"):
        assert campaigns_take(path), path
    for path in ("/", "/match/x/1", "/sw.js", "/a b"):
        assert not campaigns_take(path), path


def test_no_campaign_can_take_the_apis_own_docs_addresses():
    """BUG-197: FastAPI's docs are at /docs and /redoc on a development server that asks for them: never a campaign's."""
    for slug in ("docs", "redoc", "Docs"):
        with pytest.raises(campaigns.Refused, match="already a page"):
            campaigns.setup(None, slug, sport="chess", title="x")  # pyright: ignore[reportArgumentType]: refused before the database is asked


def test_a_games_time_is_typed_and_shown_in_india_time_unless_said():
    start = campaigns.when("2026-11-24 15:00")
    assert start == datetime(2026, 11, 24, 9, 30, tzinfo=timezone.utc)
    assert campaigns.when("2026-11-24 09:30", utc=True) == start
    assert campaigns.said(start.isoformat()) == "Tue 24 Nov, 3:00 pm IST"
    assert campaigns.said("2026-11-14T03:30:00+00:00") == "Sat 14 Nov, 9:00 am IST"
    with pytest.raises(campaigns.Refused):
        campaigns.when("next tuesday")


def _shown(**over):
    return {"slug": "gukesh", "sport": "chess", "title": "Gukesh v Sindarov", "event": "World Chess Championship", "lines": ["Fourteen games for the title."],
            "players": [{"name": "Gukesh D"}, {"name": "Javokhir Sindarov"}], "teams": [],
            "games": [{"start_utc": "2020-01-01T00:00:00+00:00", "label": "Old"}, {"start_utc": "2099-11-24T09:30:00+00:00", "label": "Game 1"}], **over}


def test_a_campaigns_preview_says_its_event_its_next_game_and_who_to_follow():
    p = share.of_campaign(_shown())
    assert p.title == "Gukesh v Sindarov · World Chess Championship" and p.path == "/gukesh" and p.image == "/api/campaigns/gukesh/share.png"
    assert p.picture.word == "Gukesh v Sindarov" and p.picture.tag == "World Chess Championship"
    assert p.picture.ground == sports.BY_KEY["chess"].colour["sport"]
    assert p.picture.lines == ("Game 1: Tue 24 Nov, 3:00 pm IST", "Follow Gukesh D and Javokhir Sindarov")
    assert p.description.startswith("World Chess Championship. Game 1: Tue 24 Nov, 3:00 pm IST. Fourteen games for the title.")
    # Teams to come, no games yet, no event: the sport's name on the tag, and still something to say.
    p = share.of_campaign(_shown(slug="worlds", sport="esports", title="Worlds final", event=None, players=[], games=[], lines=[]))
    assert p.picture.tag == "Esports" and p.picture.lines == () and p.description == f"Live boards and alerts on {APP_NAME}."
    p = share.of_campaign(_shown(players=[], teams=[{"name": "T1"}, {"name": "Gen.G"}, {"name": "BLG"}]))
    assert p.picture.lines[1] == "Follow T1, Gen.G and BLG"
    assert share.png(p.picture)[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_pandascore_team_is_its_active_players_in_its_game():
    team = parse_team({"id": 126061, "name": "T1 ", "current_videogame": {"slug": "league-of-legends"}, "players": [
        {"id": 585, "name": "Faker", "active": True}, {"id": 9, "name": "Zeus "}, {"id": 10, "name": "Benched", "active": False}, {"id": 11, "name": ""}]})
    assert team is not None and (team.system, team.team_id, team.name, team.game) == ("pandascore_esports", "126061", "T1", "League of Legends")
    assert [(p.athlete_id, p.name, p.league, p.team_names) for p in team.players] == [("585", "Faker", "league-of-legends", ["T1"]), ("9", "Zeus", "league-of-legends", ["T1"])]
    assert parse_team({"id": 1, "name": "PUBG side", "current_videogame": {"slug": "pubg"}}) is None  # not a team title
    assert parse_team({"name": "No id", "current_videogame": {"slug": "valorant"}}) is None


# ---------------------------------------------------------------- the rows


@pytest.fixture
def db(monkeypatch):
    if not URL:
        pytest.skip("needs SPORTS_FOLLOW_TEST_DATABASE_URL")
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE campaign, athlete, athlete_override, player, follow, starter RESTART IDENTITY CASCADE"))
    made = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def unit():
        s = made()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for module in (campaigns, players, registry):
        monkeypatch.setattr(module, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


class Teams:
    """A running esports source that lists teams' rosters: `rosters` by team id, or an AdapterError."""

    system, sports = "pandascore_esports", ("lol", "valorant")  # two games, a team in each
    team_id_pattern = re.compile(r"[0-9]{1,12}")

    def __init__(self):
        self.rosters: dict[str, list[str] | Exception] = {}
        self.games: dict[str, str] = {}  # a team's game, by team id: League of Legends unless said

    def team(self, team_id):
        got = self.rosters.get(team_id)
        if isinstance(got, Exception):
            raise got
        if got is None:
            return None
        return SourceTeam(self.system, team_id, f"Team {team_id}", self.games.get(team_id, "League of Legends"),
                          [PlayerRef(self.system, f"{team_id}{i}", name, [team_id], [f"Team {team_id}"], league="league-of-legends") for i, name in enumerate(got)])

    def find_teams(self, name):
        return [t for t in (self.team(tid) for tid in self.rosters if not isinstance(self.rosters[tid], Exception)) if t and name.lower() in t.name.lower()]


@pytest.fixture
def teams(monkeypatch):
    source = Teams()
    monkeypatch.setitem(adapters.ADAPTERS, source.system, source)
    return source


def _athlete(db, qid: str, sport: str, name: str, ids: dict[str, str]) -> Athlete:
    registry.upsert(db, [{"qid": qid, "sport": sport, "name": name, "aliases": [], "search_text": registry.search_text(name, []), "birth_date": None,
                          "birth_precision": None, "country": None, "teams": [], "league": None, "title": None, "ids": ids, "image": None,
                          "sitelinks": 40, "current": True}])
    db.commit()
    return db.scalars(select(Athlete).where(Athlete.qid == qid, Athlete.sport == sport)).one()


@needs_db
def test_a_campaign_is_made_and_changed_by_command_and_refuses_what_isnt_an_address(db):
    assert campaigns.main(["set", "gukesh", "--sport", "chess", "--title", "Gukesh v Sindarov", "--event", "World Chess Championship", "--line", "One", "--line", "Two"]) == 0
    assert campaigns.main(["set", "gukesh", "--title", "Gukesh  v  Sindarov!"]) == 0
    c = db.scalars(select(Campaign)).one()
    assert (c.slug, c.sport, c.title, c.event, c.lines) == ("gukesh", "chess", "Gukesh v Sindarov!", "World Chess Championship", ["One", "Two"])
    for argv in (["set", "search", "--sport", "chess", "--title", "x"], ["set", "Bad Slug", "--sport", "chess", "--title", "x"],
                 ["set", "new", "--title", "no sport"], ["set", "x", "--sport", "golf", "--title", "x"],
                 ["set", "gukesh", "--line", "<b>"], ["set", "gukesh", *[a for i in range(5) for a in ("--line", str(i))]]):
        assert campaigns.main(argv) == 2, argv
    assert campaigns.main(["game", "add", "gukesh", "2026-11-25 15:00", "Game 2"]) == 0
    assert campaigns.main(["game", "add", "gukesh", "2026-11-24 15:00", "Game 1"]) == 0
    db.expire_all()
    assert [g["label"] for g in db.scalars(select(Campaign)).one().games] == ["Game 1", "Game 2"]  # in time order
    assert campaigns.main(["game", "remove", "gukesh", "2"]) == 0 and campaigns.main(["game", "remove", "gukesh", "5"]) == 2
    assert campaigns.main(["remove", "gukesh"]) == 0
    assert db.scalar(select(Campaign.id)) is None


@needs_db
def test_its_players_are_offered_as_rows_and_a_player_an_operator_removes_leaves_it(db, monkeypatch):
    gukesh = _athlete(db, "Q1", "chess", "Gukesh D", {"lichess_chess": "46616543"})
    _athlete(db, "Q2", "chess", "Javokhir Sindarov", {"lichess_chess": "14205483"})
    _athlete(db, "Q3", "chess", "No Source", {})
    monkeypatch.setattr(players, "check_source", lambda person: "lichess_chess")
    campaigns.main(["set", "gukesh", "--sport", "chess", "--title", "Gukesh v Sindarov"])
    for qid in ("Q2", "Q1", "Q3"):
        assert campaigns.main(["player", "add", "gukesh", qid]) == 0
    assert campaigns.main(["player", "add", "gukesh", "Q404"]) == 2
    # Gukesh is on Sports Follow already, and this fan follows him: his row says so.
    player = Player(slug="gukesh-d", name="Gukesh D", sport="chess")
    db.add(player)
    db.flush()
    db.add_all([PlayerIdentity(player_id=player.id, system="lichess_chess", external_id="46616543"), PlayerCard(player_id=player.id, status="ready", card={}),
                Follow(fan_id="fan", player_id=player.id, alert_rules={})])
    db.commit()
    shown = campaigns.page(db, "gukesh", "fan")
    assert shown is not None
    assert [(r["name"], r["key"], r["player_id"], r["following"]) for r in shown["players"]] == [
        ("Javokhir Sindarov", "lichess_chess:14205483", None, False), ("Gukesh D", "lichess_chess:46616543", player.id, True)]  # no source, no row
    assert campaigns.to_follow(shown["players"][0]) == {"system": "lichess_chess", "athlete_id": "14205483", "league": None, "qid": "Q2"}
    assert campaigns.to_follow(shown["players"][1]) == {"player_id": player.id}
    # Each was pinned, so no import drops them; removing Gukesh takes him off the campaign too.
    assert {a.qid for a in players.pin(db, "Q1")} == {"Q1"}
    players.remove(db, players.find(db, "Q1", "chess"))
    db.commit()
    db.expire_all()
    assert db.scalars(select(Campaign)).one().players == ["Q2", "Q3"]
    assert [r["name"] for r in campaigns.page(db, "gukesh", None)["players"]] == ["Javokhir Sindarov"]
    assert campaigns.main(["player", "remove", "gukesh", "Q2"]) == 0
    db.expire_all()
    assert db.scalars(select(Campaign)).one().players == ["Q3"] and gukesh.qid == "Q1"


@needs_db
def test_a_team_is_added_from_its_source_and_its_roster_kept_up_to_date(db, teams, monkeypatch):
    teams.rosters = {"126061": ["Faker", "Zeus"], "2": ["Chovy"], "3": ["TenZ"]}
    teams.games = {"3": "Valorant"}
    campaigns.main(["set", "worlds", "--sport", "lol", "--title", "Worlds final"])  # each esports game is a sport
    assert campaigns.page(db, "worlds", None)["teams"] == []  # the finalists come later: the page works without them
    assert campaigns.main(["team", "add", "worlds", "pandascore_esports:126061"]) == 0
    for bad in ("pandascore_esports:404", "pandascore_esports:x1", "nosuch:1", "lichess_chess:1", "pandascore_esports:3"):  # 3: a Valorant team
        assert campaigns.main(["team", "add", "worlds", bad]) == 2, bad
    shown = campaigns.page(db, "worlds", "fan")
    assert [(t["team_id"], t["name"], [(r["key"], r["name"]) for r in t["players"]]) for t in shown["teams"]] == [
        ("126061", "Team 126061", [("pandascore_esports:1260610", "Faker"), ("pandascore_esports:1260611", "Zeus")])]
    assert campaigns.to_follow(shown["teams"][0]["players"][0]) == {"system": "pandascore_esports", "athlete_id": "1260610", "league": "league-of-legends", "qid": None}

    # A day later: a new player is on, and the other team's source fails, which leaves its roster as read.
    assert campaigns.main(["team", "add", "worlds", "pandascore_esports:2"]) == 0
    teams.rosters = {"126061": ["Faker", "Zeus", "Oner"], "2": AdapterError("down")}
    assert campaigns.refresh() == {"worlds": 1}
    shown = campaigns.page(db, "worlds", None)
    assert [[r["name"] for r in t["players"]] for t in shown["teams"]] == [["Faker", "Zeus", "Oner"], ["Chovy"]]

    # A player an operator removed isn't offered; a source that's turned off shows its teams without players.
    db.execute(text("INSERT INTO athlete_override (qid, sport, action, name, ids) VALUES ('pandascore:9', 'esports', 'remove', 'Zeus', '{\"pandascore_esports\": \"1260611\"}')"))
    db.commit()
    assert [r["name"] for r in campaigns.page(db, "worlds", None)["teams"][0]["players"]] == ["Faker", "Oner"]
    monkeypatch.delitem(adapters.ADAPTERS, teams.system)
    assert [t["players"] for t in campaigns.page(db, "worlds", None)["teams"]] == [[], []]
    assert campaigns.main(["team", "remove", "worlds", "2"]) == 0 and campaigns.main(["team", "remove", "worlds", "2"]) == 2


@needs_db
def test_a_campaign_of_a_sport_turned_off_has_no_page(db, monkeypatch):
    campaigns.main(["set", "gukesh", "--sport", "chess", "--title", "Gukesh v Sindarov"])
    assert campaigns.page(db, "gukesh", None) is not None and campaigns.page(db, "nope", None) is None
    monkeypatch.setattr(sports, "on", lambda key: key != "chess")
    assert campaigns.page(db, "gukesh", None) is None


@needs_db
def test_an_esports_campaign_takes_any_games_teams(db, teams, monkeypatch):
    """An event of several games (the Esports World Cup) is the esports group's: a team of any game; one of a
    game turned off offers no one, as a team whose source is off doesn't."""
    teams.rosters = {"126061": ["Faker"], "3": ["TenZ"]}
    teams.games = {"3": "Valorant"}
    campaigns.main(["set", "ewc", "--sport", "esports", "--title", "Esports World Cup"])
    assert campaigns.main(["team", "add", "ewc", "pandascore_esports:126061"]) == 0 and campaigns.main(["team", "add", "ewc", "pandascore_esports:3"]) == 0
    assert [(t["game"], len(t["players"])) for t in campaigns.page(db, "ewc", None)["teams"]] == [("League of Legends", 1), ("Valorant", 1)]
    monkeypatch.setattr(sports, "ENABLED", tuple(s for s in sports.ALL if s.key != "valorant"))
    assert [(t["game"], len(t["players"])) for t in campaigns.page(db, "ewc", None)["teams"]] == [("League of Legends", 1), ("Valorant", 0)]


@needs_db
def test_a_campaign_link_typed_with_capitals_opens_its_page(db, monkeypatch):
    """BUG-191: /Gukesh, retyped from a poster with the name capitalised, is /gukesh's page and picture, not Not found."""
    campaigns.main(["set", "gukesh", "--sport", "chess", "--title", "Gukesh v Sindarov"])
    monkeypatch.setattr(server, "session", campaigns.session)  # the fixture's sessions
    for typed in ("gukesh", "Gukesh", "GUKESH"):
        assert asyncio.run(server.campaign(typed, "fan"))["slug"] == "gukesh"
        assert asyncio.run(server.campaign_picture(typed)).status_code == 200
    for other in ("nope", "Gu kesh", "\u212aukesh"):  # K: the Kelvin sign, which lower-cases to "k"
        with pytest.raises(server.HTTPException) as missing:
            asyncio.run(server.campaign(other, "fan"))
        assert missing.value.status_code == 404
