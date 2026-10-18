"""Push to the Android app (mobile/) through Firebase Cloud Messaging's HTTP v1 API: the second transport
beside Web Push (notify.py), which plans every push and hands each device's to its own transport.

The server sends straight to FCM with a Firebase service-account key (a JSON file, like the other
secret files), never through Expo's push service. Without a key the transport is off: the API says so
(GET /api/push answers "fcm": false), the app's devices aren't registered, and none already registered
are sent to or counted as failing.

Each push is an FCM data-only message (no notification block; "v": "2" in its data): the app's own messaging
service draws it (its notifications module, in mobile/modules/), even while the app is closed, so it can look
like the app rather than a line of text. Its data is strings only, every key the app draws with (BHA-290,
BHA-232):
- what it is ("type"): a moment, as a scorecard (notify.py), a followed game's live card while it's played, or
  that card's end once it's over (livecards.py);
- its tag (the game's, alerts.Push.tag, or "live-e{event}" for a live card), so a new one replaces the game's
  notification rather than stacking another; and FCM's collapse key, which replaces one still waiting for an
  offline phone, as Web Push's Topic does;
- its game as the app draws it, from its sport and its board (livecards.keys): the sport, its colours (the
  app's blue without one), the league, its match page, its sides and where it's at, whether it's live;
- for a moment, whether it makes a sound ("loud": a loud moment outside the device's quiet hours), its title
  and body as before (the fallback text), its big word and line, the story under it and the player's photo;
  for a live card, its score line, the progress bar and the fan's own players' lines.
A moment that makes a sound goes at high priority, to wake a dozing phone; everything else at normal. The app
draws a loud moment on its "key" channel and every other push on "other", without a sound (both public, so a
locked phone shows a score). The data is kept under FCM's 4 KB (fit).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from .config import APP_NAME

log = logging.getLogger("sports_follow.fcm")

KEY_FILE = Path(os.environ.get("SPORTS_FOLLOW_FCM_KEY_FILE", Path.home() / ".config" / "sports-follow" / "fcm-service-account.json"))
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# A data-only message names no notification channel: the app picks one by "loud" (a loud moment on "key", the
# rest on "other", a live card on "live": mobile/modules/stann-notifications, Channels.kt).
VERSION = "2"  # data["v"]: a message the app draws itself, from its data (1 was FCM's own notification)
# The app's blue and white on it: a push about no sport (the test push), or a sport without a colour of its own.
COLOUR, INK = "#1f5bef", "#ffffff"
# What every message says of its game (livecards.keys), as one about no game says it.
COMMON = {"sport": "", "colour": COLOUR, "ink": INK, "league": "", "match": "", "sides": "", "status": "", "live": "0"}
# The keys of notify's payload a moment's message carries: its own, and its game's (livecards.keys).
MOMENT = ("id", "url", "event_id", "player_id", "kind", "word", "line", "story", "photo", "sport", "colour", "ink", "league", "match", "sides", "status", "live")
# FCM takes 4,096 bytes of data. Its JSON (quotes, escapes and all, so more than FCM counts) is kept under this.
DATA_BYTES = 4000
# What a push answers for a refusal that is this server's (its key, or FCM off): not the device's fault,
# so notify._health doesn't count it against the device.
OURS = 401
# Seconds after Google or FCM refuses this server's key (or doesn't answer for one) that pushes go unsent,
# answering OURS at once, rather than each asking again: a batch's other pushes, browsers' among them,
# mustn't wait behind a sign-in that won't work, and one problem is one error in the log, not one a device.
PAUSE = 60

_client = httpx.Client(timeout=10)


@dataclass(frozen=True)
class Account:
    """The service account a Firebase project gives this server: who it is and the key it signs with."""

    project_id: str
    client_email: str
    private_key: str
    private_key_id: str
    token_uri: str


@lru_cache(maxsize=1)
def account() -> Account | None:
    """This server's Firebase service account, or None (and the transport is off) without its key file."""
    if not KEY_FILE.is_file():
        log.info("Android notifications are off: no Firebase service-account key at %s", KEY_FILE)
        return None
    try:
        key = json.loads(KEY_FILE.read_text())
        found = Account(key["project_id"], key["client_email"], key["private_key"], key.get("private_key_id", ""), key.get("token_uri") or GOOGLE_TOKEN_URI)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("Android notifications are off: %s isn't a Firebase service-account key (%s)", KEY_FILE, type(exc).__name__)
        return None
    return found


