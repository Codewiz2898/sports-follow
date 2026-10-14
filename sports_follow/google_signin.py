"""Continue with Google: OpenID Connect's authorization-code flow, done by the server (PKCE, state, nonce).

Google's JavaScript button signs in through a popup, which an iPhone home-screen app can't reliably
hear back from, and its redirect mode is a cross-site POST that arrives without the device's
(SameSite=Lax) cookie, so what the device followed couldn't come into the account. Here the browser
goes to Google and comes back with a top-level GET, which carries the cookie.

The ID token comes straight from Google's token endpoint over TLS, in exchange for the code and this
server's client secret, so its claims are checked without fetching Google's signing keys (OpenID
Connect Core 3.1.3.7).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import httpx

log = logging.getLogger("sports_follow.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
CLIENT_ID = os.environ.get("SPORTS_FOLLOW_GOOGLE_CLIENT_ID", "")
SECRET_PATH = Path.home() / ".config" / "sports-follow" / "google-client-secret"
COOKIE = "sf_oauth"
COOKIE_PATH = "/api/auth/google"
LIFE = 600  # seconds from leaving for Google to coming back

_client = httpx.Client(timeout=15)


class GoogleError(Exception):
    pass


@lru_cache(maxsize=1)
def _secret() -> str | None:
    if os.environ.get("SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET"):
        return os.environ["SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET"].strip()
    path = Path(os.environ.get("SPORTS_FOLLOW_GOOGLE_CLIENT_SECRET_FILE") or SECRET_PATH)
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def configured() -> bool:
    return bool(CLIENT_ID and _secret())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def start(redirect_uri: str) -> tuple[str, str]:
    """Where to send the browser, and this attempt (state, nonce, PKCE verifier) packed for the cookie
    that remembers it until it's back: the server signs it (server.mac), since the verifier is a secret."""
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    remembered = _b64(json.dumps({"s": state, "n": nonce, "v": verifier, "t": int(time.time())}).encode())
    query = {
        "client_id": CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": "openid email",
        "state": state, "nonce": nonce, "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256", "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(query)}", remembered


def _exchange(code: str, verifier: str, redirect_uri: str) -> dict:
    """The code for tokens, at Google's token endpoint."""
    try:
        answer = _client.post(TOKEN_URL, data={
            "code": code, "client_id": CLIENT_ID, "client_secret": _secret(), "redirect_uri": redirect_uri,
            "grant_type": "authorization_code", "code_verifier": verifier,
        })
    except httpx.HTTPError as exc:
        raise GoogleError(f"token endpoint: {exc}") from exc
    if answer.status_code != 200:
        raise GoogleError(f"token endpoint answered {answer.status_code}: {answer.text[:200]}")
    return answer.json()


def finish(remembered: str | None, state: str, code: str, redirect_uri: str) -> tuple[str, str]:
    """Back from Google: check it's this attempt (`remembered`: what start packed, from the cookie, its
    signature checked), swap the code for the ID token and check its claims. Returns the Google
    account's id (sub) and its verified email address, lower case."""
    if not remembered:
        raise GoogleError("no sign-in under way on this device")
    attempt = json.loads(_unb64(remembered))
    if time.time() - attempt["t"] > LIFE:
        raise GoogleError("the sign-in took too long")
    if not state or not hmac.compare_digest(state, attempt["s"]):
        raise GoogleError("state doesn't match")
    token = str(_exchange(code, attempt["v"], redirect_uri).get("id_token") or "")
    try:
        claims = json.loads(_unb64(token.split(".")[1]))
    except (IndexError, ValueError) as exc:
        raise GoogleError("no readable ID token") from exc
    audience = claims.get("aud")
    if (audience if isinstance(audience, list) else [audience]) != [CLIENT_ID] or claims.get("iss") not in ISSUERS:
        raise GoogleError("the ID token isn't for this app")
    if float(claims.get("exp") or 0) < time.time():
        raise GoogleError("the ID token has expired")
    if not hmac.compare_digest(str(claims.get("nonce") or ""), attempt["n"]):
        raise GoogleError("nonce doesn't match")
    email = str(claims.get("email") or "").strip().lower()
    if not claims.get("sub") or not email or claims.get("email_verified") not in (True, "true"):
        raise GoogleError("Google hasn't verified this account's email address")
    return str(claims["sub"]), email
