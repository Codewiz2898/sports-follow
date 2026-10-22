"""Player photos: each followed or starter athlete's photo from Wikimedia Commons (the registry's file, from
Wikidata's P18), with its credit, served from our own origin. The app shows them on a player's profile
(their page, and the preview before following) and nowhere else: the home page draws each sport's art.

The worker's photos job (every 15 minutes) asks the Commons API about the files it hasn't got, BATCH to a
request: the WIDTH-pixel thumbnail (one of the sizes Wikimedia serves; any other width comes back at the
next one up), who made the photo, its licence and its file page. A photo under a licence that lets fans
see it (public domain, CC0, CC BY, CC BY-SA, GODL-India: the user's choice; not GFDL, Commons' generic
"Attribution" or "copyrighted free use") is downloaded once and kept in Postgres, so every API process
serves the same bytes at /api/photos/<digest>.jpg, cached for a year. Nothing is hot-linked. Each file is
asked about again every REFETCH_DAYS (a new licence, author or upload, or a file Commons deleted), one
that failed the next day, and one kept at another width than WIDTH at once (WIDTH changed: each is fetched
again, PER_RUN a run). A thumbnail Wikimedia refuses or is too busy for (a 403, 429 or 5xx) ends the run, and
is fetched again at the next, after the files that have waited longer. The job then looks for the face in
each new thumbnail (faces.py), writes each followed player's photo into their card, and forgets files
fetched over FORGET_DAYS ago that nobody follows or is offered now.

Where the face is tells the app how to frame a photo (framing): the profile shows the square around it,
head and shoulders, rather than the top of a photo that might be a full-length action shot. A photo with
no face found is shown from near its top.

The app shows each photo in its own colours, cropped to the band around the face and fading into the band
at its left edge; nothing else in it changes. CC BY and CC BY-SA ask that a change be said, so each credit
says the crop, under the photo wherever it shows: "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons,
cropped", linking to the whole file on Commons. The Credits page lists every photo in use and says how
they're cropped and faded (credits.PHOTO_NOTICES). Were the app to change a photo beyond that (recolour
it, say), its credit would have to say that too. A photo shows who a player is; it doesn't mean they
endorse the app.

Never a data provider's photos (their terms don't license them), and never generated likenesses.

    .venv/bin/python -m sports_follow.photos      # one run now, as the worker's job does
"""

from __future__ import annotations

import copy
import hashlib
import html
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import unquote, urlparse, urlunparse

import httpx
from sqlalchemy import and_, delete, exists, or_, select, union
from sqlalchemy.orm import Session, object_session, undefer

from . import bus, faces, logs, shutdown, sports
from .config import USER_AGENT
from .db import session
from .models import Athlete, Follow, Photo, Player, PlayerCard, PlayerIdentity, Starter

log = logging.getLogger("sports_follow.photos")

API = "https://commons.wikimedia.org/w/api.php"
# A size Wikimedia keeps (330, 500, 960, 1280…). The profile shows the square around the face about 300
# pixels across, 600 on a 2x phone, and that square is most of a photo's width (60% to all of it), so 960
# shows it about pixel for pixel (a thumbnail about 290 KB; 1280 doubles that for little more).
WIDTH = 960
SMALLER = (500, 330)  # the sizes Wikimedia keeps a thumbnail over MAX_BYTES (a PNG photo) is fetched at instead
BATCH = 50  # files a Commons API request asks about (its limit)
PER_RUN = 100  # files a run asks about at most; the rest wait for the next
PAUSE = 0.5  # seconds between thumbnail downloads
REFETCH_DAYS = 90
RETRY = timedelta(days=1)
FORGET_DAYS = 30
MAX_BYTES = 2_000_000  # a thumbnail's most, as fans' phones download it
FILL = 0.4  # how much of the profile's square the face takes: head and shoulders
RAISE = 0.1  # how far the square's centre sits above the face's, in face sizes, so the hair isn't cut off
# The least of a photo's width the square spans, so a far-off face isn't blown up to a blur: 120 pixels of a
# 330-pixel thumbnail. A share, so a thumbnail of any width (one kept before WIDTH changed, or a file Commons
# has only smaller) is framed alike.
CLOSEST = 120 / 330
LOCK = "photos"
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
# The licences kept, by Commons' code: "cc-by-sa-4.0", "cc-by-2.0", "cc-by-sa-3.0-de", "cc0", "pd". A
# file whose page names no code goes by its licence's short name ("GODL-India" -> "godl-india").
KEPT = re.compile(r"(cc-by(-sa)?-\d(\.\d)?(-[a-z]{2,3})?|cc0|pd|public-domain|godl-india)")
NAME = re.compile(r"([0-9a-f]{32})\.(jpg|png|webp|gif)")