def enabled() -> bool:
    return account() is not None


# A device's FCM registration token (about 160 characters: an instance id, a colon, then the token itself).
# Never a URL, so it can't be mistaken for a Web Push endpoint in the same column.
TOKEN = re.compile(r"[A-Za-z0-9_:-]{32,1000}")


def valid_token(value: Any) -> bool:
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


# ---------------------------------------------------------------- access tokens


_access: dict[str, Any] = {}  # the OAuth access token and when it expires, or until when pushes pause (time.time())
_access_lock = threading.Lock()


class Paused(RuntimeError):
    """This server's key was refused less than PAUSE seconds ago (already logged)."""


def _jwt(acc: Account, now: int) -> str:
    """The signed assertion Google's token endpoint exchanges for an access token (RFC 7523)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()

    header = {"alg": "RS256", "typ": "JWT", **({"kid": acc.private_key_id} if acc.private_key_id else {})}
    claims = {"iss": acc.client_email, "scope": SCOPE, "aud": acc.token_uri, "iat": now, "exp": now + 3600}
    signing_input = f"{part(header)}.{part(claims)}"
    key = serialization.load_pem_private_key(acc.private_key.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("the service account's key isn't an RSA key")
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def access_token(acc: Account) -> str:
    """An access token for FCM, kept until a minute before it expires (they last an hour). One thread
    fetches it while a batch's other pushes wait. A failed fetch pauses pushes (Paused) for PAUSE seconds."""
    with _access_lock:
        now = time.time()
        if _access.get("token") and _access["expires"] - 60 > now:
            return _access["token"]
        if _access.get("paused_until", 0) > now:
            raise Paused()
        try:
            answer = _client.post(acc.token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _jwt(acc, int(now))})
            if answer.status_code != 200:
                raise RuntimeError(f"Google's token endpoint answered {answer.status_code}: {answer.text[:200]}")
            said = answer.json()
            _access.clear()
            _access.update(token=str(said["access_token"]), expires=now + int(said.get("expires_in", 3600)))
        except Exception:
            _pause_locked(now)
            raise
        return _access["token"]


def _pause_locked(now: float) -> None:
    _access.clear()
    _access["paused_until"] = now + PAUSE


def _pause() -> bool:
    """FCM refused this server's key: forget its access token, and send nothing for PAUSE seconds. Whether
    this call began the pause (pushes already sending with the refused token come back refused too)."""
    with _access_lock:
        now = time.time()
        if _access.get("paused_until", 0) > now:
            return False
        _pause_locked(now)
        return True


# ---------------------------------------------------------------- messages


def message(token: str, payload: dict[str, Any], ttl: int) -> dict[str, Any]:
    """The FCM message for one moment (notify's payload, already cut to fit): tagged with the game, at the
    priority its sound needs, with everything the app draws it with and opens on a tap. The line is the title
    where notify gave none (the test push)."""
    loud = bool(payload.get("loud", True)) and not payload.get("quiet")
    title = payload.get("title") or APP_NAME
    data = {"type": "moment", "tag": payload.get("tag") or (f"moment-{payload['id']}" if payload.get("id") else ""), "loud": "1" if loud else "0",
            "title": title, "body": payload.get("body") or "", "word": "", "line": title, "story": "", "photo": "",
            **{k: payload[k] for k in MOMENT if payload.get(k) is not None}}
    return data_message(token, data, ttl, urgent=loud)


def data_message(token: str, data: dict[str, Any], ttl: int, urgent: bool = False) -> dict[str, Any]:
    """A data-only FCM message the app draws itself (the module docstring): `data` as strings (a list as its JSON,
    an empty one as ""), with the version, and what a message about no game says (COMMON) where it says nothing,
    kept under FCM's 4 KB (fit). High priority only when `urgent` (a moment that makes a sound); collapsed by its
    tag."""
    strings = fit({"v": VERSION, **COMMON, **{k: _text(v) for k, v in data.items() if v is not None}})
    android: dict[str, Any] = {"priority": "high" if urgent else "normal", "ttl": f"{max(0, ttl)}s"}
    if strings.get("tag"):
        android["collapse_key"] = strings["tag"]
    return {"token": token, "data": strings, "android": android}


