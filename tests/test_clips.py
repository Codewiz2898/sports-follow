"""Clips without a database (clips.py, clipstore.py): the upload URLs' signatures, what's refused before a row
is made, and how a clip's moment reads."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from sports_follow import clips, clipstore
from sports_follow.models import ClipKey


def test_presign_matches_awss_own_example():
    """AWS's worked example of a presigned GET (S3 docs, "Authenticating Requests: Using Query Parameters")."""
    url = clipstore.presign(
        "GET", "examplebucket.s3.amazonaws.com", "/test.txt", access_key="AKIAIOSFODNN7EXAMPLE",
        secret="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", region="us-east-1", expires=86400,
        now=datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    assert "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request" in url


def test_bucket_put_url_is_path_style_and_signs_the_type():
    store = clipstore.BucketStore("https://acct.r2.cloudflarestorage.com", "slam-clips", "auto", "AK", "SK", "https://clips.example")
    url = store.put_url("clips/abc.mp4", "video/mp4")
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert parts.netloc == "acct.r2.cloudflarestorage.com"
    assert parts.path == "/slam-clips/clips/abc.mp4"
    assert q["X-Amz-SignedHeaders"] == ["content-type;host"]  # a PUT of anything but video/mp4 fails
    sized = parse_qs(urlsplit(store.put_url("clips/abc.mp4", "video/mp4", 1000)).query)
    assert sized["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]  # nor of another size
    assert q["X-Amz-Expires"] == [str(clipstore.SIGNED_FOR)]
    assert store.public_url("clips/abc.mp4") == "https://clips.example/clips/abc.mp4"


def test_store_settings_need_all_of_them(monkeypatch, tmp_path):
    key = tmp_path / "clips-key"
    key.write_text("AKID\nSECRET\n")
    monkeypatch.setenv("SPORTS_FOLLOW_CLIPS_KEY_FILE", str(key))
    for name in ("SPORTS_FOLLOW_CLIPS_ENDPOINT", "SPORTS_FOLLOW_CLIPS_BUCKET", "SPORTS_FOLLOW_CLIPS_PUBLIC_BASE"):
        monkeypatch.delenv(name, raising=False)
    assert clipstore.from_env() is None
    monkeypatch.setenv("SPORTS_FOLLOW_CLIPS_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("SPORTS_FOLLOW_CLIPS_BUCKET", "slam-clips")
    assert clipstore.from_env() is None  # no public base yet
    monkeypatch.setenv("SPORTS_FOLLOW_CLIPS_PUBLIC_BASE", "https://clips.example")
    store = clipstore.from_env()
    assert isinstance(store, clipstore.BucketStore)
    assert (store.access_key, store.secret, store.region) == ("AKID", "SECRET", "auto")


@pytest.fixture
def bucket(monkeypatch):
    store = clipstore.MemoryStore()
    monkeypatch.setattr(clipstore, "STORE", store)
    return store


def _body(**changes):
    return {"source": "game_client", "seconds": 44.0, "bytes": 30_000_000, "width": 1386, "height": 900, **changes}


@pytest.mark.parametrize("body, status", [
    (_body(source="browser"), 403),  # a broadcast: kept on the device
    (_body(source="camera"), 400),
    (_body(seconds=clips.MAX_SECONDS + 1), 400),
    (_body(bytes=clips.MAX_BYTES + 1), 400),
    (_body(width=True), 400),
    (_body(caption="x" * 281), 400),
    (_body(moment={"line": "x" * 3000}), 400),
    (_body(match="nosuchsource/123"), 400),
    (_body(match="pandascore_esports/../etc"), 400),
])
def test_refused_before_any_row(bucket, body, status):
    with pytest.raises(clips.Refused) as refused:
        clips.create(None, ClipKey(id=1), body)  # type: ignore[arg-type]  # refused before the database is asked
    assert refused.value.status == status


def test_no_bucket_no_publishing(monkeypatch):
    monkeypatch.setattr(clipstore, "STORE", None)
    with pytest.raises(clips.Refused) as refused:
        clips.create(None, ClipKey(id=1), _body())  # type: ignore[arg-type]
    assert refused.value.status == 503


def test_key_needs_its_prefix():
    assert clips.key_from(None, "Bearer whatever") is None  # type: ignore[arg-type]  # not even looked up


@pytest.mark.parametrize("moment, line", [
    ({"teams": ["TY", "GL"], "kills": [21, 18], "gold": "TY +4.4k gold", "clock": "26:00", "game": 2},
     "TY 21–18 GL · TY +4.4k gold · 26:00 · Game 2"),
    ({"kills": [10, 5], "clock": "15:22"}, "10–5 · 15:22"),  # read off Dota's top bar: no team names
    ({"line": "Triple kill on Ember Spirit · 24:10"}, "Triple kill on Ember Spirit · 24:10"),
    ({}, ""),
])
def test_moment_line(moment, line):
    assert clips.line(moment) == line


@pytest.fixture
def api(monkeypatch):
    """The API's clip routes, with a database session that's never asked anything."""
    from fastapi.testclient import TestClient

    from sports_follow import server

    monkeypatch.setattr(clipstore, "STORE", None)
    server.app.dependency_overrides[server.get_db] = lambda: iter([object()])
    yield TestClient(server.app)
    server.app.dependency_overrides.pop(server.get_db, None)


def test_publishing_needs_a_key(api):
    body = _body()
    assert api.post("/api/clips", json=body).status_code == 401
    assert api.post("/api/clips", json=body, headers={"Authorization": "Bearer not-a-key"}).status_code == 401
    assert api.post("/api/clips/abc/done", headers={"Authorization": "Basic xyz"}).status_code == 401


def test_no_bucket_lists_nothing(api):
    r = api.get("/api/clips", params={"match": "pandascore_esports/1753729"})
    assert r.status_code == 200 and r.json() == {"clips": []}
    assert api.get("/api/clips", params={"before": "yesterday"}).status_code == 400
