"""The noise rules (alerts.py): what reaches a fan's phone, one notification per game, and when it may
make a sound."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sports_follow.alerts import BREAKTHROUGH, LIVE_TTL, LOUD_PER_HOUR, Item, Sent, plan, voiced_by

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
KOHLI, ROHIT = 1, 2
GAME, OTHER = 10, 11


def item(id: int, kind: str, title: str, *, level: str = "key", priority: int = 70, player: int = KOHLI, event: int | None = GAME, body: str = "IND 240/3 (38 ov)", ago: int = 0, corrects: int | None = None) -> Item:
    return Item(id, player, event, kind, level, priority, title, body, f"/player/{player}", NOW - timedelta(seconds=ago), corrects)


def sent(event: int | None, *lines: str, loud: bool = True, ago: int = 600, player: int = KOHLI, moments: list[int] | None = None) -> Sent:
    return Sent(event, player, loud, list(lines), NOW - timedelta(seconds=ago), moments or [])


def test_levels_pick_what_reaches_the_phone():
    batch = [item(1, "fifty", "Virat Kohli reaches 50"), item(2, "set", "A minor moment", level="minor", priority=30)]
    [key] = plan(batch, {KOHLI: "key"}, set(), [], NOW)
    assert (key.moment_ids, key.loud) == ([1], True)
    [everything] = plan(batch, {KOHLI: "everything"}, set(), [], NOW)
    assert everything.moment_ids == [1, 2] and everything.loud  # the minor one rides along, silently
    assert plan([batch[1]], {KOHLI: "everything"}, set(), [], NOW)[0].loud is False
    assert plan(batch, {KOHLI: "results"}, set(), [], NOW) == []
    [result] = plan([item(3, "final", "Virat Kohli: India won by 5 wickets", priority=80)], {KOHLI: "results"}, set(), [], NOW)
    assert result.loud
    assert plan(batch, {KOHLI: "off"}, set(), [], NOW) == []


def test_one_notification_per_game_led_by_the_biggest_moment():
    batch = [item(1, "out", "Rohit Sharma out for 45", player=ROHIT, priority=60), item(2, "hundred", "Virat Kohli reaches 100", priority=90, body="100* (80) · IND 240/3")]
    [p] = plan(batch, {KOHLI: "key", ROHIT: "key"}, set(), [], NOW)
    assert (p.tag, p.title, p.player_id, p.moment_ids) == ("e10", "Virat Kohli reaches 100", KOHLI, [2, 1])
    assert p.body == "100* (80) · IND 240/3\nRohit Sharma out for 45"
    assert p.lines == ["Virat Kohli reaches 100", "Rohit Sharma out for 45"]


def test_only_the_players_a_fan_follows_reach_them():
    # One poll records every followed player in the game, and the tick's sweep mixes games: one batch, many fans.
    batch = [
        item(1, "hundred", "Rohit Sharma reaches 100", player=ROHIT, priority=90),
        item(2, "final", "Virat Kohli: India won by 5 wickets", priority=80),
        item(3, "goal", "Goal: Mohamed Salah", player=3, event=OTHER, priority=85, body="Egypt 1–0 South Africa"),
    ]
    assert plan([batch[0]], {KOHLI: "key"}, set(), [], NOW) == []
    [p] = plan(batch, {KOHLI: "key"}, set(), [], NOW)
    assert (p.tag, p.player_id, p.lines) == ("e10", KOHLI, ["Virat Kohli: India won by 5 wickets"])
    [both] = plan(batch, {KOHLI: "key", ROHIT: "key"}, set(), [], NOW)
    assert (both.tag, both.moment_ids) == ("e10", [1, 2])
    assert plan(batch, {}, set(), [], NOW) == []


def test_the_story_so_far_rides_underneath():
    earlier = [sent(GAME, "Virat Kohli reaches 50", ago=3600), sent(GAME, "Rohit Sharma out for 45", ago=1800), sent(OTHER, "Another game")]
    [p] = plan([item(1, "hundred", "Virat Kohli reaches 100", priority=90)], {KOHLI: "key"}, set(), earlier, NOW)
    assert p.body.split("\n") == ["IND 240/3 (38 ov)", "Rohit Sharma out for 45", "Virat Kohli reaches 50"]
    assert p.story == ["Rohit Sharma out for 45", "Virat Kohli reaches 50"]
    long_game = [sent(GAME, f"Moment {n}", ago=3600 - n) for n in range(10)]
    assert len(plan([item(1, "hundred", "100", priority=90)], {KOHLI: "key"}, set(), long_game, NOW)[0].body.split("\n")) == 5


def test_news_is_one_notification_per_player_without_a_story():
    [p] = plan([item(1, "news", "Injury news: Virat Kohli", event=None, priority=75)], {KOHLI: "key"}, set(), [sent(None, "Transfer news: Virat Kohli")], NOW)
    assert (p.tag, p.story, p.loud) == ("p1", [], True)


def test_a_muted_game_sends_nothing():
    batch = [item(1, "hundred", "Virat Kohli reaches 100", priority=95), item(2, "news", "Injury news", event=None)]
    assert [p.tag for p in plan(batch, {KOHLI: "key"}, {GAME}, [], NOW)] == ["p1"]


def test_late_live_moments_are_dropped_but_results_keep():
    stale = [item(1, "goal", "Goal: Mohamed Salah", ago=20 * 60), item(2, "final", "Mohamed Salah: Egypt 2–0 South Africa", priority=80, event=OTHER, ago=2 * 3600)]
    assert [p.moment_ids for p in plan(stale, {KOHLI: "key"}, set(), [], NOW)] == [[2]]
    lineup = item(3, "lineup", "Mohamed Salah starts", ago=40 * 60)
    assert plan([lineup], {KOHLI: "key"}, set(), [], NOW)[0].ttl == 3600


def test_a_game_makes_a_sound_once_every_few_minutes():
    just_now = [sent(GAME, "Virat Kohli reaches 50", ago=60)]
    assert plan([item(1, "wickets", "Kohli takes 3 wickets", priority=70)], {KOHLI: "key"}, set(), just_now, NOW)[0].loud is False
    assert plan([item(1, "hundred", "Kohli reaches 100", priority=BREAKTHROUGH)], {KOHLI: "key"}, set(), just_now, NOW)[0].loud is True
    assert plan([item(1, "wickets", "Kohli takes 3 wickets")], {KOHLI: "key"}, set(), [sent(GAME, "50", ago=600)], NOW)[0].loud is True
    assert plan([item(1, "wickets", "Kohli takes 3 wickets")], {KOHLI: "key"}, set(), [sent(GAME, "50", ago=60, loud=False)], NOW)[0].loud is True


def test_a_phone_makes_a_sound_a_few_times_an_hour():
    busy = [sent(100 + n, "x", ago=300 + n) for n in range(LOUD_PER_HOUR - 1)]
    batch = [item(1, "fifty", "Kohli reaches 50", priority=70), item(2, "goal", "Goal: Salah", priority=85, player=3, event=OTHER)]
    loud = {p.tag: p.loud for p in plan(batch, {KOHLI: "key", 3: "key"}, set(), busy, NOW)}
    assert loud == {"e11": True, "e10": False}  # the last sound of the hour goes to the bigger moment
    full = busy + [sent(99, "x", ago=400)]
    assert plan([item(3, "hundred", "Kohli reaches 100", priority=BREAKTHROUGH)], {KOHLI: "key"}, set(), full, NOW)[0].loud is True
    assert plan([item(3, "fifty", "Kohli reaches 50")], {KOHLI: "key"}, set(), [*full[:-1], sent(99, "x", ago=4000)], NOW)[0].loud is True


def test_the_start_is_quiet_once_the_line_up_was_heard():
    start = item(1, "start", "Mohamed Salah's game has started", priority=40)
    assert plan([start], {KOHLI: "key"}, set(), [], NOW)[0].loud is True
    assert plan([start], {KOHLI: "key"}, set(), [sent(GAME, "Mohamed Salah starts", ago=1800)], NOW)[0].loud is False


def test_a_correction_replaces_the_goal_for_the_fans_who_were_sent_it():
    # The line-up (moment 4) went out an hour ago, the goal (5) two minutes ago; VAR has ruled it out.
    heard = [sent(GAME, "Mohamed Salah starts", ago=3600, moments=[4]), sent(GAME, "Goal: Mohamed Salah puts Egypt ahead", ago=120, moments=[5])]
    correction = item(6, "correction", "Goal ruled out: Mohamed Salah", priority=85, body="Egypt 0 – 0 South Africa · 25'", corrects=5)
    [p] = plan([correction], {KOHLI: "key"}, set(), heard, NOW)
    assert (p.tag, p.title, p.kind, p.moment_ids, p.ttl) == ("e10", "Goal ruled out: Mohamed Salah", "correction", [6], LIVE_TTL)
    assert p.body.split("\n") == ["Egypt 0 – 0 South Africa · 25'", "Mohamed Salah starts"]  # the goal leaves the story
    # Sound by the same rules as the goal: the game made one two minutes ago; ten minutes ago, it may again.
    assert p.loud is False
    assert plan([correction], {KOHLI: "key"}, set(), [sent(GAME, "Goal: Mohamed Salah", moments=[5])], NOW)[0].loud is True
    # Not sent the goal (followed since, a level that left it out), the game muted, or too late: nothing.
    assert plan([correction], {KOHLI: "key"}, set(), heard[:1], NOW) == []
    assert plan([correction], {KOHLI: "results"}, set(), heard, NOW) == []
    assert plan([correction], {KOHLI: "off"}, set(), heard, NOW) == []
    assert plan([correction], {KOHLI: "key"}, {GAME}, heard, NOW) == []
    assert plan([item(6, "correction", "Goal ruled out", corrects=5, ago=LIVE_TTL + 60)], {KOHLI: "key"}, set(), heard, NOW) == []
    # Later in the game the goal stays out of the story, and the correction stays in it.
    later = [*heard, sent(GAME, "Goal ruled out: Mohamed Salah", ago=60, moments=[6])]
    [p] = plan([item(7, "yellow", "Yellow card: Mohamed Salah", level="minor", priority=20)], {KOHLI: "everything"}, set(), later, NOW, {5})
    assert p.story == ["Goal ruled out: Mohamed Salah", "Mohamed Salah starts"]


def test_a_goal_taken_back_before_it_went_isnt_sent():
    goal = item(5, "goal", "Goal: Mohamed Salah puts Egypt ahead", priority=90)
    correction = item(6, "correction", "Goal ruled out: Mohamed Salah", priority=85, corrects=5)
    # A restart dropped the goal's batch, and its correction went to nobody (nobody was sent the goal):
    # the tick's sweep, still inside the goal's TTL, doesn't send it now.
    assert plan([correction], {KOHLI: "key"}, set(), [], NOW) == []
    assert plan([goal], {KOHLI: "key"}, set(), [], NOW, {5}) == []
    # The sweep found both in one batch: neither goes.
    assert plan([goal, correction], {KOHLI: "key"}, set(), [], NOW) == []


# ---------------------------------------------------------------- a game under several fixtures (BHA-378)

CARLSEN, LAZAVIK = 5, 6
ON_CARLSENS, ON_LAZAVIKS = 21, 22  # the board under each player's fixture: an event each, polled apart (structured.boards)


def side(id: int, player: int, title: str, voice: int, *, kind: str = "swing", priority: int = 70) -> Item:
    """A chess board's moment as one side's fixture found it, naming whose side tells it (Found.voice)."""
    event = ON_CARLSENS if player == CARLSEN else ON_LAZAVIKS
    return replace(item(id, kind, title, player=player, event=event, priority=priority, body="Engine +4.0 · move 40"), fixtures=frozenset({ON_CARLSENS, ON_LAZAVIKS}), voice=voice)


def test_a_fan_of_both_players_on_a_board_hears_each_moment_once_from_the_side_it_went_toward():
    trouble, winning = side(1, LAZAVIK, "Denis Lazavik is in trouble", CARLSEN), side(2, CARLSEN, "Magnus Carlsen is winning", CARLSEN)
    both = {CARLSEN: "key", LAZAVIK: "key"}
    assert plan([trouble], both, set(), [], NOW) == []  # Lazavik's fixture found it first: nothing, and nothing waits for Carlsen's
    [p] = plan([winning], both, set(), [], NOW)
    assert (p.title, p.player_id, p.event_id) == ("Magnus Carlsen is winning", CARLSEN, ON_CARLSENS)
    # A fan of one of them hears it from their player's side, as before.
    assert [q.title for q in plan([trouble], {LAZAVIK: "key"}, set(), [], NOW)] == ["Denis Lazavik is in trouble"]
    assert [q.title for q in plan([winning], {CARLSEN: "key"}, set(), [], NOW)] == ["Magnus Carlsen is winning"]
    # And so does one who set the other side to results only: its words wouldn't reach them.
    assert [q.title for q in plan([trouble], {CARLSEN: "results", LAZAVIK: "key"}, set(), [], NOW)] == ["Denis Lazavik is in trouble"]
    # The start and the result: once each, White's start and the winner's result.
    starts = [side(3, CARLSEN, "Magnus Carlsen is playing", CARLSEN, kind="start"), side(4, LAZAVIK, "Denis Lazavik is playing", CARLSEN, kind="start")]
    finals = [side(5, LAZAVIK, "Denis Lazavik: Lost with Black vs Magnus Carlsen", CARLSEN, kind="final"), side(6, CARLSEN, "Magnus Carlsen: Won with White vs Denis Lazavik", CARLSEN, kind="final")]
    assert [[q.title for q in plan([m], both, set(), [], NOW)] for m in [*starts, *finals]] == [["Magnus Carlsen is playing"], [], [], ["Magnus Carlsen: Won with White vs Denis Lazavik"]]


def test_the_alerts_list_takes_a_boards_moment_for_the_other_sides_only_on_the_same_play():
    trouble = replace(side(1, LAZAVIK, "Denis Lazavik is in trouble", CARLSEN), detail="w2m41")
    winning = replace(side(2, CARLSEN, "Magnus Carlsen is winning", CARLSEN), detail="w2m41")
    assert voiced_by(trouble, winning) and not voiced_by(winning, trouble)  # the side it went toward keeps its own
    assert not voiced_by(trouble, replace(winning, id=3, detail="w2m47"))  # another swing a few moves later: both stay


def test_a_moment_left_to_the_other_side_is_kept_and_its_twin_after_it_went_late_isnt_said():
    trouble = replace(side(1, LAZAVIK, "Denis Lazavik is in trouble", CARLSEN), detail="gw2m41")
    winning = replace(side(2, CARLSEN, "Magnus Carlsen is winning", CARLSEN), detail="gw2m41")
    both = {CARLSEN: "key", LAZAVIK: "key"}
    held: list[Item] = []
    assert plan([trouble], both, set(), [], NOW, held=held) == [] and held == [trouble]  # for notify.send_held, should Carlsen's never come
    # Sent late after all (notify.send_held), Carlsen's word for it, coming later still, isn't said too.
    late = sent(ON_LAZAVIKS, "Denis Lazavik is in trouble", player=LAZAVIK, moments=[1], ago=60)
    assert plan([winning], both, set(), [late], NOW, heard=[trouble]) == []
    assert [p.title for p in plan([winning], {CARLSEN: "key"}, set(), [], NOW)] == ["Magnus Carlsen is winning"]


def test_a_board_under_two_fixtures_is_one_notification_with_one_story():
    # The turnaround is Lazavik's to tell, from his fixture; the game's notification is the one Carlsen's winning went on.
    turned = side(3, LAZAVIK, "Turnaround: Denis Lazavik is winning", LAZAVIK, priority=85)
    [winning] = plan([side(2, CARLSEN, "Magnus Carlsen is winning", CARLSEN)], {CARLSEN: "key"}, set(), [], NOW)
    earlier = [sent(ON_CARLSENS, "Magnus Carlsen is winning", player=CARLSEN, moments=[2], ago=900)]
    [p] = plan([turned], {CARLSEN: "key", LAZAVIK: "key"}, set(), earlier, NOW)
    assert p.tag == winning.tag == f"e{ON_CARLSENS}" and p.story == ["Magnus Carlsen is winning"]  # the board's first fixture's
    # Muted under either fixture, it's muted.
    assert plan([turned], {CARLSEN: "key", LAZAVIK: "key"}, {ON_CARLSENS}, earlier, NOW) == []
    # A game whose fixture is the game keeps its event's notification.
    assert plan([item(1, "fifty", "Virat Kohli reaches 50")], {KOHLI: "key"}, set(), [], NOW)[0].tag == "e10"


def test_teammates_telling_the_same_play_are_one_line_naming_them_both():
    # BUG-206: a fan who follows two teammates got one push, but every line in it twice: "zekken: Sentinels win 2–0 |
    # TenZ: Sentinels win 2–0". The same play in the same words but for whom they start with is one line, naming both.
    tenz, zekken, aspas = 3, 4, 5

    def said(id: int, kind: str, title: str, who: int, detail: str = "", body: str = "v NRG · Upper bracket final", priority: int = 80) -> Item:
        name = {tenz: "TenZ", zekken: "zekken", aspas: "aspas"}[who]
        return replace(item(id, kind, title, player=who, body=body, priority=priority), who=name, detail=detail)

    batch = [said(1, "final", "TenZ: Sentinels win 2–0", tenz), said(2, "final", "zekken: Sentinels win 2–0", zekken),
             said(3, "kda", "TenZ: 12/2/9 on Jett", tenz, "kda2", "Map 2 won", 60), said(4, "kda", "zekken: 3/5/10 on Raze", zekken, "kda2", "Map 2 won", 30)]
    both = {tenz: "everything", zekken: "everything"}
    [p] = plan(batch, both, set(), [], NOW)
    assert (p.title, p.lines) == ("TenZ and zekken: Sentinels win 2–0", ["TenZ and zekken: Sentinels win 2–0", "TenZ: 12/2/9 on Jett", "zekken: 3/5/10 on Raze"])
    assert p.body == "v NRG · Upper bracket final\nTenZ: 12/2/9 on Jett\nzekken: 3/5/10 on Raze"  # each player's own numbers stay theirs
    assert sorted(p.moment_ids) == [1, 2, 3, 4] and p.moment_ids[:3] == [2, 3, 4]  # each line's moment first, then those folded in
    assert p.who == "TenZ and zekken"
    # A fan of one of them hears theirs, as before.
    [one] = plan(batch, {tenz: "everything"}, set(), [], NOW)
    assert one.lines == ["TenZ: Sentinels win 2–0", "TenZ: 12/2/9 on Jett"]
    # A series starting, and a whole squad: one line.
    starts = [said(5 + n, "start", f"{who}'s series has started", p, body="SEN v NRG · Bo3") for n, (p, who) in enumerate([(tenz, "TenZ"), (zekken, "zekken"), (aspas, "aspas")])]
    [start] = plan(starts, {**both, aspas: "key"}, set(), [], NOW)
    assert start.lines == ["aspas, TenZ and zekken's series has started"] and start.loud
    # Moments that only look alike aren't folded: other plays, or other games.
    goals = [said(9, "goal", "Goal: TenZ", tenz, "goal@1", "1–0"), said(10, "goal", "Goal: zekken", zekken, "goal@2", "2–0")]
    assert plan(goals, both, set(), [], NOW)[0].lines == ["Goal: zekken", "Goal: TenZ"]
    apart = [batch[0], replace(batch[1], event_id=OTHER)]
    assert sorted(len(p.lines) for p in plan(apart, both, set(), [], NOW)) == [1, 1]
