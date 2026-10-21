"""Clips: short videos published from the app's Mac recorder (desktop/), shown in a game's feed (its match page),
its competition's (the tournament feed) and its players'.

Publishing, by a publishing key's holder (admins, for now):

  1. POST /api/clips with what the app knows (the match page's address, the moment, length and size): a clip
     row, and two upload URLs signed for a few minutes (clipstore.py), for the video and its poster frame.
  2. The app puts both files straight into the bucket: the server never carries a video.
  3. POST /api/clips/<id>/done: the files are checked (there, not too big) and the clip goes live.

What may be published depends on what it was recorded from (RIGHTS): a game's own window, or a file of one's
own game, yes; a browser window (a stream, which is the broadcaster's production) is kept on the device while
the app posts clips itself. Fans report clips; enough reports hide one until an admin looks.

Keys are made, listed and revoked here:

    .venv/bin/python -m sports_follow.clips key "Bharath's Mac"     # prints the key once
    .venv/bin/python -m sports_follow.clips keys
    .venv/bin/python -m sports_follow.clips revoke <id>
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import clipstore, match
from .adapters import KNOWN
from .models import Clip, ClipKey, ClipReport, Player

MAX_SECONDS = 70.0  # a 30 s clip with its half-speed replay and end card is ~44 s; a montage stops at 60
MAX_BYTES = 80 * 1024 * 1024
MAX_POSTER = 2 * 1024 * 1024
MAX_PLAYERS = 20
HIDE_AT = 5  # fans' reports that hide a clip until an admin looks
PAGE = 30
# What may be published, by what it was recorded from (BHA-369). A stream is its broadcaster's production:
# while the app's own admins post clips, those stay on the device.
RIGHTS = {"game_client": True, "file": True, "browser": False}
VIDEO_TYPE = "video/mp4"
POSTER_TYPE = "image/jpeg"


class Refused(Exception):
    """A request the clips can't take: its HTTP status and what to tell the app."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def store() -> clipstore.Store:
    if clipstore.STORE is None:
        raise Refused(503, "Clips aren't set up on this server (no bucket).")
    return clipstore.STORE


def video_key(slug: str) -> str:
    return f"clips/{slug}.mp4"


def poster_key(slug: str) -> str:
    return f"clips/{slug}.jpg"


# ---------------------------------------------------------------- publishing keys

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_key(db: Session, name: str) -> tuple[ClipKey, str]:
    """A new publishing key: its row, and the key itself, shown once and kept nowhere."""
    token = "slam_pk_" + secrets.token_urlsafe(24)
    row = ClipKey(name=name.strip()[:80] or "unnamed", token_hash=token_hash(token))
    db.add(row)
    db.flush()
    return row, token


def key_from(db: Session, token: str, now: datetime | None = None) -> ClipKey | None:
    """The key a request carries, if it's one and not revoked; its last use is noted."""
    if not token.startswith("slam_pk_"):
        return None
    row = db.scalar(select(ClipKey).where(ClipKey.token_hash == token_hash(token), ClipKey.revoked_at.is_(None)))
    if row is not None:
        row.last_used_at = now or datetime.now(timezone.utc)
    return row