def _text(value: Any) -> str:
    """A data value as FCM takes it: a string; a list (sides, a story) as its JSON, "" for none."""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value else ""
    return str(value)


def fit(data: dict[str, str]) -> dict[str, str]:
    """A message's data at most DATA_BYTES as JSON: its story's oldest lines, then its players' last, dropped; then
    its body, title and line cut short on a whole character. notify._fit has already cut a moment's to fit a Web
    Push, so this is rarely needed."""
    def size() -> int:
        return len(json.dumps(data, ensure_ascii=False).encode())

    data = dict(data)
    for key in ("story", "players"):
        lines = json.loads(data[key]) if data.get(key) else []
        while lines and size() > DATA_BYTES:
            lines.pop()
            data[key] = _text(lines)
    for key in ("body", "title", "line"):
        over = size() - DATA_BYTES
        if over <= 0:
            break
        # A character cut saves at least its UTF-8 bytes in the JSON (escaping only adds), so this is enough.
        raw = data.get(key, "").encode()
        keep = len(raw) - over - len("…".encode())
        data[key] = raw[:keep].decode(errors="ignore").rstrip() + "…" if keep > 0 else ""
    return data


def push(token: str, payload: dict[str, Any], ttl: int) -> int | str | None:
    """One moment to one Android device (message). Returns as send() does."""
    return send(message(token, payload, ttl))


def send(msg: dict[str, Any]) -> int | str | None:
    """One ready message (message, data_message) to its device. Returns None when FCM took it, else what
    notify._health reads: 404 for a token FCM no longer knows (the app was uninstalled), 410 for one of another
    Firebase project (it never will work), OURS when the refusal is this server's own key, else FCM's status
    code (or the error, if it never answered)."""
    token = msg["token"]
    acc = account()
    if acc is None:
        return OURS
    try:
        bearer = access_token(acc)
    except Paused:
        return OURS
    except Exception as exc:
        log.error("fcm: no access token for %s, so no Android pushes for %ss: %s", acc.client_email, PAUSE, str(exc)[:200], extra={"push_service": "fcm"})
        return OURS
    try:
        answer = _client.post(SEND_URL.format(project=acc.project_id), headers={"authorization": f"Bearer {bearer}"}, json={"message": msg})
    except Exception as exc:  # a timeout or a refused connection is that device's failure, not the batch's
        log.warning("push to fcm %s… failed: %s", token[:8], exc, extra={"push_service": "fcm"})
        return str(exc)[:100]
    if answer.status_code == 200:
        return None
    code, said = _error(answer)
    if answer.status_code == 401 or code in ("PERMISSION_DENIED", "THIRD_PARTY_AUTH_ERROR"):
        if _pause():
            log.error("fcm: FCM refused %s's key (%s %s), so no Android pushes for %ss", acc.client_email, answer.status_code, code, PAUSE, extra={"push_service": "fcm", "status": answer.status_code})
        return OURS
    log.warning("push to fcm %s… failed (%s %s)", token[:8], answer.status_code, code, extra={"push_service": "fcm", "status": answer.status_code})
    if code == "UNREGISTERED":
        return 404
    if code == "SENDER_ID_MISMATCH":
        return 410
    if code == "INVALID_ARGUMENT" and "registration token" in said:
        # "The registration token is not a valid FCM registration token": never a device's, so it goes as
        # one FCM no longer knows does. Any other INVALID_ARGUMENT is our message's fault, so stays a failure.
        return 404
    return answer.status_code


def _error(answer: httpx.Response) -> tuple[str | None, str]:
    """FCM's reason for a refusal: its FcmError code (UNREGISTERED, SENDER_ID_MISMATCH, …), else the
    error's status (PERMISSION_DENIED, …); and its message."""
    try:
        error = answer.json().get("error") or {}
    except ValueError:
        return None, ""
    said = str(error.get("message") or "")
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"]), said
    return error.get("status"), said