_client = httpx.Client(headers={"user-agent": USER_AGENT}, timeout=30, follow_redirects=True)


class Unavailable(Exception):
    """Commons didn't answer, refused for now or asked us to wait: the rest of this run waits for the next."""


class TooBig(ValueError):
    """A thumbnail over MAX_BYTES."""


@dataclass
class Info:
    """What Commons says about one file."""

    status: str  # ok | unfree | missing
    reason: str | None = None  # why it isn't shown: "licence not kept: GFDL"
    licence: str | None = None  # "CC BY-SA 4.0", "public domain"
    licence_url: str | None = None
    author: str | None = None
    page: str | None = None  # the file's page on Commons, where its credit links
    thumb: str | None = None
    sha1: str | None = None  # the original upload's: a new upload is a new photo


# ---------------------------------------------------------------- what Commons says


def _text(value: Any, longest: int = 100) -> str:
    """Commons' HTML ("<a href=…>Bryan Berlin</a>") as one line of plain text, cut at a word."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())
    if len(text) > longest:
        text = text[: longest - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _https(url: str | None) -> str | None:
    """A licence or page link Commons gave, as https ("//creativecommons.org/…" too); None if it isn't one."""
    if not url:
        return None
    url = "https:" + url if url.startswith("//") else url.replace("http://", "https://", 1)
    return url if url.startswith("https://") else None


def judge(info: dict[str, Any]) -> Info:
    """One file's imageinfo as what we keep of it. Pure, so it's tested without the network."""
    meta = {k: (v or {}).get("value") for k, v in (info.get("extmetadata") or {}).items()}
    short = _text(meta.get("LicenseShortName"), 60)
    code = str(meta.get("License") or "").strip().lower() or re.sub(r"\s+", "-", short.lower())
    licence = "public domain" if code in ("pd", "public-domain") else short
    found = Info(status="ok", licence=licence or None, licence_url=_https(meta.get("LicenseUrl")), page=_https(info.get("descriptionurl")), sha1=info.get("sha1"))
    found.author = _text(meta.get("Artist")) or _text(meta.get("Attribution")) or "unknown author"
    thumb = info.get("thumburl") or ""
    if str(meta.get("NonFree") or "").lower() == "true" or not KEPT.fullmatch(code):
        found.status, found.reason = "unfree", f"licence not kept: {short or code or 'none given'}"
    elif info.get("mime") not in TYPES and not thumb:
        found.status, found.reason = "unfree", f"not a picture: {info.get('mime')}"
    elif not found.page:
        found.status, found.reason = "missing", "no file page"
    else:
        found.thumb = urlunparse(urlparse(thumb)._replace(query="")) if thumb else None
    return found


def read(answer: dict[str, Any], files: list[str]) -> dict[str, Info]:
    """A Commons API answer about these files, by file name as asked: Commons writes "File:Ab_c.jpg" as
    "File:Ab c.jpg", and a renamed file answers under its new name."""
    query = answer.get("query") or {}
    renamed = {r["from"]: r["to"] for key in ("normalized", "redirects") for r in query.get(key) or []}
    pages = {p.get("title"): p for p in query.get("pages") or []}
    out: dict[str, Info] = {}
    for file in files:
        title = f"File:{file}"
        for _ in range(3):
            title = renamed.get(title, title)
        page = pages.get(title) or {}
        info = (page.get("imageinfo") or [None])[0]
        out[file] = judge(info) if info and not page.get("missing") else Info(status="missing", reason="not on Commons")
    return out


