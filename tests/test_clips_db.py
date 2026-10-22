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

from sports_follow import clips, clipstore, match, pipeline
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
        assert up.video_put.startswith("https://bucket.test/clips/") and up.video_put.endswith(f"signed-for=video/mp4&length={len(video)}")
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


def test_reports_flag_it_for_an_admin_and_never_hide_it(db, bucket, page):
    key_id, _ = _key(db)
    slug, _ = _publish(db, bucket, key_id)
    with db() as s:
        assert clips.report(s, slug, "fan-1") is True
        assert clips.report(s, slug, "fan-1") is False  # once each
        for n in range(2, 12):
            clips.report(s, slug, f"fan-{n}")
    with db() as s:
        clip = s.query(Clip).filter_by(slug=slug).one()
        assert clip.reports == 11 and clip.status == "live"  # however many: an anonymous fan is a request
        assert clips.one(s, slug) is not None
        assert [(c["id"], c["reports"]) for c in clips.for_admin(s, "reported")] == [(slug, 11)]
    with db() as s:
        assert clips.set_hidden(s, slug, True).status == "hidden"
    with db() as s:
        assert clips.listed(s) == [] and clips.one(s, slug) is None
        assert clips.report(s, slug, "fan-99") is None  # hidden: nothing to report
        assert clips.set_hidden(s, slug, False).status == "live"
    with db() as s:
        assert s.query(Clip).filter_by(slug=slug).one().reports == 0  # cleared with it
        assert clips.report(s, slug, "fan-1") is True


def test_a_hidden_clip_is_off_its_public_address_until_its_put_back(db, bucket, page, monkeypatch):
    """BUG-126: hiding a clip took it off the app, but its video and poster stayed at their public address, so a link
    to them still played it (one hidden over a rights complaint, say). Hidden, its files are kept at a name only the
    store can work out; put back, they're where they were; removed, they're gone either way."""
    key_id, _ = _key(db)
    slug, _ = _publish(db, bucket, key_id)
    video, poster = clips.video_key(slug), clips.poster_key(slug)
    with db() as s:
        assert clips.set_hidden(s, slug, True).status == "hidden"
    assert bucket.size(video) is None and bucket.size(poster) is None  # nothing at bucket.public_url(video)
    assert bucket.size(bucket.hidden(video)) == 1000 and slug not in bucket.hidden(video)
    with db() as s:
        clips.set_hidden(s, slug, True)  # hidden twice: nothing more to move
        assert clips.set_hidden(s, slug, False).status == "live"
    assert (bucket.size(video), bucket.size(poster), len(bucket.objects)) == (1000, 100, 2)
    with db() as s:
        clips.set_hidden(s, slug, True)
    with db() as s:
        assert clips.remove(s, slug)
    assert bucket.objects == {}
    # The bucket failing on it: refused, and the clip stays as it was.
    other, _ = _publish(db, bucket, key_id)
    monkeypatch.setattr(bucket, "copy", lambda source, key: (_ for _ in ()).throw(OSError("the bucket is down")))
    with db() as s, pytest.raises(clips.Refused) as refused:
        clips.set_hidden(s, other, True)
    assert refused.value.status == 502
    with db() as s:
        assert clips.one(s, other) is not None and bucket.size(clips.video_key(other)) == 1000


def test_the_poster_is_signed_for_the_size_the_app_says(db, bucket, page):
    """BUG-126: the poster's upload URL was signed for its type alone, so a file of any size could be put there (its
    2 MB was checked only at done). The app says its size, and the URL is signed for that, as the video's is."""
    key_id, _ = _key(db)
    body = {"source": "file", "seconds": 10.0, "bytes": 1000, "width": 640, "height": 360}
    with db() as s:
        key = s.get(clips.ClipKey, key_id)
        assert clips.create(s, key, {**body, "poster_bytes": 500}).poster_put.endswith("signed-for=image/jpeg&length=500")
        assert clips.create(s, key, body).poster_put.endswith("signed-for=image/jpeg")  # an app from before says nothing
        for wrong in (clips.MAX_POSTER + 1, 0, True, "500"):
            with pytest.raises(clips.Refused) as refused:
                clips.create(s, key, {**body, "poster_bytes": wrong})
            assert refused.value.status == 400


