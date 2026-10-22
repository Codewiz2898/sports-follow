"""Team logos (BHA-665): each team's logo from its source, kept and served by us, and the two colours the app draws its
side of a game in (look).

A source says where it keeps a team's logo (SourceTeam.logo, on one of its Adapter.logo_hosts: Sportmonks' image_path,
a national cricket side's being its flag; PandaScore's image_url), and the import keeps that address on the team
(Team.logo_url). The worker's logos job (every half hour) downloads the logos it hasn't got, PER_RUN a run: from that
host only, over HTTPS, MAX_BYTES at most, a PNG, JPEG, WebP or GIF. Each is drawn again as a PNG at most SIZE pixels
each way, so nothing of the source's file but its pixels is kept, and kept in Postgres (TeamLogo): every API process
serves the same bytes at /api/logos/<digest>.png, cached a year. Nothing is hot-linked: a fan's phone never asks a
source for anything. A logo is fetched again every REFETCH_DAYS, at once when the source's address for it changes,
and one that failed the next day.

A team's colours: an operator's (Team.colours: the admin panel's Teams tab, or `logos colours`), else read from its
logo as it's kept (palette, then auto): its most used colour that isn't a grey, with the next one as its accent; a
logo drawn in greys alone (black and white) is drawn on white. A team with no logo, or a logo too faint to read,
has none, and the app draws the game in its sport's colour. Whether a side's logo needs a disc to show on its colour
is the app's to say (web/src/look.ts), from the logo's own colours (tones).

Logos belong to their owners. Sportmonks' terms say they aren't licensed with its data; the app shows them only to
name a game's teams, as scores apps do (Bharath's call, 22 Oct 2026), and each can come down at once: the admin
panel turns a sport's logos off (controls.LOGOS_OFF) or takes one team's down (controls.LOGOS_HIDDEN), and then look()
gives no logo and its address serves nothing, so the app draws our badge (the team's short code) in its place. The
Credits page says whose they are (credits.LOGO_NOTICES).

    .venv/bin/python -m sports_follow.logos                                   # one run now, as the worker's job does
    .venv/bin/python -m sports_follow.logos hide <team>                       # take a team's logo down (its id, or system:team id)
    .venv/bin/python -m sports_follow.logos show <team>                       # and put it back
    .venv/bin/python -m sports_follow.logos colours <team> <fill> [accent]    # its colours ("#004d98"); "auto" for its logo's
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import logging
import re
import sys
import time
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import Session

from . import bus, controls, logs, sports
from .adapters import ADAPTERS, KNOWN
from .config import USER_AGENT
from .db import session
from .models import Team, TeamLogo

log = logging.getLogger("sports_follow.logos")

SIZE = 256  # the most pixels a logo is kept at each way: the match page shows it 112 across, 224 on a 2x phone
MAX_BYTES = 1_000_000  # a source's file at most
MAX_PIXELS = 4096 * 4096  # and its picture: a bigger one isn't decoded (a decompression bomb)
PER_RUN = 150
PAUSE = 0.3  # seconds between downloads
RUN_MOST = 800  # seconds a run goes on for: within its lock's 900, so two runs never fetch the same logos
HOPS = 3  # redirects a download follows, each to one of its source's logo hosts
REFETCH_DAYS = 30
RETRY = timedelta(days=1)
LOOKS_MOST = 80  # teams one looks() call answers for
LOCK = "logos"
FORMATS = {"PNG", "JPEG", "WEBP", "GIF"}
NAME = re.compile(r"([0-9a-f]{32})\.png")
HEX = re.compile(r"#[0-9a-f]{6}")
GREY = 40  # a colour whose channels are this close is a grey (white, black, silver)
OPAQUE = 200  # alpha from which a pixel is part of the logo
MIN_SHARE = 0.08  # of a logo, the least a colour covers to be its team's
SAME = 90  # colours closer than this (the sum of the channels' differences) are one

# Redirects are followed by hand (download), each checked before it's asked: never anywhere but a source's logo hosts.
_client = httpx.Client(headers={"user-agent": USER_AGENT}, timeout=20, follow_redirects=False)


class Refused(ValueError):
    """A logo we won't keep: not on its source's host, too big, not an image."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- reading a logo