def ask(files: list[str]) -> dict[str, Info]:
    """What Commons says about up to BATCH files, in one request."""
    params = {
        "action": "query", "format": "json", "formatversion": "2", "redirects": "1", "maxlag": "5",
        "prop": "imageinfo", "iiprop": "url|extmetadata|sha1|mime", "iiurlwidth": str(WIDTH),
        "iiextmetadatafilter": "Artist|Attribution|LicenseShortName|LicenseUrl|License|NonFree",
        "titles": "|".join(f"File:{f}" for f in files),
    }
    try:
        # Posted: fifty names in another script, percent-encoded, can be too long for an address.
        res = _client.post(API, data=params)
        answer = res.json() if res.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise Unavailable(f"Commons API: {exc}") from exc
    if res.status_code != 200 or "error" in answer:
        # Busy (429, or "maxlag": its databases are behind): wait for the next run.
        raise Unavailable(f"Commons API: {res.status_code} {(answer.get('error') or {}).get('code', '')}".strip())
    return read(answer, files)


def download(url: str) -> tuple[bytes, str]:
    """A thumbnail's bytes and media type, from Wikimedia's own hosts only."""
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not host.endswith(".wikimedia.org"):
        raise ValueError(f"not a Wikimedia address: {url}")
    try:
        res = _client.get(url)
    except httpx.HTTPError as exc:
        raise Unavailable(f"thumbnail: {exc}") from exc
    if not (res.url.host or "").endswith(".wikimedia.org"):
        raise ValueError(f"thumbnail redirected away from Wikimedia: {res.url.host}")
    if res.status_code in (403, 429) or res.status_code >= 500:
        # Refused for now (Wikimedia answers a client it's slowing down with a 403 as well as a 429) or busy: not
        # the file's doing, so it isn't left a day (BUG-180).
        raise Unavailable(f"thumbnail: {res.status_code}")
    if res.status_code != 200:
        raise ValueError(f"thumbnail: {res.status_code}")
    media = res.headers.get("content-type", "").split(";")[0].strip()
    if media not in TYPES:
        raise ValueError(f"thumbnail is {media or 'of no type'}")
    if not res.content:
        raise ValueError("thumbnail is 0 bytes")
    if len(res.content) > MAX_BYTES:
        raise TooBig(f"thumbnail is {len(res.content)} bytes")
    return res.content, media


def thumbnail(url: str) -> tuple[bytes, str]:
    """A thumbnail's bytes and media type at WIDTH, or at the first size of SMALLER that's under MAX_BYTES
    when that one isn't (a PNG photo: Commons keeps its thumbnails PNG)."""
    # The size is the last part of a thumbnail's address: a file's own name may start "960px-" too.
    head, found, tail = url.rpartition(f"/{WIDTH}px-")
    sizes = (WIDTH, *SMALLER) if found else (WIDTH,)  # not a thumbnail: Commons' own file
    for width in sizes[:-1]:
        try:
            return download(f"{head}/{width}px-{tail}")
        except TooBig:
            pass
    return download(f"{head}/{sizes[-1]}px-{tail}" if found else url)


# ---------------------------------------------------------------- whose photos


def _file(image: str) -> str:
    """The registry's file name as Commons names it (an old import kept them percent-encoded)."""
    return unquote(image) if re.search(r"%[0-9A-Fa-f]{2}", image) else image


def _qids():
    """Each player's registry athlete, by Wikidata id: their "wikidata" identity, or the qid a player in an
    athlete's second sport carries without one (pipeline.start_from_registry)."""
    held = select(PlayerIdentity.player_id.label("player_id"), PlayerIdentity.external_id.label("qid")).where(PlayerIdentity.system == "wikidata")
    carried = select(Player.id.label("player_id"), Player.wikidata_qid.label("qid")).where(Player.wikidata_qid.is_not(None))
    return union(held, carried).subquery()


def wanted(db: Session) -> dict[str, str]:
    """The photo files to have, with whose each is: every followed player's registry athlete, and every
    starter of a running sport."""
    qids = _qids()
    followed = (
        select(Athlete.image, Athlete.name)
        .join(qids, qids.c.qid == Athlete.qid)
        .where(Athlete.image.is_not(None), exists(select(Follow.id).where(Follow.player_id == qids.c.player_id)))
    )
    offered = (
        select(Athlete.image, Athlete.name)
        .join(Starter, (Starter.qid == Athlete.qid) & (Starter.sport == Athlete.sport))
        .where(Athlete.image.is_not(None), Starter.sport.in_([s.key for s in sports.ENABLED]))
    )
    out: dict[str, str] = {}
    for image, name in [*db.execute(followed).all(), *db.execute(offered).all()]:
        if image:
            out.setdefault(_file(image), name)
    return out


