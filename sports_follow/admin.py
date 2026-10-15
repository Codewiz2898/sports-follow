"""The admin panel (/admin): turn sports and sources off and on, set what each source may spend, and look
after accounts (sign one out everywhere, block it, delete it), on the running app.

It opens with a password of its own, not a fan's account. Only its scrypt hash is kept, in
SPORTS_FOLLOW_ADMIN_PASSWORD_FILE (else ~/.config/sports-follow/admin-password); without one the panel
is off and every /api/admin route is a 404. Set it, or change it (which signs every admin out), with:

    .venv/bin/python -m sports_follow.admin password

What the panel changes is kept by controls.py, within what the server's settings allow; the routes are
in server.py.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from . import accounts, controls, sports
from .adapters import ADAPTERS, ALL, KNOWN, budget
from .config import SOURCES
from .models import Account, AccountSession, Follow, Player, PushSubscription

PASSWORD_PATH = Path.home() / ".config" / "sports-follow" / "admin-password"
MIN_PASSWORD = 12
SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # about 50 ms and 16 MB a try
COOKIE = "sf_admin"
COOKIE_PATH = "/api/admin"
SESSION_LIFE = 12 * 3600  # seconds an admin stays signed in, however busy
ATTEMPTS_PER_ADDRESS = 10  # tries a quarter of an hour from one client address
FAILURES_PER_HOUR = 30  # wrong passwords an hour from everyone: past it the panel waits out the hour
PAGE = 50  # accounts a page


# ---------------------------------------------------------------- the password


def password_file() -> Path:
    return Path(os.environ.get("SPORTS_FOLLOW_ADMIN_PASSWORD_FILE") or PASSWORD_PATH)


def stored_hash() -> str | None:
    """The password's hash, read on each use: a new one works at once, without a restart."""
    try:
        return password_file().read_text().strip() or None
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return None


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


def try_password(password: str, stored: str) -> bool:
    """check_password, two at a time at most: each takes about 50 ms of a core and 16 MB."""
    with _checking:
        return check_password(password, stored)


def fingerprint(stored: str) -> str:
    """What an admin session remembers of the password it was opened with: a new password ends it."""
    return hashlib.sha256(stored.encode()).hexdigest()[:16]


def session_key(token: str) -> str:
    return f"admin-session:{hashlib.sha256(token.encode()).hexdigest()}"


# ---------------------------------------------------------------- sports


def sports_view() -> list[dict[str, Any]]:
    """Every sport: whether the server's settings let it run, whether the panel has it on, and its sources."""
    off = controls.current().sports_off
    running = sports.ENABLED
    return [{
        "key": s.key,
        "label": s.label,
        "allowed": s in sports.ALLOWED,
        "on": s.key not in off,
        "running": s in running,
        "sources": list(dict.fromkeys(a.name for a in ADAPTERS.values() if a.sport == s.key)),
    } for s in sports.ALL]


def set_sport(key: str, on: bool) -> None:
    if key not in sports.BY_KEY:
        raise LookupError(key)
    controls.turn_sport(key, on)


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
        out.append({
            "system": system,
            "name": a.name,
            "provider": a.provider.name,
            "covers": a.covers,
            "sport": a.sport,
            "sport_label": sports.BY_KEY[a.sport].label if a.sport in sports.BY_KEY else a.sport,
            "licensed": a.provider.licensed,
            "token": system in can,  # a licensed source needs its token
            "allowed": not SOURCES or system in SOURCES,
            "sport_on": a.sport in sport_on,
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


def main(argv: list[str]) -> int:
    if argv[:1] != ["password"]:
        print(__doc__)
        return 2
    first = getpass.getpass(f"New admin password (at least {MIN_PASSWORD} characters): ")
    if len(first) < MIN_PASSWORD:
        print(f"Too short: at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != first:
        print("The two didn't match.", file=sys.stderr)
        return 1
    path = password_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(hash_password(first) + "\n")
    tmp.replace(path)
    print(f"Saved its hash to {path}. Every admin signed in before is signed out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
