"""Runtime settings, all from the environment with local-dev defaults matching docker-compose.yml."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/0")
# Where the data sources' answers are cached (adapters/cache.py), shared by the API and the worker and kept
# across deploys. In production a Redis of its own, capped and evicting the least recently used key, so a
# full cache never pushes out a job, a lock or a budget (deploy/compose.yaml). Unset: the Redis above.
CACHE_REDIS_URL = os.environ.get("SPORTS_FOLLOW_CACHE_REDIS_URL", "").strip() or REDIS_URL

# The app's name as fans see it ("Slam"). It's written once, in app.json next to this file, because the
# web build (web/vite.config.ts) and the Android app (android/generate.mjs) read it there too: the page
# title, the manifest, the service worker's notifications and the launcher are built with it. So it's not
# an environment setting: a server can't call itself anything its web app wasn't built with.
APP_NAME: str = json.loads(Path(__file__).with_name("app.json").read_text(encoding="utf-8"))["name"]

# Logs (logs.py): one line per record on stdout, "json" for a server's log collector or "text" to read,
# and our own lines from this level up (other libraries' from WARNING).
LOG_FORMAT = os.environ.get("SPORTS_FOLLOW_LOG_FORMAT", "text").strip().lower()
LOG_LEVEL = os.environ.get("SPORTS_FOLLOW_LOG_LEVEL", "INFO").strip().upper()

# The commit the image was built from (deploy.sh, through the Dockerfile): /api/health says it, so the
# deploy workflow and its summary know what's live. Empty outside a deployed image.
COMMIT = os.environ.get("SPORTS_FOLLOW_COMMIT", "").strip()
# FastAPI's own docs (/docs, /redoc and /openapi.json), which list every route: "1" on a development server
# that wants them, never on the site.
API_DOCS = os.environ.get("SPORTS_FOLLOW_API_DOCS", "").strip() == "1"
# The app's public address: compose sets it from DOMAIN (https://<domain>). Empty in development.
SITE_URL = os.environ.get("SPORTS_FOLLOW_SITE_URL", "").strip().rstrip("/")
# Where fans write about their data: the privacy page shows it (and has no Questions part without it),
# and data sources see it in the user agent.
CONTACT_EMAIL = os.environ.get("SPORTS_FOLLOW_CONTACT_EMAIL", "").strip()
# Sign-in codes a UTC day, to everyone: the mail plan's daily allowance (Resend's free plan sends 100). Past
# it, a fan hears "try tomorrow" from us rather than a failed send.
MAIL_CODES_PER_DAY = int(os.environ.get("SPORTS_FOLLOW_MAIL_CODES_PER_DAY", 100))


def user_agent(site: str, contact: str) -> str:
    """How every source and Wikidata see this app: honestly, as itself (ESPN refuses browser-like agents),
    with where to reach whoever runs it, as Wikimedia asks. Not the repo: that names who wrote the code,
    not who runs this copy of it."""
    reach = "; ".join(c for c in (site, contact) if c)
    return f"sports-follow/0.1 ({reach})" if reach else "sports-follow/0.1"


USER_AGENT = user_agent(SITE_URL, CONTACT_EMAIL)

# How long the research agent's words on a page stay fresh: a page only it writes is researched again when a fan
# opens it after this (seconds; pipeline.agent_due).
CARD_MAX_AGE = int(os.environ.get("SPORTS_FOLLOW_CARD_MAX_AGE", 6 * 3600))
# How often a live (or about-to-start) player gets a live check (seconds).
LIVE_CHECK_INTERVAL = int(os.environ.get("SPORTS_FOLLOW_LIVE_INTERVAL", 60))
# An event is "armed" this long before its start (seconds).
ARM_BEFORE_START = int(os.environ.get("SPORTS_FOLLOW_ARM_BEFORE", 30 * 60))

# How often a bound player's fixtures and stats are re-read from their structured source (seconds).
FIXTURES_MAX_AGE = int(os.environ.get("SPORTS_FOLLOW_FIXTURES_MAX_AGE", 30 * 60))
# How long one live-poll job keeps polling before handing over to the next tick's job (seconds).
POLL_SESSION = int(os.environ.get("SPORTS_FOLLOW_POLL_SESSION", 10 * 60))

# Retention (retention.py, daily): days a finished game keeps its poll-by-poll timeline before it's cut
# to what's still read, and days moments and the push log are kept (never fewer than the activity list's 30).
KEEP_TIMELINE_DAYS = int(os.environ.get("SPORTS_FOLLOW_KEEP_TIMELINE_DAYS", 14))
KEEP_MOMENTS_DAYS = int(os.environ.get("SPORTS_FOLLOW_KEEP_MOMENTS_DAYS", 90))

FAN_COOKIE = "sf_fan"
# The key fan cookies are signed with (server.fan_id): SPORTS_FOLLOW_COOKIE_SECRET, or this file, which a
# local server makes on first use. Every API process must read the same key, and it must never change:
# under a new key no fan's cookie verifies, and every fan starts over with nothing followed.
COOKIE_SECRET = os.environ.get("SPORTS_FOLLOW_COOKIE_SECRET", "").strip()
COOKIE_SECRET_FILE = Path(os.environ.get("SPORTS_FOLLOW_COOKIE_SECRET_FILE", Path.home() / ".config" / "sports-follow" / "cookie-secret"))

# AI researches beyond each fan's daily three (search.RESEARCH_PER_DAY): at most this many an hour across
# all fans, well under the gateway's daily budget (about 40 builds), and this many a UTC day from one
# client address, so a script that drops its cookie can't spend the day's research for everyone.
RESEARCH_PER_HOUR = int(os.environ.get("SPORTS_FOLLOW_RESEARCH_PER_HOUR", 10))
RESEARCH_PER_ADDRESS = int(os.environ.get("SPORTS_FOLLOW_RESEARCH_PER_ADDRESS", 10))
# Searches a minute, from one fan and from one client address, that may go on to the sources' own searches
# (adapters.base.LiveSearch): a source may take one request at a time, with its live games in the same line.
SOURCE_SEARCHES_PER_MINUTE = int(os.environ.get("SPORTS_FOLLOW_SOURCE_SEARCHES_PER_MINUTE", 20))
# Previews and follows a minute that ask a live source for an athlete not on the app yet, from one fan and from
# one client address: a script walking a source's ids would otherwise spend fans' share of its allowance, and
# make a player polled every half hour for each. Following a whole team is one for each player it asks for. An
# address gets more than a fan: many fans can share one (a carrier's, an office's).
SOURCE_LOOKUPS_PER_MINUTE = int(os.environ.get("SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_MINUTE", 30))
SOURCE_LOOKUPS_PER_ADDRESS = int(os.environ.get("SPORTS_FOLLOW_SOURCE_LOOKUPS_PER_ADDRESS", 120))
# Backgrounds the research agent writes an hour, across all players: who a player is, for one a live source has
# and Wikipedia has no article on (pipeline.needs_agent), new or a month old. Past it, a page goes without one
# until a fan opens it in a later hour, so following a source's ids one by one can't spend the day's model budget.
BACKGROUNDS_PER_HOUR = int(os.environ.get("SPORTS_FOLLOW_BACKGROUNDS_PER_HOUR", 6))

# The country whose athletes count double among the best-known players new fans are offered (starters.py),
# as the registry writes it ("India"). Empty: nobody counts double.
STARTERS_HOME_COUNTRY = os.environ.get("SPORTS_FOLLOW_STARTERS_HOME_COUNTRY", "India").strip()
# Where the feed's fans watch from (ISO 3166-1 alpha-2): a video that doesn't play there isn't kept (candidates.py).
FEED_COUNTRY = os.environ.get("SPORTS_FOLLOW_FEED_COUNTRY", "IN").strip().upper() or "IN"
# How the apps draw the fan's Feed (feed.layout, BHA-349): "edge", posts edge to edge, or "full", a video a screen.
# The admin panel's Feed tab changes it live; this is what it is until it does (and once the panel's word is taken back).
FEED_LAYOUT = os.environ.get("SPORTS_FOLLOW_FEED_LAYOUT", "edge").strip().lower() or "edge"
# Searches in any 24 hours for fans' videos of followed players' big moments (searches.py), each run counted: a
# moment's search runs twice. The biggest moments go first when there are more. 0 turns them off.
FEED_MOMENT_SEARCHES = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_MOMENT_SEARCHES", 40)))
# Searches in any 24 hours for a followed player with nothing to watch this week (sweeps.py: the gap search), the
# most followed first. 0 turns them off.
FEED_GAP_SEARCHES = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_GAP_SEARCHES", 20)))
# Searches in any 24 hours for each followed player's most watched videos of the last six months (sweeps.py: the
# wide sweep), each player at most weekly, a seventh of them a day. 0 turns them off.
FEED_WIDE_SEARCHES = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_WIDE_SEARCHES", 14)))
# Searches in any 7 days for a followed player's most watched videos ever (sweeps.py: the famous sweep), spread
# over the week's days; each player at most once a month. 0 turns them off.
FEED_FAMOUS_SEARCHES = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_FAMOUS_SEARCHES", 35)))
# The feed's pool of top players (feedpool.py, BHA-382): besides followed players, the curator fetches for this many
# of each running sport's best known (for a group of sports, each of its running sports' share: esports' games), the
# by Wikipedia editions, then their team's tier; their videos fill For you's others. 0 turns the pool off.
FEED_POOL_SIZE = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_POOL_SIZE", 50)))
# Pool players made a day at most, each found in their sport's live source as a fan's follow would (a request or a
# few each): the pool fills over days, and a source's allowance never pays for it all at once.
FEED_POOL_ADDS = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_POOL_ADDS", 40)))
# Searches in any 24 hours for pool players nobody follows (sweeps.py: the pool sweep), after the followed players'
# sweeps, each player at most every two weeks. 0 turns them off.
FEED_POOL_SEARCHES = max(0, int(os.environ.get("SPORTS_FOLLOW_FEED_POOL_SEARCHES", 10)))

# Which data sources run, by adapter name. Empty means every source (development). A public build lists
# only licensed ones, e.g. "lichess_chess,sportmonks_football,sportmonks_cricket": the ESPN adapters
# read unofficial endpoints whose terms forbid automated and commercial use.
SOURCES = {s.strip() for s in os.environ.get("SPORTS_FOLLOW_SOURCES", "").split(",") if s.strip()}
# Which sports run, by key ("football,cricket,chess"). Empty means every sport (sports.ALL). A sport left
# out isn't searched, imported, polled or researched: its sources don't run, it's not a search filter,
# and the pages of its players already followed stay as they are, without updates.
SPORTS = {s.strip().lower() for s in os.environ.get("SPORTS_FOLLOW_SPORTS", "").split(",") if s.strip()}
# The research agent (agent.py, through the llm-providers gateway): the background of a player with a live
# source but no English Wikipedia article, once a month (pipeline.needs_agent), and with RESEARCH the whole
# page of a player no source has. "0" runs the app without a model, as with no OpenRouter key: pages come
# from their source, with their profile from Wikidata and Wikipedia.
AGENT = os.environ.get("SPORTS_FOLLOW_AGENT", "1") != "0"
# Pages for players no live source has, which the agent writes whole: a typed name no source knows, a
# registry athlete no source has, offered as "AI research". Off by default (Bharath, 21 Oct): a player
# with no live source gets no page, isn't listed in search, and nothing offers AI research.
RESEARCH = AGENT and os.environ.get("SPORTS_FOLLOW_RESEARCH", "0") == "1"
# Live scores researched by the AI agent, for those pages. Off by default: those players' pages come from
# the agent without live scores or alerts, and no model calls go to watching games.
AGENT_LIVE = RESEARCH and os.environ.get("SPORTS_FOLLOW_AGENT_LIVE", "0") == "1"
# The content judge's model stage (curate.py): a share about a player its rules can't settle goes to a small
# model through the same gateway. "0" (or the agent off) keeps the rules alone: what they settle is judged, the
# rest waits unjudged, and nothing unjudged reaches a fan.
CURATE_AI = AGENT and os.environ.get("SPORTS_FOLLOW_CURATE_AI", "1") != "0"

# The Android app (mobile/): its package name and the SHA-256 fingerprints of its signing certificates (comma
# separated), published at /.well-known/assetlinks.json so stann.in links open it (App Links).
ANDROID_PACKAGE = os.environ.get("SPORTS_FOLLOW_ANDROID_PACKAGE", "")
ANDROID_CERT_SHA256 = [x.strip() for x in os.environ.get("SPORTS_FOLLOW_ANDROID_CERT_SHA256", "").split(",") if x.strip()]
