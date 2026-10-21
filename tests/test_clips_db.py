"""Clips on real rows (clips.py): a publishing key, a clip put up and checked, the feeds that show it, fans'
reports hiding it, an admin putting it back or taking it down. These run against a throwaway database at
alembic head and are skipped without one (test_accounts_db.py says how to make it).

Every test empties the tables first.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from sports_follow import clips, clipstore, match
from sports_follow.models import Clip, Player

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(URL)
    with engine.begin() as c:
        c.execute(text("TRUNCATE clip_report, clip, clip_key, player RESTART IDENTITY CASCADE"))
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

    yield unit
    engine.dispose()


@pytest.fixture
def bucket(monkeypatch):
    store = clipstore.MemoryStore()
    monkeypatch.setattr(clipstore, "STORE", store)
    return store


@pytest.fixture
def page(monkeypatch):
    """The match page a clip's game is read from: PARI's TY v GL, as stann.in shows it."""
    pages = {("pandascore_esports", "1753729"): {"sport": "dota2", "competition": "PARI Universe 2026", "title": "Team Yandex vs GamerLegion"}}
    monkeypatch.setattr(match, "view", lambda db, fan, system, game: (pages.get((system, game)), False))
    return pages


def _publish(db, bucket, key_id, *, at=NOW, video=b"v" * 1000, poster=b"p" * 100, **body):
    with db() as s:
        key = s.get(clips.ClipKey, key_id)
        up = clips.create(s, key, {"source": "game_client", "seconds": 44.0, "bytes": len(video), "width": 1386, "height": 900, **body})
        slug = up.clip.slug
        assert up.video_put.startswith("https://bucket.test/clips/") and up.video_put.endswith("signed-for=video/mp4")
    if video:
        bucket.put(clips.video_key(slug), video)
    if poster:
        bucket.put(clips.poster_key(slug), poster)
    with db() as s:
        clip = clips.finish(s, s.get(clips.ClipKey, key_id), slug, now=at)
        return slug, clip.status


def _key(db, name="Bharath's Mac"):
    with db() as s:
        row, token = clips.new_key(s, name)
        return row.id, token


def test_publish_check_and_feeds(db, bucket, page):
    key_id, token = _key(db)
    with db() as s:
        s.add_all([Player(id=7, slug="larl", name="Larl", sport="dota2"), Player(id=8, slug="noone", name="Noone", sport="dota2")])
    a, status = _publish(db, bucket, key_id, match="pandascore_esports/1753729", players=[7, 999],
                         moment={"teams": ["TY", "GL"], "kills": [21, 18], "clock": "26:00", "game": 2}, caption="What a fight", at=NOW - timedelta(minutes=5))
    assert status == "live"
    b, _ = _publish(db, bucket, key_id, source="file", moment={"kills": [10, 5], "clock": "15:22"})  # own game: no match
    with db() as s:
        clip = s.query(Clip).filter_by(slug=a).one()
        assert (clip.sport, clip.competition, clip.game_title, clip.players) == ("dota2", "PARI Universe 2026", "Team Yandex vs GamerLegion", [7])
        assert clips.key_from(s, token).last_used_at is not None
        everyone = clips.listed(s)
        game = clips.listed(s, match_="pandascore_esports/1753729")
        tournament = clips.listed(s, competition="PARI Universe 2026")
        larl = clips.listed(s, player=7)
        older = clips.listed(s, before=NOW - timedelta(minutes=1))
    assert [c["id"] for c in everyone] == [b, a]  # newest first
    assert [c["id"] for c in game] == [a] == [c["id"] for c in tournament] == [c["id"] for c in larl] == [c["id"] for c in older]
    shown = game[0]
    assert shown["video"] == f"https://clips.test/clips/{a}.mp4" and shown["poster"] == f"https://clips.test/clips/{a}.jpg"
    assert shown["line"] == "TY 21–18 GL · 26:00 · Game 2" and shown["caption"] == "What a fight"


def test_missing_or_big_files_fail_and_are_cleared(db, bucket, page):
    key_id, _ = _key(db)
    slug, status = _publish(db, bucket, key_id, poster=b"")
    assert status == "failed"
    assert bucket.size(clips.video_key(slug)) is None  # the half that came is gone too
    slug, status = _publish(db, bucket, key_id, video=b"v" * 10, poster=b"p" * (clips.MAX_POSTER + 1))
    assert status == "failed"
    with db() as s:
        assert clips.listed(s) == []
        with pytest.raises(clips.Refused) as again:
            clips.finish(s, s.get(clips.ClipKey, key_id), slug)
        assert again.value.status == 409


def test_unknown_game_is_refused(db, bucket, page):
    key_id, _ = _key(db)
    with db() as s, pytest.raises(clips.Refused) as refused:
        clips.create(s, s.get(clips.ClipKey, key_id), {"source": "game_client", "seconds": 30, "bytes": 1, "width": 100, "height": 100,
                                                        "match": "pandascore_esports/404"})
    assert refused.value.status == 404


def test_only_its_key_finishes_or_removes_it(db, bucket, page):
    mine, _ = _key(db)
    other, _ = _key(db, "someone else")
    slug, _ = _publish(db, bucket, mine)
    with db() as s, pytest.raises(clips.Refused) as refused:
        clips.remove(s, slug, s.get(clips.ClipKey, other))
    assert refused.value.status == 404
    with db() as s:
        assert clips.remove(s, slug, s.get(clips.ClipKey, mine))
        assert clips.listed(s) == []
    assert bucket.objects == {}


def test_reports_hide_it_and_an_admin_puts_it_back(db, bucket, page):
    key_id, _ = _key(db)
    slug, _ = _publish(db, bucket, key_id)
    with db() as s:
        assert clips.report(s, slug, "fan-1") is False
        assert clips.report(s, slug, "fan-1") is False  # once each
        assert s.query(Clip).filter_by(slug=slug).one().reports == 1
        for n in range(2, clips.HIDE_AT + 1):
            hidden = clips.report(s, slug, f"fan-{n}")
        assert hidden is True
        assert clips.listed(s) == [] and clips.one(s, slug) is None
        assert clips.report(s, slug, "fan-99") is None  # hidden: nothing to report
        assert [c["status"] for c in clips.for_admin(s)] == ["hidden"]
    with db() as s:
        assert clips.set_hidden(s, slug, False).status == "live"
    with db() as s:
        clip = s.query(Clip).filter_by(slug=slug).one()
        assert clip.reports == 0 and clips.one(s, slug) is not None
        assert clips.report(s, slug, "fan-1") is False  # its reports start again


def test_revoked_key_stops_at_once(db):
    key_id, token = _key(db)
    with db() as s:
        assert clips.key_from(s, token) is not None
        assert clips.revoke(s, key_id) and not clips.revoke(s, key_id)
    with db() as s:
        assert clips.key_from(s, token) is None
        assert clips.keys(s)[0]["revoked"] is not None