def allowed(system: str, url: str) -> bool:
    """Whether this address is one of its source's logo hosts (Adapter.logo_hosts), over HTTPS on its own port."""
    adapter = KNOWN.get(system)
    try:
        parsed = urlparse(url or "")
        port = parsed.port
    except ValueError:  # a port that isn't a number
        return False
    return bool(adapter and parsed.scheme == "https" and port in (None, 443) and not parsed.username and not parsed.password
                and (parsed.hostname or "") in getattr(adapter, "logo_hosts", ()))


def download(system: str, url: str) -> bytes:
    """A logo's file, from its source's logo hosts only, MAX_BYTES at most. A redirect is followed (HOPS at most) only
    to another address on them, checked before it's asked: nothing is ever asked of any other host."""
    for _ in range(HOPS + 1):
        if not allowed(system, url):
            raise Refused(f"not one of {system}'s logo hosts: {url}")
        with _client.stream("GET", url) as res:
            if res.is_redirect:
                url = str(res.url.join(res.headers.get("location", "")))
                continue
            if res.status_code != 200:
                raise Refused(f"HTTP {res.status_code}")
            body = bytearray()
            for chunk in res.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise Refused(f"over {MAX_BYTES} bytes")
        if not body:
            raise Refused("0 bytes")
        return bytes(body)
    raise Refused(f"over {HOPS} redirects")


def redraw(data: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """A logo's file drawn again as a PNG at most SIZE pixels each way, with its colours (palette). Refused if it
    isn't a PNG, JPEG, WebP or GIF, or is bigger than MAX_PIXELS."""
    try:
        # Only these formats' readers ever see a source's file: the rest of Pillow's never parse it.
        with Image.open(io.BytesIO(data), formats=sorted(FORMATS)) as img:
            if img.format not in FORMATS:
                raise Refused(f"not an image we keep: {img.format}")
            if img.width * img.height > MAX_PIXELS:
                raise Refused(f"{img.width}x{img.height} pixels")
            img.seek(0)
            pic = img.convert("RGBA")
    except Refused:
        raise
    except Exception as exc:  # Pillow raises many kinds for a file that isn't a picture
        raise Refused(f"not an image: {exc}") from exc
    pic.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    pic.save(out, format="PNG", optimize=True)
    return out.getvalue(), palette(pic)


def palette(pic: Image.Image, most: int = 6) -> list[dict[str, Any]]:
    """The colours a logo is drawn in, most used first: each one's average over the pixels near it, its share of
    the logo (its opaque pixels), and whether it's a grey."""
    small = pic.convert("RGBA").resize((96, 96))
    sums: dict[tuple[int, int, int], list[int]] = {}
    total = 0
    pixels = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()  # getdata goes in Pillow 14
    for r, g, b, a in pixels:  # pyright: ignore[reportGeneralTypeIssues]: RGBA pixels are 4-tuples
        if a < OPAQUE:
            continue
        total += 1
        s = sums.setdefault((r // 32, g // 32, b // 32), [0, 0, 0, 0])
        s[0] += r
        s[1] += g
        s[2] += b
        s[3] += 1
    if not total:
        return []
    found: list[list[float]] = []  # [r, g, b, count], merged when SAME
    for r, g, b, n in sorted(sums.values(), key=lambda s: -s[3]):
        colour = (r / n, g / n, b / n)
        near = next((c for c in found if sum(abs(x - y) for x, y in zip(colour, c[:3])) < SAME), None)
        if near is None:
            found.append([*colour, n])
        else:  # weighted into the nearer, bigger one
            k = near[3] + n
            near[:3] = [(near[i] * near[3] + colour[i] * n) / k for i in range(3)]
            near[3] = k
    found.sort(key=lambda c: -c[3])
    return [{"hex": _hex(c[:3]), "share": round(c[3] / total, 3), "grey": max(c[:3]) - min(c[:3]) < GREY} for c in found[:most]]


def _hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(x))):02x}" for x in rgb)


