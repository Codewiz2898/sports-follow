"""Today's "This week" posters (week.py): each sport picks a game's poster from the moments it found for the
player in it (Sport.poster), and words each of the player's highs (Sport.high) off the titles its own rules
write, so these tests find their moments the way live polls do (moments.detect). A game first seen after it
ended has no moments: its sport words its poster off the line and result it left the player
(Sport.finished_poster), in the same words and as big as the moment would have been."""

from __future__ import annotations

from datetime import date

import pytest

from sports_follow import moments, sports, week
from sports_follow.moments import Seen, detect
from sports_follow.sports import esports
from sports_follow.sports.base import Found, Poster

from .test_moments import goal, line, scored


def poster(sport: str, found: Found, versus: str = "") -> Poster | None:
    """One moment as a poster, if it's one of the player's highs."""
    return sports.of(sport).high(found, versus)


def game(sport: str, found: list[Found], versus: str = "") -> Poster | None:
    """A game's poster, from every moment found for the player in it, oldest first."""
    return sports.of(sport).poster(found, versus)


def finished(sport: str, headline: str, result: str = "", versus: str = "", **stats: str) -> Poster | None:
    """A finished game's poster, off the player's line in it and the result from their side."""
    return sports.of(sport).finished_poster(line(headline, **stats), result, versus)


def polls(sport: str, who: str, athlete: str, *seen: Seen) -> list[Found]:
    """Every moment a run of polls finds, oldest first."""
    return [f for before, now in zip(seen, seen[1:]) for f in detect(sport, who, athlete, before, now)]


def test_cricket_posters_are_milestones_and_hauls_never_being_out():
    before = Seen("live", line=line("48* (39)", Runs="48*", Balls="39"), score="IND 160/2")
    now = Seen("live", line=line("103* (80)", Runs="103*", Balls="80"), score="IND 240/2")
    fifty, hundred = detect("Cricket", "Virat Kohli", "253802", before, now)
    assert poster("Cricket", hundred, "v West Indies") == Poster("Hundred", "103* (80) v West Indies")
    assert poster("Cricket", fifty) == Poster("Fifty", "103* (80)")  # both marks passed between two polls
    big = detect("Cricket", "Virat Kohli", "253802", Seen("live", line=line("190* (200)", Runs="190*", Balls="200")), Seen("live", line=line("201* (210)", Runs="201*", Balls="210")))
    assert [poster("Cricket", f).word for f in big] == ["Double hundred"]
    # The kind is "wickets" whatever the haul: the title says how many.
    hauls = detect("Cricket", "Jasprit Bumrah", "1", Seen("live", line=line("2/20", Wickets="2")), Seen("live", line=line("5/23 (4 ov)", Wickets="5", Runs_conceded="23", Overs="4")))
    assert [f.kind for f in hauls] == ["wickets"] * 3
    assert [poster("Cricket", f, "v Australia") for f in hauls] == [Poster(w, "5/23 (4 ov) v Australia") for w in ("Three-for", "Four-for", "Five-for")]
    out = Seen("live", line=line("103 (82)", Runs="103", Balls="82", Out="c Holder b Joseph"))
    [gone] = detect("Cricket", "Virat Kohli", "253802", now, out)
    assert poster("Cricket", gone) is None
    # A game's poster is its biggest high: the hundred over the fifty, the five-for over the three.
    assert game("Cricket", [fifty, hundred, gone]).word == "Hundred"
    assert game("Cricket", hauls).word == "Five-for"


def test_football_posters_say_what_the_goal_did():
    before = Seen("live", line=line("Started"))
    [equaliser] = detect("Football", "Mohamed Salah", "173896", before, scored(1, 1, [goal("88'", assist="Trezeguet"), goal("20'", athlete="9", team="2")]))
    assert poster("Football", equaliser, "v South Africa") == Poster("Late equaliser", "88' v South Africa")
    [plain] = detect("Football", "Mohamed Salah", "173896", before, scored(3, 0, [goal("70'"), goal("20'", athlete="7"), goal("5'", athlete="7")]))
    assert poster("Football", plain, "v South Africa") == Poster("Goal", "70' v South Africa")
    assert game("Football", [plain], "v South Africa") == Poster("Goal", "70' v South Africa")
    now = Seen("live", line=line("1 goal", Goals="1", Assists="1", Yellow="1"), score="Egypt 2–0 South Africa", clock="67'")
    found = {f.kind: f for f in detect("Football", "Mohamed Salah", "173896", Seen("live", line=line("On the bench")), now)}
    assert poster("Football", found["assist"]) == Poster("Assist", "67'")
    assert poster("Football", found["yellow"]) is None and poster("Football", found["on"]) is None
    assert poster("Football", Found("correction", "key", "Goal ruled out: Mohamed Salah", "Egypt 1–0 South Africa · 70'")) is None


