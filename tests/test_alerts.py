"""The noise rules (alerts.py): what reaches a fan's phone, one notification per game, and when it may
make a sound."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sports_follow.alerts import BREAKTHROUGH, LIVE_TTL, LOUD_PER_HOUR, Item, Sent, plan

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
