"""Team logos (logos.py, BHA-666) against a throwaway database at alembic head (skipped without one; see
tests/test_players.py for setting it up): the import keeps where a team's source keeps its logo, the job downloads it
into our own copy and reads its colours, looks tell the app how to draw each team, the logo is served from our origin,
and the admin panel's switches and colours take effect at once. The network is made up."""

from __future__ import annotations

import io
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import admin, controls, logos, server, teams
from sports_follow.adapters import KNOWN, SourceTeam
from sports_follow.models import Team, TeamLogo

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
BARCA = "https://cdn.sportmonks.com/images/soccer/teams/19/83.png"
MADRID = "https://cdn.sportmonks.com/images/soccer/teams/12/3468.png"


def png(*colours: tuple[int, int, int]) -> bytes:
    """A logo of equal stripes of these colours."""
    pic = Image.new("RGBA", (90, 90), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pic)
    for n, colour in enumerate(colours):
        draw.rectangle([n * 90 // len(colours), 0, (n + 1) * 90 // len(colours) - 1, 89], fill=(*colour, 255))
    out = io.BytesIO()
    pic.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text("TRUNCATE team, team_logo RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(logos, "session", unit)
    monkeypatch.setattr(server, "session", unit)
    s = made()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def running(monkeypatch):
    """The sources with logos run here, less any the admin panel turns off: tests have no tokens for them."""

    class Running:
        def _now(self) -> list[str]:
            return [s for s in ("sportmonks_football", "sportmonks_cricket", "pandascore_esports") if s not in controls.current().sources_off]

        def __iter__(self):
            return iter(self._now())

        def __getitem__(self, system: str):
            if system not in self._now():
                raise KeyError(system)
            return KNOWN[system]

    monkeypatch.setattr(logos, "ADAPTERS", Running())


@pytest.fixture
def files(monkeypatch):
    """The sources' logo files, by address; what was asked for."""
    served: dict[str, bytes] = {BARCA: png((0, 77, 152), (165, 0, 68)), MADRID: png((255, 255, 255), (18, 91, 156), (250, 190, 17))}
    asked: list[str] = []

    def download(system: str, url: str) -> bytes:
        asked.append(url)
        if url not in served:
            raise logos.Refused("HTTP 404")
        return served[url]

    monkeypatch.setattr(logos, "download", download)
    return type("Files", (), {"served": served, "asked": asked})


def team(db, external: str, name: str, url: str | None, sport: str = "football", system: str = "sportmonks_football") -> Team:
    made = teams.upsert(db, system, SourceTeam(system=system, team_id=external, name=name, short=name[:3].upper(), logo=url or ""), sport, NOW)
    db.commit()
    return made


def test_the_import_keeps_where_a_teams_logo_is_and_the_job_keeps_our_copy(db, files):
    barca = team(db, "83", "FC Barcelona", BARCA)
    team(db, "1", "No Logo FC", None)
    assert barca.logo_url == BARCA
    assert logos.refresh(NOW, pause=0) == {"asked": 1, "kept": 1, "failed": 0}  # only a team with an address is asked about
    kept = db.get(TeamLogo, barca.id)
    assert kept is not None and kept.status == "ok" and kept.digest and kept.data and kept.palette
    # Kept: not asked again until it's due, or its source's address changes.
    assert logos.refresh(NOW + timedelta(days=1), pause=0)["asked"] == 0
    assert logos.refresh(NOW + timedelta(days=logos.REFETCH_DAYS + 1), pause=0)["asked"] == 1
    files.served["https://cdn.sportmonks.com/images/soccer/teams/19/83-new.png"] = files.served[BARCA]
    team(db, "83", "FC Barcelona", "https://cdn.sportmonks.com/images/soccer/teams/19/83-new.png")
    assert logos.refresh(NOW + timedelta(days=logos.REFETCH_DAYS + 2), pause=0)["asked"] == 1


def test_a_logo_that_fails_is_tried_again_the_next_day_and_one_kept_shows_on(db, files):
    barca = team(db, "83", "FC Barcelona", BARCA)
    logos.refresh(NOW, pause=0)
    digest = db.get(TeamLogo, barca.id).digest  # pyright: ignore[reportOptionalMemberAccess]
    del files.served[BARCA]
    logos.refresh(NOW + timedelta(days=logos.REFETCH_DAYS + 1), pause=0)  # due again, and gone at the source for now
    db.expire_all()
    kept = db.get(TeamLogo, barca.id)
    assert kept is not None and kept.status == "ok" and kept.digest == digest and kept.reason == "HTTP 404"
    nobody = team(db, "2", "Gone FC", "https://cdn.sportmonks.com/images/soccer/teams/0/2.png")
    logos.refresh(NOW, pause=0)
    assert db.get(TeamLogo, nobody.id).status == "failed"  # pyright: ignore[reportOptionalMemberAccess]
    assert logos.refresh(NOW + timedelta(hours=1), pause=0)["asked"] == 0
    assert logos.refresh(NOW + logos.RETRY + timedelta(minutes=1), pause=0)["asked"] == 1


def test_looks_tell_the_app_each_teams_logo_and_colours_and_the_switches_take_logos_down(db, files):
    barca = team(db, "83", "FC Barcelona", BARCA)
    team(db, "3468", "Real Madrid", MADRID)
    team(db, "9", "Waiting FC", "https://cdn.sportmonks.com/images/soccer/teams/0/9.png")
    logos.refresh(NOW, pause=0)
    found = logos.looks(db, ["sportmonks_football:83", "sportmonks_football:3468", "sportmonks_football:404", "no-colon"])
    assert set(found) == {"sportmonks_football:83", "sportmonks_football:3468"}  # those the registry has
    look = found["sportmonks_football:83"]
    digest = db.get(TeamLogo, barca.id).digest  # pyright: ignore[reportOptionalMemberAccess]
    assert look["logo"] == f"/api/logos/{digest}.png" and look["short"] == "FC "
    assert look["colours"]["fill"] == "#004d98" and look["tones"][0] == look["colours"]["fill"] and look["set"] is False
    assert logos.serve(db, f"{digest}.png") == db.get(TeamLogo, barca.id).data  # pyright: ignore[reportOptionalMemberAccess]

    # A sport's logos off: our badge in their place, and the address serves nothing; the colours stay.
    controls.turn_logos("football", False)
    off = logos.looks(db, ["sportmonks_football:83"])["sportmonks_football:83"]
    assert off["logo"] is None and off["colours"] == look["colours"]
    assert logos.serve(db, f"{digest}.png") is None
    controls.turn_logos("football", True)
    # One team's taken down.
    controls.hide_logo("sportmonks_football:83", True)
    assert logos.looks(db, ["sportmonks_football:83"])["sportmonks_football:83"]["logo"] is None
    assert logos.serve(db, f"{digest}.png") is None
    assert logos.looks(db, ["sportmonks_football:3468"])["sportmonks_football:3468"]["logo"] is not None
    # And isn't fetched while it's down.
    assert all(t[0] != barca.id for t in logos.wanted(db, NOW + timedelta(days=logos.REFETCH_DAYS + 1)))


def test_an_operators_colours_win_and_go_back_to_the_logos(db, files):
    madrid = team(db, "3468", "Real Madrid", MADRID)
    logos.refresh(NOW, pause=0)
    shown = admin.set_team_look(db, madrid.id, {"colours": {"fill": "#FFFFFF", "accent": "#e0b21b"}})
    db.commit()
    assert shown["look"]["colours"] == {"fill": "#ffffff", "accent": "#e0b21b"} and shown["look"]["set"] is True
    assert shown["read"] and shown["read"] != shown["look"]["colours"]  # what its logo says, beside
    assert logos.looks(db, ["sportmonks_football:3468"])["sportmonks_football:3468"]["colours"]["fill"] == "#ffffff"
    shown = admin.set_team_look(db, madrid.id, {"colours": None, "hidden": True})
    db.commit()
    assert shown["look"]["set"] is False and shown["hidden"] is True and shown["look"]["logo"] is None
    with pytest.raises(ValueError):
        admin.set_team_look(db, madrid.id, {"colours": {"fill": "red"}})
    with pytest.raises(LookupError):
        admin.set_team_look(db, 999, {"hidden": False})


def test_the_admin_panel_finds_teams_and_says_how_their_logos_stand(db, files):
    team(db, "83", "FC Barcelona", BARCA)
    team(db, "5", "Barcelona SC", None)
    logos.refresh(NOW, pause=0)
    rows = {r["name"]: r for r in logos.for_admin(db, "barcelona")}
    assert rows["FC Barcelona"]["logo_status"] == "ok" and rows["FC Barcelona"]["key"] == "sportmonks_football:83"
    assert rows["Barcelona SC"]["logo_status"] == "none" and rows["Barcelona SC"]["look"]["colours"] is None
    assert logos.for_admin(db, "  ") == []
    sport = next(s for s in admin.sports_view() if s["key"] == "football")
    assert sport["logos"] is True
    admin.set_logos("football", False)
    assert next(s for s in admin.sports_view() if s["key"] == "football")["logos"] is False
    with pytest.raises(LookupError):
        admin.set_logos("quidditch", True)


def test_the_api_serves_logos_and_looks_from_our_own_origin(db, files):
    barca = team(db, "83", "FC Barcelona", BARCA)
    logos.refresh(NOW, pause=0)
    digest = db.get(TeamLogo, barca.id).digest  # pyright: ignore[reportOptionalMemberAccess]
    client = TestClient(server.app)
    got = client.get(f"/api/logos/{digest}.png")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and "immutable" in got.headers["cache-control"]
    assert got.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/logos/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get(f"/api/logos/{'0' * 32}.png").status_code == 404
    looks = client.get("/api/looks", params={"teams": "sportmonks_football:83,sportmonks_football:1"}).json()["looks"]
    assert looks["sportmonks_football:83"]["logo"] == f"/api/logos/{digest}.png" and "sportmonks_football:1" not in looks
    controls.turn_logos("football", False)
    assert client.get(f"/api/logos/{digest}.png").status_code == 404
    assert db.scalar(select(Team.logo_url).where(Team.id == barca.id)) == BARCA


def test_a_picture_teams_share_stops_being_served_when_any_of_them_is_taken_down(db, files):
    """One flag for a country's sides (men's, women's): taking one down takes the picture down."""
    men = team(db, "10", "India", BARCA, sport="football")
    team(db, "11", "India Women", BARCA, sport="football")
    logos.refresh(NOW, pause=0)
    digest = db.get(TeamLogo, men.id).digest  # pyright: ignore[reportOptionalMemberAccess]
    assert logos.serve(db, f"{digest}.png") is not None
    controls.hide_logo("sportmonks_football:11", True)
    assert logos.serve(db, f"{digest}.png") is None
    assert logos.looks(db, ["sportmonks_football:11"])["sportmonks_football:11"]["logo"] is None


def test_no_logo_is_fetched_for_a_source_or_a_sport_turned_off(db, files):
    team(db, "83", "FC Barcelona", BARCA)
    controls.turn_sport("football", False)
    assert logos.wanted(db, NOW) == []
    controls.turn_sport("football", True)
    controls.turn_source("sportmonks_football", False)
    assert logos.wanted(db, NOW) == []
    controls.turn_source("sportmonks_football", True)
    assert [t[2] for t in logos.wanted(db, NOW)] == [BARCA]


def test_a_download_that_goes_wrong_any_way_is_that_teams_and_the_run_goes_on(db, files, monkeypatch):
    team(db, "83", "FC Barcelona", BARCA)
    team(db, "3468", "Real Madrid", MADRID)

    def download(system: str, url: str) -> bytes:
        if url == BARCA:
            raise RuntimeError("a stream that broke off")  # not a Refused, not an httpx.HTTPError
        return files.served[url]

    monkeypatch.setattr(logos, "download", download)
    assert logos.refresh(NOW, pause=0) == {"asked": 2, "kept": 1, "failed": 1}
    failed = db.scalar(select(TeamLogo).join(Team, Team.id == TeamLogo.team_id).where(Team.external_id == "83"))
    assert failed is not None and failed.status == "failed" and "broke off" in (failed.reason or "")
    monkeypatch.setattr(logos, "RUN_MOST", -1)  # past its time: the rest wait for the next run
    assert logos.refresh(NOW + timedelta(days=2), pause=0)["asked"] == 0
