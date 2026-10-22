"""The feed's news (BHA-325): stories about followed players from the publishers' feeds each sport reads, kept as
candidates beside the videos (feed_candidates), for the same judge and ranking (rank.py). Run by the curator
(curator.py), never by a request.

Every 20 minutes (poll), for each running news source (adapters.news): the latest stories of each feed a running
sport lists (Sport.news_feeds), published in the last KEEP_FOR, and of those, each one whose headline or description
names a followed player of that sport is a candidate for that player (link), one row per player and story, the
story known by its address written one way (NewsSource articles' url), so two feeds listing it make one row:

- their full name (in any of its spellings, or as a hashtag) names them as it does in a video (candidates.phrases);
  so does their own one-word name or handle that no other followed player of the sport shares, but only written
  as they write it, capitalised or not ("Less" the pro; not "less accurate"), and not as part of another name
  ("Mortal Kombat");
- their surname alone, or a one-word name fans found them by, names them only when nothing else does: a surname
  no other followed player of the sport has, and fewer than COMMON athletes of the sport in the registry ("Bumrah";
  not "Smith" or "Sharma"); else only when the story also names one of their sides (a team, their country), and
  no other player whose name it is (two Sharmas followed: the story about Mumbai Indians is Rohit's). Only
  capitalised ("head coach" isn't Travis Head), and never next to another capitalised name, which makes it someone
  else's ("Deepti Sharma" isn't Rohit, "Rahul Dravid" isn't KL Rahul), so a word inside a full name already found
  ("Rohit Sharma") names nobody else either. In a headline Written In Title Case, where every word is capitalised,
  only the name before it counts.

A story is never kept whole: its headline, its link, when it was published and the feed's own short description,
which the Feed shows as they are, crediting the publisher. Stories about the same thing (clusters, read by rank.py)
are one item in the Feed with the number of outlets that ran it.

A candidate goes with the player (ON DELETE CASCADE). The curator's weekly prune drops stories older than
PRUNE_AFTER, but those an admin approved or pinned (evergreen).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import db, metrics, naming, shutdown, sports
from .adapters import NEWS, AdapterError, Article, NewsSource, budget, news
from .candidates import _POSSESSIVE, SHORT_NAME, Followed, _holds, _longest_first, followed
from .models import Athlete, FeedCandidate

log = logging.getLogger("sports_follow.news")

KEEP_FOR = timedelta(days=3)  # a story published longer ago than this isn't kept: a feed's archive isn't news
PRUNE_AFTER = timedelta(days=30)  # stories older than this go, weekly, but an admin's evergreen ones
EVERGREEN = ("approved", "pinned")  # an admin's word that keeps a story past PRUNE_AFTER (rank.REVIEWS)
COMMON = 3  # a surname this many of the sport's athletes in the registry share names a player only with their side
# Two stories about one player are one story when published within CLUSTER_WITHIN of each other and they say the
# same (same_story), in their words (headline and description) less the player's names and words every story has:
# the same figure ("179") with FIGURE_AND more words (one more for a figure under 100); or SAME_HEADLINE words of
# their headlines, HEADLINE_SHARE of the shorter one's; or SAME_WORDS words, SAME_STORY of the shorter one's. In
# those last two, the words USUAL_SHARE of the player's stories within USUAL_WITHIN have (at least USUAL_STORIES of
# them) don't count: a series' opponent and ground are in every one of its stories.
CLUSTER_WITHIN = timedelta(hours=24)
FIGURE_AND = 1  # words besides a figure two stories share to be one; one more for a figure under 100
SAME_HEADLINE, HEADLINE_SHARE = 2, 0.5
SAME_WORDS, SAME_STORY = 3, 0.3
USUAL_WITHIN, USUAL_STORIES, USUAL_SHARE = timedelta(hours=24), 3, 1 / 3
# Capitalised words that may stand before a player's surname without making it someone else's name: small words a
# title-case headline capitalises, and what a story calls a player ("Captain Verma", "GM Carlsen").
BEFORE_A_NAME = frozenset("""
a an and as at but by for from in into of on or the to v vs with without after before over says said how why who
captain skipper coach star pacer bowler batter opener spinner keeper striker forward winger midfielder defender
goalkeeper gm im grandmaster champion veteran rookie ace legend
""".split())
TITLE_CASE = 0.8  # of a text's words capitalised: written In Title Case, where capitals say nothing
STORY_WORD = 4  # letters a word needs to say what a story is about ("ruled", "injury"; not "out", "win")
# Words most sports stories have, which say nothing of which story it is.
STOP = frozenset("""
about after again against ahead also amid another back been before being best big could does down during each
first from game games happened have having here into just last latest live made make match more most much news
next over play played player players report says season some still such take than that their them then there
these they this three time today told true under updates week what when where which while will with would year
years your
""".split())


# ---------------------------------------------------------------- which feeds


@dataclass
class Listed:
    """A feed a running sport lists: the publisher's name for it, the sports whose players its stories are looked at
    for (with their groups'), and which sport's count it goes in (a group's, for an esports game's)."""

    outlet: str
    sports: set[str] = field(default_factory=set)
    counted: str = ""


def feeds(source: NewsSource) -> dict[str, Listed]:
    """The feeds of the source's shape that the running sports list, as videos' channels() are."""
    out: dict[str, Listed] = {}
    for sport in sports.ENABLED:
        for url, outlet in sport.news_feeds.items():
            if source.feed_pattern.fullmatch(url):
                listed = out.setdefault(url, Listed(outlet, counted=sport.group or sport.key))
                listed.sports.update({sport.key, *([sport.group] if sport.group else [])})
    return out


# ---------------------------------------------------------------- names


@dataclass
class Names:
    """A player's names as a story must hold them: full names (word runs) that name them alone, and one-word names
    (a surname, a nickname) that name them only as the module's docstring says, each with whether it's a surname."""

    full: list[tuple[str, ...]]
    single: dict[str, bool]
    sides: list[tuple[str, ...]]
    own: dict[str, bool] = field(default_factory=dict)  # their own one-word name, in full: whether they capitalise it


def names(players: Sequence[Followed]) -> dict[int, Names]:
    """Each player's Names, judged among these players (those a feed's stories may be about)."""
    owners: dict[str, set[int]] = {}
    for p in players:
        for name in (p.name, *p.aliases):
            for spelling in naming.spellings(name):
                for word in spelling:
                    owners.setdefault(word, set()).add(p.player_id)
    out = {}
    for p in players:
        full: set[tuple[str, ...]] = set()
        single: dict[str, bool] = {}
        own: dict[str, bool] = {}
        for name in (p.name, *p.aliases):
            for spelling in naming.spellings(name):
                if len(spelling) >= 2:
                    full.add(tuple(spelling))
                    if len(spelling) <= 3:
                        full.add(("".join(spelling),))  # a hashtag of it
                    if len(spelling[-1]) >= SHORT_NAME:
                        single[spelling[-1]] = True
                elif spelling and name == p.name and len(spelling[0]) >= 3 and owners.get(spelling[0]) == {p.player_id}:
                    full.add(tuple(spelling))  # their own one-word name or handle, no one else's
                    own[spelling[0]] = p.name.strip()[:1].isupper()
                elif spelling and len(spelling[0]) >= SHORT_NAME:
                    single.setdefault(spelling[0], False)
        sides = [tuple(w) for side in p.teams if (w := naming.words(side))]
        out[p.player_id] = Names(_longest_first(full), {w: s for w, s in single.items() if (w,) not in full}, sides, own)
    return out


def common(session: Session, players: Sequence[Followed], found: dict[int, Names]) -> set[tuple[str, str]]:
    """The (sport, surname) pairs of these players' surnames that COMMON or more of the sport's athletes in the
    registry share: one query a sport, over the registry's names."""
    wanted: dict[str, set[str]] = {}
    for p in players:
        registry = sports.of(p.sport).group or p.sport
        wanted.setdefault(registry, set()).update(w for w, surname in found[p.player_id].single.items() if surname)
    out: set[tuple[str, str]] = set()
    for registry, words in wanted.items():
        if not words:
            continue
        counts: dict[str, int] = {}
        rows = session.scalars(select(Athlete.name).where(Athlete.sport == registry, or_(*(Athlete.name.ilike(f"% {w}") for w in sorted(words)))))
        for name in rows:
            last = naming.words(name)[-1:]
            if last and last[0] in words:
                counts[last[0]] = counts.get(last[0], 0) + 1
        out |= {(registry, w) for w, n in counts.items() if n >= COMMON}
    return {(p.sport, w) for p in players for registry, w in out if registry == (sports.of(p.sport).group or p.sport)}