def _rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def _light(hex_: str) -> bool:
    r, g, b = _rgb(hex_)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 150


def auto(colours: list[dict[str, Any]]) -> dict[str, str] | None:
    """A team's two colours as its logo has them: its most used colour that isn't a grey (covering MIN_SHARE of it
    at least), with the next as its accent, or, with no other, black or white, whichever shows on it. A logo drawn
    in greys alone is drawn on white, its accent its strongest colour if it has one, else black. None for none."""
    if not colours:
        return None
    strong = [c for c in colours if not c["grey"]]
    big = [c for c in strong if c["share"] >= MIN_SHARE]
    if not big:
        return {"fill": "#ffffff", "accent": strong[0]["hex"] if strong else "#0d0d0d"}
    # A crest's gold is most often its trim (Barcelona's border, Real Madrid's crown): a team with another colour
    # about as big is that colour.
    if len(big) > 1 and _gold(big[0]["hex"]) and big[1]["share"] >= big[0]["share"] * 0.4:
        big = [*big[1:], big[0]]
    fill = big[0]["hex"]
    accent = next((c["hex"] for c in big[1:] if not _gold(c["hex"])), None) or next((c["hex"] for c in strong if c["hex"] != fill and c["share"] >= MIN_SHARE / 2), None)
    return {"fill": fill, "accent": accent or ("#0d0d0d" if _light(fill) else "#ffffff")}


def _gold(hex_: str) -> bool:
    """A gold or a yellow: a hue of 35° to 55°, strong."""
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in _rgb(hex_)))
    return 35 / 360 <= h <= 55 / 360 and s > 0.5 and v > 0.5


# ---------------------------------------------------------------- what the app is told


def key(team: Team) -> str:
    """A team as the app names it for its look: its source and the source's id for it ("sportmonks_football:83"),
    which a game's board, its fixtures and Call it all carry."""
    return f"{team.system}:{team.external_id}"


def shown(team: Team, now: controls.Controls | None = None) -> bool:
    """Whether a team's logo may show: its sport's logos are on and its own isn't taken down."""
    now = now or controls.current()
    return team.sport not in now.logos_off and key(team) not in now.logos_hidden


def url(logo: TeamLogo | None) -> str | None:
    return f"/api/logos/{logo.digest}.png" if logo is not None and logo.status == "ok" and logo.digest else None


def look(team: Team, logo: TeamLogo | None, now: controls.Controls | None = None) -> dict[str, Any]:
    """How the app draws a team: its logo's address (None: our badge, its short code), its two colours (None: its
    sport's), the logo's own colours (tones, most used first: whether it needs a disc on a colour), and whether an
    operator set the colours."""
    colours = team.colours if isinstance(team.colours, dict) and team.colours.get("fill") else None
    tones = [c["hex"] for c in (logo.palette if logo is not None and logo.status == "ok" else None) or []][:3]
    return {
        "logo": url(logo) if shown(team, now) else None,
        "short": team.short or "",
        "colours": colours or (auto(logo.palette) if logo is not None and logo.status == "ok" else None),
        "tones": tones,
        "set": colours is not None,
    }


