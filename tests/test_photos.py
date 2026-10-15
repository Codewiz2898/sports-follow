"""Player photos (photos.py): which Commons licences are kept and how each is credited, from answers shaped as
the Commons API gives them (no network); and, against a throwaway database at alembic head, the job that
fetches followed and starter athletes' photos, writes them into cards, serves them and forgets them.
The database tests are skipped without one:

    SPORTS_FOLLOW_TEST_DATABASE_URL=postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow_test .venv/bin/python -m pytest tests/test_photos.py
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from sports_follow import credits, photos, pipeline, registry
from sports_follow.models import Follow, Photo, Player, PlayerCard, PlayerIdentity, Starter

from .test_api import api  # noqa: F401  (the fixture)

SAKA = "Bukayo Saka England v Ghana 23 June 2026-057 (cropped).jpg"
BUMRAH = "Jasprit Bumrah in PMO New Delhi.jpg"
PAGE = "https://commons.wikimedia.org/wiki/File:"


def info(file: str, *, licence: str = "CC BY-SA 4.0", code: str | None = "cc-by-sa-4.0", artist: str | None = '<a href="//commons.wikimedia.org/wiki/User:Berlination" title="User:Berlination">Bryan Berlin</a>', sha1: str = "aa", **meta) -> dict:
    """One file's imageinfo, as the Commons API answers it."""
    ext = {"LicenseShortName": {"value": licence}, "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}
    if code:
        ext["License"] = {"value": code}
    if artist:
        ext["Artist"] = {"value": artist}
    ext |= {k: {"value": v} for k, v in meta.items()}
    slug = file.replace(" ", "_")
    return {
        "thumburl": f"https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/{slug}/330px-{slug}?utm_source=commons.wikimedia.org&utm_content=thumbnail",
        "descriptionurl": PAGE + slug, "sha1": sha1, "mime": "image/jpeg", "extmetadata": ext,
    }


# ---------------------------------------------------------------- which licences, and how credited


def test_a_cc_by_sa_photo_is_kept_and_credited_to_its_author_as_recoloured():
    found = photos.judge(info(SAKA))
    assert (found.status, found.author, found.licence) == ("ok", "Bryan Berlin", "CC BY-SA 4.0")
    assert found.page == PAGE + SAKA.replace(" ", "_")
    assert found.thumb and found.thumb.endswith("330px-" + SAKA.replace(" ", "_")) and "?" not in found.thumb  # no tracking query
    photo = Photo(file=SAKA, status="ok", author=found.author, licence=found.licence, digest="0" * 32, media_type="image/jpeg", page=found.page)
    assert photos.credit(photo) == "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons, recoloured"
    assert photos.public(photo)["url"] == f"/api/photos/{'0' * 32}.jpg"


def test_a_licence_named_only_by_its_short_name_is_read_from_it():
    # Commons gives the Indian government's photos no licence code, only "GODL-India".
    found = photos.judge(info(BUMRAH, licence="GODL-India", code=None, artist="<bdi><a href='x'><span title='Office of the Prime Minister of India'>Prime Minister's Office</span></a>\n</bdi>"))
    assert (found.status, found.licence, found.author) == ("ok", "GODL-India", "Prime Minister's Office")


@pytest.mark.parametrize("licence, code", [("CC BY 2.0", "cc-by-2.0"), ("CC BY-SA 3.0 de", "cc-by-sa-3.0-de"), ("CC0", "cc0"), ("Public domain", "pd")])
def test_free_licences_are_kept(licence, code):
    assert photos.judge(info("x.jpg", licence=licence, code=code)).status == "ok"


def test_public_domain_is_written_in_words():
    assert photos.judge(info("x.jpg", licence="Public domain", code="pd")).licence == "public domain"


@pytest.mark.parametrize("licence, code", [("GFDL", "gfdl"), ("Attribution", "attribution"), ("Copyrighted free use", "copyrighted-free-use"), ("OGL 3", "ogl-3"), ("CC BY-NC 2.0", "cc-by-nc-2.0")])
def test_other_licences_are_left_out(licence, code):
    found = photos.judge(info("x.jpg", licence=licence, code=code))
    assert (found.status, found.reason, found.thumb) == ("unfree", f"licence not kept: {licence}", None)


def test_a_file_commons_marks_non_free_is_left_out():
    assert photos.judge(info("x.jpg", NonFree="true")).status == "unfree"


def test_a_photo_without_an_author_is_credited_to_an_unknown_one_and_a_long_name_is_cut():
    assert photos.judge(info("x.jpg", artist=None)).author == "unknown author"
    assert photos.judge(info("x.jpg", artist=None, Attribution="Bryan Berlin")).author == "Bryan Berlin"
    long = photos.judge(info("x.jpg", artist="word " * 40)).author or ""
    assert len(long) <= 100 and long.endswith("…")


def test_an_http_licence_link_becomes_https():
    raw = info("x.jpg")
    raw["extmetadata"]["LicenseUrl"]["value"] = "http://creativecommons.org/licenses/by-sa/3.0/"
    assert photos.judge(raw).licence_url == "https://creativecommons.org/licenses/by-sa/3.0/"


def test_an_answer_is_read_back_by_the_names_asked_for():
    """Commons writes underscores as spaces and capitalises the first letter, follows a renamed file to its
    new name, and marks a file it hasn't got missing."""
    answer = {"query": {
        "normalized": [{"from": "File:schalke_kobi02.jpg", "to": "File:Schalke kobi02.jpg"}],
        "redirects": [{"from": "File:Old name.jpg", "to": "File:New name.jpg"}],
        "pages": [
            {"title": "File:Schalke kobi02.jpg", "imageinfo": [info("Schalke kobi02.jpg", sha1="s")]},
            {"title": "File:New name.jpg", "imageinfo": [info("New name.jpg", sha1="n")]},
            {"title": "File:Gone.jpg", "missing": True},
        ],
    }}
    found = photos.read(answer, ["schalke_kobi02.jpg", "Old name.jpg", "Gone.jpg"])
    assert (found["schalke_kobi02.jpg"].sha1, found["Old name.jpg"].sha1) == ("s", "n")
    assert (found["Gone.jpg"].status, found["Gone.jpg"].reason) == ("missing", "not on Commons")


def test_only_wikimedias_own_hosts_are_downloaded_from():
    for url in ("https://example.com/x.jpg", "http://upload.wikimedia.org/x.jpg", "https://wikimedia.org.example.com/x.jpg"):
        with pytest.raises(ValueError):
            photos.download(url)


def test_a_photo_address_is_its_digest_and_kind():
    assert photos.NAME.fullmatch("0123456789abcdef0123456789abcdef.jpg")
    for name in ("../photo.jpg", "0123.jpg", "0123456789abcdef0123456789abcdef.svg", "0123456789ABCDEF0123456789ABCDEF.jpg"):
        assert not photos.NAME.fullmatch(name)


def test_the_credits_page_lists_each_photo_and_what_recolouring_means():
    shown = [{"name": "Bukayo Saka", "url": "/api/photos/x.jpg", "credit": "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons, recoloured", "page": PAGE + "x", "author": "Bryan Berlin", "licence": "CC BY-SA 4.0", "licence_url": "https://creativecommons.org/licenses/by-sa/4.0"}]
    page = credits.credits({}, stockfish=False, agent=False, photos=shown)
    assert page["photos"] == [{k: v for k, v in shown[0].items() if k != "url"}]
    assert "Wikimedia Commons" in [c["name"] for c in page["also"]]
    assert any("recolour" in n and "keeps its licence" in n for n in page["notices"])
    bare = credits.credits({}, stockfish=False, agent=False)
    assert bare["photos"] == [] and "Wikimedia Commons" not in [c["name"] for c in bare["also"]] and bare["notices"] == credits.NOTICES


# ---------------------------------------------------------------- the job, against Postgres

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")


@pytest.fixture
def db(monkeypatch):
    engine = create_engine(URL or "")
    with engine.begin() as c:
        c.execute(text("TRUNCATE athlete, player, follow, starter, photo RESTART IDENTITY CASCADE"))
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

    monkeypatch.setattr(photos, "session", unit)
    monkeypatch.setattr(photos, "PAUSE", 0)
    s = made()
    s.published = {}
    monkeypatch.setattr(photos.bus, "store_card", lambda player_id, card: s.published.__setitem__(player_id, card))
    yield s
    s.close()
    engine.dispose()


class Commons:
    """The Commons API and its thumbnails, as the job reaches them: what each file is, and what was asked."""

    def __init__(self, monkeypatch):
        self.files: dict[str, dict] = {}
        self.asked: list[list[str]] = []
        self.downloads: list[str] = []
        monkeypatch.setattr(photos, "ask", self.ask)
        monkeypatch.setattr(photos, "download", self.download)

    def ask(self, files):
        self.asked.append(list(files))
        return photos.read({"query": {"pages": [{"title": f"File:{f}", "imageinfo": [self.files[f]]} if f in self.files else {"title": f"File:{f}", "missing": True} for f in files]}}, files)

    def download(self, url):
        self.downloads.append(url)
        return f"jpeg bytes of {url}".encode(), "image/jpeg"


def athlete(qid: str, sport: str, name: str, image: str | None, ids: dict | None = None) -> dict:
    return {"qid": qid, "sport": sport, "name": name, "aliases": [], "search_text": registry.search_text(name, []), "birth_date": None, "birth_precision": None,
            "country": None, "teams": [], "league": None, "title": None, "ids": ids or {}, "image": image, "sitelinks": 50, "current": True}


def follow(db, player_id: int, qid: str, name: str, sport: str = "Football") -> None:
    db.add(Player(id=player_id, slug=name.lower().replace(" ", "-"), name=name, sport=sport))
    db.flush()
    db.add_all([PlayerCard(player_id=player_id, status="ready", version=3, card={"player_id": player_id, "version": 3, "player": {"name": name, "sport": sport}}),
                PlayerIdentity(player_id=player_id, system="wikidata", external_id=qid, confidence=1.0), Follow(fan_id="fan", player_id=player_id, alert_rules={})])


@pytest.fixture
def commons(monkeypatch):
    return Commons(monkeypatch)


@pytest.fixture
def squad(db, commons):
    """Saka followed, Bumrah a cricket starter, Haaland followed under a licence we don't keep, Mbappé in the
    registry but neither followed nor offered, and an esports pro with no photo at all."""
    registry.upsert(db, [
        athlete("Q59306386", "football", "Bukayo Saka", SAKA), athlete("Q16227998", "cricket", "Jasprit Bumrah", BUMRAH, {"espn_cricket": "625383"}),
        athlete("Q28967995", "football", "Erling Haaland", "Haaland.jpg"), athlete("Q21621995", "football", "Kylian Mbappé", "Mbappe.jpg"),
        athlete("pandascore:1", "esports", "TenZ", None),
    ])
    follow(db, 1, "Q59306386", "Bukayo Saka")
    follow(db, 2, "Q28967995", "Erling Haaland")
    follow(db, 3, "pandascore:1", "TenZ", "Valorant")
    db.add(Starter(qid="Q16227998", sport="cricket", position=1))
    db.commit()
    commons.files = {SAKA: info(SAKA, sha1="saka1"), BUMRAH: info(BUMRAH, licence="GODL-India", code=None, artist="Prime Minister's Office"), "Haaland.jpg": info("Haaland.jpg", licence="GFDL", code="gfdl")}
    return db


def _photo(db, file: str) -> Photo | None:
    db.expire_all()
    return db.scalar(select(Photo).where(Photo.file == file))


@needs_db
def test_followed_and_starter_athletes_photos_are_fetched_once_and_written_into_cards(squad, commons):
    db = squad
    assert set(photos.wanted(db)) == {SAKA, BUMRAH, "Haaland.jpg"}  # not Mbappé: nobody follows or is offered him
    done = photos.refresh()
    assert done == {"asked": 3, "shown": 2, "left_out": 1, "cards": 1, "forgotten": 0}
    assert len(commons.asked) == 1 and len(commons.downloads) == 2  # one API request; Haaland's GFDL photo isn't downloaded
    saka = _photo(db, SAKA)
    assert saka and saka.status == "ok" and saka.author == "Bryan Berlin" and saka.data and saka.digest
    haaland = _photo(db, "Haaland.jpg")
    assert haaland and (haaland.status, haaland.reason, haaland.data) == ("unfree", "licence not kept: GFDL", None)

    db.expire_all()
    card = db.get(PlayerCard, 1)
    assert card and card.version == 4 and card.card["version"] == 4
    assert card.card["player"]["photo"] == photos.public(saka) and card.card["player"]["photo"]["credit"] == "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons, recoloured"
    assert db.published[1]["player"]["photo"]["url"] == f"/api/photos/{saka.digest}.jpg"
    assert "photo" not in db.get(PlayerCard, 2).card["player"] and "photo" not in db.get(PlayerCard, 3).card["player"]

    # The next run asks about nothing: every file was asked about just now.
    assert photos.refresh() == {"asked": 0, "shown": 0, "left_out": 0, "cards": 0, "forgotten": 0}
    assert len(commons.asked) == 1


@needs_db
def test_a_photo_is_served_from_our_copy_by_its_address(squad, commons):
    db = squad
    photos.refresh()
    saka = _photo(db, SAKA)
    assert saka
    assert photos.serve(db, f"{saka.digest}.jpg") == (saka.data, "image/jpeg")
    assert photos.serve(db, f"{saka.digest}.png") is None  # not its kind
    assert photos.serve(db, "0" * 32 + ".jpg") is None


@needs_db
def test_a_photo_is_asked_about_again_after_a_while_and_one_no_longer_free_leaves_the_card(squad, commons):
    db = squad
    photos.refresh()
    later = datetime.now(timezone.utc) + timedelta(days=photos.REFETCH_DAYS + 1)
    photos.refresh(now=later)  # the same upload: not downloaded again
    assert len(commons.downloads) == 2
    commons.files[SAKA] = info(SAKA, sha1="saka2")  # a new upload of the file
    photos.refresh(now=later + timedelta(days=photos.REFETCH_DAYS + 1))
    assert len(commons.downloads) == 3 and _photo(db, SAKA).source_sha1 == "saka2"

    commons.files[SAKA] = info(SAKA, licence="GFDL", code="gfdl")  # relicensed, or deleted: it may not show
    photos.refresh(now=later + timedelta(days=2 * photos.REFETCH_DAYS + 2))
    saka = _photo(db, SAKA)
    assert saka and (saka.status, saka.data, saka.digest) == ("unfree", None, None)
    db.expire_all()
    assert "photo" not in db.get(PlayerCard, 1).card["player"] and "photo" not in db.published[1]["player"]


@needs_db
def test_a_failed_download_is_tried_again_the_next_day(squad, commons, monkeypatch):
    db = squad

    def refused(url):
        raise ValueError("thumbnail: 404")

    monkeypatch.setattr(photos, "download", refused)
    photos.refresh()
    assert (_photo(db, SAKA).status, _photo(db, SAKA).reason) == ("failed", "thumbnail: 404")
    monkeypatch.setattr(photos, "download", commons.download)
    photos.refresh(now=datetime.now(timezone.utc) + timedelta(hours=1))
    assert _photo(db, SAKA).status == "failed"
    photos.refresh(now=datetime.now(timezone.utc) + timedelta(days=1, hours=1))
    assert _photo(db, SAKA).status == "ok"


@needs_db
def test_commons_busy_leaves_the_rest_for_the_next_run(squad, commons, monkeypatch):
    def busy(files):
        raise photos.Unavailable("Commons API: 429")

    monkeypatch.setattr(photos, "ask", busy)
    assert photos.refresh()["asked"] == 0
    assert _photo(squad, SAKA) is None


@needs_db
def test_photos_nobody_wants_are_forgotten_after_a_month(squad, commons):
    db = squad
    photos.refresh()
    db.execute(text("DELETE FROM follow WHERE player_id = 1"))
    db.commit()
    assert photos.refresh(now=datetime.now(timezone.utc) + timedelta(days=1))["forgotten"] == 0
    assert photos.refresh(now=datetime.now(timezone.utc) + timedelta(days=photos.FORGET_DAYS + 1))["forgotten"] == 1
    assert _photo(db, SAKA) is None and _photo(db, BUMRAH) is not None


@needs_db
def test_new_cards_and_previews_carry_the_photo(squad, commons):
    """A player's profile shows it: their page, from a new card on, and the preview of an athlete picked in
    search, found by their id in the source (a starter's, here, fetched before anyone follows them)."""
    db = squad
    photos.refresh()
    assert photos.of_source(db, "espn_cricket", "625383") == photos.public(_photo(db, BUMRAH))
    assert photos.of_source(db, "espn_cricket", "1") is None
    saka = db.get(Player, 1)
    assert pipeline.placeholder_card(saka)["player"]["photo"] == photos.public(_photo(db, SAKA))
    assert "photo" not in pipeline.placeholder_card(db.get(Player, 3))["player"]


@needs_db
def test_a_rebuilt_card_keeps_the_players_photo(squad, commons):
    """The research agent's report knows nothing of photos: a rebuild from it carries the player's own."""
    db = squad
    photos.refresh()
    report = {"player": {"name": "Bukayo Saka", "sport": "Football", "status": "active", "teams": ["Arsenal F.C."]}, "live": {"is_live": False, "player_stats": []},
              "news": [], "upcoming": [], "recent_results": [], "season_stats": [], "sources": []}
    card = pipeline._card_from_report(db.get(Player, 1), report, 5, datetime.now(timezone.utc))
    assert card["player"]["photo"] == photos.public(_photo(db, SAKA)) and card["player"]["teams"] == ["Arsenal F.C."]


@needs_db
def test_a_player_in_an_athletes_second_sport_gets_their_photo_too(squad, commons):
    """Bumrah's chess page, say: the qid is his cricket player's, so this one carries it without an identity."""
    db = squad
    db.add(Player(id=4, slug="jasprit-bumrah-chess", name="Jasprit Bumrah", sport="Chess", wikidata_qid="Q16227998"))
    db.flush()
    db.add_all([PlayerCard(player_id=4, status="ready", version=1, card={"player_id": 4, "version": 1, "player": {"name": "Jasprit Bumrah", "sport": "Chess"}}),
                Follow(fan_id="fan", player_id=4, alert_rules={})])
    db.commit()
    photos.refresh()
    db.expire_all()
    assert db.get(PlayerCard, 4).card["player"]["photo"] == photos.public(_photo(db, BUMRAH))


@needs_db
def test_the_credits_page_lists_the_photos_in_use(squad, commons):
    db = squad
    photos.refresh()
    assert [(p["name"], p["licence"]) for p in photos.used(db)] == [("Bukayo Saka", "CC BY-SA 4.0"), ("Jasprit Bumrah", "GODL-India")]


# ---------------------------------------------------------------- the route


def test_the_api_serves_a_photo_for_a_year_and_nothing_else(api, monkeypatch):
    kept = {"0123456789abcdef0123456789abcdef.jpg": (b"jpeg bytes", "image/jpeg")}
    monkeypatch.setattr(photos, "serve", lambda db, name: kept.get(name))
    res = api.call("GET", "/api/photos/0123456789abcdef0123456789abcdef.jpg")
    assert (res.status_code, res.content, res.headers["content-type"]) == (200, b"jpeg bytes", "image/jpeg")
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable" and res.headers["x-content-type-options"] == "nosniff"
    assert api.call("GET", "/api/photos/ffffffffffffffffffffffffffffffff.jpg").status_code == 404