# ---------------------------------------------------------------- linking


@dataclass
class Linked:
    """One story that names one followed player."""

    player_id: int
    sport: str
    article: Article
    name: str
    where: str


def _texts(article: Article) -> list[tuple[str, list[str]]]:
    return [(where, naming.words(_POSSESSIVE.sub("", text))) for where, text in (("title", article.title), ("description", article.description))]


_TOKEN = re.compile(r"[^\W_]+(?:['’`.][^\W_]+)*")


def _named(text: str, word: str, names: Names, capital: bool | None = None) -> bool:
    """Whether the text has this one-word name as the player's: written capitalised (or, for their own name, as they
    write it: capital), and not next to another capitalised word that isn't theirs, a side's or one that may stand
    before a name (BEFORE_A_NAME), which would make it someone else's name ("Deepti Sharma", "Rahul Dravid"). In a
    title-case text only the word before it counts."""
    found = list(_TOKEN.finditer(text))
    tokens = [("".join(naming.words(_POSSESSIVE.sub("", m[0]))), m[0][:1].isupper()) for m in found]
    lettered = [up for w, up in tokens if w[:1].isalpha()]
    titled = len(lettered) >= 4 and sum(lettered) >= TITLE_CASE * len(lettered)
    mine = {w for phrase in names.full for w in phrase} | {w for side in names.sides for w in side} | set(names.single) | BEFORE_A_NAME

    def a_name(i: int, j: int) -> bool:
        """Token j, next to token i with only spaces between (not "Ashes: Starc", "Cummins, Starc"), is a name not theirs."""
        if not 0 <= j < len(tokens):
            return False
        a, b = sorted((i, j))
        return not text[found[a].end():found[b].start()].strip() and tokens[j][1] and tokens[j][0] not in mine

    for i, (w, up) in enumerate(tokens):
        if w != word or up != (True if capital is None else capital):
            continue
        if a_name(i, i - 1) or (not titled and a_name(i, i + 1)):
            continue
        return True
    return False


