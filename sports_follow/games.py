"""Esports by game. A pro's sport is the game they play, as fans name it ("Valorant", "CS2", "BGMI",
"Free Fire"), not "Esports"; esports is the family those games share: one search filter, one set of
moment and form rules, one colour in the app. The registry keeps "esports" as the athlete's sport.
"""

from __future__ import annotations

from .adapters import liquipedia_esports, pandascore_esports, pubg_esports

ESPORTS = "esports"
# Every name a player's sport may have that means an esports game: the sources' own names, and what an
# AI-built page may say ("Esports", "CS:GO", "PUBG Mobile").
NAMES = {
    n.lower()
    for n in (*pandascore_esports.GAMES.values(), *liquipedia_esports.WIKIS.values(), *liquipedia_esports.GAME_NAMES.values())
} | {
    "esports", "e-sports", "electronic sports", "counter-strike", "counter-strike 2", "cs:go", "lol", "dota",
    "mlbb", "honor of kings", "battlegrounds mobile india", "pubg", "pubg: battlegrounds", "apex", "call of duty: mobile", "codm",
}


def family(sport: str | None) -> str:
    """The family a sport's rules belong to: "esports" for any game, else the sport, lower case."""
    s = (sport or "").strip().lower()
    return ESPORTS if s in NAMES else s


def label(sport: str | None) -> str:
    """A sport as the app shows it: "Cricket" for "cricket", but a game's own name as written ("CS2",
    "PUBG Mobile", "League of Legends")."""
    s = (sport or "").strip()
    return s if family(s) == ESPORTS and s.lower() != ESPORTS else s.title()


def of_athlete(league: str | None, country: str | None = None) -> str | None:
    """A registry esports athlete's game, from the league their source keeps (a PandaScore slug or a
    Liquipedia wiki): "valorant" -> "Valorant"; an Indian player on the PUBG Mobile wiki plays BGMI."""
    if league == "pubg":
        return pubg_esports.GAME
    return pandascore_esports.game_name(league) or liquipedia_esports.player_game(league, country)