def test_football_counts_the_goals_that_stand():
    # A goal's title says what it did, and that can drop its count: the first puts Egypt ahead (at a higher
    # priority than the second's plain "Goal (2)"), or the second levels the game. Two goals are a brace.
    started = Seen("live", line=line("Started"))
    ahead = polls("Football", "Mohamed Salah", "173896", started, scored(1, 0, [goal("10'")], clock="10'"), scored(2, 0, [goal("60'"), goal("10'")], goals=2, clock="60'"))
    assert [(f.title, f.priority) for f in ahead] == [("Goal: Mohamed Salah puts Egypt ahead", 90), ("Goal: Mohamed Salah (2)", 85)]
    assert game("Football", ahead, "v South Africa") == Poster("Brace", "v South Africa")
    level = polls("Football", "Mohamed Salah", "173896", started, scored(1, 1, [goal("30'"), goal("20'", athlete="9", team="2")], clock="30'"), scored(2, 1, [goal("70'"), goal("30'"), goal("20'", athlete="9", team="2")], goals=2, clock="70'"))
    assert [f.title for f in level] == ["Equaliser: Mohamed Salah", "Goal: Mohamed Salah puts Egypt ahead"]
    assert game("Football", level).word == "Brace"
    three = polls("Football", "Mohamed Salah", "173896", started, scored(4, 1, [goal("80'"), goal("60'"), goal("10'")], goals=3))
    assert [f.kind for f in three] == ["goal"] * 3
    assert game("Football", three, "v South Africa") == Poster("Hat-trick", "v South Africa")  # three minutes: none
    four = [*three, *polls("Football", "Mohamed Salah", "173896", scored(4, 1, [goal("80'"), goal("60'"), goal("10'")], goals=3), scored(5, 1, [goal("85'"), goal("80'"), goal("60'"), goal("10'")], goals=4))]
    assert game("Football", four).word == "4 goals"  # not the third's "Hat-trick"
    # One goal standing, whatever its title counted when it went in.
    assert game("Football", [Found("goal", "key", "Goal: Mohamed Salah (2)", "Egypt 2 – 0 South Africa · 60'", priority=85)]) == Poster("Goal", "60'")
    assert game("Football", [Found("goal", "key", "Hat-trick: Mohamed Salah", "Egypt 3 – 0 South Africa · 80'", priority=95)]) == Poster("Goal", "80'")
    # Assists count too, below any goal.
    assists = [Found("assist", "key", "Assist: Mohamed Salah", "1–0 · 20'"), Found("assist", "key", "Assist: Mohamed Salah (2)", "2–0 · 50'")]
    assert game("Football", assists, "v Ghana") == Poster("2 assists", "v Ghana")
    assert game("Football", [*assists, ahead[0]]).word == "Goal"


def test_basketball_posters_are_thirty_points_and_triple_doubles():
    before = Seen("live", line=line("28 PTS · 9 REB · 9 AST"))
    now = Seen("live", line=line("31 PTS · 10 REB · 11 AST", Steals="1"), score="Fever 90–88 Aces", clock="Q4 1:02")
    points, double, triple = detect("Basketball", "Caitlin Clark", "4433403", before, now)
    assert poster("Basketball", points, "v Las Vegas Aces") == Poster("30 points", "v Las Vegas Aces")
    assert poster("Basketball", triple) == Poster("Triple-double", "31 PTS · 10 REB · 11 AST")
    assert poster("Basketball", double) is None
    [twenty] = detect("Basketball", "Caitlin Clark", "4433403", Seen("live", line=line("18 PTS · 2 REB · 3 AST")), Seen("live", line=line("21 PTS · 2 REB · 3 AST")))
    assert poster("Basketball", twenty) is None


def test_tennis_posters_are_upsets():
    before = Seen("live", line=line("Leads 2–1 in sets", Sets="2–1"), score="6-4 3-6 6-2")
    final = Seen("final", state={"players": [{"id": "3782", "winner": True, "seed": 5}, {"id": "9", "name": "Jannik Sinner", "seed": 1}]}, line=line("Won in 3 sets", Sets="2–1"), score="6-4 3-6 6-2")
    result, upset = detect("Tennis", "Carlos Alcaraz", "3782", before, final)
    assert poster("Tennis", upset) == Poster("Upset", "beat Jannik Sinner, the No. 1 seed")
    assert poster("Tennis", result) is None  # a plain win isn't a big moment; an upset is


