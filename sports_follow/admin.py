"""The admin panel (/admin): turn sports and sources off and on, set what each source may spend, and look
after accounts (sign one out everywhere, block it, delete it), on the running app.

It opens with a password of its own, not a fan's account, and a 6-digit code from an authenticator app
(TOTP, RFC 6238: Google Authenticator, 1Password, Authy…). Both live in one file,
SPORTS_FOLLOW_ADMIN_PASSWORD_FILE (else ~/.config/sports-follow/admin-password): the password's scrypt
hash, and the app's key. Without the file the panel is off and every /api/admin route is a 404; without a
key it asks for the password alone, and says so. Set either, or change it (which signs every admin out):

    .venv/bin/python -m sports_follow.admin password
    .venv/bin/python -m sports_follow.admin code        # the authenticator app's key, checked before it's kept

What the panel changes is kept by controls.py, within what the server's settings allow; the routes are
in server.py.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import logging
import os
import re
import secrets
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from . import accounts, controls, sports
from .adapters import ADAPTERS, ALL, KNOWN, budget
from .config import APP_NAME, SOURCES
from .models import Account, AccountSession, Follow, Player, PushSubscription

log = logging.getLogger("sports_follow.admin")

PASSWORD_PATH = Path.home() / ".config" / "sports-follow" / "admin-password"
MIN_PASSWORD = 12
SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # about 50 ms and 16 MB a try
COOKIE = "sf_admin"
COOKIE_PATH = "/api/admin"
SESSION_LIFE = 12 * 3600  # seconds an admin stays signed in, however busy
ATTEMPTS_PER_ADDRESS = 10  # tries a quarter of an hour from one client address
FAILURES_PER_HOUR = 30  # wrong passwords or codes an hour from everyone: past it the panel waits out the hour
CODE_STEP = 30  # seconds each authenticator code lasts
CODE_DIGITS = 6
CODE_DRIFT = 1  # steps either side a code still works, for a phone whose clock is a little off
PAGE = 50  # accounts a page


# ---------------------------------------------------------------- the password


def password_file() -> Path:
    return Path(os.environ.get("SPORTS_FOLLOW_ADMIN_PASSWORD_FILE") or PASSWORD_PATH)


@dataclass(frozen=True)
class Keys:
    """What the admin signs in with, as the file keeps it: the password's hash on its first line, and the
    authenticator app's key on a line "totp:<base32>", or none."""

    password: str
    totp: str | None = None

    def text(self) -> str:
        return f"{self.password}\n" + (f"totp:{self.totp}\n" if self.totp else "")


def stored() -> Keys | None:
    """The admin's keys, read on each use: a new password or code works at once, without a restart. None
    when there's no file, or its first line isn't a password's hash: the panel is off."""
    try:
        lines = [line.strip() for line in password_file().read_text().splitlines() if line.strip()]
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None
    if not lines or not lines[0].startswith("scrypt$"):
        return None
    totp = next((line[len("totp:"):] for line in lines[1:] if line.startswith("totp:")), None)
    if totp is not None:
        totp = "".join(totp.split()).upper().rstrip("=")  # pasted by hand, as `code` prints it, in fours
        try:
            base64.b32decode(totp + "=" * (-len(totp) % 8))
        except ValueError:
            # Shut, never open: without its code the panel would take the password alone.
            log.error("admin panel: the authenticator key in %s isn't a key; the panel is shut until it is (python -m sports_follow.admin code)", password_file())
            return None
    return Keys(lines[0], totp or None)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """ "scrypt$16384$8$1$<salt>$<hash>", base64."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=64 * 1024 * 1024, dklen=32, **SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${b64(salt)}${b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        tried = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(tried, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_checking = threading.BoundedSemaphore(2)  # a burst of sign-ins waits its turn, rather than taking every core


def try_password(password: str, hashed: str) -> bool:
    """check_password, two at a time at most: each takes about 50 ms of a core and 16 MB."""
    with _checking:
        return check_password(password, hashed)


def fingerprint(keys: Keys) -> str:
    """What an admin session remembers of the keys it was opened with: a new password or code ends it."""
    return hashlib.sha256(keys.text().encode()).hexdigest()[:16]


def code_at(key: str, step: int) -> str:
    """The authenticator app's code for a 30-second step (RFC 6238 over RFC 4226: HMAC-SHA1, 6 digits)."""
    secret = base64.b32decode(key.upper() + "=" * (-len(key) % 8))
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    at = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[at:at + 4])[0] & 0x7FFFFFFF) % 10**CODE_DIGITS:0{CODE_DIGITS}d}"


