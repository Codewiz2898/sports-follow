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

log = logging.getLogger("sports_follow.mail")

RESEND_URL = "https://api.resend.com/emails"
KEY_PATH = Path.home() / ".config" / "sports-follow" / "resend-key"
MAIL_FROM = os.environ.get("SPORTS_FOLLOW_MAIL_FROM", "")  # "Sports Follow <signin@your-domain>"
MAIL_LOG = os.environ.get("SPORTS_FOLLOW_MAIL_LOG") == "1"

_client = httpx.Client(timeout=15)


class MailError(Exception):
    pass


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


def send_code(email: str, code: str) -> None:
    subject = f"{code} is your Sports Follow code"
    text = (
        f"Your Sports Follow sign-in code is {code}\n\n"
        "Type it into the app to sign in. It works for 10 minutes.\n\n"
        "Didn't ask for it? You can ignore this email: nobody can sign in without the code.\n"
    )
    body = (
        '<div style="font-family:Helvetica,Arial,sans-serif;max-width:420px;margin:0 auto;padding:24px;color:#111">'
        '<p style="font-size:15px;margin:0 0 16px">Your Sports Follow sign-in code:</p>'
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
        answer = _client.post(RESEND_URL, headers={"authorization": f"Bearer {_key()}"}, json={"from": MAIL_FROM, "to": [email], "subject": subject, "text": text, "html": body})
    except httpx.HTTPError as exc:
        raise MailError(f"Resend: {exc}") from exc
    if answer.status_code >= 300:
        # The answer names the problem (a bad key, an unverified domain), never the recipient's address.
        log.warning("mail: Resend answered %s: %s", answer.status_code, answer.text[:200])
        raise MailError(f"Resend answered {answer.status_code}")
