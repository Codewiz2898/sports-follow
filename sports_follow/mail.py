"""Sign-in codes by email, sent through Resend's HTTPS API: DigitalOcean blocks SMTP ports on new
servers, and an HTTPS API needs nothing opened.

Without a key, development can log the code instead (SPORTS_FOLLOW_MAIL_LOG=1). A server answering over
HTTPS never does that: the code would be one log line from anyone's account.
"""

from __future__ import annotations

import html
import logging
import os
from functools import lru_cache
from pathlib import Path

import httpx

from .config import APP_NAME

log = logging.getLogger("sports_follow.mail")

RESEND_URL = "https://api.resend.com/emails"
KEY_PATH = Path.home() / ".config" / "sports-follow" / "resend-key"
# The address sign-in codes come from ("signin@your-domain"), sent as "<APP_NAME> <address>" so the name in
# the inbox follows a rename. One given as "Name <address>" is sent as it is.
MAIL_FROM = os.environ.get("SPORTS_FOLLOW_MAIL_FROM", "").strip()
MAIL_LOG = os.environ.get("SPORTS_FOLLOW_MAIL_LOG") == "1"

_client = httpx.Client(timeout=15)


class MailError(Exception):
    pass


class Undeliverable(MailError):
    """Resend won't send to this address (a domain it refuses, like example.com): the fan's to change,
    not something wrong with this server or worth trying again."""


@lru_cache(maxsize=1)
def _key() -> str | None:
    if os.environ.get("SPORTS_FOLLOW_RESEND_KEY"):
        return os.environ["SPORTS_FOLLOW_RESEND_KEY"].strip()
    path = Path(os.environ.get("SPORTS_FOLLOW_RESEND_KEY_FILE") or KEY_PATH)
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def configured(https: bool) -> bool:
    """Can this server send a sign-in code? (`https`: is the request it's for served over HTTPS.)"""
    return bool(_key() and MAIL_FROM) or (MAIL_LOG and not https)


def _sender() -> str:
    return MAIL_FROM if "<" in MAIL_FROM else f"{APP_NAME} <{MAIL_FROM}>"


def send_code(email: str, code: str) -> None:
    subject = f"{code} is your {APP_NAME} code"
    text = (
        f"Your {APP_NAME} sign-in code is {code}\n\n"
        "Type it into the app to sign in. It works for 10 minutes.\n\n"
        "Didn't ask for it? You can ignore this email: nobody can sign in without the code.\n"
    )
    body = (
        '<div style="font-family:Helvetica,Arial,sans-serif;max-width:420px;margin:0 auto;padding:24px;color:#111">'
        f'<p style="font-size:15px;margin:0 0 16px">Your {html.escape(APP_NAME)} sign-in code:</p>'
        f'<p style="font-size:36px;font-weight:800;letter-spacing:6px;margin:0 0 16px">{html.escape(code)}</p>'
        '<p style="font-size:15px;margin:0 0 16px">Type it into the app to sign in. It works for 10 minutes.</p>'
        '<p style="font-size:13px;color:#666;margin:0">Didn\'t ask for it? You can ignore this email: nobody can sign in without the code.</p>'
        "</div>"
    )
    if not (_key() and MAIL_FROM):
        if not MAIL_LOG:
            raise MailError("no mail key")
        log.warning("Sign-in code for %s: %s (SPORTS_FOLLOW_MAIL_LOG; nothing was emailed)", email, code)
        return
    try:
        answer = _client.post(RESEND_URL, headers={"authorization": f"Bearer {_key()}"}, json={"from": _sender(), "to": [email], "subject": subject, "text": text, "html": body})
    except httpx.HTTPError as exc:
        raise MailError(f"Resend: {exc}") from exc
    if answer.status_code >= 300:
        # The answer names the problem (a bad key, an unverified domain, an address it won't send to),
        # never the recipient's address.
        refused = _refuses_recipient(answer)
        log.log(logging.INFO if refused else logging.WARNING, "mail: Resend answered %s: %s", answer.status_code, answer.text[:200])
        raise (Undeliverable if refused else MailError)(f"Resend answered {answer.status_code}")


def _refuses_recipient(answer: httpx.Response) -> bool:
    """Whether Resend refused the address itself: a 422 "Invalid `to` field" (a domain it won't send to,
    like example.com). Anything else about our request (a missing `to`, too many) is ours to fix, so stays
    a server error the API's alert sees."""
    if answer.status_code != 422:
        return False
    try:
        said = answer.json()
    except ValueError:
        return False
    return isinstance(said, dict) and str(said.get("message", "")).startswith("Invalid `to` field")