def test_chess_posters_are_wins():
    [won] = detect("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Won with White vs Gukesh D"), score="1–0"))
    assert poster("Chess", won) == Poster("Win", "with White v Gukesh D")
    [match] = detect("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Won the match 2½–1½ vs Hikaru Nakamura")))
    assert poster("Chess", match) == Poster("Match won", "2½–1½ v Hikaru Nakamura")
    [drew] = detect("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Drew with Black vs Gukesh D"), score="½–½"))
    assert poster("Chess", drew) is None


def series(ours: int, theirs: int) -> Found:
    state = {"kind": "esports", "stage": "Upper bracket final", "teams": [
        {"id": "s", "name": "Sentinels", "score": ours, "winner": ours > theirs}, {"id": "n", "name": "NRG", "score": theirs, "winner": theirs > ours}]}
    return esports.SPORT.final("TenZ", Seen("final", state=state, teams=["s"]))


def test_esports_posters_are_the_teams_wins():
    assert poster("Valorant", series(2, 1)) == Poster("Win 2–1", "v NRG")
    assert poster("Valorant", series(3, 0)) == Poster("Sweep 3–0", "v NRG")
    assert poster("Valorant", series(0, 2)) is None
    lobby = {"kind": "battle_royale", "stage": "Grand final", "standings": [
        {"id": "g", "name": "GodLike Esports", "place": 1, "points": 46, "games": [{"n": 1, "place": 1}]}, {"id": "s", "name": "Team SouL", "place": 2, "points": 30}]}
    won = esports.SPORT.final("Jonathan", Seen("final", state=lobby, teams=["g"]))
    assert poster("BGMI", won) == Poster("Lobby won", "GodLike Esports · 46 pts")
    placed = esports.SPORT.final("Owais", Seen("final", state=lobby, teams=["s"]))
    assert poster("BGMI", placed) is None
    dinner = Found("dinner", "key", "Chicken dinner: Team SouL", "Owais · Match 2 · 7 finishes · 1st overall, 30 pts")
    assert poster("BGMI", dinner) == Poster("Chicken dinner", "Team SouL · match 2")


def test_a_sport_no_module_knows_and_the_news_make_no_poster():
    assert poster("Golf", Found("final", "key", "Rory McIlroy: full time", "")) is None
    assert poster("Cricket", Found("news", "key", "Virat Kohli: retirement news", "Kohli retires from Tests")) is None
    assert poster("Football", Found("lineup", "key", "Mohamed Salah starts", "Egypt v South Africa · line-ups are out")) is None


def test_a_finished_cricket_line_is_its_best_innings():
    assert finished("Cricket", "112 (98)", "India won by 40 runs", "v Australia") == Poster("Hundred", "112 (98) v Australia")
    assert finished("Cricket", "5/23 (4 ov)", versus="v Australia") == Poster("Five-for", "5/23 (4 ov) v Australia")
    assert [finished("Cricket", h).word for h in ("57* (40)", "163* (120)", "201 (180)", "3/40 (10 ov)")] == ["Fifty", "150", "Double hundred", "Three-for"]
    # A Test's two innings and the bowling: the best of them by their moments' priorities, the batting of equals.
    assert finished("Cricket", "103 (152) & 48* (60) · 5/40 (20 ov)") == Poster("Hundred", "103 (152)")
    assert finished("Cricket", "12 (30) & 61 (90) · 1/20 (8 ov) & 6/50 (22 ov)") == Poster("Six-for", "6/50 (22 ov)")
    assert finished("Cricket", "103 (152) & 120 (200)") == Poster("Hundred", "120 (200)")  # the bigger of equals
    assert finished("Cricket", "6/50 (22 ov) & 5/23 (9 ov)") == Poster("Six-for", "6/50 (22 ov)")
    assert finished("Cricket", "49 (30) · 2/10 (3 ov)") is None
    assert finished("Cricket", "Did not bat or bowl") is None


