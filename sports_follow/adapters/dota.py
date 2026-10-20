"""Dota 2 inside the map, from OpenDota, laid over a PandaScore series (pure functions; the requests are
in pandascore_esports.PandascoreEsports._dota).

PandaScore says which map of a series is on and who won each; OpenDota's live list has the map being
played (kills, clock, gold lead, each player's hero), and its finished matches every player's hero,
kills, deaths, assists, gold a minute and last hits. The two are joined by team name, and a player by
their Steam account: the OpenDota pro with the PandaScore player's name on the same team. Each map's
OpenDota match is remembered while it's live (remember), since its list of finished pro matches leaves
out smaller leagues and keeps only the last hundred.

A finished map's match page part (map_detail) is drawn from the same match: its gold lead by minute with
each Roshan marked, both sides' totals, how the map went in a line a moment (first blood, towers and
barracks, the fights that swung the gold, Roshan and the Aegis, the Ancient), all ten players with their
items, and the draft in order. Its words are written here, from the teams' short names, so the web app and
the Android app only lay it out.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .. import naming
from .base import Line

FILLER = naming.TEAM_FILLER


def team_words(name: str | None) -> str:
    """ "Team Spirit" -> "spirit", "Tundra Esports" -> "tundra"; for comparing names, never shown."""
    words = [w for w in naming.team_key(name or "").split() if w not in FILLER]
    return " ".join(words) or naming.team_key(name or "")


def same_team(a: str | None, b: str | None) -> bool:
    ka, kb = team_words(a), team_words(b)
    return bool(ka and kb) and (ka == kb or ka.replace(" ", "") == kb.replace(" ", ""))


def accounts(roster: list[dict[str, Any]], team_names: list[str], pros: list[dict[str, Any]], live_players: list[dict[str, Any]] = ()) -> dict[int, str]:
    """Steam account -> PandaScore player id, for the roster's players OpenDota can name: the pro with the
    same name on the same team (or the only pro with that name), else a live game's player of that name.
    Names compare by their letters and digits: PandaScore's "Chira JUNIOR" and "not me" are OpenDota's
    "CHIRA_JUNIOR" and "not_me", and its "watson" OpenDota's "医者watson`"."""
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in [*pros, *live_players]:
        if p.get("name") and p.get("account_id"):
            by_name[naming.team_key(p["name"])].append(p)
    found: dict[int, str] = {}
    for player in roster:
        candidates = by_name.get(naming.team_key(player.get("name") or ""), []) if naming.team_key(player.get("name") or "") else []
        on_team = [c for c in candidates if any(same_team(c.get("team_name"), t) for t in team_names)]
        pick = {c["account_id"] for c in (on_team or candidates)}
        if len(pick) == 1:
            found[int(pick.pop())] = str(player["id"])
    return found


def _sides(radiant: str | None, dire: str | None, teams: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The series' two teams as (Radiant, Dire), or None when the names aren't these two teams."""
    if len(teams) != 2:
        return None
    a, b = teams
    if same_team(radiant, a["name"]) and same_team(dire, b["name"]):
        return a, b
    if same_team(radiant, b["name"]) and same_team(dire, a["name"]):
        return b, a
    return None


def find_live(games: list[dict[str, Any]], teams: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The live game between the series' two teams, if OpenDota lists it."""
    return next((g for g in games if g.get("league_id") and _sides(g.get("team_name_radiant"), g.get("team_name_dire"), teams)), None)


def clock(seconds: Any) -> str:
    """ "37:12"; "draft" before the horn (the game clock is 0 or below while heroes are picked)."""
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}" if s > 0 else "draft"


