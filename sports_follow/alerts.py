"""The noise rules: which moments reach a fan's phone, grouped how, and which of them make a sound.

plan() is pure. It takes a batch of new moments, one fan's level for each player they follow (a
moment about anyone else isn't theirs) and for each game they follow by itself (matches.py), the games
they muted and what they were sent lately, and returns the pushes to make (notify.py sends them). On a
phone, notifications should feel like one sports app, not a feed of every poll:

- **What reaches the phone.** The fan's level for the player, or for the game: key moments make a
  sound; everything adds the minor ones (a yellow card, each set) without sound; results only is the
  result; off is nothing. What's left out is still in the app's activity list.
- **A game followed by itself** sends its own moments (a goal whoever scores it, every result), each
  found until the game ended (Watch.until): what comes after the follow lapsed isn't theirs. A followed
  team's games are followed games too (teams.py), and their result is told from the team's side ("Arsenal
  won 2–1 v Chelsea") in place of the game's own, to its fans only, once even for a fan who follows both
  sides. A fan who
  follows the game and a player in it hears each moment once: one of the game's about a player whose
  moment of the same kind reaches them (their goal, their wicket, the result) goes as their player's;
  and one the other side already said in an earlier push of the game (a source's events and its players'
  lines a poll apart) isn't said again, the same play by its detail.
- **One notification per game.** Everything a fan hears from one game, whichever of their players
  it's about, lands on the same notification (tag "e{event}"): a new moment replaces it, with the
  story so far underneath, so the tray holds "Kohli reaches 100" over "Rohit out for 45", not a
  stack. Moments found in one poll go as one push, led by the highest priority. News is one
  notification per player.
- **A game under several fixtures is one game.** A chess board is a fixture of each of its players, so an
  event of each, polled apart (BHA-378): its notification is the game's first fixture's (Item.fixtures),
  whichever finds the moment, all through a mini-match, and each of its moments is found from both sides,
  "Gukesh D is winning" and "Sindarov is in trouble". A fan who follows both hears it once, from the side
  the sport says tells it (voiced: the side it went toward, the winner, White for the start), where that
  side's fixture is being polled to say it; one who follows one of them hears it from their side. What's
  left to the other side is kept (held), and sent late should that side never say it (notify.send_held);
  the other side's word for a moment they were told already, late or not, isn't said again.
- **Muted games** ("Mute this game" on the notification) send nothing more, a game under several
  fixtures muted under any of them.
- **Sound is rationed.** A game makes a sound at most once every GAME_COOLDOWN and a fan's phone at
  most LOUD_PER_HOUR times an hour; past either, the push still arrives, silently. A moment of
  BREAKTHROUGH priority (a hundred, a five-for, a late equaliser, an upset) always makes a sound. A
  game's start is silent if the fan already heard about that game (its line-up).
- **Late is worse than never** for a live moment: one older than its TTL isn't pushed.
- **Corrections.** A moment the source takes back (a goal VAR rules out) is corrected on the same
  notification, by the same rules, for the fans who were sent that moment; a fan who never heard it
  hears nothing, and a moment taken back before it went isn't sent at all. The same rules include the
  fan's level now: one who has since set the player to results only or off isn't sent the correction.
  The moment leaves the story underneath.

Quiet hours are per device, so notify.py applies them after this; a push that arrived silent on every device in them is
logged as silent, so it leaves the hour's sound and the game's to the pushes after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import zip_longest

from .moments import wanted

GAME_COOLDOWN = timedelta(minutes=3)
LOUD_PER_HOUR = 6
BREAKTHROUGH = 90
MAX_LINES = 5  # a phone shows about this many lines of an expanded notification
# How close in time a game's own moment and a player's must be found to be taken for one play by their places
# alone (same): a source's events and a player's line a poll or two apart. Past it, both are sent: twice beats never.
PLAY_WINDOW = timedelta(minutes=5)

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
    player_id: int | None  # None: the game's own, for the fans following the game (matches.py)
    event_id: int | None
    kind: str
    level: str  # key | minor
    priority: int
    title: str
    body: str
    url: str
    created_at: datetime
    corrects: int | None = None  # the moment a correction takes back
    # Whom it's about, by their ids in the game's source: a game's own moment's (Moment.about, "*" the whole
    # game), a player's own (their id there), so the two can be told to be the same moment.
    about: frozenset[str] = frozenset()
    detail: str = ""  # what tells two moments of a kind apart (its key's: "goal@501", "50", "set2", a game's own "253802:50")
    # Its place among its game's moments of its kind about the same player (their 2nd goal; the game's 2nd goal
    # by them): the same play where one side names it by the source's id ("goal@501") and the other by count.
    nth: int = 0
    # The events of its game where one game is under several fixtures (a chess board is one of each player's:
    # structured.boards), its own among them, so it lands on one notification whichever of them found it. Empty
    # where its event is the game.
    fixtures: frozenset[int] = frozenset()
    # A player's moment of such a game: the player here whose side tells it to a fan who follows both (Found.voice),
    # its own player or the other side's, whose fixture is being polled to say it. None where only its player says it.
    voice: int | None = None


@dataclass
class Watch:
    """A game a fan follows by itself (matches.py), or a team in (teams.py): their level for it, when the follow
    lapsed (the game ended), after which its moments aren't theirs, and the teams in it they follow, by their
    tags (teams.tag): a moment from one team's side (its result) is for that team's fans."""

    level: str
    until: datetime | None = None
    teams: frozenset[str] = frozenset()


def sided(item: Item) -> frozenset[str]:
    """The teams a game's own moment is told from the side of (its about's team tags: teams.TAG)."""
    return frozenset(a for a in item.about if a.startswith("team:"))


@dataclass
class Sent:
    """An earlier push to this fan (a notification row)."""

    event_id: int | None
    player_id: int | None
    loud: bool
    lines: list[str]
    at: datetime
    moment_ids: list[int] = field(default_factory=list)  # each line's moment


@dataclass
class Push:
    event_id: int | None
    player_id: int | None  # the lead moment's; None: the game's own
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


def level_of(item: Item, levels: dict[int, str], games: dict[int, Watch]) -> str | None:
    """The fan's level for a moment: their player's, or, for a game's own, the game's while they followed it.
    None: not theirs (someone else's player, a game they don't follow, or found after the follow lapsed)."""
    if item.player_id is not None:
        return levels.get(item.player_id)
    watch = games.get(item.event_id) if item.event_id is not None else None
    if watch is None or (watch.until is not None and item.created_at > watch.until):
        return None
    if (sides := sided(item)) and not sides & watch.teams:
        return None  # the other side's word for it
    return watch.level


def same(game: Item, player: Item, exact: bool = False) -> bool:
    """Whether a game's own moment is one of a player's: of one game, of a kind, about that player or the
    whole game ("Kohli out for 82" from the game's side and from Kohli's, a result). Found at one poll, that's
    enough; `exact`, for one found at another poll, the play too, as a player may score twice: the same detail
    ("goal@501", "50"), or, where one side names it by the source's id and the other only by count (a goal the
    player's line counted before the source's events had it, any card: "red1" and "red@601"), the same place
    among the player's of its kind (nth: their first, the game's first by them), found within PLAY_WINDOW of
    each other: a goal the source dropped and listed again, told again by the game, isn't taken for the player's
    next one later on. A game's own detail may lead with the player's id where the sport tells each player's
    moments as theirs (Sport.game_during: "253802:out" is Kohli's "out")."""
    if not (game.event_id == player.event_id and game.kind == player.kind and ("*" in game.about or bool(game.about & player.about))):
        return False
    if not exact or game.detail in (player.detail, *(f"{a}:{player.detail}" for a in game.about & player.about)):
        return True
    by_id = "@" in game.detail, "@" in player.detail
    near = abs(game.created_at - player.created_at) <= PLAY_WINDOW
    return by_id[0] != by_id[1] and "*" not in game.about and game.nth > 0 and game.nth == player.nth and near


def voiced(item: Item, levels: dict[int, str]) -> bool:
    """Whether a player's moment of a game under several fixtures reaches this fan in another of their players'
    words: the player whose side tells it (Item.voice), whom they follow too at a level that hears it. Each side of a
    chess board finds its moments from its own fixture, a poll apart ("Gukesh D is winning", "Sindarov is in
    trouble"): a fan who follows both hears the side it went toward, never waiting for it, and one who follows only
    Sindarov still hears his."""
    voice = item.voice
    return voice is not None and voice != item.player_id and voice in levels and tier(levels[voice], item) is not None


def voiced_by(item: Item, other: Item, within: timedelta | None = PLAY_WINDOW) -> bool:
    """Whether a player's moment is `other` told from the other side of its board: `other` is the moment of the player
    whose side tells it (voiced), of the same game, kind and play (its detail, which a sport words the same from either
    side: a chess swing's game, band from White's and move), found within `within` of it (None: whenever)."""
    return (item.voice is not None and item.voice != item.player_id and other.player_id == item.voice and other.kind == item.kind and other.detail == item.detail
            and other.event_id in item.fixtures and (within is None or abs(item.created_at - other.created_at) <= within))


def tag_of(item: Item) -> str:
    """The notification a moment lands on: its game's ("e{event}"; a game under several fixtures goes by its first
    here, whichever of them found it, all through a mini-match's games); a player's news, theirs."""
    return f"e{min(item.fixtures or {item.event_id})}" if item.event_id else f"p{item.player_id}"


def plan(items: list[Item], levels: dict[int, str], muted: set[int], sent: list[Sent], now: datetime, taken_back: set[int] | None = None,
         games: dict[int, Watch] | None = None, heard: list[Item] | None = None, held: list[Item] | None = None) -> list[Push]:
    """One fan's pushes for a batch of new moments (see the module docstring). `taken_back` is the
    moments corrections in these games have taken back, which leave the story (this batch's own
    corrections count without it). `games` is the games the fan follows by themselves, by event; `heard`,
    the moments of these games earlier pushes sent them (`sent`'s), each one's own and whom it's about. A moment
    left to the other side of its board to tell (voiced) goes in `held`, to send late should that side never say it
    (notify.send_held); and one whose other side's word reached them already, late or not, isn't said again."""
    told = {m for s in sent for m in s.moment_ids}
    taken = (taken_back or set()) | {i.corrects for i in items if i.corrects is not None}
    kept: list[tuple[Item, str]] = []
    for item in items:
        level = level_of(item, levels, games or {})
        if level is None:
            continue  # someone else's player, or game: a batch holds every player in the game, a sweep every game
        how = tier(level, item)
        if how is None or item.event_id in muted or item.fixtures & muted or (now - item.created_at).total_seconds() > ttl(item.kind):
            continue
        if item.id in taken or (item.corrects is not None and item.corrects not in told):
            # Taken back before it went (a batch the tick swept up late), or the correction of something
            # this fan was never sent.
            continue
        kept.append((item, how))
    # A game's result from the side of a team the fan follows in it goes in place of the game's own, and of
    # the other side's, should they follow both.
    by_side: dict[int | None, int] = {}
    for item, _ in kept:
        if item.player_id is None and item.kind == "final" and sided(item):
            by_side.setdefault(item.event_id, item.id)
    kept = [(i, h) for i, h in kept if not (i.player_id is None and i.kind == "final" and i.event_id in by_side and by_side[i.event_id] != i.id)]
    # A game's own moment that one of the fan's players' says too goes once, as their player's; and one the
    # other side said in an earlier push isn't said again. A player's that another of theirs tells from the
    # other side of a board goes in that one's words (voiced), and the game's own of it with it.
    theirs = [i for i, _ in kept if i.player_id is not None]
    heard_theirs = [h for h in heard or [] if h.player_id is not None]
    heard_games = [h for h in heard or [] if h.player_id is None]
    groups: dict[str, list[tuple[Item, bool]]] = {}
    for item, how in kept:
        if item.player_id is None and (any(same(item, p) for p in theirs) or any(same(item, p, exact=True) for p in heard_theirs)):
            continue
        if item.player_id is not None and voiced(item, levels):
            if held is not None:
                held.append(item)
            continue
        if item.player_id is not None and (any(same(g, item, exact=True) for g in heard_games) or any(voiced_by(h, item, None) for h in heard_theirs)):
            continue
        groups.setdefault(tag_of(item), []).append((item, how == "loud"))

    loud_this_hour = sum(1 for s in sent if s.loud and now - s.at <= timedelta(hours=1))
    pushes = []
    # The biggest moments get the hour's sound first.
    for tag, members in sorted(groups.items(), key=lambda g: -max(i.priority for i, _ in g[1])):
        members.sort(key=lambda m: (m[0].priority, m[0].id), reverse=True)
        lead = members[0][0]
        # The game's earlier pushes, of any of its fixtures; a player's news, theirs.
        of_it = [s for s in sent if s.event_id == lead.event_id or s.event_id in lead.fixtures] if lead.event_id else [s for s in sent if s.event_id is None and s.player_id == lead.player_id]
        earlier = sorted(of_it, key=lambda s: s.at, reverse=True)
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
        story = [line for s in earlier for line, m in zip_longest(s.lines, s.moment_ids) if line is not None and line not in lines and m not in taken] if lead.event_id else []
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
