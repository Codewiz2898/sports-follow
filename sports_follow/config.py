"""Runtime settings, all from the environment with local-dev defaults matching docker-compose.yml."""

from __future__ import annotations

import json
import os
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://sports:sports@127.0.0.1:5433/sports_follow"
)
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/0")

# The app's name as fans see it ("stann"). It's written once, in app.json next to this file, because the
# web build (web/vite.config.ts) and the Android app (android/generate.mjs) read it there too: the page
# title, the manifest, the service worker's notifications and the launcher are built with it. So it's not
# an environment setting: a server can't call itself anything its web app wasn't built with.
APP_NAME: str = json.loads(Path(__file__).with_name("app.json").read_text(encoding="utf-8"))["name"]

# Logs (logs.py): one line per record on stdout, "json" for a server's log collector or "text" to read,
# and our own lines from this level up (other libraries' from WARNING).
LOG_FORMAT = os.environ.get("SPORTS_FOLLOW_LOG_FORMAT", "text").strip().lower()
LOG_LEVEL = os.environ.get("SPORTS_FOLLOW_LOG_LEVEL", "INFO").strip().upper()

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

# How long a full card stays fresh before the scheduler rebuilds it (seconds).
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

# Which data sources run, by adapter name. Empty means every source (development). A public build lists
# only licensed ones, e.g. "lichess_chess,sportmonks_football,sportmonks_cricket": the ESPN adapters
# read unofficial endpoints whose terms forbid automated and commercial use.
SOURCES = {s.strip() for s in os.environ.get("SPORTS_FOLLOW_SOURCES", "").split(",") if s.strip()}
# Which sports run, by key ("football,cricket,chess"). Empty means every sport (sports.ALL). A sport left
# out isn't searched, imported, polled or researched: its sources don't run, it's not a search filter,
# and the pages of its players already followed stay as they are, without updates.
SPORTS = {s.strip().lower() for s in os.environ.get("SPORTS_FOLLOW_SPORTS", "").split(",") if s.strip()}
# Live scores researched by the AI agent, for sports no source covers. Off by default: those players'
# pages come from the agent without live scores or alerts, and no model calls go to watching games.
AGENT_LIVE = os.environ.get("SPORTS_FOLLOW_AGENT_LIVE", "0") == "1"

# The Android app (a Trusted Web Activity wrapping the web app): its package name and the SHA-256
# fingerprints of its signing certificates (comma separated), published at /.well-known/assetlinks.json.
ANDROID_PACKAGE = os.environ.get("SPORTS_FOLLOW_ANDROID_PACKAGE", "")
ANDROID_CERT_SHA256 = [x.strip() for x in os.environ.get("SPORTS_FOLLOW_ANDROID_CERT_SHA256", "").split(",") if x.strip()]