def live_map(game: dict[str, Any], teams: list[dict[str, Any]], players: dict[int, str], hero_names: dict[int, str], n: int) -> dict[str, Any]:
    """The map being played: each side's kills, the clock, the gold lead, and the followed roster's heroes."""
    radiant, dire = _sides(game.get("team_name_radiant"), game.get("team_name_dire"), teams)
    lead = int(game.get("radiant_lead") or 0)
    return {
        "n": n,
        "clock": clock(game.get("game_time")),
        "radiant": {"id": radiant["id"], "name": radiant["name"], "kills": int(game.get("radiant_score") or 0)},
        "dire": {"id": dire["id"], "name": dire["name"], "kills": int(game.get("dire_score") or 0)},
        "gold_lead": {"id": (radiant if lead >= 0 else dire)["id"], "amount": abs(lead)},
        "heroes": {players[int(p["account_id"])]: hero_names.get(int(p.get("hero_id") or 0)) for p in game.get("players") or [] if int(p.get("account_id") or 0) in players and p.get("hero_id")},
    }


SERIES_SLACK = 3600  # OpenDota's start times run ahead of PandaScore's (17 minutes, seen 13 Oct 2026)


def find_maps(finished: list[dict[str, Any]], teams: list[dict[str, Any]], begin_at: int) -> list[int]:
    """The OpenDota matches of this series' finished maps, oldest first: between these two teams, since
    about when the series began."""
    rows = [m for m in finished if (m.get("start_time") or 0) >= begin_at - SERIES_SLACK and _sides(m.get("radiant_name"), m.get("dire_name"), teams)]
    return [int(m["match_id"]) for m in sorted(rows, key=lambda m: m["start_time"])]


def remember(known: dict[str, int], n: int, match_id: int) -> dict[str, int]:
    """The series' maps by number -> OpenDota match, with the game now live as map n, unless map n or the
    game is already known: the first sighting wins, so a map PandaScore hasn't caught up on yet (it still
    says map 2 while map 3 is on) can't take map 2's place."""
    if str(n) in known or match_id in known.values():
        return known
    return {**known, str(n): match_id}


def number_maps(ids: list[int], finished: int) -> list[tuple[int, int]]:
    """(map number, OpenDota match) for the series' finished maps. More matches than PandaScore has
    finished maps means PandaScore hasn't caught up: the first ones are its maps. Fewer means the earliest
    have left OpenDota's last 100 pro matches: the ones found are the latest maps."""
    if len(ids) >= finished:
        return [(i + 1, mid) for i, mid in enumerate(ids[:finished])]
    return [(finished - len(ids) + i + 1, mid) for i, mid in enumerate(ids)]


def map_stats(details: dict[str, Any], players: dict[int, str], hero_names: dict[int, str]) -> dict[str, dict[str, Any]]:
    """A finished map's numbers for the roster's players, by PandaScore player id."""
    out = {}
    for p in details.get("players") or []:
        pid = players.get(int(p.get("account_id") or 0))
        if pid is None:
            continue
        radiant = bool(p.get("isRadiant", (p.get("player_slot") or 0) < 128))
        out[pid] = {
            "hero": hero_names.get(int(p.get("hero_id") or 0), "?"), "kills": int(p.get("kills") or 0), "deaths": int(p.get("deaths") or 0),
            "assists": int(p.get("assists") or 0), "gpm": int(p.get("gold_per_min") or 0), "last_hits": int(p.get("last_hits") or 0),
            "won": bool(details.get("radiant_win")) == radiant,
        }
    return out


def lines(maps: list[dict[str, Any]], live: dict[str, Any] | None, final: bool) -> dict[str, Line]:
    """Each roster player's line: the hero they're on now, or their last map; the series' totals beside."""
    ids = {pid for m in maps for pid in m.get("players") or {}} | set((live or {}).get("heroes") or {})
    out = {}
    for pid in ids:
        played = [(m["n"], m["players"][pid]) for m in maps if pid in (m.get("players") or {})]
        k, d, a = (sum(p[x] for _, p in played) for x in ("kills", "deaths", "assists"))
        if final and played:
            headline = f"{k}/{d}/{a} over {len(played)} map{'s' if len(played) != 1 else ''}"
        elif live and pid in live.get("heroes", {}):
            headline = f"On {live['heroes'][pid]} · map {live['n']}"
        elif played:
            n, last = played[-1]
            headline = f"{last['kills']}/{last['deaths']}/{last['assists']} on {last['hero']} · map {n}"
        else:
            continue
        stats = []
        if played:
            stats = [
                {"label": "K/D/A", "value": f"{k}/{d}/{a}"},
                {"label": "GPM", "value": str(round(sum(p["gpm"] for _, p in played) / len(played)))},
                {"label": "Last hits", "value": str(sum(p["last_hits"] for _, p in played))},
                {"label": "Heroes", "value": ", ".join(dict.fromkeys(p["hero"] for _, p in played))},
            ]
        out[pid] = Line(headline=headline, stats=stats)
    return out