def _due(photo: Photo | None, now: datetime) -> bool:
    """A file not asked about yet; one kept at another width than WIDTH at once, or a day after fetching it
    again failed (its reason says why); one that failed a day ago; any other every REFETCH_DAYS."""
    if photo is None:
        return True
    if photo.status == "ok" and photo.asked_width != WIDTH:
        return photo.reason is None or photo.fetched_at < now - RETRY
    return photo.fetched_at < now - (RETRY if photo.status == "failed" else timedelta(days=REFETCH_DAYS))


# ---------------------------------------------------------------- what the app is sent


def credit(photo: Photo) -> str:
    """ "Photo: Bryan Berlin, CC BY-SA 4.0, Wikimedia Commons, cropped": the app shows each one cropped to the
    band, and changed in no other way."""
    return f"Photo: {photo.author or 'unknown author'}, {photo.licence}, Wikimedia Commons, cropped"


def framing(width: int | None, height: int | None, face: dict[str, float] | None) -> dict[str, Any]:
    """{"crop": the square around the player's face the profile shows, head and shoulders}: FILL of it
    face, a little above the middle, never closer than CLOSEST of its width across and never past the photo's
    edges, as {x, y, w, h}, fractions of the photo's width and height. A full-length action shot then
    shows the player's face as a headshot does. {} when no face was found: the profile shows the photo
    from near its top."""
    if not (face and width and height):
        return {}
    x, y = (face["x"] + face["w"] / 2) * width, (face["y"] + face["h"] / 2) * height
    size = max(face["w"] * width, face["h"] * height)
    side = min(max(size / FILL, CLOSEST * width), width, height)
    left, top = min(max(x - side / 2, 0), width - side), min(max(y - RAISE * size - side / 2, 0), height - side)
    return {"crop": {"x": round(left / width, 4), "y": round(top / height, 4), "w": round(side / width, 4), "h": round(side / height, 4)}}


def public(photo: Photo) -> dict[str, Any]:
    """A photo as the app gets it: our own address for it, its credit, and where its face is."""
    return {
        "url": f"/api/photos/{photo.digest}.{TYPES[photo.media_type or 'image/jpeg']}",
        "credit": credit(photo),
        "page": photo.page,
        "author": photo.author,
        "licence": photo.licence,
        "licence_url": photo.licence_url,
        **framing(photo.width, photo.height, photo.face),
    }


def _shown(db: Session, files: Iterable[str]) -> dict[str, dict[str, Any]]:
    files = sorted({_file(f) for f in files if f})
    if not files:
        return {}
    rows = db.scalars(select(Photo).where(Photo.file.in_(files), Photo.status == "ok", Photo.digest.is_not(None))).all()
    return {p.file: public(p) for p in rows}


def of_file(db: Session, image: str | None) -> dict[str, Any] | None:
    """The photo for a registry file name, if it's here and free to show."""
    return _shown(db, [image]).get(_file(image)) if image else None


def of_player(db: Session, player_id: int) -> dict[str, Any] | None:
    """The photo a player's card shows: their registry athlete's, once it's here and free to show."""
    qids = _qids()
    image = db.scalar(select(Athlete.image).join(qids, qids.c.qid == Athlete.qid).where(qids.c.player_id == player_id, Athlete.image.is_not(None)).limit(1))
    return of_file(db, image)


def for_card(player: Player) -> dict[str, Any]:
    """{"photo": …} for a new card's player block, when the player is in a session and has one; else {}."""
    db = object_session(player)
    found = of_player(db, player.id) if db is not None and player.id is not None else None
    return {"photo": found} if found else {}


def of_source(db: Session, system: str, athlete_id: str) -> dict[str, Any] | None:
    """The photo for an athlete a source knows by this id, when the registry has them (a preview from search)."""
    image = db.scalar(select(Athlete.image).where(Athlete.ids.contains({system: athlete_id}), Athlete.image.is_not(None)).limit(1))
    return of_file(db, image)


def used(db: Session) -> list[dict[str, Any]]:
    """Every photo the app may show, for the Credits page: whose it is, and its credit. Not only those
    wanted now: one not yet forgotten still shows on the page of a player nobody follows any more."""
    rows = db.scalars(select(Photo).where(Photo.status == "ok", Photo.digest.is_not(None))).all()
    return sorted(({"name": p.name or "", **public(p)} for p in rows), key=lambda p: p["name"])


