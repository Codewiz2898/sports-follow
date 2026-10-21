"""Sources describe themselves (adapters.base.Adapter), and nothing outside a source's own modules names it,
so adding a source is its module plus one entry in adapters.KNOWN (CLAUDE.md, "Code organization")."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from sports_follow import agent, search, sports
from sports_follow.adapters import ADAPTERS, KNOWN, VIDEOS, Provider, budget, providers, source_name

PACKAGE = Path(__file__).resolve().parent.parent / "sports_follow"
# Every module in adapters/ is a source's, but what all of them share: what an adapter is, the budget, and the
# cache their answers are kept in (which Wikidata's and Wikipedia's are kept in too: wiki.py).
SOURCE_MODULES = {p.stem for p in (PACKAGE / "adapters").glob("*.py")} - {"__init__", "base", "budget", "cache"}


def test_every_source_describes_itself():
    for system, adapter in KNOWN.items():
        assert adapter.system == system and adapter.sports and all(sports.get(k) for k in adapter.sports) and adapter.live_cadence > 0, system
        assert adapter.name and adapter.covers and isinstance(adapter.provider, Provider), system
        assert not adapter.id_pattern.fullmatch(""), system
    assert {p.name for p, _ in providers(KNOWN)} == {"Sportmonks", "ESPN", "Lichess", "PandaScore", "Liquipedia", "PUBG"}
    # The feed's video sources (adapters.VIDEOS) describe themselves too.
    for system, source in VIDEOS.items():
        assert source.system == system and system not in KNOWN and source.name and source.covers, system
        assert isinstance(source.provider, Provider) and source.budgets(), system
        assert not source.id_pattern.fullmatch("") and not source.channel_pattern.fullmatch(""), system


def test_providers_keep_the_order_of_preference():
    order = [(p.name, [a.system for a in adapters]) for p, adapters in providers(KNOWN)]
    assert order[:3] == [
        ("Sportmonks", ["sportmonks_football", "sportmonks_cricket"]),
        ("ESPN", ["espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis"]),
        ("Lichess", ["lichess_chess"]),
    ]


def test_a_source_no_longer_known_is_named_by_its_system():
    # A stored binding or event may name a source since removed.
    assert source_name("espn_cricket") == "ESPNcricinfo" and source_name("sportmonks_cricket") == "Sportmonks"
    assert source_name("gone_source") == "gone_source"


def test_live_searches_are_asked_only_under_the_sports_their_sources_serve(monkeypatch):
    monkeypatch.setattr(search, "providers", lambda: providers(KNOWN))

    def asked(query: str, sport: str | None = None, by_word: bool = False) -> list[str]:
        return [p.name for p, _ in search._live(query, sport, by_word)]

    assert asked("kohli") == asked("kohli", "cricket") == ["ESPN"]
    assert asked("carlsen", "chess") == ["Lichess"]  # FIDE's list only for a chess search
    assert asked("ca", "chess") == []  # three letters before Lichess is asked
    assert asked("tenz", "valorant") == []
    assert asked("clark", by_word=True) == ["ESPN"]  # did-you-mean, word by word: ESPN has no typo tolerance


def _docstrings(tree: ast.AST) -> set[int]:
    nodes = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(n.body[0].value) for n in nodes if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}


def _naming() -> Callable[[str], str | None]:
    """How a text names a source, if it does: by its system ("espn_cricket"); by its URL, on the domain (the
    last two labels) of its provider's site or of where its requests go ("espn.com"); or by name, its
    provider's or an API's it reads, in any case ("ESPN", "OpenDota", or "opendota", its budget's key). A
    game as a source writes it (a slug in its games) or as the app names it (an esports game's key, label or
    other name, as written or in lower case) names the game, not a source ("pubg", "PUBG Mobile"), unless
    it's exactly a provider's name ("PUBG")."""
    provider_names = {p.name for p, _ in providers(KNOWN)}
    systems = re.compile(rf"\b({'|'.join([*KNOWN, *VIDEOS])})\b")  # the feed's video sources too
    names = sorted(provider_names | {b.name for b in budget.known()}, key=len, reverse=True)  # "lichess-anonymous" first
    by_name = re.compile(rf"\b({'|'.join(map(re.escape, names))})\b", re.I)
    written = {g for a in KNOWN.values() for g in getattr(a, "games", ())}
    written |= {n for s in sports.ALL if s.group for g in (s.key, s.label, *s.names) for n in (g, g.lower())}
    games = sorted(written - provider_names, key=len, reverse=True)
    game = re.compile(rf"\b({'|'.join(map(re.escape, games)) or '(?!)'})\b")
    urls = [p.url for p, _ in providers(KNOWN)] + [u for b in budget.known() for u in b.urls]
    domains = {".".join((urlparse(u).hostname or "").split(".")[-2:]) for u in urls}
    by_url = re.compile(rf"\b({'|'.join(map(re.escape, domains))})\b", re.I)

    def named(text: str) -> str | None:
        found = [*systems.findall(text), *by_url.findall(text), *by_name.findall(game.sub(" ", text))]
        return found[0] if found else None

    return named


def test_a_source_is_known_by_its_system_its_name_or_its_url():
    named = _naming()
    assert [named(t) for t in ("espn_cricket:253802", "e.g. ESPN (", " and OpenDota", "LIQUIPEDIA", "credit PUBG")] == ["espn_cricket", "ESPN", "OpenDota", "LIQUIPEDIA", "PUBG"]
    assert [named(t) for t in ('if b.name == "espn":', '{"opendota": 1}')] == ["espn", "opendota"]
    # "lichess-anonymous" is a budget of its own only with a Lichess token (as on a developer's machine):
    # reported whole then, as "lichess" without one (as in CI). Either way it names the source.
    assert named("lichess-anonymous") in ("lichess-anonymous", "lichess")
    assert [named(t) for t in ("https://pubg.com/en/news", "https://site.api.espn.com/apis/x", "https://www.opendota.com/matches/1")] == ["pubg.com", "espn.com", "opendota.com"]
    # Not a source: a game (its slug, its name), a website none of them reads.
    assert [named(t) for t in ("valorant|pubg|dota", "PUBG Mobile", "PUBG: Battlegrounds", "ESPNcricinfo pages", "https://www.espncricinfo.com")] == [None] * 5


def test_no_shared_module_names_a_source():
    named = _naming()
    found = []
    shared = [PACKAGE / "adapters" / f"{m}.py" for m in ("base", "budget")]  # what every source shares, beside them
    for path in [*PACKAGE.glob("*.py"), *(PACKAGE / "sports").glob("*.py"), *shared]:
        tree = ast.parse(path.read_text())
        skip = _docstrings(tree)
        # A sport's Wikidata ids are keyed by the id they hold, which may be a source's ("espn_cricket").
        wikidata = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and [getattr(t, "id", None) for t in n.targets] == ["wikidata"]]
        skip |= {id(n) for w in wikidata for n in ast.walk(w)}
        # A game's names are the game's, one its provider shares too ("PUBG", sports/esports.py's GAMES).
        games = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Game"]
        skip |= {id(n) for g in games for n in ast.walk(g)}
        # The platforms the Feed's judge knows (curate.Source, Literal["youtube", "x", …]) are where shares are, not sources.
        platforms = [n for n in ast.walk(tree) if isinstance(n, ast.Subscript) and getattr(n.value, "id", None) == "Literal"]
        skip |= {id(n) for p in platforms for n in ast.walk(p)}
        # So are the platforms a game's broadcasts are on (watch.PLATFORMS' Platform("YouTube", …)): where it's watched.
        streamed = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Platform"]
        skip |= {id(n) for p in streamed for n in ast.walk(p)}
        for node in ast.walk(tree):
            module = (getattr(node, "module", None) or "").split(".")
            if isinstance(node, ast.ImportFrom) and path in shared and node.level == 1:
                imported = [node.module] if node.module else [a.name for a in node.names]
                found += [f"{path.name}:{node.lineno} imports {m}" for m in imported if m in SOURCE_MODULES]  # from . import espn
            elif isinstance(node, ast.ImportFrom) and module[-2:-1] == ["adapters"] and module[-1] in SOURCE_MODULES:
                found.append(f"{path.name}:{node.lineno} imports adapters.{module[-1]}")  # from .adapters.espn import …
            elif isinstance(node, ast.ImportFrom) and module[-1] == "adapters":
                found += [f"{path.name}:{node.lineno} imports {a.name}" for a in node.names if a.name in SOURCE_MODULES]  # from .adapters import espn
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip and (name := named(node.value)):
                found.append(f"{path.name}:{node.lineno} names {name}")
    assert not found, "\n".join(found)


def test_the_research_agent_is_pointed_only_at_the_running_sources_apis():
    # Today's sources, word for word as the prompt said it when it named them itself.
    line = next(x for x in agent.system_prompt(KNOWN).splitlines() if "Cricbuzz" in x)
    assert line == (
        "- Public JSON APIs are often the most reliable live source and can be fetched directly, e.g. "
        "ESPN (https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard, .../summary?event={id}; "
        "leagues like soccer/eng.1, soccer/ksa.1, soccer/uefa.champions, basketball/nba, tennis/atp) "
        "and Lichess (https://lichess.org/api/broadcast/top). Cricbuzz and ESPNcricinfo pages work for cricket."
    )
    # A public build (licensed sources only) has no ESPN; without ESPN's football source, no football leagues.
    public = agent.system_prompt({s: a for s, a in KNOWN.items() if a.provider.licensed})
    assert "e.g. Lichess (https://lichess.org/api/broadcast/top). Cricbuzz" in public and "espn.com" not in public
    assert "leagues like basketball/nba, tennis/atp)" in agent.system_prompt({s: a for s, a in KNOWN.items() if s != "espn_soccer"})
    assert "- Cricbuzz and ESPNcricinfo pages work for cricket.\n" in agent.system_prompt({}) and "Public JSON" not in agent.system_prompt({})
    assert agent.system_prompt() == agent.system_prompt(ADAPTERS)


def test_the_research_agent_is_pointed_only_at_urls_its_sources_budget():
    # What the agent fetches there spends the source's own allowance (tools.fetch_url), never nothing.
    for provider, theirs in providers(KNOWN):
        if provider.research:
            urls = re.findall(r"https?://[^\s,;)]+", provider.research(theirs))
            assert urls and all(budget.for_url(u) in provider.budgets for u in urls), provider.name


def test_no_source_borrows_from_another():
    # What two sources of a sport share lives in sports/ (Sportmonks cricket once read ESPN's scorecard
    # parser), so removing one never breaks another. A provider's client module (espn, sportmonks) is
    # shared by its own adapters.
    adapter_modules = {type(a).__module__.rsplit(".", 1)[-1] for a in [*KNOWN.values(), *VIDEOS.values()]}
    found = []
    for name in adapter_modules:
        tree = ast.parse((PACKAGE / "adapters" / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                imported = [node.module] if node.module else [a.name for a in node.names]
                found += [f"{name} imports {m}" for m in imported if m in adapter_modules - {name}]
    assert not found, "\n".join(found)
