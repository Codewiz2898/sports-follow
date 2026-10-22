"""A player's profile from their Wikidata person and English Wikipedia article, with no model (BHA-324).

A player linked to the registry (a "wikidata" identity, or the qid a player in an athlete's second sport
carries) gets the facts on their page from Wikidata: date of birth, the country they compete for, their
current teams (as the registry lists them: registry.team_list), their role, and the awards their sport
names (Sport.profile_nation, profile_role, honours). The About paragraph is the article's summary, from
Wikipedia's REST summary endpoint, shown with a link to the article and its licence (CC BY-SA 4.0); the
Credits page names Wikipedia.

Three requests a player, through the source cache (adapters/cache.py, namespace "wikimedia"): their
Wikidata person (only the statements read here, without their references: a footballer's whole entity is
300 KB), the names of the items it names (English, else "mul": _name), and the article's summary. A page's
first build reads them (a copy younger than MAX_AGE will do), and the scheduler reads them again once a
profile is MAX_AGE old (pipeline.due_work), so a transfer or a new award shows within a month. Wikidata and
Wikipedia ask for no key and publish no limit; requests go one at a time, with the app's user agent, as
Wikimedia asks.

What a live source says of a player wins over Wikidata (pipeline.apply_profile): a bound player's name,
sport and teams are their source's. What the research agent wrote (agent.py) is used only for what neither
has (pipeline.needs_agent): the background of a player with no article, written monthly when a fan opens
their page.

    .venv/bin/python -m sports_follow.wiki Q213854 cricket     # one player's profile, as a page gets it
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from . import registry, sports
from .adapters import cache
from .config import USER_AGENT

log = logging.getLogger("sports_follow.wiki")

API = "https://www.wikidata.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MAX_AGE = 30 * 86400  # a profile is read again after a month
REREAD = 86400  # the scheduler's monthly read takes a copy at most this old: the month is up
LABELS = 50  # items a labels request asks about (the API's limit)
LANGUAGES = "|".join(registry.NAME_LANGS)  # the labels asked for: English, and "mul" (_name)
BORN = "P569"
TEAMS = "P54"
ENDED = "P582"  # a stint's end: a team with one is a former team
STARTED = "P580"
AWARD = "P166"
WHEN = "P585"  # an award's year
LICENCE = "CC BY-SA 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
QID = re.compile(r"Q[1-9]\d{0,11}")
ABOUT_MAX = 1200  # characters of the summary the page shows at most, cut at a sentence

_client = httpx.Client(headers={"user-agent": USER_AGENT, "accept": "application/json"}, timeout=20, follow_redirects=True)
_cache = cache.Cache("wikimedia", size=500)
_turn = threading.Lock()  # this process's requests go one at a time


class Unavailable(Exception):
    """Wikidata or Wikipedia didn't answer, or asked us to wait: the page keeps what it has."""