def test_a_finished_football_line_counts_its_goals_then_its_assists():
    assert finished("Football", "2 goals, 1 assist", "Won 3–1", "v Ghana") == Poster("Brace", "v Ghana")
    assert [finished("Football", h).word for h in ("3 goals", "4 goals", "1 goal")] == ["Hat-trick", "4 goals", "Goal"]
    assert finished("Football", "1 goal, 2 assists", versus="v Ghana") == Poster("Goal", "v Ghana")  # one goal outshines any number of assists
    assert finished("Football", "2 assists", versus="v Ghana") == Poster("2 assists", "v Ghana")
    assert finished("Football", "1 assist").word == "Assist"
    assert [finished("Football", h) for h in ("Started", "Came on", "On the bench", "Not in the matchday squad")] == [None] * 4


def test_a_finished_basketball_line_is_thirty_points_or_a_triple_double():
    # The points scored, where the moment says the mark reached ("30 points").
    assert finished("Basketball", "38 PTS · 6 REB · 2 AST", versus="v Utah Jazz") == Poster("38 points", "v Utah Jazz")
    assert finished("Basketball", "22 PTS · 11 REB · 10 AST", versus="v Utah Jazz") == Poster("Triple-double", "22 PTS · 11 REB · 10 AST")
    assert finished("Basketball", "10 PTS · 12 REB · 4 AST", Blocks="10").word == "Triple-double"  # blocks and steals count
    assert finished("Basketball", "44 PTS · 11 REB · 10 AST").word == "Triple-double"  # 40 points is as big: the triple-double
    assert finished("Basketball", "52 PTS · 11 REB · 10 AST").word == "52 points"
    assert finished("Basketball", "29 PTS · 10 REB · 9 AST") is None and finished("Basketball", "Did not play (rest)") is None


def test_a_finished_chess_or_esports_result_is_a_win():
    assert finished("Chess", "Won with Black vs Alireza Firouzja", "Won 1–0") == Poster("Win", "with Black v Alireza Firouzja")
    assert finished("Chess", "Won the match 2½–1½ vs Hikaru Nakamura", "Won 2½–1½") == Poster("Match won", "2½–1½ v Hikaru Nakamura")
    assert finished("Chess", "Drew with White vs Gukesh D", "Drew ½–½") is None and finished("Chess", "Lost with White vs Gukesh D", "Lost 0–1") is None
    # Esports results are the team's: a series, or a lobby (whose result doesn't name the team).
    assert finished("Valorant", "", "Won 2–1", "v NRG") == Poster("Win 2–1", "v NRG")
    assert finished("Valorant", "", "Won 2–0", "v NRG") == Poster("Sweep 2–0", "v NRG")
    assert finished("CS2", "", "Won 1–0").word == "Win 1–0"  # one map is no sweep
    assert [finished("Valorant", "", r) for r in ("Lost 1–2", "Drew 1–1", "Won by forfeit")] == [None] * 3
    assert finished("BGMI", "1st · 46 pts", "1st of 16 · 46 pts") == Poster("Lobby won", "46 pts · 16 teams")
    assert finished("BGMI", "2nd · 30 pts", "2nd of 16 · 30 pts") is None


def test_a_finished_line_makes_no_poster_only_a_moment_could():
    assert finished("Tennis", "Won in 3 sets", "Won 6-4 3-6 6-2") is None  # an upset needs the seeds the score had
    assert finished("Golf", "-14", "Won by 2") is None


def test_a_finished_poster_is_as_big_as_its_moment_would_have_been():
    # The moments a live poll finds for the same line: the poster from them, and its moment's priority.
    def live(sport: str, who: str, athlete: str, before: Seen, now: Seen) -> tuple[str, int]:
        made = game(sport, detect(sport, who, athlete, before, now))
        assert made is not None and made.of is not None
        return made.word, made.of.priority

    hundred = live("Cricket", "Smriti Mandhana", "1", Seen("live", line=line("48* (39)", Runs="48*", Balls="39")), Seen("live", line=line("112 (98)", Runs="112", Balls="98", Out="b Starc")))
    assert hundred == ("Hundred", finished("Cricket", "112 (98)").priority)
    haul = live("Cricket", "Jasprit Bumrah", "2", Seen("live", line=line("2/20 (3 ov)", Wickets="2")), Seen("live", line=line("4/23 (4 ov)", Wickets="4", Runs_conceded="23", Overs="4")))
    assert haul == ("Four-for", finished("Cricket", "4/23 (4 ov)").priority)
    started = Seen("live", line=line("Started"))
    hat_trick = polls("Football", "Mohamed Salah", "173896", started, scored(4, 1, [goal("80'"), goal("60'"), goal("10'")], goals=3))
    assert (game("Football", hat_trick).word, max(f.priority for f in hat_trick)) == ("Hat-trick", finished("Football", "3 goals").priority)
    forty = live("Basketball", "Caitlin Clark", "3", Seen("live", line=line("38 PTS · 2 REB · 3 AST")), Seen("live", line=line("41 PTS · 2 REB · 3 AST")))
    assert forty == ("40 points", finished("Basketball", "41 PTS · 2 REB · 3 AST").priority)
    triple = live("Basketball", "Caitlin Clark", "3", Seen("live", line=line("18 PTS · 9 REB · 9 AST")), Seen("live", line=line("19 PTS · 10 REB · 10 AST")))
    assert triple == ("Triple-double", finished("Basketball", "19 PTS · 10 REB · 10 AST").priority)
    chess = live("Chess", "Magnus Carlsen", "1503014", Seen("live"), Seen("final", line=line("Won with White vs Gukesh D"), score="1–0"))
    assert chess == ("Win", finished("Chess", "Won with White vs Gukesh D").priority)
    assert finished("Valorant", "", "Won 2–1").priority == moments.PRIORITY["final"]  # a series' result