def test_done_twice_is_fine(db, bucket, page):
    key_id, _ = _key(db)
    slug, _ = _publish(db, bucket, key_id)
    with db() as s:
        assert clips.finish(s, s.get(clips.ClipKey, key_id), slug).status == "live"  # an answer lost, asked again


def test_a_sport_turned_off_hides_its_clips(db, bucket, page, monkeypatch):
    key_id, _ = _key(db)
    dota, _ = _publish(db, bucket, key_id, match="pandascore_esports/1753729")
    own, _ = _publish(db, bucket, key_id)  # no match, no sport
    monkeypatch.setattr(clips.sports, "on", lambda key: key != "dota2")
    with db() as s:
        assert [c["id"] for c in clips.listed(s)] == [own]
        assert clips.one(s, dota) is None and clips.one(s, own) is not None


def test_removed_players_come_off_clips(db, bucket, page):
    key_id, _ = _key(db)
    with db() as s:
        s.add_all([Player(id=7, slug="larl", name="Larl", sport="dota2"), Player(id=8, slug="noone", name="Noone", sport="dota2")])
    slug, _ = _publish(db, bucket, key_id, players=[7, 8])
    with db() as s:
        clips.forget_players(s, [7])
    with db() as s:
        assert s.query(Clip).filter_by(slug=slug).one().players == [8]
        assert clips.listed(s, player=7) == []


def test_a_page_folded_into_another_keeps_its_clips(db, bucket, page):
    # BUG-226: a page folded into the one that holds its athlete (pipeline._fold: the same player found twice) left
    # its clips tagged with an id no page has, so they dropped out of the merged page's.
    key_id, _ = _key(db)
    with db() as s:
        s.add_all([Player(id=7, slug="larl", name="Larl", sport="dota2"), Player(id=8, slug="larl-dota", name="Larl", sport="dota2"),
                   Player(id=9, slug="noone", name="Noone", sport="dota2")])
    a, _ = _publish(db, bucket, key_id, players=[8, 9])
    b, _ = _publish(db, bucket, key_id, players=[7, 8])
    with db() as s:
        pipeline._fold(s, s.get(Player, 8), s.get(Player, 7))  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    with db() as s:
        assert {c.slug: c.players for c in s.query(Clip)} == {a: [7, 9], b: [7]}
        assert {c["id"] for c in clips.listed(s, player=7)} == {a, b}


def test_reports_move_with_a_sign_in(db, bucket, page):
    key_id, _ = _key(db)
    a, _ = _publish(db, bucket, key_id)
    b, _ = _publish(db, bucket, key_id)
    with db() as s:
        clips.report(s, a, "anon-1")
        clips.report(s, b, "anon-1")
        clips.report(s, a, "acct-1")  # the account had reported a already
    with db() as s:
        clips.merge(s, "anon-1", "acct-1")
    with db() as s:
        assert sorted(s.execute(text("SELECT c.slug, r.fan_id FROM clip_report r JOIN clip c ON c.id = r.clip_id")).all()) == sorted([(a, "acct-1"), (b, "acct-1")])


def test_revoked_key_stops_at_once(db):
    key_id, token = _key(db)
    with db() as s:
        assert clips.key_from(s, token) is not None
        assert clips.revoke(s, key_id) and not clips.revoke(s, key_id)
    with db() as s:
        assert clips.key_from(s, token) is None
        assert clips.keys(s)[0]["revoked"] is not None