def _find(words: list[str], phrase: tuple[str, ...]) -> list[int]:
    n = len(phrase)
    return [i for i, w in enumerate(words) if w == phrase[0] and tuple(words[i:i + n]) == phrase]


def link(articles: Iterable[Article], players: Sequence[Followed], found: dict[int, Names], usual: set[tuple[str, str]]) -> list[Linked]:
    """Each story, once for each of these players it names (the module's docstring says how). found: names(players);
    usual: common()."""
    by_id = {p.player_id: p for p in players}
    owners: dict[str, list[int]] = {}
    for pid, n in found.items():
        for word in n.single:
            owners.setdefault(word, []).append(pid)
    out = []
    for article in articles:
        texts = _texts(article)
        every = [w for _, words in texts for w in words]
        hits: dict[int, tuple[str, str]] = {}
        raw = {"title": article.title, "description": article.description}
        masked = []
        for where, words in texts:
            covered: set[int] = set()
            for pid, n in found.items():
                for phrase in n.full:
                    at = _find(words, phrase)
                    if at and len(phrase) == 1 and phrase[0] in n.own and not _named(raw[where], phrase[0], n, n.own[phrase[0]]):
                        continue
                    if at:
                        hits.setdefault(pid, (" ".join(phrase), where))
                        covered |= {i + k for i in at for k in range(len(phrase))}
            masked.append((where, [w if i not in covered else "" for i, w in enumerate(words)]))
        for where, words in masked:
            for word in dict.fromkeys(w for w in words if w in owners):
                theirs = [pid for pid in owners[word] if _named(raw[where], word, found[pid])]
                if not theirs:
                    continue
                if len(owners[word]) == 1 and theirs and (by_id[theirs[0]].sport, word) not in usual and found[theirs[0]].single[word]:
                    hits.setdefault(theirs[0], (word, where))
                    continue
                sided = [pid for pid in theirs if any(_holds(every, side) for side in found[pid].sides)]
                if len(sided) == 1:
                    hits.setdefault(sided[0], (word, where))
        out += [Linked(pid, by_id[pid].sport, article, *hit) for pid, hit in hits.items()]
    return out


# ---------------------------------------------------------------- keeping


def external_id(url: str) -> str:
    """A story's id in feed_candidates: a digest of its address (which may be longer than the column)."""
    return hashlib.blake2b(url.encode(), digest_size=16).hexdigest()