@pytest.mark.parametrize("title, mine, said", [
    ("India v Australia", ["India"], "v Australia"),
    ("GenOne vs 6666", ["6666"], "v GenOne"),
    ("San Antonio Spurs at Utah Jazz", ["San Antonio Spurs"], "v Utah Jazz"),
    ("Zizou Bergs vs Carlos Alcaraz", ["Carlos Alcaraz"], "v Zizou Bergs"),  # one player a side: their name
    ("India v Australia · 3rd T20I", ["India"], "v Australia"),
    ("India A v Australia A", ["India"], ""),  # neither side is theirs by whole name
    ("Round 5", ["Gukesh D"], ""),
    (None, ["India"], ""),
])
def test_versus_names_the_other_side_only_when_the_title_says_which_is_theirs(title, mine, said):
    assert week.versus(title, mine) == said


@pytest.mark.parametrize("headline, plain", [
    ("BREAKING: Saka ruled out for a month", "Saka ruled out for a month"),
    ("Breaking News - Saka ruled out", "Saka ruled out"),
    ("BREAKING | Saka signs for Real Madrid", "Saka signs for Real Madrid"),
    ("BREAKING Salah ruled out", "Salah ruled out"),
    ("Breaking news Salah ruled out", "Salah ruled out"),
    ("Breaking Bad star at the Emirates", "Breaking Bad star at the Emirates"),  # not a site's label
    ("Saka ruled out, Arteta confirms", "Saka ruled out, Arteta confirms"),
])
def test_the_wire_never_says_breaking(headline, plain):
    assert week._plain(headline) == plain


def test_football_tells_who_is_out_and_who_is_back_in_its_own_words():
    football = sports.of("Football")
    assert football.out_fact("William Saliba", "injury", "Back Injury", date(2026, 12, 11)) == sports.Fact("Out", "William Saliba · back injury · until 11 Dec")
    assert football.out_fact("Kai Havertz", "injury", "Unknown Injury", None) == sports.Fact("Out", "Kai Havertz · injury")
    assert football.out_fact("Phil Foden", "suspension", "Red Card Suspension", date(2026, 10, 17)).line == "Phil Foden · red card suspension · until 17 Oct"
    assert football.back_fact("William Saliba", "injury") == sports.Fact("Back", "William Saliba is fit again")
    assert football.back_fact("Phil Foden", "suspension") == sports.Fact("Back", "Phil Foden is back from suspension")
    # The source's words, but for a few that don't read as what's wrong.
    assert [football.out_fact("X", kind, what, None).line for kind, what in (("injury", "Fitness"), ("injury", "Ill"), ("suspension", "Indirect Card Suspension"))] == [
        "X · lacking fitness", "X · illness", "X · yellow card suspension"]
    # Not registered for the competition: not news.
    assert football.out_fact("Wataru Endo", "other", "No Eligibility", None) is None and football.back_fact("Wataru Endo", "other") is None


@pytest.mark.parametrize("sport", ["Cricket", "Chess", "Esports", "Basketball", "Tennis", "Golf"])
def test_a_sport_whose_sources_list_nobody_out_tells_no_facts(sport):
    assert sports.of(sport).out_fact("Someone", "injury", "Knee Injury", None) is None
    assert sports.of(sport).back_fact("Someone", "injury") is None
