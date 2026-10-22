"""Credits: where the app's scores, ratings, evaluations, words, photos and code come from (the Credits page).

Built from what this build can run, so a public build (licensed sources only) credits exactly those and
a development build also names ESPN; a source or sport the admin panel has turned off for now is still credited,
as the players' pages and results it left are still served. Lichess's broadcast games and Wikipedia's articles
(each player's About paragraph, wiki.py) are CC BY-SA 4.0, which requires the credit and the licence link; Sportmonks,
Wikidata and the rest don't require one, but fans should know where a score comes from and that it isn't
an official record. Every player photo shown is listed
with its author and licence (photos.py): the licences fans may see them under all ask for the credit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import engine, sports, wiki
from .adapters import ALL, KNOWN, NEWS, VIDEOS, Provider, pick, providers
from .agent import MODEL
from .config import APP_NAME, SOURCES

# Server, web app and services, with the licence each is used under (tests check the Python ones against the
# installed packages, and that each package the web app ships, by its npm name, is here with its licence).
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
    {"name": "OpenTelemetry", "url": "https://opentelemetry.io", "licence": "Apache-2.0", "package": "opentelemetry-sdk"},
    {"name": "OpenCV", "url": "https://opencv.org", "licence": "Apache 2.0", "package": "opencv-python-headless"},
    {"name": "Pillow", "url": "https://python-pillow.github.io", "licence": "MIT-CMU", "package": "pillow"},
    {"name": "NumPy", "url": "https://numpy.org", "licence": "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "package": "numpy"},
    {"name": "YuNet face detector by Shiqi Yu", "url": "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet", "licence": "MIT"},
    {"name": "PostgreSQL", "url": "https://www.postgresql.org", "licence": "PostgreSQL"},
    {"name": "Redis", "url": "https://redis.io", "licence": "RSALv2 or SSPLv1"},
    {"name": "React", "url": "https://react.dev", "licence": "MIT", "npm": "react"},
    {"name": "React Router", "url": "https://reactrouter.com", "licence": "MIT", "npm": "react-router-dom"},
    {"name": "Vite", "url": "https://vite.dev", "licence": "MIT", "npm": "vite"},
    {"name": "chess.js by Jeff Hlywa", "url": "https://github.com/jhlywa/chess.js", "licence": "BSD-2-Clause", "npm": "chess.js"},
    {"name": "Anton font by Vernon Adams", "url": "https://fonts.google.com/specimen/Anton", "licence": "OFL-1.1"},
    {"name": "Manrope font by Mikhail Sharanda", "url": "https://fonts.google.com/specimen/Manrope", "licence": "OFL-1.1"},
    {"name": "Fredoka font by Milena Brandão", "url": "https://fonts.google.com/specimen/Fredoka", "licence": "OFL-1.1"},
]

NOTICES = [
    "Scores and statistics come from the providers above and aren't official records; if one looks wrong, the governing body's record is the one that counts.",
    f"Team, league and competition names belong to their owners. {APP_NAME} isn't affiliated with or endorsed by any league, team, player, governing body or data provider.",
]


def _joined(words: list[str]) -> str:
    """["The Guardian", "Sky Sports", "HLTV"] -> "The Guardian, Sky Sports and HLTV"."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _listed(sports: list[str]) -> str:
    """["Football", "Cricket (ESPNcricinfo)", "Tennis"] -> "Football, cricket (ESPNcricinfo) and tennis"."""
    words = list(dict.fromkeys(sports))
    return _joined([words[0], *(w[0].lower() + w[1:] for w in words[1:])])


def _model() -> str:
    """ "openrouter:moonshotai/kimi-k2.6:nitro" -> "moonshotai/kimi-k2.6 via OpenRouter"."""
    route, _, rest = MODEL.partition(":")
    name = rest.split(":")[0] if rest else route
    return f"{name} via OpenRouter" if route == "openrouter" else name


PHOTO_NOTICES = [
    "Player photos come from Wikimedia Commons, under the licences listed with each. We show each in its own colours, cropped to fit the page around the player's face and faded into it at one edge; its credit links to the whole photo.",
    "A photo shows who a player is. It doesn't mean the player endorses us.",
]


def buildable() -> dict[str, Any]:
    """The sources this build can run, by system: those it has the keys for and SPORTS_FOLLOW_SOURCES lists (or lists
    none), of the sports it may run (SPORTS_FOLLOW_SPORTS), and the Feed's video and news sources likewise. Whether or
    not the admin panel has them, or their sports, on now: turning one off stops it being read, but its players'
    pages, their results, their games' boards and the Feed's videos and stories stay up, and so does the credit their
    data's licence asks for."""
    feeds = {s.system: s for s in (*VIDEOS.values(), *NEWS.values()) if s.available() and (not SOURCES or s.system in SOURCES)}
    return {**pick(ALL, SOURCES, sports.ALLOWED), **feeds}