def candidate(found: Linked) -> dict[str, Any]:
    """What a story's row keeps: never more of the story than the feed said."""
    a = found.article
    return {
        "title": a.title, "description": a.description, "channel": a.outlet, "channel_id": a.feed, "image": a.image,
        "published": a.published.isoformat() if a.published else None, "named": found.name, "where": found.where, "sport": found.sport,
    }  # fmt: skip


def keep(source: NewsSource, found: list[Linked], now: datetime) -> int:
    """Store what's new of what was found, one row per player and story; returns how many."""
    kept = 0
    with db.session() as session:
        for f in found:
            # Its own savepoint: a player removed meanwhile fails only their row.
            try:
                with session.begin_nested():
                    added = session.scalar(insert(FeedCandidate).values(
                        player_id=f.player_id, source=source.system, external_id=external_id(f.article.url), url=f.article.url,
                        candidate=candidate(f), features={"outlets": 1}, fetched_at=now, checked_at=now,
                    ).on_conflict_do_nothing(index_elements=["player_id", "external_id"]).returning(FeedCandidate.id))  # fmt: skip
                    kept += added is not None
            except Exception as exc:  # noqa: BLE001
                log.info("feed: story %s for player %s not kept: %s", f.article.url, f.player_id, type(exc).__name__)
    return kept


def poll(now: datetime | None = None) -> dict[str, dict[str, int]]:
    """Read every running news source's feeds once, and keep what's new. Returns what it did for each sport (a
    group's for an esports game's): feeds read and unread, stories read (published in the last KEEP_FOR), matched
    (a story for each player it names) and kept (new rows)."""
    now = now or datetime.now(timezone.utc)
    done: dict[str, dict[str, int]] = {}
    running = [(source, feeds(source)) for source in news()]
    with db.session() as session:
        players = followed(session)
        by_sports: dict[frozenset[str], tuple[list[Followed], dict[int, Names], set[tuple[str, str]]]] = {}
        for _, listing in running:
            for listed in listing.values():
                keys = frozenset(listed.sports)
                if keys not in by_sports:
                    group = [p for p in players if p.sport in keys]
                    theirs = names(group)
                    by_sports[keys] = (group, theirs, common(session, group, theirs))
    for source, listing in running:
        for url, listed in listing.items():
            if shutdown.stopping.is_set():
                return done
            counts = done.setdefault(listed.counted, {"feeds": 0, "unread": 0, "read": 0, "matched": 0, "kept": 0})
            try:
                stories = source.articles(url, listed.outlet)
            except budget.RateLimited as exc:
                log.warning("feed: %s: %s; its other feeds wait for the next poll", source.system, exc)
                break
            except AdapterError as exc:
                counts["unread"] += 1
                log.info("feed: %s feed %s not read: %s", source.system, url, exc)
                continue
            counts["feeds"] += 1
            fresh = [a for a in stories if a.published is not None and now - KEEP_FOR <= a.published <= now + timedelta(hours=1)]
            counts["read"] += len(fresh)
            found = link(fresh, *by_sports[frozenset(listed.sports)])
            counts["matched"] += len(found)
            counts["kept"] += keep(source, found, now)
    metrics.feed("kept", sum(c["kept"] for c in done.values()))
    return done


def prune(now: datetime | None = None) -> dict[str, int]:
    """Weekly: drop the stories fetched more than PRUNE_AFTER ago, but those an admin approved or pinned."""
    now = now or datetime.now(timezone.utc)
    with db.session() as session:
        gone = session.execute(delete(FeedCandidate).where(
            FeedCandidate.source.in_(list(NEWS)), FeedCandidate.fetched_at < now - PRUNE_AFTER,
            or_(FeedCandidate.review.is_(None), FeedCandidate.review.not_in(EVERGREEN)),
        ).returning(FeedCandidate.id)).all()  # fmt: skip
    if gone:
        log.info("feed: pruned %d stories older than %d days", len(gone), PRUNE_AFTER.days)
    return {"pruned": len(gone)}


# ---------------------------------------------------------------- the same story


# A number that isn't one of the story's figures: a year, a season ("2026-27"), an age ("the 29-year-old", "aged 44").
_YEAR = re.compile(r"(?:19|20)\d\d")
_NOT_A_FIGURE = re.compile(r"\b(?:19|20)\d\d\s*[-–/]\s*\d\d\b|\b\d+[- ]?years?[- ]old\b|\baged? \d+\b", re.I)