def revoke(db: Session, key_id: int, now: datetime | None = None) -> bool:
    row = db.get(ClipKey, key_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or datetime.now(timezone.utc)
    return True


# ---------------------------------------------------------------- publishing

@dataclass
class Upload:
    clip: Clip
    video_put: str
    poster_put: str


def _number(body: dict[str, Any], field: str, low: float, high: float) -> float:
    value = body.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise Refused(400, f"{field} must be a number from {low:g} to {high:g}.")
    return float(value)


def _moment(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(json.dumps(value)) > 2000:
        raise Refused(400, "moment must be an object, under 2 kB.")
    return value


def create(db: Session, key: ClipKey, body: dict[str, Any], now: datetime | None = None) -> Upload:
    """A clip row waiting for its files, and the URLs to put them at. Refused when its source mayn't be
    published, it's too long or big, or its game isn't one a match page knows."""
    bucket = store()
    source = body.get("source")
    if source not in RIGHTS:
        raise Refused(400, f"source must be one of {', '.join(RIGHTS)}.")
    if not RIGHTS[source]:
        raise Refused(403, "Clips recorded from a browser (a broadcast) stay on your device for now: save or share them instead.")
    seconds = _number(body, "seconds", 1, MAX_SECONDS)
    size = int(_number(body, "bytes", 1, MAX_BYTES))
    width = int(_number(body, "width", 16, 4096))
    height = int(_number(body, "height", 16, 4096))
    caption = body.get("caption") or ""
    if not isinstance(caption, str) or len(caption) > 280:
        raise Refused(400, "caption must be text, at most 280 characters.")
    moment = _moment(body.get("moment"))

    # Its game, by its match page's address, read as that page reads it: an address no source named is refused.
    sport = competition = None
    game_title = ""
    address = body.get("match")
    if address is not None:
        system, _, game = str(address).partition("/")
        if system not in KNOWN or not match.ID.fullmatch(game):
            raise Refused(400, "match must be a match page's address: <source>/<game id>.")
        page, _ = match.view(db, None, system, game)
        if page is None:
            raise Refused(404, "No match page for that game.")
        sport = page.get("sport")
        competition = page.get("competition") or None
        game_title = (page.get("title") or "")[:300]
        address = f"{system}/{game}"

    asked = body.get("players") or []
    if not isinstance(asked, list) or len(asked) > MAX_PLAYERS or not all(isinstance(p, int) and not isinstance(p, bool) for p in asked):
        raise Refused(400, f"players must be a list of up to {MAX_PLAYERS} player ids.")
    players = sorted(set(db.scalars(select(Player.id).where(Player.id.in_(asked))))) if asked else []

    clip = Clip(slug=secrets.token_urlsafe(9), key_id=key.id, match=address, sport=sport, competition=competition,
                game_title=game_title, players=players, moment=moment, caption=caption.strip(), source=source,
                seconds=seconds, width=width, height=height, bytes=size, status="uploading",
                created_at=now or datetime.now(timezone.utc))
    db.add(clip)
    db.flush()
    return Upload(clip, bucket.put_url(video_key(clip.slug), VIDEO_TYPE), bucket.put_url(poster_key(clip.slug), POSTER_TYPE))


def _own(db: Session, key: ClipKey, slug: str) -> Clip:
    clip = db.scalar(select(Clip).where(Clip.slug == slug))
    if clip is None or clip.key_id != key.id:
        raise Refused(404, "No such clip of yours.")
    return clip


def finish(db: Session, key: ClipKey, slug: str, now: datetime | None = None) -> Clip:
    """The uploader says both files are up: checked, and the clip goes live; else it's failed (status), its
    files gone."""
    bucket = store()
    clip = _own(db, key, slug)
    if clip.status != "uploading":
        raise Refused(409, f"This clip is {clip.status}.")
    video = bucket.size(video_key(slug))
    poster = bucket.size(poster_key(slug))
    if not video or video > MAX_BYTES or not poster or poster > MAX_POSTER:
        # Kept as failed (not raised: the row's change must stick), its files gone; the app starts again.
        clip.status = "failed"
        bucket.delete(video_key(slug))
        bucket.delete(poster_key(slug))
        return clip
    clip.bytes = video
    clip.status = "live"
    clip.live_at = now or datetime.now(timezone.utc)
    return clip


def remove(db: Session, slug: str, key: ClipKey | None = None) -> bool:
    """Takes a clip down for good: its files deleted, its row kept as removed. By its uploader's key, or an admin."""
    clip = _own(db, key, slug) if key is not None else db.scalar(select(Clip).where(Clip.slug == slug))
    if clip is None:
        return False
    bucket = store()
    bucket.delete(video_key(slug))
    bucket.delete(poster_key(slug))
    clip.status = "removed"
    return True


def set_hidden(db: Session, slug: str, hidden: bool) -> Clip | None:
    """An admin hides a clip, or puts a hidden one back (its reports start again)."""
    clip = db.scalar(select(Clip).where(Clip.slug == slug))
    if clip is None or clip.status not in ("live", "hidden"):
        return None
    clip.status = "hidden" if hidden else "live"
    if not hidden:
        clip.reports = 0
        db.execute(delete(ClipReport).where(ClipReport.clip_id == clip.id))
    return clip


def report(db: Session, slug: str, fan: str, reason: str = "") -> bool | None:
    """A fan's report, once each: whether the clip is hidden now; None for no live clip."""
    clip = db.scalar(select(Clip).where(Clip.slug == slug, Clip.status == "live"))
    if clip is None:
        return None
    try:
        with db.begin_nested():
            db.add(ClipReport(clip_id=clip.id, fan_id=fan, reason=reason[:40]))
    except IntegrityError:
        return False  # reported already
    db.execute(update(Clip).where(Clip.id == clip.id).values(reports=Clip.reports + 1))
    db.refresh(clip)
    if clip.reports >= HIDE_AT:
        clip.status = "hidden"
        return True
    return False


# ---------------------------------------------------------------- reading

def line(moment: dict[str, Any]) -> str:
    """The moment in a line, as the Mac app writes it on the clip: "TY 21–18 GL · TY +4.4k gold · 26:00 · Game 2"."""
    if isinstance(moment.get("line"), str):
        return moment["line"][:160]
    parts: list[str] = []
    teams, kills = moment.get("teams"), moment.get("kills")
    if isinstance(kills, list) and len(kills) == 2:
        parts.append(f"{teams[0]} {kills[0]}–{kills[1]} {teams[1]}" if isinstance(teams, list) and len(teams) == 2 else f"{kills[0]}–{kills[1]}")
    for field in ("gold", "clock"):
        if isinstance(moment.get(field), str):
            parts.append(moment[field])
    if isinstance(moment.get("game"), int):
        parts.append(f"Game {moment['game']}")
    return " · ".join(parts)


def public(clip: Clip, bucket: clipstore.Store) -> dict[str, Any]:
    return {
        "id": clip.slug,
        "video": bucket.public_url(video_key(clip.slug)),
        "poster": bucket.public_url(poster_key(clip.slug)),
        "seconds": round(clip.seconds, 2),
        "width": clip.width,
        "height": clip.height,
        "caption": clip.caption,
        "line": line(clip.moment or {}),
        "moment": clip.moment or {},
        "match": clip.match,
        "sport": clip.sport,
        "competition": clip.competition,
        "game_title": clip.game_title,
        "players": clip.players or [],
        "published": clip.live_at.isoformat() if clip.live_at else None,
    }


def listed(db: Session, *, match_: str | None = None, competition: str | None = None, player: int | None = None,
           before: datetime | None = None, limit: int = PAGE) -> list[dict[str, Any]]:
    """Live clips, newest first: a game's (its feed), a competition's (the tournament feed), a player's, or all."""
    bucket = clipstore.STORE
    if bucket is None:
        return []
    q = select(Clip).where(Clip.status == "live")
    if match_ is not None:
        q = q.where(Clip.match == match_)
    if competition is not None:
        q = q.where(Clip.competition == competition)
    if player is not None:
        q = q.where(Clip.players.contains([player]))
    if before is not None:
        q = q.where(Clip.live_at < before)
    rows = db.scalars(q.order_by(Clip.live_at.desc(), Clip.id.desc()).limit(max(1, min(limit, PAGE))))
    return [public(c, bucket) for c in rows]


def one(db: Session, slug: str) -> dict[str, Any] | None:
    bucket = clipstore.STORE
    clip = db.scalar(select(Clip).where(Clip.slug == slug, Clip.status == "live"))
    return public(clip, bucket) if clip is not None and bucket is not None else None


def for_admin(db: Session, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Clips for the admin panel, newest first, whatever their status, with their reports."""
    bucket = clipstore.STORE
    q = select(Clip, ClipKey.name).outerjoin(ClipKey, ClipKey.id == Clip.key_id)
    if status:
        q = q.where(Clip.status == status)
    out = []
    for clip, by in db.execute(q.order_by(Clip.created_at.desc()).limit(limit)):
        item = public(clip, bucket) if bucket is not None else {"id": clip.slug, "line": line(clip.moment or {})}
        out.append({**item, "status": clip.status, "reports": clip.reports, "source": clip.source,
                    "by": by, "created": clip.created_at.isoformat()})
    return out


def keys(db: Session) -> list[dict[str, Any]]:
    return [{"id": k.id, "name": k.name, "created": k.created_at.isoformat(),
             "last_used": k.last_used_at.isoformat() if k.last_used_at else None,
             "revoked": k.revoked_at.isoformat() if k.revoked_at else None}
            for k in db.scalars(select(ClipKey).order_by(ClipKey.id))]


def main(argv: list[str]) -> int:
    from .db import session

    if argv[:1] == ["key"] and len(argv) == 2:
        with session() as db:
            row, token = new_key(db, argv[1])
            print(f"Publishing key {row.id} for {row.name!r}. Paste it into the Mac app; it isn't shown again:\n\n  {token}\n")
        return 0
    if argv == ["keys"]:
        with session() as db:
            for k in keys(db):
                print(f"{k['id']:>4}  {k['name']:<30} made {k['created'][:16]}  last used {(k['last_used'] or '-')[:16]}"
                      + (f"  REVOKED {k['revoked'][:16]}" if k["revoked"] else ""))
        return 0
    if argv[:1] == ["revoke"] and len(argv) == 2 and argv[1].isdigit():
        with session() as db:
            ok = revoke(db, int(argv[1]))
        print("Revoked." if ok else "No such key, or revoked already.")
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