# ---------------------------------------------------------------- a finished map, for its match page

# A building in a parsed match's objectives: Radiant's are "goodguys", Dire's "badguys". Towers by tier and
# lane (the two tier 4s by the Ancient have no lane), barracks melee and ranged by lane, and the Ancient.
BUILDING = re.compile(r"npc_dota_(goodguys|badguys)_(?:tower([1-4])(?:_(top|mid|bot))?|(?:melee|range)_rax_(top|mid|bot)|(fort))$")
TOGETHER = 90  # seconds: one side's buildings falling this close together are one moment ("mid and top T1")
SWING = 3000  # gold: a fight that moves the lead this much is one of the map's moments
NUMBERS = ("no", "one", "two", "three", "four", "five")
NTH = ("", "", "second", "third", "fourth", "fifth", "sixth")


def gold(amount: int) -> str:
    """ "32.5k", "850"."""
    return f"{amount / 1000:.1f}k" if abs(amount) >= 1000 else str(amount)


def _join(parts: list[str]) -> str:
    return " and ".join(parts) if len(parts) <= 2 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _towers(fallen: list[tuple[str, str | None]]) -> str:
    """Towers by (tier, lane), as a fan says them: "mid and top T1", "bot T1 and T2", "T4s"."""
    tiers = list(dict.fromkeys(t for t, _ in fallen))
    lanes = list(dict.fromkeys(lane for _, lane in fallen if lane))
    if tiers == ["4"]:
        return "T4s" if len(fallen) > 1 else "T4"
    if len(tiers) == 1:
        return f"{_join(lanes)} T{tiers[0]}"
    if len(lanes) == 1 and all(lane for _, lane in fallen):
        return f"{lanes[0]} {_join([f'T{t}' for t in tiers])}"
    return _join([f"{lane} T{t}" if lane else f"T{t}" for t, lane in fallen])