def _stem(word: str) -> str:
    """A word's stem, so that "wickets" and "wicket", "giving" and "give" are one: an ending off, then a last e."""
    for end in ("ing", "ed", "es", "s"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            word = word[: -len(end)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def story_words(title: str, description: str, drop: Iterable[str] = ()) -> set[str]:
    """What a story is about, as words to compare with another's: its headline's and description's words of at
    least STORY_WORD letters, stemmed (_stem), with "Bavuma's" read as Bavuma, and its figures (a score: "179";
    two digits at least, and no year, season or age), but the player's names (drop) and the words every story
    has (STOP)."""
    dropped = {w for name in drop for w in naming.words(name)}
    out = set()
    for w in naming.words(_NOT_A_FIGURE.sub(" ", _POSSESSIVE.sub("", f"{title} {description}"))):
        if w in dropped or w in STOP:
            continue
        if w.isdigit():
            if len(w) >= 2 and not _YEAR.fullmatch(w):
                out.add(w)
        elif len(w) >= STORY_WORD:
            out.add(_stem(w))
    return out


@dataclass(frozen=True)
class Story:
    """One stored story of one player's, as clusters() compares them: its words (story_words), and its headline's."""

    id: int
    published: datetime | None
    words: frozenset[str]
    headline: frozenset[str] = frozenset()


def story(sid: int, published: datetime | None, title: str, description: str, drop: Iterable[str] = ()) -> Story:
    """A stored story as clusters() compares it; drop: the player's names."""
    drop = list(drop)
    return Story(sid, published, frozenset(story_words(title, description, drop)), frozenset(story_words(title, "", drop)))


def same_story(a: Story, b: Story, usual: frozenset[str] = frozenset()) -> bool:
    """Whether two of one player's stories tell the same thing, as the outlets that ran it word it: they give the
    same figure (a score) and another word or two (FIGURE_AND); or their headlines share SAME_HEADLINE words, at
    least HEADLINE_SHARE of the shorter one's; or their words share SAME_WORDS, at least SAME_STORY of the shorter
    one's. Neither of the last two counts the usual words (usual_words) of the days they're from."""
    shared = a.words & b.words
    if any(w.isdigit() and len(shared) > FIGURE_AND + (len(w) < 3) for w in shared):
        return True
    heads = (a.headline & b.headline) - usual
    if len(heads) >= SAME_HEADLINE and len(heads) >= HEADLINE_SHARE * min(len(a.headline - usual), len(b.headline - usual)):
        return True
    rest = shared - usual
    return len(rest) >= SAME_WORDS and len(rest) >= SAME_STORY * min(len(a.words - usual), len(b.words - usual))


def usual_words(stories: Sequence[Story]) -> dict[int, frozenset[str]]:
    """Each dated story's usual words: those at least USUAL_STORIES and USUAL_SHARE of the player's stories published
    within USUAL_WITHIN of it have (itself too). Who they played, where and in what ("Australia", "Gqeberha",
    "second Test") are in every story of a series, whichever day they tell. A story with fewer around it has none."""
    dated = sorted(((s.published, s) for s in stories if s.published is not None), key=lambda p: (p[0], p[1].id))
    out: dict[int, frozenset[str]] = {}
    lo = hi = 0
    for at, s in dated:
        while dated[lo][0] < at - USUAL_WITHIN:
            lo += 1
        while hi < len(dated) and dated[hi][0] <= at + USUAL_WITHIN:
            hi += 1
        counts = Counter(w for _, o in dated[lo:hi] for w in o.words if not w.isdigit())
        out[s.id] = frozenset(w for w, n in counts.items() if n >= USUAL_STORIES and n >= USUAL_SHARE * (hi - lo))
    return out


def clusters(stories: Iterable[Story]) -> list[list[int]]:
    """One player's stories grouped by what they're about, oldest first: each joins the group of the first story
    published within CLUSTER_WITHIN before it that's the same story (same_story), else starts one. A story with no
    time is a group of its own."""
    stories = list(stories)
    usual = usual_words(stories)
    groups: list[list[Story]] = []
    for s in sorted(stories, key=lambda s: (s.published or datetime.max.replace(tzinfo=timezone.utc), s.id)):
        home = None
        if s.published is not None:
            home = next((g for g in groups for o in g if o.published is not None and s.published - o.published <= CLUSTER_WITHIN
                         and same_story(s, o, usual.get(s.id, frozenset()) | usual.get(o.id, frozenset()))), None)  # fmt: skip
        if home is None:
            groups.append([s])
        else:
            home.append(s)
    return [[s.id for s in g] for g in groups]