def serve(db: Session, name: str) -> tuple[bytes, str] | None:
    """A photo's bytes and media type by its address ("<digest>.jpg"), if it's one we may show."""
    m = NAME.fullmatch(name)
    if not m:
        return None
    row = db.execute(select(Photo.data, Photo.media_type).where(Photo.digest == m.group(1), Photo.status == "ok").limit(1)).first()
    if row is None or row.data is None or TYPES.get(row.media_type or "") != m.group(2):
        return None
    return bytes(row.data), row.media_type


# ---------------------------------------------------------------- the job


def _keep(db: Session, file: str, name: str, info: Info, now: datetime, known: Photo | None) -> Photo:
    """Write what Commons said, downloading the thumbnail when it's new, or was kept at another width (look()
    then finds its face); a photo no longer free to show loses its bytes. One kept at another width that
    can't be fetched again keeps showing as it was, and is tried again in a day. A thumbnail refused or busy
    for now (Unavailable) is noted the same way, but tried again at the next run, and raises: the run stops."""
    photo = known or Photo(file=file)
    if known is None:
        db.add(photo)
    photo.name = name
    kept = info.status == "ok" and photo.status == "ok" and bool(photo.digest) and photo.source_sha1 == info.sha1  # this upload's
    photo.status, photo.reason, photo.fetched_at = info.status, info.reason, now
    photo.licence, photo.licence_url, photo.author, photo.page, photo.source_sha1 = info.licence, info.licence_url, info.author, info.page, info.sha1
    if info.status != "ok":
        photo.data = photo.digest = photo.media_type = photo.asked_width = photo.width = photo.height = photo.face = None
    elif not (kept and photo.asked_width == WIDTH):
        try:
            data, media = thumbnail(info.thumb or "")
        except (ValueError, Unavailable) as exc:
            if kept:
                photo.reason = f"not fetched again at {WIDTH} pixels: {exc}"
                log.warning("photos: %s kept at its old width: %s", file, exc)
            else:
                photo.status, photo.reason, photo.data, photo.digest, photo.media_type, photo.asked_width = "failed", str(exc), None, None, None, None
                photo.width = photo.height = photo.face = None
            if isinstance(exc, Unavailable):
                # Due again at the next run, as one asked a day ago (_due), so it waits behind the files that
                # have waited longer: one Wikimedia refuses every time never stops every run before them.
                photo.fetched_at = now - RETRY
                raise
            return photo
        photo.data, photo.media_type, photo.digest, photo.asked_width = data, media, hashlib.sha256(data).hexdigest()[:32], WIDTH
        photo.width = photo.height = photo.face = None
        time.sleep(PAUSE)
    return photo


def look() -> int:
    """Find the face in each kept thumbnail not looked at yet: a new one, or one kept before the job looked
    (faces.py; a few milliseconds each, in this process). Returns how many."""
    with session() as db:
        ids = db.scalars(select(Photo.id).where(Photo.status == "ok", Photo.digest.is_not(None), Photo.width.is_(None)).order_by(Photo.id)).all()
    looked = failed = 0
    for photo_id in ids:
        try:
            with session() as db:
                photo = db.get(Photo, photo_id, options=[undefer(Photo.data)])
                if photo is None or photo.data is None or photo.width is not None:
                    continue
                photo.width, photo.height, box = faces.find(photo.data)
                photo.face = box._asdict() if box else None
            looked, failed = looked + 1, 0
        except Exception:
            # One photo the detector chokes on is passed over; two in a row is the detector itself (its
            # model missing, say), and the rest wait for the next run. Neither holds up the cards, and the
            # profile shows these photos from near the top meanwhile.
            failed += 1
            log.exception("photos: looking for the face in photo %s failed", photo_id)
            if failed > 1:
                break
    return looked


