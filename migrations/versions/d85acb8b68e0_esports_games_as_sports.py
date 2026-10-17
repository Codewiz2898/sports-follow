"""Each esports game a sport of its own (BHA-266): the registry's esports pros, and what hangs off them, move
from "esports" to their game ("valorant", "cs2", "bgmi"), data only.

- Registry rows move by the game their source keeps as their league (a PandaScore slug, a Liquipedia wiki,
  PUBG's "pubg"); an Indian player on the PUBG Mobile wiki plays BGMI. A row whose game can't be told stays
  "esports", the esports group: still found by search, and matched to Wikidata with the games.
- An operator's decisions (kept, removed, unmatched) and starter pins follow their athlete's row. One whose
  row is gone (a removed pro) stays "esports", which the app reads as every game's: a removed pro stays
  removed whichever game they come back in.
- A player whose sport is "Esports" (followed before their game was known) takes their game from their
  source binding, as their next refresh would; their events follow them. Players already named by their
  game ("CS2") are that game's already.
- A campaign page of "esports" whose teams and players are all of one game becomes that game's; one of
  several games' (an event of many) stays "esports", which takes a team or player of any game.
- The admin panel's sports turned off: "esports" becomes every game.

The game keys and names are written out here, as they are today, so this runs the same whatever the code
says later. Downgrading moves them all back to "esports" (a pro in two games keeps one row), and every game
turned off back to "esports"; players keep their game's name, which the app before this read as esports.

Revision ID: d85acb8b68e0
Revises: 723d839b685f
Create Date: 2026-10-16
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "d85acb8b68e0"
down_revision = "723d839b685f"
branch_labels = None
depends_on = None

GROUP = "esports"
# Each game: its key, and the name the app shows.
GAMES = {
    "valorant": "Valorant", "cs2": "CS2", "lol": "League of Legends", "dota2": "Dota 2", "bgmi": "BGMI", "freefire": "Free Fire",
    "pubgmobile": "PUBG Mobile", "pubg": "PUBG", "mlbb": "Mobile Legends", "hok": "Honor of Kings", "r6siege": "Rainbow Six Siege",
    "overwatch": "Overwatch", "apex": "Apex Legends", "rocketleague": "Rocket League", "cod": "Call of Duty", "wildrift": "Wild Rift",
}
# A registry row's league (its source's name for the game) -> the game.
LEAGUES = {
    "valorant": "valorant", "cs-go": "cs2", "league-of-legends": "lol", "dota-2": "dota2", "mlbb": "mlbb", "r6-siege": "r6siege", "ow": "overwatch",
    "rl": "rocketleague", "cod-mw": "cod", "lol-wild-rift": "wildrift", "kog": "hok",  # PandaScore's slugs
    "pubgmobile": "pubgmobile", "freefire": "freefire", "apexlegends": "apex",  # Liquipedia's wikis
    "pubg": "pubg", "bgmi": "bgmi",  # PUBG's own; an operator's
}
INDIA = {"pubgmobile": "bgmi"}  # a league whose Indian players play another game
# The game a binding's locator names (its game as the app named it then, lower case), or its league.
NAMES = {**{label.lower(): label for label in GAMES.values()}, "king of glory": "Honor of Kings", "free fire max": "Free Fire"}
GENERIC = ("esports", "e-sports", "electronic sports")
# What hangs off a registry row by (qid, sport).
DECISIONS = ("athlete_override", "athlete_unmatch", "starter")


def _values(pairs: dict[str, str]) -> str:
    return ", ".join(f"('{k}', '{v}')" for k, v in pairs.items())  # constants above, never input


def upgrade() -> None:
    db = op.get_bind()
    india = " ".join(f"WHEN m.league = '{league}' AND lower(coalesce(a.country, '')) = 'india' THEN '{game}'" for league, game in INDIA.items())
    db.execute(sa.text(f"""
        UPDATE athlete a SET sport = CASE {india} ELSE m.game END
        FROM (VALUES {_values(LEAGUES)}) AS m(league, game)
        WHERE a.sport = '{GROUP}' AND a.league = m.league"""))
    for table in DECISIONS:
        db.execute(sa.text(f"""
            UPDATE {table} t SET sport = a.sport FROM athlete a
            WHERE t.sport = '{GROUP}' AND a.qid = t.qid AND a.sport <> '{GROUP}'"""))
    generic = ", ".join(f"'{g}'" for g in GENERIC)
    db.execute(sa.text(f"""
        UPDATE player p SET sport = m.label
        FROM source_binding b, (VALUES {_values(NAMES)}) AS m(name, label)
        WHERE b.player_id = p.id AND b.purpose = 'fixtures' AND lower(p.sport) IN ({generic}) AND lower(b.locator->>'game') = m.name"""))
    by_league = {league: GAMES[game] for league, game in LEAGUES.items()}
    db.execute(sa.text(f"""
        UPDATE player p SET sport = m.label
        FROM source_binding b, (VALUES {_values(by_league)}) AS m(league, label)
        WHERE b.player_id = p.id AND b.purpose = 'fixtures' AND lower(p.sport) IN ({generic}) AND b.locator->>'league' = m.league"""))
    db.execute(sa.text(f"""
        UPDATE event e SET sport = p.sport FROM event_player ep JOIN player p ON p.id = ep.player_id
        WHERE ep.event_id = e.id AND lower(e.sport) IN ({generic}) AND lower(p.sport) NOT IN ({generic})"""))
    by_label = NAMES  # a team's game as its source named it, lower case -> the game's name
    key_of = {label: key for key, label in GAMES.items()}
    for cid, teams, qids in db.execute(sa.text(f"SELECT id, teams, players FROM campaign WHERE sport = '{GROUP}'")).all():
        games = {key_of.get(by_label.get(str(t.get("game") or "").lower(), ""), "?") for t in teams or []}
        if qids:
            games |= set(db.execute(sa.text("SELECT DISTINCT sport FROM athlete WHERE qid = ANY(:qids)"), {"qids": list(qids)}).scalars())
        if len(games) == 1 and (game := games.pop()) in GAMES:
            db.execute(sa.text("UPDATE campaign SET sport = :game WHERE id = :id"), {"game": game, "id": cid})
    off = db.execute(sa.text("SELECT value FROM control WHERE key = 'sports_off'")).scalar()
    if isinstance(off, list) and GROUP in off:
        now = sorted({*(k for k in off if k != GROUP), *GAMES})
        db.execute(sa.text("UPDATE control SET value = CAST(:value AS jsonb), updated_at = now() WHERE key = 'sports_off'"), {"value": json.dumps(now)})


def downgrade() -> None:
    db = op.get_bind()
    games = ", ".join(f"'{g}'" for g in GAMES)
    for table in ("athlete", *DECISIONS):
        # A qid in two games (or in a game and with no game) keeps one row: "esports" holds one a qid.
        db.execute(sa.text(f"""
            DELETE FROM {table} t USING {table} o
            WHERE t.qid = o.qid AND t.sport IN ({games}) AND (o.sport = '{GROUP}' OR (o.sport IN ({games}) AND o.id < t.id))
            {"AND t.person IS NOT DISTINCT FROM o.person" if table == "athlete_unmatch" else ""}"""))
        db.execute(sa.text(f"UPDATE {table} SET sport = '{GROUP}' WHERE sport IN ({games})"))
    db.execute(sa.text(f"UPDATE campaign SET sport = '{GROUP}' WHERE sport IN ({games})"))
    db.execute(sa.text("UPDATE player SET sport = 'King of Glory' WHERE sport = 'Honor of Kings'"))  # the name before
    off = db.execute(sa.text("SELECT value FROM control WHERE key = 'sports_off'")).scalar()
    if isinstance(off, list) and set(GAMES) <= set(off):
        now = sorted({*(k for k in off if k not in GAMES), GROUP})
        db.execute(sa.text("UPDATE control SET value = CAST(:value AS jsonb), updated_at = now() WHERE key = 'sports_off'"), {"value": json.dumps(now)})