def code_step(key: str, code: object, now: float | None = None) -> int | None:
    """The step a typed code belongs to (now, or CODE_DRIFT either side), or None if it's none of them.
    The caller refuses a step it has seen before, so a code works once."""
    # [0-9], not \d: Python's \d takes any script's digits ("١٢٣٤٥٦"), which compare_digest refuses with an
    # error, and an error only after a right password would tell a guesser the password was right.
    if not isinstance(code, str) or not re.fullmatch(rf"[0-9]{{{CODE_DIGITS}}}", code.strip()):
        return None
    step = int((time.time() if now is None else now) // CODE_STEP)
    found = None
    for s in range(step - CODE_DRIFT, step + CODE_DRIFT + 1):
        if hmac.compare_digest(code_at(key, s), code.strip()):
            found = s
    return found


def session_key(token: str) -> str:
    return f"admin-session:{hashlib.sha256(token.encode()).hexdigest()}"


# ---------------------------------------------------------------- sports


def sports_view() -> list[dict[str, Any]]:
    """Every sport: whether the server's settings let it run, whether the panel has it on, its sources, and
    the group it's one of (each esports game is a sport, under "Esports")."""
    off = controls.current().sports_off
    running = sports.ENABLED
    return [{
        "key": s.key,
        "label": s.label,
        "group": sports.BY_KEY[s.group].label if s.group in sports.BY_KEY else "",
        "allowed": s in sports.ALLOWED,
        "on": not sports.named(s, off),
        "running": s in running,
        "sources": list(dict.fromkeys(a.name for a in ADAPTERS.values() if s.key in a.sports)),
    } for s in sports.ALL]


def set_sport(key: str, on: bool) -> None:
    """Turn one sport off or on; one turned off with its whole group ("esports", in a row from before each
    game was a sport) leaves the group's others as they were."""
    sport = sports.BY_KEY.get(key)
    if sport is None or sport not in sports.ALL:
        raise LookupError(key)
    group = sports.BY_KEY.get(sport.group)
    controls.turn_sport(key, on, {group.key: [m.key for m in sports.members(group)]} if group else None)


# ---------------------------------------------------------------- sources and what they may spend


def _windows(b: budget.Budget) -> list[dict[str, Any]]:
    return [{"seconds": w.seconds, "requests": w.requests, "margin": w.margin} for w in b.windows]


def _limits(b: budget.Budget) -> dict[str, Any]:
    return {"windows": _windows(b), "reserve": b.reserve, "fan": b.fan, "pause": b.pause}


def sources_view(now: float | None = None) -> dict[str, Any]:
    """Every source this app knows, in order of preference, with why it does or doesn't run; and every
    allowance they spend: as declared, as the panel has it, where it stands now and what was spent."""
    off = controls.current().sources_off
    can = {a.system for a in ALL}
    running = ADAPTERS.now()
    sport_on = {s.key for s in sports.ENABLED}
    out = []
    for system, a in KNOWN.items():
        # A source of several sports of one group (esports games) is listed under the group.
        first, groups = a.sports[0], {sports.of(k).group for k in a.sports}
        sport = groups.pop() if len(a.sports) > 1 and len(groups) == 1 and "" not in groups else first
        out.append({
            "system": system,
            "name": a.name,
            "provider": a.provider.name,
            "covers": a.covers,
            "sport": sport,
            "sport_label": sports.BY_KEY[sport].label if sport in sports.BY_KEY else sport,
            "licensed": a.provider.licensed,
            "token": system in can,  # a licensed source needs its token
            "allowed": not SOURCES or system in SOURCES,
            "sport_on": bool(sport_on.intersection(a.sports)),
            "on": system not in off,
            "running": system in running,
            "budgets": [b.name for b in a.budgets()],
        })
    used = {(r["source"], r["lane"]): r for r in budget.usage(budget.known(), now)}
    budgets = []
    for b in budget.known():
        live = budget.effective(b)
        budgets.append({
            "name": b.name,
            "sources": list(dict.fromkeys(a.name for a in KNOWN.values() if b in a.budgets())),
            "running": any(b in a.budgets() for a in running.values()),
            "declared": _limits(b),
            "limits": _limits(live),
            "changed": live != b,
            "standing": budget.standing(b, now),
            "usage": [{"lane": lane, **{k: v for k, v in used[(b.name, lane)].items() if k not in ("source", "lane")}} for lane in budget.LANES if (b.name, lane) in used],
        })
    return {"sources": out, "budgets": budgets}


def set_source(system: str, on: bool) -> None:
    if system not in KNOWN:
        raise LookupError(system)
    controls.turn_source(system, on)


SPANS = (60, 3600, 86400)


def _whole(value: Any, low: int, high: int, what: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{what} must be a whole number from {low:,} to {high:,}.")
    return value


def checked_limits(name: str, body: dict[str, Any]) -> dict[str, Any]:
    """What the panel may set of a budget, checked: windows of a minute, an hour or a day (each at most
    once), each with its allowance and a margin under it; a reserve and fans' share each window can
    still pay for; a pause of a second to an hour; and a source whose plan has a limit keeps at least one
    window. Raises ValueError, saying what's wrong."""
    declared = next((b for b in budget.known() if b.name == name), None)
    if declared is None:
        raise LookupError(name)
    windows = body.get("windows")
    if not isinstance(windows, list) or len(windows) > len(SPANS):
        raise ValueError("Give the windows as a list: a minute, an hour or a day, each at most once.")
    seen: set[int] = set()
    out = []
    for w in windows:
        if not isinstance(w, dict) or w.get("seconds") not in SPANS or w["seconds"] in seen:
            raise ValueError("Each window is a minute, an hour or a day, each at most once.")
        seen.add(w["seconds"])
        requests = _whole(w.get("requests"), 1, 10_000_000, "A window's allowance")
        margin = _whole(w.get("margin", 0), 0, requests - 1, "A window's margin")
        out.append({"seconds": w["seconds"], "requests": requests, "margin": margin})
    out.sort(key=lambda w: w["seconds"])
    if declared.windows and not out:
        raise ValueError("Keep at least one window: its plan has a limit, and without one nothing would stop every lane spending past it.")
    room = min((w["requests"] - w["margin"] for w in out), default=10_000_001)  # what every window can spend
    reserve = _whole(body.get("reserve", 0), 0, room - 1, "The reserve (less than every window's allowance after its margin)")
    fan = body.get("fan")
    if fan is not None:
        fan = _whole(fan, 0, out[0]["requests"] if out else 10_000_000, "The fans' share (at most the first window's allowance)")
    pause = _whole(body.get("pause", 60), 1, 3600, "The pause after the source says slow down, in seconds,")
    return {"windows": out, "reserve": reserve, "fan": fan, "pause": pause}


def set_limits(name: str, body: dict[str, Any] | None) -> None:
    """Change what a source may spend (checked_limits), or go back to what it declares (None)."""
    if body is None:
        if name not in {b.name for b in budget.known()}:
            raise LookupError(name)
        controls.set_budget(name, None)
        return
    controls.set_budget(name, checked_limits(name, body))


# ---------------------------------------------------------------- accounts


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def overview(db: Session) -> dict[str, Any]:
    """How many fans there are: accounts (signed in this week, blocked), anonymous fans following
    someone, and devices with alerts on."""
    week = datetime.now(timezone.utc) - timedelta(days=7)
    seen = select(AccountSession.account_id).where(AccountSession.last_seen_at >= week)
    return {
        "accounts": db.scalar(select(func.count()).select_from(Account)) or 0,
        "active_week": db.scalar(select(func.count()).select_from(Account).where(or_(Account.last_sign_in_at >= week, Account.id.in_(seen)))) or 0,
        "blocked": db.scalar(select(func.count()).select_from(Account).where(Account.blocked_at.is_not(None))) or 0,
        "anonymous": db.scalar(select(func.count(func.distinct(Follow.fan_id))).where(Follow.fan_id.not_like(f"{accounts.FAN_PREFIX}%"))) or 0,
        "devices": db.scalar(select(func.count()).select_from(PushSubscription)) or 0,
    }


def account_page(db: Session, query: str = "", offset: int = 0) -> dict[str, Any]:
    """A page of accounts, newest first, or those whose address has `query` in it."""
    follows = select(func.count()).where(Follow.fan_id == Account.fan_id).scalar_subquery()
    devices = select(func.count()).where(PushSubscription.fan_id == Account.fan_id).scalar_subquery()
    seen = select(func.max(AccountSession.last_seen_at)).where(AccountSession.account_id == Account.id).scalar_subquery()
    stmt = select(Account, follows, devices, seen).order_by(Account.created_at.desc(), Account.id.desc())
    if query.strip():
        like = query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Account.email.like(f"%{like}%", escape="\\"))
    rows = db.execute(stmt.offset(offset).limit(PAGE + 1)).all()
    return {
        "accounts": [_row(a, f, d, s) for a, f, d, s in rows[:PAGE]],
        "next": offset + PAGE if len(rows) > PAGE else None,
    }


def _row(a: Account, follows: int, devices: int, seen: datetime | None) -> dict[str, Any]:
    return {
        "id": a.id,
        "email": a.email,
        "google": a.google_sub is not None,
        "created_at": _iso(a.created_at),
        "last_sign_in_at": _iso(a.last_sign_in_at),
        "last_seen_at": _iso(seen),
        "blocked_at": _iso(a.blocked_at),
        "players": follows,
        "devices": devices,
    }


def account(db: Session, account_id: int) -> dict[str, Any] | None:
    """One account: who they follow, their signed-in devices and the devices with alerts on."""
    a = db.get(Account, account_id)
    if a is None:
        return None
    players = db.execute(
        select(Player.id, Player.name, Player.sport, Follow.created_at).join(Follow, Follow.player_id == Player.id)
        .where(Follow.fan_id == a.fan_id).order_by(Follow.created_at.desc())
    ).all()
    sessions = db.execute(
        select(AccountSession.created_at, AccountSession.last_seen_at, AccountSession.expires_at)
        .where(AccountSession.account_id == a.id).order_by(AccountSession.last_seen_at.desc())
    ).all()
    devices = db.execute(
        select(PushSubscription.created_at, PushSubscription.last_ok_at, PushSubscription.timezone, PushSubscription.failures)
        .where(PushSubscription.fan_id == a.fan_id).order_by(PushSubscription.created_at.desc())
    ).all()
    seen = max((s.last_seen_at for s in sessions), default=None)
    return {
        **_row(a, len(players), len(devices), seen),
        "following": [{"id": p, "name": n, "sport": sports.label(s), "since": _iso(at)} for p, n, s, at in players],
        "sessions": [{"created_at": _iso(c), "last_seen_at": _iso(ls), "expires_at": _iso(e)} for c, ls, e in sessions],
        "alert_devices": [{"created_at": _iso(c), "last_ok_at": _iso(ok), "timezone": tz, "failures": f} for c, ok, tz, f in devices],
    }


def block(db: Session, account_id: int, blocked: bool) -> list[str] | None:
    """Block an account (it can't sign in, and every device signed in to it is signed out, its alerts with
    it) or let it back in. Returns the ended sessions' token hashes, for their cached copies; None if
    there's no such account. Signed out, a device is an anonymous fan, which no one can block."""
    if db.scalar(update(Account).where(Account.id == account_id).values(blocked_at=func.now() if blocked else None).returning(Account.id)) is None:
        return None
    return accounts.end_sessions(db, account_id) if blocked else []


def sign_out(db: Session, account_id: int) -> list[str] | None:
    """Sign every device out of the account (accounts.end_sessions); None if there's no such account."""
    if db.get(Account, account_id) is None:
        return None
    return accounts.end_sessions(db, account_id)


# ---------------------------------------------------------------- the command


def _save(keys: Keys) -> Path:
    path = password_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(keys.text())
    tmp.replace(path)
    return path


def set_password() -> int:
    first = getpass.getpass(f"New admin password (at least {MIN_PASSWORD} characters): ")
    if len(first) < MIN_PASSWORD:
        print(f"Too short: at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != first:
        print("The two didn't match.", file=sys.stderr)
        return 1
    had = stored()
    path = _save(Keys(hash_password(first), had.totp if had else None))
    print(f"Saved its hash to {path}. Every admin signed in before is signed out.")
    return 0


def set_code(ask: Any = input) -> int:
    """A new key for the authenticator app: shown once, and kept only after a code from the app checks out,
    so a key typed wrong into the app can't lock the panel."""
    had = stored()
    if had is None:
        print("Set the admin password first: python -m sports_follow.admin password", file=sys.stderr)
        return 1
    key = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    label = quote(f"{APP_NAME} admin")
    print("Add this key to your authenticator app (\"Enter a setup key\", time-based, 6 digits):\n")
    print("    " + " ".join(key[i:i + 4] for i in range(0, len(key), 4)) + "\n")
    print(f"or open this in an app that takes links (1Password):\n\n    otpauth://totp/{label}?secret={key}&issuer={quote(APP_NAME)}&digits={CODE_DIGITS}&period={CODE_STEP}\n")
    for _ in range(3):
        if code_step(key, ask("The code the app shows now: ").strip()) is not None:
            path = _save(Keys(had.password, key))
            print(f"Saved in {path}. Sign-in asks for a code from now on; every admin signed in before is signed out.")
            print("Send it to the server: deploy/push-secrets.sh deploy@<server-ip> (from a checkout at the latest main).")
            return 0
        print("That code doesn't match: check the key in the app and its clock, and try the next one.", file=sys.stderr)
    print("Nothing changed: the key wasn't kept.", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    commands = {"password": set_password, "code": set_code}
    if len(argv) != 1 or argv[0] not in commands:
        print(__doc__)
        return 2
    return commands[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
