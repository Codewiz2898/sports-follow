"""More for the Feed (BHA-382) without a database: a video naming a player's team and not them is kept for them as
their team's ("Falcons vs Vitality"), for the team's followed players and a few of its pool players; the judge reads
it as a share about the team, and the score weighs it a little under one about them. Every team, player and video
here is made up or a public name; nothing is fetched."""

from __future__ import annotations

from sports_follow import candidates, curate, rank, sports
from sports_follow.candidates import Followed
from tests.test_feed_fetch import up

NIKO = Followed(10, "cs2", "NiKo", squads=["Team Falcons"])
M0NESY = Followed(11, "cs2", "m0NESY", squads=["Team Falcons"])
ZYWOO = Followed(12, "cs2", "ZywOo", squads=["Team Vitality"])
KOHLI = Followed(1, "cricket", "Virat Kohli", ["King Kohli"], squads=["India", "Royal Challengers Bengaluru"])


def found(uploads, players):
    names = candidates.names_for(players)
    return [(f.player_id, f.name, f.where, f.team) for f in candidates.match(uploads, players, names, candidates.sides(players, names))]


def test_a_followed_players_names_are_theirs_as_before_whoever_is_in_the_pool():
    """Following doesn't change with the pool: a pool player sharing a followed one's surname never takes it."""
    rohit, ishant = Followed(2, "cricket", "Rohit Sharma"), Followed(3, "cricket", "Ishant Sharma", pooled=True)
    video = up("Sharma hundred seals the series")
    assert found([video], [rohit]) == found([video], [rohit, ishant]) == [(2, "sharma", "title", "")]
    assert candidates.names_for([rohit, ishant])[2] == candidates.phrases([rohit])[2]
    assert ("sharma",) not in candidates.names_for([rohit, ishant])[3]  # a followed player's: never the pool's
    # Rohit's surname still names him, as it did before the pool (the judge tells them apart).
    assert found([up("Ishant Sharma's spell")], [rohit, ishant]) == [(2, "sharma", "title", ""), (3, "ishant sharma", "title", "")]


def test_on_a_channel_every_game_lists_a_team_is_matched_only_in_the_game_the_video_names():
    falcons_val = Followed(30, "valorant", "Shao", squads=["Team Falcons"], pooled=True)
    group = [NIKO, ZYWOO, falcons_val]
    assert found([up("Falcons vs Vitality | EWC Grand Final")], group) == []  # which Falcons? no game named: none
    assert found([up("Falcons vs Vitality | CS2 Major Grand Final")], group) == [(10, "falcons", "title", "Team Falcons"), (12, "vitality", "title", "Team Vitality")]
    assert found([up("Falcons beat Paper Rex | VALORANT Masters Toronto")], group) == [(30, "falcons", "title", "Team Falcons")]
    # A game's own players alone: no game needs naming.
    assert found([up("Falcons vs Vitality | EWC Grand Final")], [NIKO, ZYWOO])[0] == (10, "falcons", "title", "Team Falcons")


def test_a_teams_short_name_inside_another_of_these_teams_longer_name_isnt_it():
    milan = Followed(40, "football", "Rafael Leao", squads=["AC Milan"])
    inter = Followed(41, "football", "Lautaro Martinez", squads=["Inter Milan"])
    assert found([up("Inter Milan 2-0 Lecce | Highlights")], [milan, inter]) == [(41, "inter milan", "title", "Inter Milan")]
    assert found([up("Milan 1-1 Napoli | Highlights")], [milan, inter]) == [(40, "milan", "title", "AC Milan")]
    # An esports event's name is no other side: Masters is VALORANT's.
    prx = Followed(42, "valorant", "f0rsakeN", squads=["Paper Rex"])
    assert found([up("Paper Rex Masters Toronto run")], [prx]) == [(42, "paper rex", "title", "Paper Rex")]
    assert found([up("India Legends vs Australia Legends")], [KOHLI]) == []  # cricket's retired sides


def test_a_team_is_named_as_its_sports_titles_write_it():
    assert sports.of("cs2").team_names("Team Falcons") == ["team falcons", "falcons"]
    assert sports.of("valorant").team_names("G2 Esports") == ["g2 esports", "g2"]
    assert sports.of("football").team_names("Real Madrid Club de Fútbol") == ["real madrid club de futbol", "real madrid"]
    assert sports.of("football").team_names("FC Barcelona") == ["fc barcelona", "barcelona"]
    assert sports.of("cs2").team_names("Team") == ["team"]  # never left empty
    assert sports.of("cricket").team_names("India") == ["india"]


def test_a_video_naming_a_players_team_and_not_them_is_kept_for_them_as_their_teams():
    group = [NIKO, M0NESY, ZYWOO]
    assert found([up("Falcons vs Vitality | IEM Chennai 2026 Grand Final")], group) == [
        (10, "falcons", "title", "Team Falcons"), (11, "falcons", "title", "Team Falcons"), (12, "vitality", "title", "Team Vitality"),
    ]
    # Named by name, it's theirs as before; their teammates get it as the team's.
    assert found([up("NiKo's ace against Vitality")], group) == [(10, "niko", "title", ""), (12, "vitality", "title", "Team Vitality")]
    assert found([up("Grand final day", "Team Falcons take on the champions in Chennai.")], group)[0] == (10, "team falcons", "description", "Team Falcons")
    assert found([up("Map pool talk with the analysts")], group) == []


