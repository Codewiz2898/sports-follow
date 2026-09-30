"""Credits: where the app's scores, ratings, evaluations, words and code come from (the Credits page).

Built from what this build runs, so a public build (licensed sources only) credits exactly those and
a development build also names ESPN. Lichess's broadcast games are CC BY-SA 4.0, which requires the
credit and the licence link; Sportmonks, Wikidata and the rest don't require one, but fans should
know where a score comes from and that it isn't an official record.
"""

from __future__ import annotations

from typing import Any

from . import engine
from .adapters import ADAPTERS
from .agent import MODEL

CC_BY_SA = "https://creativecommons.org/licenses/by-sa/4.0/"

# One entry per provider; "systems" are the adapters that read from it, "sports" what each covers.
PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "Sportmonks",
        "url": "https://www.sportmonks.com",
        "systems": {"sportmonks_football": "Football", "sportmonks_cricket": "Cricket"},
        "what": "fixtures, live scores, line-ups, scorecards and player stats.",
        "note": "Collected by Sportmonks; not official league records.",
    },
    {
        "name": "Lichess",
        "url": "https://lichess.org",
        "systems": {"lichess_chess": "Chess"},
        "what": "broadcasts of over-the-board events (games, moves and clocks), shown with each organiser's delay.",
        "licence": "Broadcast games: CC BY-SA 4.0",
        "licence_url": CC_BY_SA,
        "note": "The games are shown unchanged; the evaluations beside them are ours.",
    },
    {
        "name": "ESPN",
        "url": "https://www.espn.com",
        "systems": {"espn_soccer": "Football", "espn_cricket": "Cricket (ESPNcricinfo)", "espn_basketball": "Basketball", "espn_tennis": "Tennis"},
        "what": "schedules, live scores and player stats.",
        "note": "Development builds only; a public build uses licensed sources.",
    },
]

# Server, web app and services, with the licence each is used under (tests check the Python ones
# against the installed packages).
SOFTWARE: list[dict[str, str]] = [
    {"name": "FastAPI", "url": "https://fastapi.tiangolo.com", "licence": "MIT", "package": "fastapi"},
    {"name": "Starlette", "url": "https://www.starlette.io", "licence": "BSD-3-Clause", "package": "starlette"},
    {"name": "Uvicorn", "url": "https://www.uvicorn.org", "licence": "BSD-3-Clause", "package": "uvicorn"},
    {"name": "Pydantic", "url": "https://docs.pydantic.dev", "licence": "MIT", "package": "pydantic"},
    {"name": "SQLAlchemy", "url": "https://www.sqlalchemy.org", "licence": "MIT", "package": "sqlalchemy"},
    {"name": "Alembic", "url": "https://alembic.sqlalchemy.org", "licence": "MIT", "package": "alembic"},
    {"name": "Psycopg", "url": "https://www.psycopg.org", "licence": "LGPL-3.0-only", "package": "psycopg"},
    {"name": "arq", "url": "https://github.com/python-arq/arq", "licence": "MIT", "package": "arq"},
    {"name": "redis-py", "url": "https://github.com/redis/redis-py", "licence": "MIT", "package": "redis"},
    {"name": "HTTPX", "url": "https://www.python-httpx.org", "licence": "BSD-3-Clause", "package": "httpx"},
    {"name": "pywebpush", "url": "https://github.com/web-push-libs/pywebpush", "licence": "MPL-2.0", "package": "pywebpush"},
    {"name": "py-vapid", "url": "https://github.com/web-push-libs/vapid", "licence": "MPL-2.0", "package": "py-vapid"},
    {"name": "cryptography", "url": "https://cryptography.io", "licence": "Apache-2.0 OR BSD-3-Clause", "package": "cryptography"},
    {"name": "Trafilatura", "url": "https://trafilatura.readthedocs.io", "licence": "Apache-2.0", "package": "trafilatura"},
    {"name": "DDGS", "url": "https://github.com/deedy5/ddgs", "licence": "MIT", "package": "ddgs"},
    {"name": "python-dotenv", "url": "https://github.com/theskumar/python-dotenv", "licence": "BSD-3-Clause", "package": "python-dotenv"},
    {"name": "PostgreSQL", "url": "https://www.postgresql.org", "licence": "PostgreSQL"},
    {"name": "Redis", "url": "https://redis.io", "licence": "RSALv2 or SSPLv1"},
    {"name": "React", "url": "https://react.dev", "licence": "MIT"},
    {"name": "React Router", "url": "https://reactrouter.com", "licence": "MIT"},
    {"name": "Vite", "url": "https://vite.dev", "licence": "MIT"},
    {"name": "Barlow fonts by Jeremy Tribby", "url": "https://fonts.google.com/specimen/Barlow", "licence": "OFL-1.1"},
]

NOTICES = [
    "Scores and statistics come from the providers above and aren't official records; if one looks wrong, the governing body's record is the one that counts.",
    "Team, league and competition names belong to their owners. Sports Follow isn't affiliated with or endorsed by any league, team, player, governing body or data provider.",
]


def _listed(sports: list[str]) -> str:
    """["Football", "Cricket (ESPNcricinfo)", "Tennis"] -> "Football, cricket (ESPNcricinfo) and tennis"."""
    words = list(dict.fromkeys(sports))
    words = [words[0], *(w[0].lower() + w[1:] for w in words[1:])]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _model() -> str:
    """ "openrouter:moonshotai/kimi-k2.6:nitro" -> "moonshotai/kimi-k2.6 via OpenRouter"."""
    route, _, rest = MODEL.partition(":")
    name = rest.split(":")[0] if rest else route
    return f"{name} via OpenRouter" if route == "openrouter" else name


def credits(running: dict[str, Any] | None = None, stockfish: bool | None = None) -> dict[str, Any]:
    """The Credits page for this build: the data providers it runs, the rest it relies on, the software."""
    running = ADAPTERS if running is None else running
    data = []
    for p in PROVIDERS:
        sports = [sport for system, sport in p["systems"].items() if system in running]
        if not sports:
            continue
        entry = {k: v for k, v in p.items() if k != "systems"}
        entry["what"] = f"{_listed(sports)}: {p['what']}"
        data.append(entry)

    also: list[dict[str, Any]] = [{
        "name": "Wikidata",
        "url": "https://www.wikidata.org",
        "what": "Player search: names, other names, teams and birth dates for about 320,000 athletes.",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    }]
    if "lichess_chess" in running:
        also.append({
            "name": "FIDE",
            "url": "https://ratings.fide.com",
            "what": "Chess titles and ratings, looked up player by player through Lichess.",
            "note": "Ratings are FIDE's.",
        })
    if engine.enabled() if stockfish is None else stockfish:
        also.append({
            "name": "Stockfish",
            "url": "https://stockfishchess.org",
            "what": "The chess engine behind \"winning\", \"in trouble\" and turnaround alerts, run on our server.",
            "licence": "GPL-3.0",
            "licence_url": "https://www.gnu.org/licenses/gpl-3.0.html",
        })
    also += [
        {
            "name": "AI",
            "what": f"Player profiles, news summaries, and pages for sports without a live source are written by an AI model ({_model()}) from the sources linked on each page.",
            "note": "It can get things wrong: check the linked sources before relying on a detail.",
        },
        {
            "name": "News publishers",
            "what": "Each story links to the publisher's original; we show the headline and a short summary.",
        },
    ]
    return {"data": data, "also": also, "software": [{k: v for k, v in s.items() if k != "package"} for s in SOFTWARE], "notices": NOTICES}