@dataclass
class Profile:
    """What Wikidata and Wikipedia say of one player, as the page shows it."""

    qid: str
    name: str | None = None  # Wikidata's label: the English one, else "mul" (_name)
    born: str | None = None  # "1988-11-05"; "1988" when Wikidata knows only the year
    nationality: str | None = None
    role: str | None = None  # "Forward", "Batter", "Grandmaster"
    teams: list[str] = field(default_factory=list)  # current teams, as the registry lists them
    honours: list[str] = field(default_factory=list)  # "Ballon d'Or ×8", "World Chess Champion"
    summary: str | None = None  # the article's summary: the About paragraph
    article: str | None = None  # the article's address, where the paragraph links

    def facts(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


# ---------------------------------------------------------------- reading (pure, so it's tested without the network)


def _best(statements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The statements Wikidata counts as true now: the preferred ones if any, else the normal ones; never deprecated."""
    preferred = [s for s in statements if s.get("rank") == "preferred"]
    return preferred or [s for s in statements if s.get("rank") == "normal"]


def _value(statement: dict[str, Any]) -> Any:
    return ((statement.get("mainsnak") or {}).get("datavalue") or {}).get("value")


def _item(statement: dict[str, Any]) -> str | None:
    value = _value(statement)
    return value.get("id") if isinstance(value, dict) else None


def _qualifier_items(statement: dict[str, Any], prop: str) -> list[Any]:
    return [((q.get("datavalue") or {}).get("value")) for q in (statement.get("qualifiers") or {}).get(prop, [])]


def born(statements: list[dict[str, Any]]) -> str | None:
    """A date of birth as the page shows it: the day when Wikidata knows it, the year when it knows that."""
    for s in _best(statements):
        value = _value(s)
        if not isinstance(value, dict) or not str(value.get("time", "")).startswith("+"):
            continue
        day, precision = value["time"][1:11], value.get("precision")
        if precision == 11:
            try:
                return date.fromisoformat(day).isoformat()
            except ValueError:
                continue
        if precision in (9, 10):
            return day[:4]
    return None


def _year(value: Any) -> str | None:
    time = value.get("time") if isinstance(value, dict) else None
    return time[1:5] if isinstance(time, str) and time.startswith("+") else None


def wanted(claims: dict[str, list[dict[str, Any]]], kind: sports.Sport) -> list[str]:
    """The items whose names the profile needs: countries, role, teams, and the awards the sport names."""
    ids: list[str] = []
    for prop in (*kind.profile_nation, *kind.profile_role):
        ids += [i for s in _best(claims.get(prop, [])) if (i := _item(s))]
    if kind.team:
        ids += [i for s in claims.get(TEAMS, []) if s.get("rank") != "deprecated" and not (s.get("qualifiers") or {}).get(ENDED) and (i := _item(s))]
    ids += [i for s in claims.get(AWARD, []) if (i := _item(s)) in kind.honours]
    return list(dict.fromkeys(ids))


def _first(claims: dict[str, list[dict[str, Any]]], props: tuple[str, ...], labels: dict[str, str]) -> list[str]:
    """The names of the first of these properties that has any, in Wikidata's order."""
    for prop in props:
        named = [labels[i] for s in _best(claims.get(prop, [])) if (i := _item(s)) in labels]
        if named:
            return list(dict.fromkeys(named))
    return []


def _honours(claims: dict[str, list[dict[str, Any]]], kind: sports.Sport) -> list[str]:
    """The awards the sport names, each once, with how many times it was won: "Ballon d'Or ×8". A win is a
    year; an award recorded with no year counts once."""
    years: dict[str, set[str]] = {}
    undated: dict[str, int] = {}
    for s in claims.get(AWARD, []):
        title = kind.honours.get(_item(s) or "")
        if not title or s.get("rank") == "deprecated":
            continue
        dated = {y for v in _qualifier_items(s, WHEN) if (y := _year(v))}
        years.setdefault(title, set()).update(dated)
        undated[title] = undated.get(title, 0) + (not dated)
    wins = {title: len(dated) + undated[title] for title, dated in years.items()}
    return [f"{title} ×{n}" if n > 1 else title for title, n in sorted(wins.items(), key=lambda tn: -tn[1])]


def _teams(claims: dict[str, list[dict[str, Any]]], kind: sports.Sport, labels: dict[str, str]) -> list[str]:
    if not kind.team:
        return []
    stints = []
    for s in claims.get(TEAMS, []):
        label = labels.get(_item(s) or "", "")
        if s.get("rank") == "deprecated" or (s.get("qualifiers") or {}).get(ENDED) or not registry.keeps_team(kind, label):
            continue
        start = next((v["time"] for v in _qualifier_items(s, STARTED) if isinstance(v, dict) and v.get("time")), "")
        stints.append((start, registry.team_name(label), registry.is_national(label)))
    return registry.team_list(kind, stints)


def _name(labels: dict[str, Any]) -> str | None:
    """An item's name from its labels: the English one, else the one every language shares ("mul"), where
    Wikidata keeps many people's and clubs' names now (MS Dhoni's, Chennai Super Kings')."""
    return next((v for lang in registry.NAME_LANGS if isinstance(v := (labels.get(lang) or {}).get("value"), str) and v), None)


def _role(names: list[str]) -> str | None:
    """ "forward", "midfielder" -> "Forward, midfielder"."""
    text = ", ".join(names[:2])
    return text[:1].upper() + text[1:] if text else None


def about(summary: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """The About paragraph and the article's address, from Wikipedia's summary of it; none for a
    disambiguation page or an empty one. A long summary is cut at the last sentence that fits."""
    if not summary or summary.get("type") not in (None, "standard"):
        return None, None
    text = " ".join(str(summary.get("extract") or "").split())
    if len(text) > ABOUT_MAX:
        cut = text[:ABOUT_MAX]
        text = cut[: cut.rfind(". ") + 1] or cut.rsplit(" ", 1)[0] + "…"
    url = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page")
    return (text or None), (url if isinstance(url, str) and url.startswith("https://") else None)


def parse(qid: str, entity: dict[str, Any], labels: dict[str, str], summary: dict[str, Any] | None, kind: sports.Sport) -> Profile:
    """One player's profile from their Wikidata entity (claims, labels, sitelinks), the names of the items
    it names, and their article's summary."""
    claims = entity.get("claims") or {}
    nations = _first(claims, kind.profile_nation, labels)
    text, url = about(summary)
    return Profile(
        qid=qid,
        name=_name(entity.get("labels") or {}),
        born=born(claims.get(BORN, [])),
        nationality=registry.COUNTRIES.get(nations[0], nations[0]) if nations else None,
        role=_role(_first(claims, kind.profile_role, labels)),
        teams=_teams(claims, kind, labels),
        honours=_honours(claims, kind),
        summary=text,
        article=url if text else None,
    )


# ---------------------------------------------------------------- fetching


def _get(url: str, params: dict[str, str] | None = None, missing_ok: bool = False, needs: str = "entities") -> dict[str, Any]:
    """A JSON answer that has `needs` in it. The Wikidata API answers an error with HTTP 200 and {"error": …}:
    that's no answer, not an empty one, so it's never kept as one."""
    try:
        with _turn:
            cache.sending()
            r = _client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise Unavailable(f"{url}: {exc}") from exc
    if r.status_code == 404 and missing_ok:
        return {}
    if r.status_code >= 400:
        raise Unavailable(f"{url}: HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError as exc:
        raise Unavailable(f"{url}: not JSON") from exc
    if not isinstance(data, dict) or needs not in data:
        error = data.get("error") if isinstance(data, dict) else None
        raise Unavailable(f"{url}: {(error or {}).get('code') if isinstance(error, dict) else 'no ' + needs}")
    return data


def _properties() -> list[str]:
    """Every property a profile reads, in any sport: what's kept of an entity."""
    every = {BORN, TEAMS, AWARD}
    for kind in (*sports.ALL, *sports.GROUPS, sports.OTHER):
        every |= {*kind.profile_nation, *kind.profile_role}
    return sorted(every)


def entity(qid: str, ttl: float) -> dict[str, Any]:
    """A Wikidata person's labels (English and "mul"), enwiki article and the statements a profile reads,
    without their references. The key names the properties kept, so a sport reading another is fetched again."""
    props = _properties()

    def fetch() -> dict[str, Any]:
        data = _get(API, {"action": "wbgetentities", "ids": qid, "props": "labels|claims|sitelinks", "languages": LANGUAGES, "sitefilter": "enwiki", "format": "json"})
        found = (data.get("entities") or {}).get(qid) or {}
        if "missing" in found:
            return {}
        claims = {p: [{k: v for k, v in s.items() if k != "references"} for s in found.get("claims", {}).get(p, [])] for p in props}
        return {"labels": found.get("labels") or {}, "sitelinks": found.get("sitelinks") or {}, "claims": {p: c for p, c in claims.items() if c}}

    return _cache.read(f"entity:{qid}:{','.join(props)}", ttl, fetch)


def labels(ids: list[str], ttl: float) -> dict[str, str]:
    """The names of these Wikidata items, in English where they have one (_name)."""
    out: dict[str, str] = {}
    for start in range(0, len(ids), LABELS):
        batch = sorted(ids[start : start + LABELS])

        def fetch(batch: list[str] = batch) -> dict[str, str]:
            data = _get(API, {"action": "wbgetentities", "ids": "|".join(batch), "props": "labels", "languages": LANGUAGES, "format": "json"})
            return {q: label for q, e in (data.get("entities") or {}).items() if (label := _name(e.get("labels") or {}))}

        out |= _cache.read(f"labels:{'|'.join(batch)}", ttl, fetch)
    return out


def summary(title: str, ttl: float) -> dict[str, Any]:
    """Wikipedia's summary of an English article: {} when it has none."""
    return _cache.read(f"summary:{title}", ttl, lambda: _get(SUMMARY + quote(title.replace(" ", "_"), safe=""), missing_ok=True, needs="type"))


def read(qid: str, sport: str | None, ttl: float = MAX_AGE) -> Profile | None:
    """A player's profile, from copies younger than ttl seconds where the cache has them. None for an id
    that isn't a Wikidata person's (an esports pro's source key, a deleted item). Raises Unavailable."""
    if not QID.fullmatch(qid or ""):
        return None
    kind = sports.of(sport)
    found = entity(qid, ttl)
    if not found:
        return None
    named = labels(wanted(found.get("claims") or {}, kind), ttl)
    title = ((found.get("sitelinks") or {}).get("enwiki") or {}).get("title")
    return parse(qid, found, named, summary(title, ttl) if title else None, kind)


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: python -m sports_follow.wiki <Wikidata id> [sport]")
    found = read(argv[0], argv[1] if len(argv) > 1 else None)
    print(json.dumps(found.facts() if found else None, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