def looks(db: Session, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    """The looks of these teams ("system:team id", LOOKS_MOST at most), of those the registry has."""
    pairs = list(dict.fromkeys(tuple(k.split(":", 1)) for k in keys if ":" in k))[:LOOKS_MOST]
    if not pairs:
        return {}
    now = controls.current()
    rows = db.execute(select(Team, TeamLogo).outerjoin(TeamLogo, TeamLogo.team_id == Team.id).where(tuple_(Team.system, Team.external_id).in_(pairs)))
    return {key(team): look(team, logo, now) for team, logo in rows}


def serve(db: Session, name: str) -> bytes | None:
    """A logo's PNG by its address ("<digest>.png"), while it may show: a picture several teams share (one flag for a
    country's sides) stops being served when any of them is taken down."""
    m = NAME.fullmatch(name)
    if not m:
        return None
    now = controls.current()
    rows = db.execute(select(Team, TeamLogo.data).join(TeamLogo, TeamLogo.team_id == Team.id).where(TeamLogo.digest == m.group(1), TeamLogo.status == "ok")).all()
    if not rows or not all(shown(team, now) for team, _ in rows):
        return None
    data = next((d for _, d in rows if d is not None), None)
    return bytes(data) if data is not None else None


# ---------------------------------------------------------------- the job


def wanted(db: Session, now: datetime, limit: int = PER_RUN) -> list[tuple[int, str, str]]:
    """The teams whose logo to fetch now, biggest first (team id, its source, the address): those with an address
    and no copy, one of another address, one kept REFETCH_DAYS, or one that failed RETRY ago; of a source and a sport
    running now (not turned off in the admin panel), and not taken down."""
    hidden = controls.current().logos_hidden
    systems = [s for s in ADAPTERS if getattr(ADAPTERS[s], "logo_hosts", ())]
    if not systems:
        return []
    due = or_(
        TeamLogo.team_id.is_(None),
        TeamLogo.url != Team.logo_url,
        and_(TeamLogo.status == "ok", TeamLogo.fetched_at < now - timedelta(days=REFETCH_DAYS)),
        and_(TeamLogo.status != "ok", TeamLogo.fetched_at < now - RETRY),
    )
    rows = db.execute(select(Team.id, Team.system, Team.external_id, Team.sport, Team.logo_url).outerjoin(TeamLogo, TeamLogo.team_id == Team.id)
                      .where(Team.logo_url.is_not(None), Team.system.in_(systems), due).order_by(Team.tier.desc(), Team.id).limit(limit * 2)).all()
    return [(r.id, r.system, r.logo_url) for r in rows if f"{r.system}:{r.external_id}" not in hidden and r.logo_url and sports.on(r.sport)][:limit]


def keep(db: Session, team_id: int, address: str, data: bytes | None, error: str | None, now: datetime) -> TeamLogo:
    """Write what a download gave: a logo drawn again, or why there's none (a logo kept from before stays until a
    new one comes, unless the source's address changed)."""
    logo = db.get(TeamLogo, team_id) or TeamLogo(team_id=team_id, url=address, status="failed")
    db.add(logo)
    moved = logo.url != address
    logo.url, logo.fetched_at = address, now
    try:
        if data is None:
            raise Refused(error or "not fetched")
        png, colours = redraw(data)
    except Refused as exc:
        if moved or logo.status != "ok":
            logo.status, logo.reason, logo.digest, logo.data, logo.palette = "failed", str(exc)[:500], None, None, []
        else:
            logo.reason = str(exc)[:500]  # the copy from before shows on
        return logo
    logo.status, logo.reason, logo.digest, logo.data, logo.palette = "ok", None, hashlib.md5(png).hexdigest(), png, colours
    return logo


def refresh(now: datetime | None = None, pause: float = PAUSE) -> dict[str, int]:
    """Fetch the logos wanted now, one at a time, each written as it comes, for RUN_MOST seconds at most: the rest wait
    for the next run. One that can't be fetched, whatever went wrong, is written as failed, and the run goes on."""
    now = now or _now()
    started = time.monotonic()
    with session() as db:
        todo = wanted(db, now)
    done = {"asked": 0, "kept": 0, "failed": 0}
    for n, (team_id, system, address) in enumerate(todo):
        if time.monotonic() - started > RUN_MOST:
            break
        if n and pause:
            time.sleep(pause)
        data, error = None, None
        try:
            data = download(system, address)
        except Exception as exc:  # a host, an address or an answer gone wrong: this team's, not the run's
            error = str(exc) or type(exc).__name__
        with session() as db:
            if db.get(Team, team_id) is None:
                continue  # removed meanwhile
            logo = keep(db, team_id, address, data, error, now)
            done["asked"] += 1
            done["kept" if logo.status == "ok" else "failed"] += 1
    return done


def run() -> dict[str, int] | None:
    """The worker's run, under a lock; None if another run holds it."""
    token = bus.try_lock(LOCK, 900)
    if token is None:
        return None
    try:
        done = refresh()
    finally:
        bus.release(LOCK, token)
    if done["asked"]:
        log.info("logos: %(asked)d fetched, %(kept)d kept, %(failed)d failed", done)
    return done


# ---------------------------------------------------------------- an operator's word


def colours_of(fill: Any, accent: Any = None) -> dict[str, str] | None:
    """An operator's colours, checked ("#004d98"; an accent's left out for black or white, whichever shows on the
    fill); None for "auto": the logo's. ValueError for one that isn't a colour."""
    if fill in (None, "", "auto"):
        return None
    if not isinstance(fill, str) or not isinstance(accent, (str, type(None))):
        raise ValueError("A colour is written #rrggbb.")
    fill = fill.strip().lower()
    accent = (accent or "").strip().lower() or ("#0d0d0d" if HEX.fullmatch(fill) and _light(fill) else "#ffffff")
    for c in (fill, accent):
        if not HEX.fullmatch(c):
            raise ValueError(f"A colour is written #rrggbb: {c!r} isn't.")
    return {"fill": fill, "accent": accent}


def set_colours(db: Session, team: Team, colours: dict[str, str] | None) -> None:
    team.colours = colours


def row(team: Team, logo: TeamLogo | None, now: controls.Controls | None = None) -> dict[str, Any]:
    """A team as the admin panel's Teams tab shows it: its look, the colours read from its logo, and whether its logo
    is taken down."""
    now = now or controls.current()
    return {
        "id": team.id,
        "key": key(team),
        "name": team.name,
        "sport": team.sport,
        "detail": " · ".join(x for x in [*(team.competitions or [])[:1], team.country or ""] if x),
        "look": look(team, logo, now),
        "read": auto(logo.palette) if logo is not None and logo.status == "ok" else None,
        "hidden": key(team) in now.logos_hidden,
        "logo_status": logo.status if logo is not None else ("waiting" if team.logo_url else "none"),
    }


def for_admin(db: Session, query: str, limit: int = 30) -> list[dict[str, Any]]:
    """The registry's teams named like this, for the admin panel's Teams tab, biggest first."""
    from . import naming

    words = naming.fold(query).split()
    if not words:
        return []
    found = select(Team, TeamLogo).outerjoin(TeamLogo, TeamLogo.team_id == Team.id)
    for w in words:
        found = found.where(Team.search_text.contains(w))
    now = controls.current()
    return [row(team, logo, now) for team, logo in db.execute(found.order_by(Team.tier.desc(), Team.name).limit(limit))]


def main(argv: list[str]) -> int:
    from . import teams

    if not argv:
        result = run()
        if result is None:
            print(f"another logos run is going (the worker's); try again when it's done, or redis-cli del lock:{LOCK} if none is", file=sys.stderr)
            return 1
        print(result)
        return 0
    what, rest = argv[0], argv[1:]
    if what in ("hide", "show") and len(rest) == 1 or what == "colours" and 2 <= len(rest) <= 3:
        with session() as db:
            team = teams.get(db, rest[0])
            if team is None:
                print(f"no team {rest[0]!r}: give its id here, or system:team id (teams list <name>)", file=sys.stderr)
                return 1
            if what == "colours":
                try:
                    set_colours(db, team, colours_of(rest[1], rest[2] if len(rest) > 2 else None))
                except ValueError as exc:
                    print(exc, file=sys.stderr)
                    return 1
                print(f"{team.name}: {team.colours or 'colours from its logo'}")
                return 0
            controls.hide_logo(key(team), what == "hide")
            print(f"{team.name}: logo {'taken down' if what == 'hide' else 'shown'}")
            return 0
    print((__doc__ or "").rsplit("\n\n", 1)[-1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    logs.setup()
    sys.exit(main(sys.argv[1:]))