def _feeds(sources: Mapping[str, Any], on: Sequence[sports.Sport]) -> list[dict[str, Any]]:
    """The Feed's sources among these that read one of these sports (a channel it lists, Sport.video_channels; a
    publisher's feed, Sport.news_feeds), each credited for what it gives; a news source with the publishers whose
    feeds it reads, as each story in the Feed links its own."""
    read: dict[Provider, list[str]] = {}
    for video in VIDEOS.values():
        if video.system in sources and any(video.channel_pattern.fullmatch(c) for s in on for c in s.video_channels):
            read.setdefault(video.provider, [])
    for news in NEWS.values():
        publishers = [outlet for s in on for feed, outlet in s.news_feeds.items() if news.feed_pattern.fullmatch(feed)]
        if news.system in sources and publishers:
            read.setdefault(news.provider, []).extend(publishers)
    out = []
    for provider, publishers in read.items():
        named = f" From {_joined(list(dict.fromkeys(publishers)))}." if publishers else ""
        entry = {"name": provider.name, "url": provider.url, "what": f"The Feed: {provider.what}{named}"}
        out.append(entry | {k: v for k in ("licence", "licence_url", "note") if (v := getattr(provider, k))})
    return out


def credits(sources: Mapping[str, Any] | None = None, stockfish: bool | None = None, agent: bool | None = None, photos: list[dict[str, Any]] | None = None, on: Sequence[sports.Sport] | None = None) -> dict[str, Any]:
    """The Credits page for this build: the data providers of its sources (`sources`, by default buildable()), the
    rest it relies on, the software, and every player photo shown (photos.used). Each source describes its provider
    and what it covers (adapters.base.Provider); licensed ones come first; each sport it may run (`on`, by default
    sports.ALLOWED, those turned off for now too) adds whose work its own pages run (Sport.credits_also), and each of
    the Feed's sources that reads one of them what it gives (_feeds). The AI model and the news it summarises only
    when the research agent runs (config.AGENT)."""
    from .config import AGENT, RESEARCH

    sources = buildable() if sources is None else sources
    agent = AGENT if agent is None else agent
    adapters = {system: adapter for system, adapter in KNOWN.items() if system in sources}
    data = []
    for provider, theirs in sorted(providers(adapters), key=lambda pa: not pa[0].licensed):
        entry = {"name": provider.name, "url": provider.url, "what": f"{_listed([a.covers for a in theirs])}: {provider.what}"}
        entry |= {k: v for k in ("licence", "licence_url", "note") if (v := getattr(provider, k))}
        data.append(entry)

    also: list[dict[str, Any]] = [{
        "name": "Wikidata",
        "url": "https://www.wikidata.org",
        "what": "Player search, and the facts on each player's page: names, other names, teams, birth dates, countries, positions and major awards for about 320,000 athletes.",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    }, {
        "name": "Wikipedia",
        "url": "https://en.wikipedia.org",
        "what": "The About paragraph on a player's page: the summary of their English Wikipedia article, linked under it.",
        "licence": wiki.LICENCE,
        "licence_url": wiki.LICENCE_URL,
    }]
    if photos:
        also.append({
            "name": "Wikimedia Commons",
            "url": "https://commons.wikimedia.org",
            "what": "Player photos, on each player's page. Each one's author and licence are under Photos.",
        })
    for adapter in adapters.values():
        also += adapter.credits_also()
    if engine.enabled() if stockfish is None else stockfish:
        also.append({
            "name": "Stockfish",
            "url": "https://stockfishchess.org",
            "what": "The chess engine behind \"winning\", \"in trouble\" and turnaround alerts, run on our server.",
            "licence": "GPL-3.0",
            "licence_url": "https://www.gnu.org/licenses/gpl-3.0.html",
        })
    on = sports.ALLOWED if on is None else on
    for sport in on:
        also += sport.credits_also()
    also += _feeds(sources, on)
    if agent:
        also += [
            {
                "name": "AI",
                "what": f"The background of a player Wikipedia has no article on, news summaries{', and pages for sports without a live source' if RESEARCH else ''} are written by an AI model ({_model()}) from the sources linked on each page.",
                "note": "It can get things wrong: check the linked sources before relying on a detail.",
            },
            {
                "name": "News publishers",
                "what": "Each story links to the publisher's original; we show the headline and a short summary.",
            },
        ]
    return {
        "data": data,
        "also": also,
        "photos": [{k: p.get(k) for k in ("name", "credit", "page", "author", "licence", "licence_url")} for p in photos or []],
        "software": [{k: v for k, v in s.items() if k not in ("package", "npm")} for s in SOFTWARE],
        "notices": [*NOTICES, *(PHOTO_NOTICES if photos else [])],
    }
