"""The Android app's push transport (fcm.py), against a fake FCM: Google's token endpoint and FCM's
messages:send, answering as they do. tests/test_fcm_db.py sends planned moments through it to devices
registered in a real database."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sports_follow import fcm, notify

from .test_api import api  # noqa: F401  (the fixture)

ROOT = Path(__file__).resolve().parent.parent
PROJECT = "sports-follow-test"
TOKEN_URI = "https://oauth2.example.test/token"
EMAIL = "fcm-sender@sports-follow-test.iam.gserviceaccount.com"
TOKEN = "dGVzdC1pbnN0YW5jZQ:APA91bE" + "x" * 140  # the shape of a real one: an instance id, a colon, the token


def unpad(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@dataclass
class FakeFcm:
    """Google's token endpoint and FCM's send, for one Firebase project. It checks what a real one checks
    (the assertion's signature and claims, the bearer token) and answers for each device token as FCM
    would: `gone` ones are UNREGISTERED, `elsewhere` ones another project's, `made_up` ones no token at
    all, and with `revoked` the service account may no longer send."""

    key: rsa.RSAPrivateKey
    sent: list[dict] = field(default_factory=list)
    sign_ins: list[dict] = field(default_factory=list)
    asked: int = 0  # requests to the token endpoint, answered or not
    gone: set[str] = field(default_factory=set)
    elsewhere: set[str] = field(default_factory=set)
    made_up: set[str] = field(default_factory=set)
    revoked: bool = False
    failing: int | None = None  # every send answers this status
    token_endpoint_down: bool = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URI:
            return self.sign_in(request)
        if str(request.url) == f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send":
            return self.send(request)
        return httpx.Response(404)

    def sign_in(self, request: httpx.Request) -> httpx.Response:
        self.asked += 1
        if self.token_endpoint_down:
            return httpx.Response(503, text="unavailable")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
        header, claims, signature = form["assertion"].split(".")
        self.key.public_key().verify(unpad(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
        said = json.loads(unpad(claims))
        assert json.loads(unpad(header))["alg"] == "RS256"
        assert (said["iss"], said["scope"], said["aud"]) == (EMAIL, fcm.SCOPE, TOKEN_URI) and said["exp"] - said["iat"] == 3600
        self.sign_ins.append(said)
        return httpx.Response(200, json={"access_token": f"ya29.fake-{len(self.sign_ins)}", "expires_in": 3599, "token_type": "Bearer"})

    def send(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer ya29.fake-{len(self.sign_ins)}":
            return error(401, "UNAUTHENTICATED")
        message = json.loads(request.content)["message"]
        if self.revoked:
            return error(403, "PERMISSION_DENIED")
        if self.failing == 400:
            return error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "Invalid value at 'message.android.ttl'")
        if self.failing:
            return error(self.failing, "INTERNAL", "INTERNAL")
        if message["token"] in self.made_up:
            return error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "The registration token is not a valid FCM registration token")
        if message["token"] in self.gone:
            return error(404, "NOT_FOUND", "UNREGISTERED")
        if message["token"] in self.elsewhere:
            return error(403, "PERMISSION_DENIED", "SENDER_ID_MISMATCH")
        self.sent.append(message)
        return httpx.Response(200, json={"name": f"projects/{PROJECT}/messages/{len(self.sent)}"})


def error(code: int, status: str, fcm_code: str | None = None, message: str = "…") -> httpx.Response:
    details = [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": fcm_code}] if fcm_code else []
    return httpx.Response(code, json={"error": {"code": code, "message": message, "status": status, "details": details}})


def reset() -> None:
    fcm.account.cache_clear()
    fcm._access.clear()


@pytest.fixture
def fake_fcm(tmp_path, monkeypatch):
    """This server with a Firebase service-account key (written as the console downloads it), sending to
    a fake FCM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    path = tmp_path / "fcm-service-account.json"
    path.write_text(json.dumps({"type": "service_account", "project_id": PROJECT, "private_key_id": "k1", "private_key": pem, "client_email": EMAIL, "token_uri": TOKEN_URI}))
    fake = FakeFcm(key)
    monkeypatch.setattr(fcm, "KEY_FILE", path)
    monkeypatch.setattr(fcm, "_client", httpx.Client(transport=httpx.MockTransport(fake)))
    reset()
    yield fake
    reset()


@pytest.fixture
def no_fcm(tmp_path, monkeypatch):
    monkeypatch.setattr(fcm, "KEY_FILE", tmp_path / "missing.json")
    reset()
    yield
    reset()


GOAL = {"id": 31, "title": "Goal: Mohamed Salah", "body": "Egypt 1 – 0 South Africa · 23'", "url": "/player/7", "tag": "e1", "event_id": 1, "player_id": 7, "kind": "goal", "loud": True, "quiet": False}


# ---------------------------------------------------------------- the message


def test_a_push_is_a_data_message_the_app_draws_itself():
    loud = fcm.message(TOKEN, GOAL, 600)
    assert loud == {
        "token": TOKEN,
        # Strings only, as FCM takes them: everything the app draws the moment with, opens on a tap and counts the
        # open by. No notification block: Android would draw that itself, and the app's own wouldn't run.
        "data": {"v": "2", "type": "moment", "tag": "e1", "loud": "1", "title": "Goal: Mohamed Salah", "body": "Egypt 1 – 0 South Africa · 23'",
                 "id": "31", "url": "/player/7", "event_id": "1", "player_id": "7", "kind": "goal",
                 # Its scorecard where notify gave none (the test push): a moment of no game in the app's blue, its line its title.
                 "word": "", "line": "Goal: Mohamed Salah", "story": "", "photo": "",
                 "sport": "", "colour": "#1f5bef", "ink": "#ffffff", "league": "", "match": "", "sides": "", "status": "", "live": "0"},
        "android": {"priority": "high", "ttl": "600s", "collapse_key": "e1"},
    }
    # Quiet hours, or a moment that makes no sound: normal priority (FCM doesn't wake a dozing phone for it), and
    # the app draws it without a sound.
    for quiet in ({**GOAL, "quiet": True}, {**GOAL, "loud": False}):
        message = fcm.message(TOKEN, quiet, 600)
        assert (message["android"]["priority"], message["data"]["loud"]) == ("normal", "0")
    # A player's news, outside any game, has the player's tag; a push with no tag still has one per moment.
    news = fcm.message(TOKEN, {**GOAL, "tag": "p7", "event_id": None}, 60)
    assert (news["data"]["tag"], "event_id" in news["data"], news["android"]["collapse_key"]) == ("p7", False, "p7")
    assert fcm.message(TOKEN, {**GOAL, "tag": None}, 60)["android"]["collapse_key"] == "moment-31"


def test_a_moments_scorecard_goes_as_strings():
    drawn = {**GOAL, "sport": "football", "colour": "#12c46c", "ink": "#0b1a10", "league": "Africa Cup of Nations", "match": "/match/espn_soccer/1",
             "sides": [["EGY", "1"], ["RSA", "0"]], "status": "23'", "live": "1", "word": "Goal", "line": "Mohamed Salah", "story": ["Kick-off"], "photo": ""}
    data = fcm.message(TOKEN, drawn, 600)["data"]
    assert {k: data[k] for k in ("sport", "colour", "ink", "sides", "status", "live", "word", "line", "story", "photo")} == {
        "sport": "football", "colour": "#12c46c", "ink": "#0b1a10", "sides": '[["EGY","1"],["RSA","0"]]', "status": "23'", "live": "1",
        "word": "Goal", "line": "Mohamed Salah", "story": '["Kick-off"]', "photo": ""}
    # A list with nothing in it is "", as the app takes a missing key.
    assert fcm.message(TOKEN, {**drawn, "sides": [], "story": []}, 600)["data"]["sides"] == ""


def test_a_message_too_big_for_fcm_loses_its_story_before_its_text():
    story = ["क" * 80] * 3
    big = fcm.data_message(TOKEN, {"type": "moment", "title": "Goal", "body": "ब" * 1150, "line": "Mohamed Salah", "story": story}, 600)["data"]
    assert len(json.dumps(big, ensure_ascii=False).encode()) <= fcm.DATA_BYTES
    assert json.loads(big["story"]) == story[:1] and big["body"] == "ब" * 1150  # two story lines go; the body stays whole
    bigger = fcm.data_message(TOKEN, {"type": "moment", "title": "Goal", "body": "ब" * 1500, "story": story}, 600)["data"]
    assert bigger["story"] == "" and bigger["body"].endswith("…") and len(json.dumps(bigger, ensure_ascii=False).encode()) <= fcm.DATA_BYTES


def test_a_push_the_app_doesnt_draw_falls_back_to_its_quiet_channel():
    """A message FCM shows itself (one from an older server) on a channel the app hasn't made lands on the app's
    quiet channel, not on FCM's own fallback, which rings."""
    channels = (ROOT / "mobile" / "modules" / "stann-notifications" / "android" / "src" / "main" / "java" / "io" / "stann" / "notifications" / "Channels.kt").read_text()
    quiet = re.search(r'const val QUIET = "([^"]+)"', channels)
    assert quiet and f"defaultChannel: '{quiet[1]}'" in (ROOT / "mobile" / "app.config.ts").read_text()


def test_tokens_are_checked_before_they_are_stored():
    assert fcm.valid_token(TOKEN)
    for bad in ("short:token", "https://fcm.googleapis.com/fcm/send/" + "a" * 40, TOKEN + "\n", "x" * 1001, None, 7):
        assert not fcm.valid_token(bad)


# ---------------------------------------------------------------- sending


def test_the_server_signs_in_once_an_hour_and_sends_with_its_access_token(fake_fcm):
    assert fcm.push(TOKEN, GOAL, 600) is None
    assert fcm.push(TOKEN, GOAL, 600) is None
    assert len(fake_fcm.sign_ins) == 1 and [m["data"]["tag"] for m in fake_fcm.sent] == ["e1", "e1"]
    # A minute before it expires, the next push asks for a new one.
    fcm._access["expires"] = time.time() + 59
    assert fcm.push(TOKEN, GOAL, 600) is None
    assert len(fake_fcm.sign_ins) == 2 and len(fake_fcm.sent) == 3


def test_what_fcm_answers_is_what_the_devices_health_reads(fake_fcm):
    fake_fcm.gone.add("gone-" + TOKEN)
    fake_fcm.elsewhere.add("other-" + TOKEN)
    fake_fcm.made_up.add("madeup-" + TOKEN)
    assert fcm.push("gone-" + TOKEN, GOAL, 600) == 404  # uninstalled: the device is removed
    assert fcm.push("other-" + TOKEN, GOAL, 600) == 410  # another Firebase project's: it never will work
    assert fcm.push("madeup-" + TOKEN, GOAL, 600) == 404  # never a token at all: removed too
    fake_fcm.failing = 400
    assert fcm.push(TOKEN, GOAL, 600) == 400  # our message's fault: counted, but nobody's device is removed at once
    fake_fcm.failing = 500
    assert fcm.push(TOKEN, GOAL, 600) == 500  # FCM's own trouble: counted, removed only after MAX_FAILURES
    fake_fcm.failing = None
    fake_fcm.revoked = True
    assert fcm.push(TOKEN, GOAL, 600) == fcm.OURS  # this server's key: never the device's fault
    assert not fcm._pause()  # already paused: a push that was sending with the refused token logs nothing more
    fake_fcm.revoked = False
    assert fcm.push(TOKEN, GOAL, 600) == fcm.OURS and len(fake_fcm.sent) == 0  # paused, not asked again at once
    fcm._access["paused_until"] = time.time() - 1
    assert fcm.push(TOKEN, GOAL, 600) is None and len(fake_fcm.sign_ins) == 2  # then it signs in again


def test_a_failed_sign_in_pauses_android_pushes_rather_than_each_asking_again(fake_fcm, caplog):
    # A batch's pushes run at once, behind one sign-in: with Google down or the key revoked, each asking
    # again in turn (10 s apiece) held up every other push of the batch, browsers' too.
    fake_fcm.token_endpoint_down = True
    caplog.set_level(logging.INFO, "sports_follow")
    assert [fcm.push(TOKEN, GOAL, 600) for _ in range(5)] == [fcm.OURS] * 5
    assert fake_fcm.asked == 1
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1  # one problem, one error
    fake_fcm.token_endpoint_down = False
    fcm._access["paused_until"] = time.time() - 1  # PAUSE seconds on
    assert fcm.push(TOKEN, GOAL, 600) is None and fake_fcm.asked == 2


def test_a_failed_sign_in_or_revoked_key_isnt_counted_against_the_device():
    device = SimpleNamespace(failures=0)
    db = SimpleNamespace(get=lambda model, key: device, delete=lambda row: None, execute=lambda statement: None)
    notify._health(db, 7, fcm.OURS)
    assert device.failures == 0  # five of these would remove every Android device
    notify._health(db, 7, 500)
    assert device.failures == 1


def test_a_failed_push_names_neither_the_token_nor_the_fan(fake_fcm, caplog):
    fake_fcm.gone.add(TOKEN)
    caplog.set_level(logging.INFO, "sports_follow")
    assert fcm.push(TOKEN, GOAL, 600) == 404
    [line] = [r for r in caplog.records if r.name == "sports_follow.fcm"]
    assert (line.push_service, line.status) == ("fcm", 404) and TOKEN[10:] not in line.getMessage()


def test_a_device_gets_its_push_by_its_own_transport(fake_fcm, monkeypatch):
    web = []
    monkeypatch.setattr(notify, "push", lambda endpoint, p256dh, auth, payload, ttl, topic=None: web.append((endpoint, topic)) and None)
    long = {**GOAL, "body": "क" * 3000}
    assert notify.deliver((1, "webpush", "https://web.push.apple.com/a", "k", "a"), long, 600, "e1") is None
    assert notify.deliver((2, "fcm", TOKEN, None, None), long, 600, "e1") is None
    assert web == [("https://web.push.apple.com/a", "e1")]
    [sent] = fake_fcm.sent
    # Cut to fit as a Web Push is: FCM takes 4 KB.
    assert sent["token"] == TOKEN and sent["data"]["body"].endswith("…") and len(json.dumps(sent, ensure_ascii=False).encode()) < 4096
    assert notify._service((2, "fcm", TOKEN, None, None)) == "fcm" and notify._service((1, "webpush", "https://web.push.apple.com/a", "k", "a")) == "web.push.apple.com"


# ---------------------------------------------------------------- without a key


def test_without_a_key_the_transport_is_off_and_says_so(no_fcm, api, caplog):  # noqa: F811
    caplog.set_level(logging.INFO, "sports_follow")
    assert not fcm.enabled() and notify.transports() == ("webpush",)
    assert any("Android notifications are off" in r.getMessage() for r in caplog.records)
    assert api.call("GET", "/api/push").json()["fcm"] is False
    answer = api.call("POST", "/api/push/fcm", fan="fan", body={"token": TOKEN})
    assert answer.status_code == 503 and not [s for s in api.statements if getattr(s, "table", None) is not None and s.table.name == "push_subscription"]
    assert fcm.push(TOKEN, GOAL, 600) == fcm.OURS  # and if one were sent, it wouldn't count against the device


def test_with_a_key_the_api_says_so(fake_fcm, api):  # noqa: F811
    assert api.call("GET", "/api/push").json()["fcm"] is True and notify.transports() == ("webpush", "fcm")


@pytest.mark.parametrize("method, body", [
    ("POST", {}),
    ("POST", {"token": "short"}),
    ("POST", {"token": ["x"]}),
    ("POST", {"token": "https://evil.example/" + "a" * 40}),
    ("POST", {"token": TOKEN + "\x00"}),
    ("DELETE", {"token": None}),
    ("DELETE", {"token": "a/b" * 20}),
])
def test_a_malformed_token_is_refused_plainly(fake_fcm, api, method, body):  # noqa: F811
    answer = api.call(method, "/api/push/fcm", fan="fan", body=body)
    assert answer.status_code == 400 and answer.json()["detail"] == "That isn't an FCM token."