def _buildings(objectives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each building that fell, oldest first: {"t", "radiant" (whose it was), "kind", "tier", "lane"}."""
    out = []
    for o in objectives:
        found = BUILDING.match(str(o.get("key") or "")) if o.get("type") == "building_kill" else None
        if found:
            side, tier, tower_lane, rax_lane, fort = found.groups()
            kind = "ancient" if fort else "tower" if tier else "barracks"
            out.append({"t": int(o.get("time") or 0), "radiant": side == "goodguys", "kind": kind, "tier": tier, "lane": tower_lane or rax_lane})
    return sorted(out, key=lambda b: b["t"])


def map_detail(details: dict[str, Any], n: int, teams: list[dict[str, Any]], players: dict[int, str], names: dict[str, str], hero_names: dict[int, str], item_names: dict[int, str]) -> dict[str, Any] | None:
    """A finished map's part of its match page, from its OpenDota match: None where its sides aren't the
    series' two teams. Before OpenDota has parsed the replay there are only the players and the draft; the
    gold, the marks and the moments come with the parse."""
    sides = _sides(details.get("radiant_name") or (details.get("radiant_team") or {}).get("name"), details.get("dire_name") or (details.get("dire_team") or {}).get("name"), teams)
    if sides is None:
        return None
    radiant, dire = sides
    side = {True: radiant, False: dire}
    word = {True: naming.short_team(radiant["name"]), False: naming.short_team(dire["name"])}
    rows = details.get("players") or []
    slot = {i: p for i, p in enumerate(rows)}  # objectives name players by their place in this list

    def is_radiant(p: dict[str, Any]) -> bool:
        return bool(p.get("isRadiant", (p.get("player_slot") or 0) < 128))

    def name(p: dict[str, Any] | None) -> str:
        pid = players.get(int((p or {}).get("account_id") or 0))
        return (names.get(pid) if pid else None) or (p or {}).get("name") or ""

    def hero(p: dict[str, Any] | None) -> str:
        return hero_names.get(int((p or {}).get("hero_id") or 0), "?")

    by_slot = {int(p.get("player_slot") or 0): p for p in rows}
    lead = [int(v) for v in details.get("radiant_gold_adv") or []]
    events: list[dict[str, Any]] = []
    marks: list[dict[str, Any]] = []
    roshans = {True: 0, False: 0}
    objectives = sorted(details.get("objectives") or [], key=lambda o: o.get("time") or 0)
    for o in objectives:
        t, kind = int(o.get("time") or 0), o.get("type")
        if kind == "CHAT_MESSAGE_FIRSTBLOOD":
            killer, victim = by_slot.get(int(o.get("player_slot") or 0)), slot.get(int(o.get("key") or -1))
            if killer:
                events.append({"t": t, "title": f"First blood to {word[is_radiant(killer)]}", "note": f"{name(killer)}, {hero(killer)}" + (f", on {name(victim)}" if victim and name(victim) else ""), "team": side[is_radiant(killer)]["id"]})
        elif kind in ("CHAT_MESSAGE_ROSHAN_KILL", "CHAT_MESSAGE_MINIBOSS_KILL") and o.get("team") in (2, 3):
            ours = o["team"] == 2
            if kind == "CHAT_MESSAGE_MINIBOSS_KILL":
                events.append({"t": t, "title": f"Tormentor to {word[ours]}", "team": side[ours]["id"]})
                continue
            roshans[ours] += 1
            aegis = next((by_slot.get(int(a.get("player_slot") or 0)) for a in objectives if a.get("type") == "CHAT_MESSAGE_AEGIS" and 0 <= (a.get("time") or 0) - t <= 10), None)
            title = f"Roshan to {word[ours]}" + (f", their {NTH[roshans[ours]]}" if 1 < roshans[ours] < len(NTH) else "")
            events.append({"t": t, "title": title, "note": f"Aegis to {name(aegis)}" if aegis and name(aegis) else None, "team": side[ours]["id"], "mark": "R"})
            marks.append({"t": t, "team": side[ours]["id"], "label": "R", "name": "Roshan"})

    fallen = _buildings(objectives)
    taken = {True: {"tower": 0, "barracks": 0}, False: {"tower": 0, "barracks": 0}}
    group: list[dict[str, Any]] = []
    for b in [*fallen, None]:
        if group and (b is None or b["kind"] != group[0]["kind"] or b["radiant"] != group[0]["radiant"] or b["t"] - group[-1]["t"] > TOGETHER):
            first, owner = group[0], group[0]["radiant"]
            if first["kind"] == "ancient":
                events.append({"t": first["t"], "title": f"{word[owner]}'s Ancient falls", "note": f"{word[not owner]} take map {n}", "team": side[not owner]["id"]})
            elif first["kind"] == "tower":
                events.append({"t": first["t"], "title": f"{word[not owner]} take {word[owner]}'s {_towers([(g['tier'], g['lane']) for g in group])}", "team": side[not owner]["id"]})
            else:
                lanes = list(dict.fromkeys(g["lane"] for g in group))
                events.append({"t": first["t"], "title": f"{word[not owner]} break {word[owner]}'s {_join(lanes)} barracks", "team": side[not owner]["id"]})
            group = []
        if b is not None:
            group.append(b)
            if b["kind"] in ("tower", "barracks"):
                taken[not b["radiant"]][b["kind"]] += 1

    for fight in details.get("teamfights") or []:
        start, end = int(fight.get("start") or 0), int(fight.get("end") or 0)
        if not lead:
            break
        before, after = lead[min(len(lead) - 1, max(0, start // 60))], lead[min(len(lead) - 1, -(-end // 60))]
        if abs(after - before) < SWING:
            continue
        died = {True: 0, False: 0}
        for i, p in enumerate(fight.get("players") or []):
            if i < len(rows):
                died[is_radiant(rows[i])] += int(p.get("deaths") or 0)
        worse = died[True] < died[False]  # the side that lost more, first: False is Radiant
        more, fewer = (not worse, worse)
        title = f"Fight: {NUMBERS[min(died[more], 5)]} {word[more]} {'hero dies' if died[more] == 1 else 'heroes die'}, {NUMBERS[min(died[fewer], 5)]} {word[fewer]}"
        if (before > 0) != (after > 0) and before:
            note = f"{word[before > 0]}'s {gold(abs(before))} lead gone"
        elif (after > before) == (after > 0):
            note = f"{word[after > 0]} {gold(abs(after))} up after it"
        else:
            note = f"{word[after > 0]}'s lead down to {gold(abs(after))}"
        events.append({"t": start, "title": title, "note": note, "team": None})

    for e in events:
        e.setdefault("note", None)
        e.setdefault("mark", None)
        e["clock"] = f"{'-' if e['t'] < 0 else ''}{abs(e['t']) // 60}:{abs(e['t']) % 60:02d}"
    events.sort(key=lambda e: e["t"])

    def total(p_key: str) -> dict[bool, int]:
        sums = {True: 0, False: 0}
        for p in rows:
            sums[is_radiant(p)] += int(p.get(p_key) or 0)
        return sums

    worth = total("net_worth")
    kills = {True: int(details.get("radiant_score") or total("kills")[True]), False: int(details.get("dire_score") or total("kills")[False])}
    totals = [("Kills", kills, str), ("Net worth", worth, gold)]
    if details.get("objectives"):
        totals += [("Towers taken", {s: taken[s]["tower"] for s in taken}, str), ("Barracks taken", {s: taken[s]["barracks"] for s in taken}, str), ("Roshan", roshans, str)]

    def player(p: dict[str, Any]) -> dict[str, Any]:
        k, d, a = (int(p.get(x) or 0) for x in ("kills", "deaths", "assists"))
        pid = players.get(int(p.get("account_id") or 0))
        bought = [item_names[i] for i in (int(p.get(f"item_{s}") or 0) for s in range(6)) if i and i in item_names]
        note = " · ".join(x for x in (f"{gold(int(p['hero_damage']))} damage to heroes" if p.get("hero_damage") else "", f"{p['last_hits']} last hits" if p.get("last_hits") else "") if x)
        return {"id": pid, "name": name(p), "team": side[is_radiant(p)]["id"], "as": hero(p), "kda": [k, d, a], "gpm": int(p.get("gold_per_min") or 0),
                "numbers": [f"{k}/{d}/{a}", str(int(p.get("gold_per_min") or 0)), gold(int(p.get("net_worth") or 0))], "items": bought, "note": note or None}

    heroes_of = {int(p.get("hero_id") or 0): p for p in rows}
    draft = [{"hero": hero_names.get(int(pb.get("hero_id") or 0), "?"), "team": side[pb.get("team") == 0]["id"], "pick": bool(pb.get("is_pick")),
              "player": name(heroes_of.get(int(pb.get("hero_id") or 0))) if pb.get("is_pick") else None}
             for pb in sorted(details.get("picks_bans") or [], key=lambda pb: pb.get("order") or 0)]
    duration = int(details.get("duration") or 0)
    return {
        "n": n,
        "length": f"{duration // 60}:{duration % 60:02d}" if duration else None,
        "winner": side[bool(details.get("radiant_win"))]["id"] if details.get("radiant_win") is not None else None,
        "sides": {radiant["id"]: "Radiant", dire["id"]: "Dire"},
        "lead": {"team": radiant["id"], "what": "gold", "by_minute": lead} if lead else None,
        "marks": marks,
        "totals": [{"label": label, "values": {radiant["id"]: fmt(v[True]), dire["id"]: fmt(v[False])}} for label, v, fmt in totals],
        "events": events,
        "columns": ["K/D/A", "GPM", "Net worth"],
        "players": [player(p) for p in sorted(rows, key=lambda p: (not is_radiant(p) if radiant is teams[0] else is_radiant(p), -int(p.get("net_worth") or 0)))],
        "draft": draft,
    }