def test_revoking_a_key_can_hide_its_clips_and_unfinished_uploads_are_cleared(db, bucket, page):
    """BUG-260: revoking a key (a lost laptop, a leaked key) left the clips put up with it live, and an upload under
    way with it kept its files in the bucket for good: nothing finished or cleared it. Revoking can hide its clips too;
    an upload that never reached done is cleared once its links have run out, and so is a file a link still running
    put back after its clip failed or was taken down."""
    key_id, _ = _key(db)
    other_key, _ = _key(db, "another Mac")
    live, _ = _publish(db, bucket, key_id)
    theirs, _ = _publish(db, bucket, other_key)
    body = {"source": "file", "seconds": 10.0, "bytes": 1000, "width": 640, "height": 360}
    with db() as s:
        stuck = clips.create(s, s.get(clips.ClipKey, key_id), body, now=NOW - timedelta(minutes=5)).clip.slug
        gone = clips.create(s, s.get(clips.ClipKey, other_key), body, now=NOW - timedelta(minutes=5)).clip.slug
    bucket.put(clips.video_key(stuck), b"x" * 1000)  # the app's PUT, never followed by done
    with db() as s:
        assert clips.remove(s, gone, s.get(clips.ClipKey, other_key))
    bucket.put(clips.poster_key(gone), b"y" * 10)  # put by its link after it was taken down
    with db() as s:
        assert clips.revoke(s, key_id) and clips.hide_from(s, key_id) == 1
    with db() as s:
        assert [c["id"] for c in clips.listed(s)] == [theirs]
        assert bucket.size(clips.video_key(live)) is None  # off its public address too
        assert clips.sweep(s, NOW) == 0  # their links still work: a file could come yet
    later = NOW + timedelta(seconds=clipstore.SIGNED_FOR)
    with db() as s:
        assert clips.sweep(s, later, count=True) == 2 and bucket.size(clips.video_key(stuck)) == 1000  # counted, nothing changed
        assert clips.sweep(s, later) == 2
    with db() as s:
        assert s.query(Clip).filter_by(slug=stuck).one().status == "failed"
        assert clips.sweep(s, later) == 0
    assert bucket.size(clips.video_key(stuck)) is None and bucket.size(clips.poster_key(gone)) is None
    assert sorted(bucket.objects) == sorted([clips.video_key(theirs), clips.poster_key(theirs), bucket.hidden(clips.video_key(live)), bucket.hidden(clips.poster_key(live))])


# ---------------------------------------------------------------- the routes, on the same rows


@pytest.fixture
def api(db, bucket, page):
    """The API on the test database: each request its own session, the fan and the admin given."""
    from fastapi.testclient import TestClient

    from sports_follow import server

    def session():
        with db() as s:
            yield s

    who = {"fan": "fan-1"}
    server.app.dependency_overrides[server.get_db] = session
    server.app.dependency_overrides[server.fan_id] = lambda: who["fan"]
    server.app.dependency_overrides[server.require_admin] = lambda: None
    client = TestClient(server.app)
    client.who = who  # type: ignore[attr-defined]
    yield client
    for dep in (server.get_db, server.fan_id, server.require_admin):
        server.app.dependency_overrides.pop(dep, None)