def test_another_side_of_a_teams_name_isnt_the_players_team():
    assert found([up("India vs Australia, 2nd ODI highlights")], [KOHLI]) == [(1, "india", "title", "India")]
    assert found([up("India Women vs Australia Women, 2nd ODI highlights")], [KOHLI]) == []
    assert found([up("India U19 beat Pakistan U19")], [KOHLI]) == []
    assert found([up("Royal Challengers Bengaluru's season preview")], [KOHLI]) == [(1, "royal challengers bengaluru", "title", "Royal Challengers Bengaluru")]


def test_a_teams_video_is_kept_for_its_followed_players_and_a_few_of_its_pool_players_the_best_known():
    pool = [Followed(20 + n, "cs2", f"Pro {n}x", squads=["Team Falcons"], pooled=True) for n in range(5)]
    group = [NIKO, *pool]
    ids = [pid for pid, *_ in found([up("Falcons win the major")], group)]
    assert ids == [10, 20, 21, 22] and candidates.TEAM_POOL == 3


def test_a_name_two_teams_share_a_players_name_or_one_short_word_names_no_team():
    group = [
        Followed(1, "cs2", "Alpha One", squads=["Team Liquid"]), Followed(2, "cs2", "Beta Two", squads=["Liquid Gaming"]),
        Followed(3, "lol", "Faker", squads=["T1"]), Followed(4, "cs2", "Gamma Three", squads=["GG Esports"]),
        Followed(5, "cs2", "Sentinel", squads=["Sentinel"]),  # a pro named as their team
    ]
    names = candidates.names_for(group)
    by = {s.name: s.phrases for s in candidates.sides(group, names)}
    assert by["Team Liquid"] == [("team", "liquid")] and by["Liquid Gaming"] == [("liquid", "gaming")]  # "liquid" alone is both's
    assert by["T1"] == [("t1",)]  # two letters with a digit: a name
    assert "GG Esports" in by and ("gg",) not in by["GG Esports"]  # two letters alone aren't
    assert "Sentinel" not in by  # the player's own name: theirs, not a team's


def test_a_candidate_found_as_a_teams_says_so_and_one_found_by_name_doesnt():
    kept = candidates.candidate(up("Falcons vs Vitality"), "falcons", "title", "cs2", None, "Team Falcons")
    assert kept["about"] == "team" and kept["team"] == "Team Falcons" and rank.about_team(kept) == "Team Falcons"
    plain = candidates.candidate(up("NiKo's ace"), "niko", "title", "cs2", None)
    assert "about" not in plain and rank.about_team(plain) == ""


def judged(**kw) -> dict:
    return {"about_player": "subject", "by": "official", "kind": "match_moment", "interest": 0.6, "risks": [], "sponsored": False, "reason": "r", **kw}


def test_a_share_about_the_players_team_is_served_and_weighs_a_little_under_one_about_them():
    assert rank.passes(judged(about_player="team")) and rank.passes(judged()) and not rank.passes(judged(about_player="mention"))
    team, them = rank.score(judged(about_player="team"), {}, 1.0, None), rank.score(judged(), {}, 1.0, None)
    assert team == round(them * rank.TEAM, 4) and rank.score(judged(about_player="mention"), {}, 1.0, None) < team


def candidate(title: str, team: str = "") -> curate.Candidate:
    player = curate.PlayerContext(name="NiKo", teams=["Team Falcons"], sport="cs2")
    return curate.Candidate(kind="video", source="youtube", url="https://www.youtube.com/watch?v=abcdefghijk", title=title, player=player, team=team)


def test_the_judge_is_told_a_share_was_found_as_the_players_teams_and_only_that_one_can_be_the_teams():
    facts = curate.rules(candidate("Falcons vs Vitality | Grand Final", team="Team Falcons"))
    assert facts.final is None and any("Team Falcons" in h and '"team"' in h for h in facts.hints)
    assert not any("their team's" in h for h in curate.rules(candidate("NiKo's ace")).hints)
    said = curate.Judged.model_validate(judged(about_player="team"))
    assert curate._settle(candidate("Falcons vs Vitality", team="Team Falcons"), said, curate.Facts()).about_player == "team"
    assert curate._settle(candidate("Falcons vs Vitality"), said, curate.Facts()).about_player == "mention"  # found by name: a mention, as ever
    # The rules' own verdict (a promo) says the team's for one found as the team's.
    promo = curate.rules(candidate("Falcons vs Vitality, use code FALCONS20", team="Team Falcons")).final
    assert promo is not None and promo.about_player == "team" and not rank.passes(promo.model_dump())


def test_a_curator_starting_without_a_pool_queues_its_refresh_and_one_with_a_pool_doesnt(monkeypatch):
    import asyncio

    from sports_follow import curator, db, feedpool, metrics

    monkeypatch.setattr(db, "size_pool", lambda n: None)
    monkeypatch.setattr(metrics, "feed_seed", lambda: None)

    class Redis:
        def __init__(self, keys):
            self.keys, self.queued = keys, []

        async def exists(self, key):
            return key in self.keys

        async def enqueue_job(self, name, **kw):
            self.queued.append((name, kw.get("_queue_name")))

    empty, kept = Redis(set()), Redis({feedpool.POOL_KEY})
    for redis in (empty, kept):
        asyncio.run(curator.startup({"redis": redis}))
    assert empty.queued == [("refresh_pool", curator.QUEUE)] and kept.queued == []