def stamp(db: Session) -> int:
    """Write each followed player's photo into their card, or take one out that may no longer show, and
    publish the cards that changed. Returns how many. A card nobody follows now is looked at too while it
    shows a photo: anyone may still open it, so a photo taken back (players unmatch) must leave it."""
    qids = _qids()
    followed = exists(select(Follow.id).where(Follow.player_id == PlayerCard.player_id))
    players = db.execute(
        select(PlayerCard.player_id, Athlete.image, PlayerCard.card["player"]["photo"])
        .join(qids, qids.c.player_id == PlayerCard.player_id)
        .join(Athlete, Athlete.qid == qids.c.qid)
        .where(or_(and_(followed, Athlete.image.is_not(None)), PlayerCard.card["player"].has_key("photo")))
    ).all()
    shown = _shown(db, [image for _, image, _ in players if image])
    want: dict[int, dict[str, Any] | None] = {}
    has: dict[int, Any] = {}
    for player_id, image, current in players:
        if want.get(player_id) is None:  # an athlete in two sports is two rows: either's photo
            want[player_id] = shown.get(_file(image)) if image else None
        has[player_id] = current
    changed = []
    for player_id, photo in want.items():
        if has[player_id] == photo:
            continue
        row = db.get(PlayerCard, player_id, with_for_update=True)
        if row is None or (row.card or {}).get("player", {}).get("photo") == photo:
            continue
        card = copy.deepcopy(row.card)
        block = card.setdefault("player", {})
        if photo:
            block["photo"] = photo
        else:
            block.pop("photo", None)
        card["version"] = row.version = (row.version or 0) + 1
        row.card = card
        changed.append((player_id, card))
    db.commit()
    for player_id, card in changed:
        bus.store_card(player_id, card)
    return len(changed)


def forget(db: Session, files: Iterable[str]) -> int:
    """Forget these photos now, unless still wanted (a followed or starter athlete's is the same file): one
    taken back (players unmatch) leaves the Credits page, and the cards that show it at the next stamp.
    Returns how many."""
    gone = {_file(f) for f in files} - set(wanted(db))
    return len(db.execute(delete(Photo).where(Photo.file.in_(gone)).returning(Photo.id)).all()) if gone else 0


def refresh(limit: int = PER_RUN, now: datetime | None = None) -> dict[str, int]:
    """One run: ask Commons about the files wanted and due (new ones first), keep what may be shown, find
    the faces in new thumbnails, write the photos into the cards, and forget files fetched over FORGET_DAYS
    ago that nobody wants now."""
    now = now or datetime.now(timezone.utc)
    done = {"asked": 0, "shown": 0, "left_out": 0, "framed": 0, "cards": 0, "forgotten": 0}
    with session() as db:
        want = wanted(db)
        known = {p.file: p for p in db.scalars(select(Photo).where(Photo.file.in_(list(want))))} if want else {}
        # New files first, then those asked about longest ago.
        due = sorted((f for f in want if _due(known.get(f), now)), key=lambda f: (f in known, known[f].fetched_at if f in known else now))[:limit]
    try:
        for i in range(0, len(due), BATCH):
            if shutdown.stopping.is_set():
                break  # the worker is stopping: the rest wait for the next run
            chunk = due[i : i + BATCH]
            answers = ask(chunk)
            with session() as db:
                for file in chunk:
                    if shutdown.stopping.is_set():
                        break
                    try:
                        photo = _keep(db, file, want[file], answers[file], now, db.scalar(select(Photo).where(Photo.file == file)))
                    except Unavailable:
                        db.commit()  # its thumbnail refused for now, noted (_keep)
                        raise
                    db.commit()
                    done["asked"] += 1
                    done["shown" if photo.status == "ok" else "left_out"] += 1
                    if photo.status != "ok":
                        log.info("photos: %s left out: %s", file, photo.reason)
    except Unavailable as exc:
        log.warning("photos: %s; the rest wait for the next run", exc)
    done["framed"] = look()
    with session() as db:
        done["cards"] = stamp(db)
        stale = [Photo.fetched_at < now - timedelta(days=FORGET_DAYS), *([Photo.file.not_in(list(want))] if want else [])]
        done["forgotten"] = len(db.execute(delete(Photo).where(*stale).returning(Photo.id)).all())
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
    if any(done.values()):
        log.info("photos: %(asked)d asked about, %(shown)d to show, %(left_out)d left out, %(framed)d looked at for a face, %(cards)d cards updated, %(forgotten)d forgotten", done)
    return done


if __name__ == "__main__":
    logs.setup()
    if sys.argv[1:]:
        sys.exit("usage: python -m sports_follow.photos")
    result = run()
    if result is None:
        sys.exit(f"another photos run is going (the worker's); try again when it's done, or redis-cli del lock:{LOCK} if none is")
    print(result)
