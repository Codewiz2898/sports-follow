"""The noise rules: which moments reach a fan's phone, grouped how, and which of them make a sound.

plan() is pure. It takes a batch of new moments, one fan's level for each player they follow (a
moment about anyone else isn't theirs), the games they muted and what they were sent lately, and
returns the pushes to make (notify.py sends them). On a phone, notifications should feel like one
sports app, not a feed of every poll:

- **What reaches the phone.** The fan's level for the player: key moments make a sound; everything
  adds the minor ones (a yellow card, each set) without sound; results only is the result; off is
  nothing. What's left out is still in the app's activity list.
- **One notification per game.** Everything a fan hears from one game, whichever of their players
  it's about, lands on the same notification (tag "e{event}"): a new moment replaces it, with the
  story so far underneath, so the tray holds "Kohli reaches 100" over "Rohit out for 45", not a
  stack. Moments found in one poll go as one push, led by the highest priority. News is one
  notification per player.
- **Muted games** ("Mute this game" on the notification) send nothing more.
- **Sound is rationed.** A game makes a sound at most once every GAME_COOLDOWN and a fan's phone at
  most LOUD_PER_HOUR times an hour; past either, the push still arrives, silently. A moment of
  BREAKTHROUGH priority (a hundred, a five-for, a late equaliser, an upset) always makes a sound. A
  game's start is silent if the fan already heard about that game (its line-up).
- **Late is worse than never** for a live moment: one older than its TTL isn't pushed.

Quiet hours are per device, so notify.py applies them after this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .moments import wanted

GAME_COOLDOWN = timedelta(minutes=3)
LOUD_PER_HOUR = 6
BREAKTHROUGH = 90
MAX_LINES = 5  # a phone shows about this many lines of an expanded notification

LIVE_TTL = 15 * 60  # a goal an hour late isn't news; results and injuries keep for half a day
LINEUP_TTL = 60 * 60  # a line-up is news until kick-off
LATE_TTL = 12 * 3600


def ttl(kind: str) -> int:
    """Seconds a moment of this kind is worth pushing, and the push service may hold it for an offline phone."""
    return LATE_TTL if kind in ("final", "news") else LINEUP_TTL if kind == "lineup" else LIVE_TTL


@dataclass
class Item:
    """A new moment, as the planner sees it."""

    id: int
    player_id: int
    event_id: int | None
    kind: str
    level: str  # key | minor
    priority: int
    title: str
    body: str
    url: str
    created_at: datetime


@dataclass
class Sent:
    """An earlier push to this fan (a notification row)."""

    event_id: int | None
    player_id: int
    loud: bool
    lines: list[str]
    at: datetime


@dataclass
class Push:
    event_id: int | None
    player_id: int  # the lead moment's
    kind: str  # the lead moment's
    moment_ids: list[int]
    lines: list[str]  # each moment's title, lead first
    title: str
    body: str
    url: str
    loud: bool
    tag: str  # the notification it replaces on the phone, and the push service's topic
    ttl: int
    story: list[str] = field(default_factory=list)  # earlier lines from the same game


def tier(level: str, item: Item) -> str | None:
    """"loud", "silent", or None (not pushed) for a fan who chose `level` for the player."""
    if not wanted(level, item.level, item.kind):
        return None
    if level == "results" or item.level == "key":
        return "loud"
    return "silent"


def plan(items: list[Item], levels: dict[int, str], muted: set[int], sent: list[Sent], now: datetime) -> list[Push]:
    """One fan's pushes for a batch of new moments (see the module docstring)."""
    groups: dict[str, list[tuple[Item, bool]]] = {}
    for item in items:
        if item.player_id not in levels:
            continue  # someone else's player: a batch holds every player in the game, a sweep every game
        how = tier(levels[item.player_id], item)
        if how is None or item.event_id in muted or (now - item.created_at).total_seconds() > ttl(item.kind):
            continue
        tag = f"e{item.event_id}" if item.event_id else f"p{item.player_id}"
        groups.setdefault(tag, []).append((item, how == "loud"))

    loud_this_hour = sum(1 for s in sent if s.loud and now - s.at <= timedelta(hours=1))
    pushes = []
    # The biggest moments get the hour's sound first.
    for tag, members in sorted(groups.items(), key=lambda g: -max(i.priority for i, _ in g[1])):
        members.sort(key=lambda m: (m[0].priority, m[0].id), reverse=True)
        lead = members[0][0]
        earlier = sorted((s for s in sent if (s.event_id == lead.event_id if lead.event_id else s.event_id is None and s.player_id == lead.player_id)), key=lambda s: s.at, reverse=True)
        noisy = [i for i, loud in members if loud]
        loud = bool(noisy)
        if loud and max(i.priority for i in noisy) < BREAKTHROUGH:
            if all(i.kind == "start" for i in noisy) and any(s.loud for s in earlier):
                loud = False  # they already heard about this game: the start is a quiet update
            elif any(s.loud and now - s.at < GAME_COOLDOWN for s in earlier):
                loud = False
            elif loud_this_hour >= LOUD_PER_HOUR:
                loud = False
        loud_this_hour += loud
        lines = [i.title for i, _ in members]
        story = [line for s in earlier for line in s.lines if line not in lines] if lead.event_id else []
        body = "\n".join(dict.fromkeys(x for x in [lead.body, *lines[1:], *story] if x))
        pushes.append(Push(
            event_id=lead.event_id,
            player_id=lead.player_id,
            kind=lead.kind,
            moment_ids=[i.id for i, _ in members],
            lines=lines,
            title=lead.title,
            body="\n".join(body.split("\n")[:MAX_LINES]),
            url=lead.url,
            loud=loud,
            tag=tag,
            ttl=max(ttl(i.kind) for i, _ in members),
            story=story,
        ))
    return pushes
