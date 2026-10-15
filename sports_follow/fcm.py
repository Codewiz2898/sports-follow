"""Push to the Android app (mobile/) through Firebase Cloud Messaging's HTTP v1 API: the second transport
beside Web Push (notify.py), which plans every push and hands each device's to its own transport.

The server sends straight to FCM with a Firebase service-account key (a JSON file, like the other
secret files), never through Expo's push service. Without a key the transport is off: the API says so
(GET /api/push answers "fcm": false), the app's devices aren't registered, and none already registered
are sent to or counted as failing.

Each push is an FCM notification message, so Android shows it itself while the app is closed:
- tagged with the game (alerts.Plan.tag), so a new moment replaces that game's notification rather than
  stacking another, as a browser's does;
- on one of two channels the app makes (mobile/src/push.ts): "key" with sound, "quiet" without. A loud push
  outside the device's quiet hours goes on "key"; every other one arrives silently;
- with the notification's id and the player's page in its data, for the app to open on a tap.
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
# The Android app's notification channels (mobile/src/push.ts makes them with these ids).
LOUD_CHANNEL = "key"
QUIET_CHANNEL = "quiet"
# What a push answers for a refusal that is this server's (its key, or FCM off): not the device's fault,
# so notify._health doesn't count it against the device.
OURS = 401

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


_access: dict[str, Any] = {}  # the OAuth access token and when it expires (time.time())
_access_lock = threading.Lock()


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
    fetches it while a batch's other pushes wait."""
    with _access_lock:
        if _access.get("token") and _access["expires"] - 60 > time.time():
            return _access["token"]
        now = int(time.time())
        answer = _client.post(acc.token_uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _jwt(acc, now)})
        if answer.status_code != 200:
            raise RuntimeError(f"Google's token endpoint answered {answer.status_code}: {answer.text[:200]}")
        said = answer.json()
        _access.update(token=said["access_token"], expires=now + int(said.get("expires_in", 3600)))
        return _access["token"]


def _forget_access() -> None:
    with _access_lock:
        _access.clear()


# ---------------------------------------------------------------- messages


def message(token: str, payload: dict[str, Any], ttl: int) -> dict[str, Any]:
    """The FCM message for one push (notify's payload, already cut to fit): its notification, tagged with
    the game and on the channel its sound needs, and what the app needs on a tap. FCM's data is strings only."""
    loud = payload.get("loud", True) and not payload.get("quiet")
    tag = payload.get("tag") or (f"moment-{payload['id']}" if payload.get("id") else None)
    data = {k: str(payload[k]) for k in ("id", "url", "event_id", "player_id", "kind") if payload.get(k) is not None}
    notification: dict[str, Any] = {"channel_id": LOUD_CHANNEL if loud else QUIET_CHANNEL}
    android: dict[str, Any] = {"priority": "high" if loud else "normal", "ttl": f"{max(0, ttl)}s", "notification": notification}
    if loud:
        notification["sound"] = "default"  # before Android 8, which has no channels
    if tag:
        # The tag replaces the game's notification on the phone; the collapse key replaces one still
        # waiting for an offline phone, as Web Push's Topic does.
        notification["tag"] = data["tag"] = android["collapse_key"] = tag
    return {
        "token": token,
        "notification": {"title": payload.get("title") or APP_NAME, "body": payload.get("body") or ""},
        "data": data,
        "android": android,
    }


def push(token: str, payload: dict[str, Any], ttl: int) -> int | str | None:
    """One push to one Android device. Returns None when FCM took it, else what notify._health reads: 404
    for a token FCM no longer knows (the app was uninstalled), 410 for one of another Firebase project
    (it never will work), OURS when the refusal is this server's own key, else FCM's status code (or the
    error, if it never answered)."""
    acc = account()
    if acc is None:
        return OURS
    try:
        bearer = access_token(acc)
    except Exception as exc:
        log.error("fcm: no access token for %s: %s", acc.client_email, str(exc)[:200], extra={"push_service": "fcm"})
        return OURS
    try:
        answer = _client.post(SEND_URL.format(project=acc.project_id), headers={"authorization": f"Bearer {bearer}"}, json={"message": message(token, payload, ttl)})
    except Exception as exc:  # a timeout or a refused connection is that device's failure, not the batch's
        log.warning("push to fcm %s… failed: %s", token[:8], exc, extra={"push_service": "fcm"})
        return str(exc)[:100]
    if answer.status_code == 200:
        return None
    code = _error_code(answer)
    log.warning("push to fcm %s… failed (%s %s)", token[:8], answer.status_code, code, extra={"push_service": "fcm", "status": answer.status_code})
    if code == "UNREGISTERED":
        return 404
    if code == "SENDER_ID_MISMATCH":
        return 410
    if answer.status_code == 401 or code in ("PERMISSION_DENIED", "THIRD_PARTY_AUTH_ERROR"):
        _forget_access()  # the next push asks for a new token
        return OURS
    return answer.status_code


def _error_code(answer: httpx.Response) -> str | None:
    """FCM's reason for a refusal: its FcmError code (UNREGISTERED, SENDER_ID_MISMATCH, …), else the
    error's status (PERMISSION_DENIED, …)."""
    try:
        error = answer.json().get("error") or {}
    except ValueError:
        return None
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"])
    return error.get("status")