def test_the_routes_publish_list_report_and_moderate(api, db, bucket):
    made = api.post("/api/admin/clip-keys", json={"name": "Bharath's Mac"}).json()
    auth = {"Authorization": f"Bearer {made['key']}"}
    body = {"source": "game_client", "seconds": 44.0, "bytes": 1000, "width": 1386, "height": 900,
            "match": "pandascore_esports/1753729", "moment": {"kills": [10, 5], "clock": "15:22"}}
    assert api.post("/api/clips", json={**body, "source": "browser"}, headers=auth).status_code == 403
    up = api.post("/api/clips", json=body, headers=auth).json()
    assert up["video_put"].endswith("signed-for=video/mp4&length=1000") and up["poster_type"] == "image/jpeg"
    bucket.put(clips.video_key(up["id"]), b"v" * 1000)  # the app's PUTs
    bucket.put(clips.poster_key(up["id"]), b"p" * 10)
    live = api.post(f"/api/clips/{up['id']}/done", headers=auth).json()
    assert live["line"] == "10–5 · 15:22" and live["competition"] == "PARI Universe 2026"
    assert api.post(f"/api/clips/{up['id']}/done", headers=auth).status_code == 200  # again: still fine

    feed = api.get("/api/clips", params={"match": "pandascore_esports/1753729"}).json()["clips"]
    assert [c["id"] for c in feed] == [up["id"]]
    assert api.get("/api/clips", params={"before": feed[0]["published"].replace("+", " ")}).json() == {"clips": []}  # "+" unencoded
    assert api.get("/api/clips", params={"before": "2026-10-21T10:00:00"}).status_code == 400  # no zone
    assert api.get(f"/api/clips/{up['id']}").json()["id"] == up["id"]

    assert api.post(f"/api/clips/{up['id']}/report", json={"reason": "not mine"}).json() == {"reported": True}
    api.who["fan"] = "fan-2"  # type: ignore[attr-defined]
    api.post(f"/api/clips/{up['id']}/report", json={})
    listed = api.get("/api/admin/clips", params={"status": "reported"}).json()
    assert [(c["id"], c["reports"], c["by"]) for c in listed["clips"]] == [(up["id"], 2, "Bharath's Mac")]
    assert listed["set_up"] is True and listed["keys"][0]["name"] == "Bharath's Mac"
    assert api.post(f"/api/admin/clips/{up['id']}", json={"hidden": True}).json()["status"] == "hidden"
    assert api.get(f"/api/clips/{up['id']}").status_code == 404
    assert api.delete(f"/api/admin/clips/{up['id']}").status_code == 204
    assert bucket.objects == {}

    assert api.delete(f"/api/admin/clip-keys/{made['id']}").status_code == 204
    assert api.post("/api/clips", json=body, headers=auth).status_code == 401  # revoked: at once
    # Revoked with its clips hidden (a leaked key's): its live clips go off the app.
    second = api.post("/api/admin/clip-keys", json={"name": "a leaked key"}).json()
    up = api.post("/api/clips", json=body, headers={"Authorization": f"Bearer {second['key']}"}).json()
    bucket.put(clips.video_key(up["id"]), b"v" * 1000)
    bucket.put(clips.poster_key(up["id"]), b"p" * 10)
    assert api.post(f"/api/clips/{up['id']}/done", headers={"Authorization": f"Bearer {second['key']}"}).status_code == 200
    assert api.delete(f"/api/admin/clip-keys/{second['id']}", params={"hide": "true"}).status_code == 204
    assert api.get(f"/api/clips/{up['id']}").status_code == 404
    assert [c["status"] for c in api.get("/api/admin/clips", params={"status": "hidden"}).json()["clips"]] == ["hidden"]


def test_a_failed_upload_is_kept_failed_and_only_its_key_takes_it_down(api, db, bucket):
    mine = api.post("/api/admin/clip-keys", json={"name": "mine"}).json()["key"]
    other = api.post("/api/admin/clip-keys", json={"name": "other"}).json()["key"]
    body = {"source": "file", "seconds": 30.0, "bytes": 10, "width": 640, "height": 360}
    up = api.post("/api/clips", json=body, headers={"Authorization": f"Bearer {mine}"}).json()
    r = api.post(f"/api/clips/{up['id']}/done", headers={"Authorization": f"Bearer {mine}"})  # nothing was put
    assert r.status_code == 422
    with db() as s:
        assert s.query(Clip).filter_by(slug=up["id"]).one().status == "failed"  # committed before the 422
    second = api.post("/api/clips", json=body, headers={"Authorization": f"Bearer {mine}"}).json()
    assert api.delete(f"/api/clips/{second['id']}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert api.delete(f"/api/clips/{second['id']}", headers={"Authorization": f"Bearer {mine}"}).status_code == 204
    with db() as s:
        assert s.query(Clip).filter_by(slug=second["id"]).one().status == "removed"


def test_a_key_id_no_row_can_have_is_refused_before_it_reaches_the_database(api):
    """BUG-263: revoking a publishing key by an id past what its column holds reached Postgres, which failed on it: a 500."""
    answer = api.delete("/api/admin/clip-keys/99999999999999999999")
    assert answer.status_code == 422 and "less than or equal to 2147483647" in answer.text
