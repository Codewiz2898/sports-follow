"""The content judge (curate.py): the rules stage on its own, the model stage against a fake gateway, and the
tuning command on the made-up candidates in tests/fixtures/curate_*.jsonl. No test reaches a model."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from llm_providers import LlmError
from llm_providers.results import ToolCall

from sports_follow import config, curate, players, sports
from sports_follow.curate import Candidate, CurateError, Judged

FIXTURES = Path(__file__).parent / "fixtures"
CANDIDATES = FIXTURES / "curate_candidates.jsonl"
LABELS = FIXTURES / "curate_labels.jsonl"

PLAYER = {"name": "Arjun Mehra", "aliases": ["Mehra"], "teams": ["India", "Mumbai Mariners"], "sport": "cricket", "handles": [{"platform": "x", "handle": "arjunmehra18"}]}


def candidate(**over: Any) -> Candidate:
    row: dict[str, Any] = {"kind": "post", "source": "x", "url": "https://example.test/1", "author_name": "Someone", "author_handle": "@someone",
                           "title": "", "text": "Arjun Mehra's cover drive is something else.", "player": PLAYER}  # fmt: skip
    for key, value in over.items():
        if key == "player":
            row["player"] = {**PLAYER, **value}
        else:
            row[key] = value
    return Candidate.model_validate(row)


# ---------------------------------------------------------------- the rules


def test_a_paid_partnership_is_a_promo_by_rule():
    v = curate.rules(candidate(platform_flags={"paid_partnership": True})).final
    assert v and v.stage == "rules" and v.sponsored and v.kind == "promo" and v.about_player == "subject"
    assert "paid partnership" in v.reason.lower()


@pytest.mark.parametrize("tag", ["#ad", "#Sponsored", "#collab", "#PaidPartnership"])
def test_an_ad_hashtag_in_the_words_or_the_flags_is_sponsored(tag):
    assert (v := curate.rules(candidate(text=f"Training day. {tag}")).final) and v.sponsored and v.kind == "promo"
    assert (v := curate.rules(candidate(platform_flags={"hashtags": [tag]})).final) and v.sponsored
    # A hashtag that only starts with one isn't one: #adelaide, #adventure.
    assert curate.rules(candidate(text="What a day #adelaide #adventure")).final is None


@pytest.mark.parametrize("text", ["Use code ARJUN20 at checkout", "Get 15% off this week", "Promo code below!", "code: MEHRA10"])
def test_a_discount_code_is_sponsored(text):
    assert (v := curate.rules(candidate(text=text)).final) and v.sponsored and v.kind == "promo"


@pytest.mark.parametrize("text", ["Cracking the code with Arjun Mehra", "Mehra explains the code behind his opening", "code like this is rare", "Code GOAT"])
def test_the_word_code_alone_is_no_discount(text):
    assert curate.rules(candidate(text=text)).final is None


@pytest.mark.parametrize("text", ["Dream11 team for tomorrow", "Best odds at Bet365 tonight", "Kairo's picks + Thunderpick bonus", "my 11 circle picks"])
def test_a_betting_or_fantasy_brand_is_ruled_out(text):
    v = curate.rules(candidate(text=text)).final
    assert v and v.risks == ["betting"] and v.interest == 0 and v.sponsored


def test_news_naming_a_betting_brand_is_the_models_to_judge():
    for c in (candidate(kind="article", source="rss", title="Board ends its Dream11 shirt deal after the new gaming law"),
              candidate(text="Mehra in the new Dream11 kit", author_kind="official")):
        facts = curate.rules(c)
        assert facts.final is None and any("dream11" in h for h in facts.hints)


def test_a_time_without_a_zone_is_utc():
    assert candidate(published_at="2026-10-20T10:00:00").published_at.isoformat() == "2026-10-20T10:00:00+00:00"  # type: ignore[union-attr]
    assert candidate(published_at="2026-10-20T10:00:00+05:30").published_at.utcoffset().total_seconds() == 19800  # type: ignore[union-attr]


def test_a_betting_brand_in_the_players_own_team_name_is_not_betting():
    c = candidate(text="BetBoom Ascend win the final, Zephyr MVP", player={"name": "Zephyr", "aliases": [], "teams": ["BetBoom Ascend"], "sport": "bgmi"})
    assert curate.rules(c).final is None
    # Nor is a word inside another ("dream11x" isn't Dream11; "MPL" is a league, not on the list).
    assert curate.rules(candidate(text="MPL season 14 final")).final is None


def test_a_long_video_from_nobody_known_is_a_restream():
    v = curate.rules(candidate(kind="video", source="youtube", title="IND v AUS full day", duration_seconds=3 * 3600)).final
    assert v and v.risks == ["restream"] and v.interest == 0
    # From an official channel, the same length is the model's to judge; from a known creator too, with a hint.
    assert curate.rules(candidate(kind="video", source="youtube", duration_seconds=3 * 3600, author_kind="official")).final is None
    facts = curate.rules(candidate(kind="video", source="youtube", duration_seconds=40 * 60, author_kind="creator"))
    assert facts.final is None and facts.by == "fan_or_creator" and any("40 minutes" in h for h in facts.hints)
    # Under the sport's limit it's an ordinary video.
    assert curate.rules(candidate(kind="video", source="youtube", duration_seconds=10 * 60)).final is None


def test_a_sport_where_long_videos_are_normal_leaves_them_to_the_model():
    chess = next(s for s in sports.ALL if s.restream_after is None)
    c = candidate(kind="video", source="youtube", duration_seconds=2 * 3600, player={"sport": chess.key})
    assert curate.rules(c).final is None


def test_a_video_that_cant_play_here_is_a_restream_whoever_posted_it():
    v = curate.rules(candidate(kind="video", source="youtube", author_kind="official", platform_flags={"embeddable": False})).final
    assert v and v.risks == ["restream"] and v.by == "official"


def test_the_players_own_handle_makes_it_theirs_and_leaves_the_rest_to_the_model():
    facts = curate.rules(candidate(author_handle="@ArjunMehra18", text="Back in the nets."))
    assert facts.final is None and facts.by == "player" and facts.hints
    # The same handle on another platform isn't theirs.
    assert curate.rules(candidate(source="instagram", author_handle="arjunmehra18")).by is None
    # Their own paid post is still a promo, by them.
    v = curate.rules(candidate(author_handle="arjunmehra18", platform_flags={"paid_partnership": True})).final
    assert v and v.by == "player" and v.sponsored


def test_link_in_bio_is_a_hint_not_a_verdict():
    facts = curate.rules(candidate(text="Full breakdown of his technique, link in bio"))
    assert facts.final is None and any("link in bio" in h for h in facts.hints)


def test_a_rules_verdict_tells_who_it_is_about_from_names_and_teams():
    ad = {"platform_flags": {"paid_partnership": True}}
    assert curate.rules(candidate(title="Arjun Mehra's new bat", **ad)).final.about_player == "subject"  # type: ignore[union-attr]
    assert curate.rules(candidate(title="India's new kit", text="Modelled by Mehra and others", **ad)).final.about_player == "mention"  # type: ignore[union-attr]
    assert curate.rules(candidate(title="Mumbai Mariners kit launch", text="", **ad)).final.about_player == "mention"  # type: ignore[union-attr]
    assert curate.rules(candidate(title="A new phone", text="Buy it", **ad)).final.about_player == "no"  # type: ignore[union-attr]
    # A surname alone is the player, with no alias for it.
    assert curate.rules(candidate(title="Mehra's new bat", player={"aliases": []}, **ad)).final.about_player == "subject"  # type: ignore[union-attr]


def test_long_text_is_capped_and_hashtags_are_bare():
    c = candidate(text="x" * 10_000, title="t" * 1_000, platform_flags={"hashtags": ["#Cricket", " ad ", "#"]})
    assert len(c.text) == curate.TEXT_MAX and len(c.title) == curate.TITLE_MAX
    assert c.platform_flags.hashtags == ["cricket", "ad"]


# ---------------------------------------------------------------- the model, faked


class FakeGateway:
    """Answers each complete() with the next of its replies: a dict is a submit_verdict call's input, an
    exception is raised, None is a reply with no tool call."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **request: Any) -> SimpleNamespace:
        self.calls.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        calls = [] if reply is None else [ToolCall(id=f"call-{len(self.calls)}", name="submit_verdict", args=reply)]
        return SimpleNamespace(finish="tool_calls" if calls else "stop", tool_calls=calls, message={"role": "assistant", "content": ""}, usage=None, model="fake", provider="fake")


GOOD = {"about_player": "subject", "by": "fan_or_creator", "kind": "match_moment", "interest": 0.8, "risks": [], "sponsored": False, "reason": "A fan edit of his hundred at Perth."}


def test_the_model_judges_what_the_rules_cant_and_sees_the_sports_hints():
    llm = FakeGateway(GOOD)
    v = curate.judge(candidate(kind="video", source="youtube", title="Mehra 112 edit"), llm)  # type: ignore[arg-type]
    assert v.stage == "model" and v.kind == "match_moment" and v.interest == 0.8 and v.reason == GOOD["reason"]
    [call] = llm.calls
    assert call["model"] == curate.MODEL and call["label"] == "curate" and call["tools"][0]["name"] == "submit_verdict"
    assert set(call["tools"][0]["parameters"]["properties"]) == set(Judged.model_fields)  # the model never says which stage
    hints = sports.of("cricket").content_hints()
    assert hints and hints.splitlines()[0] in call["system"]
    shown = json.loads(call["messages"][0]["content"].split("\n\n")[0])
    assert shown["player"]["name"] == "Arjun Mehra" and shown["share"]["title"] == "Mehra 112 edit"


def test_the_reason_a_fan_reads_is_one_short_line():
    v = curate.judge(candidate(), FakeGateway({**GOOD, "reason": "A fan edit\nof his hundred " + "and more " * 40}))  # type: ignore[arg-type]
    assert "\n" not in v.reason and len(v.reason) <= curate.REASON_MAX and v.reason.endswith("…")


def test_a_sport_without_hints_gets_the_judges_own_prompt():
    assert curate.system_prompt("golf") == curate.PROMPT.replace("{hints}", "").strip()


def test_what_the_rules_settled_stands_over_the_model():
    llm = FakeGateway({**GOOD, "by": "fan_or_creator", "risks": ["abuse", "made-up-risk"]})
    v = curate.judge(candidate(author_handle="arjunmehra18"), llm)  # type: ignore[arg-type]
    assert v.by == "player" and v.risks == ["abuse"]  # an unknown risk is dropped, not fatal
    assert "own accounts" in llm.calls[0]["messages"][0]["content"]


def test_only_the_players_own_accounts_make_a_share_theirs():
    by_player = {**GOOD, "by": "player"}
    # A channel named after the player, calling itself their home, isn't one of their accounts.
    fan_page = candidate(kind="video", source="youtube", author_name="Arjun Mehra", author_handle="UCarjunmehrafanclub0000000", text="The official home of Arjun Mehra fans.")
    assert curate.judge(fan_page, FakeGateway(by_player)).by == "unknown"  # type: ignore[arg-type]
    assert curate.judge(candidate(kind="article", source="rss", author_name="Arjun Mehra"), FakeGateway(by_player)).by == "outlet"  # type: ignore[arg-type]
    # A creator the fetcher knows stays one; the player's own handle is theirs, whatever the model says.
    assert curate.judge(candidate(author_kind="creator"), FakeGateway(by_player)).by == "fan_or_creator"  # type: ignore[arg-type]
    assert curate.judge(candidate(author_handle="@ArjunMehra18"), FakeGateway(GOOD)).by == "player"  # type: ignore[arg-type]


def test_the_judge_is_told_a_channels_own_boilerplate_is_not_sponsorship():
    prompt = curate.system_prompt("cricket")
    assert "boilerplate is never sponsorship" in prompt and "podcast" in prompt and "academy" in prompt
    said = Judged.model_fields["sponsored"].description or ""
    assert "boilerplate" in said and "membership" in said


def test_a_verdict_off_the_schema_is_sent_back_once_and_a_silent_reply_asked_again():
    llm = FakeGateway({**GOOD, "by": "creator"}, None, GOOD)
    v = curate.judge(candidate(), llm)  # type: ignore[arg-type]
    assert v.by == "fan_or_creator" and len(llm.calls) == 3
    said = [m["content"] for m in llm.calls[-1]["messages"] if m["role"] in ("tool", "user")]
    assert "did not match the schema" in said[1] and "call submit_verdict" in said[2]


def test_a_model_that_never_submits_leaves_it_unjudged():
    with pytest.raises(CurateError):
        curate.judge(candidate(), FakeGateway(None, None, None))  # type: ignore[arg-type]


def test_a_slow_host_is_retried_once_and_a_bad_request_is_not():
    llm = FakeGateway(LlmError("timeout", "slow"), GOOD)
    assert curate.judge(candidate(), llm).stage == "model" and len(llm.calls) == 2  # type: ignore[arg-type]
    with pytest.raises(CurateError, match="bad_request"):
        curate.judge(candidate(), FakeGateway(LlmError("bad_request", "no")))  # type: ignore[arg-type]


def test_with_the_model_off_the_rules_still_judge_and_the_rest_waits(monkeypatch):
    monkeypatch.setattr(config, "CURATE_AI", False)
    llm = FakeGateway()
    assert curate.judge(candidate(platform_flags={"paid_partnership": True}), llm).stage == "rules"  # type: ignore[arg-type]
    with pytest.raises(CurateError, match="off"):
        curate.judge(candidate(), llm)  # type: ignore[arg-type]
    assert curate.judge_many([candidate(), candidate(text="#ad")], llm) == [None, curate.rules(candidate(text="#ad")).final]  # type: ignore[arg-type]
    assert not llm.calls


def test_judge_many_keeps_the_order_and_leaves_failures_unjudged():
    items = [candidate(url=f"https://example.test/{i}", text=f"Mehra post {i}") for i in range(8)]
    items[3] = candidate(url="https://example.test/ad", text="#ad")

    class Gateway:
        def complete(self, **request: Any) -> SimpleNamespace:
            content = request["messages"][0]["content"]
            if "Mehra post 5" in content:
                raise LlmError("bad_request", "no")
            n = json.loads(content.split("\n\n")[0])["share"]["text"].rsplit(" ", 1)[-1]
            return FakeGateway({**GOOD, "reason": f"Post {n}"}).complete(**request)

    out = curate.judge_many(items, Gateway(), workers=4)  # type: ignore[arg-type]
    assert [v.reason if v else None for v in out] == ["Post 0", "Post 1", "Post 2", "Marked as an ad (#ad).", "Post 4", None, "Post 6", "Post 7"]
    assert curate.judge_many([], Gateway()) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- the fixtures and the tuning command


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_the_fixtures_parse_and_every_candidate_has_a_label():
    candidates = [Candidate.model_validate(r) for r in _rows(CANDIDATES)]
    labels = {r["url"]: r for r in _rows(LABELS)}
    assert len(candidates) >= 30 and len({c.url for c in candidates}) == len(candidates)
    assert {c.url for c in candidates} == set(labels)
    assert {sports.family(c.player.sport) for c in candidates} >= {"cricket", "chess"} and any(sports.of(c.player.sport).group for c in candidates)
    for label in labels.values():
        Judged.model_validate({**label, "reason": "-"})  # every label is a verdict the judge could give


def test_the_rules_agree_with_the_hand_labels_on_everything_they_decide():
    candidates = [Candidate.model_validate(r) for r in _rows(CANDIDATES)]
    labels = {r["url"]: r for r in _rows(LABELS)}
    decided = {c.url: v.model_dump(mode="json") for c in candidates if (v := curate.rules(c).final)}
    assert len(decided) >= 8
    for name, (agree, total) in curate.agreement(decided, labels).items():
        assert agree == total == len(decided), name


def test_the_command_prints_verdicts_and_agreement(capsys, monkeypatch):
    labels = {r["url"]: r for r in _rows(LABELS)}

    class Gateway:  # answers each candidate with its own label: full agreement
        def complete(self, **request: Any) -> SimpleNamespace:
            url = json.loads(request["messages"][0]["content"].split("\n\n")[0])["share"]["url"]
            label = {k: v for k, v in labels[url].items() if k != "url"}
            return FakeGateway({**label, "reason": "Labelled."}).complete(**request)

    monkeypatch.setattr(curate, "gateway", Gateway)
    assert curate.main([str(CANDIDATES), "--labels", str(LABELS)]) == 0
    out, err = capsys.readouterr()
    verdicts = [json.loads(line) for line in out.splitlines()]
    assert [v["url"] for v in verdicts] == list(labels) and all(v["stage"] in ("rules", "model") for v in verdicts)
    assert "0 unjudged" in err and f"about_player  {len(labels)}/{len(labels)}  100%" in err and "✗" not in err


def test_the_command_runs_on_the_rules_alone(capsys):
    assert curate.main([str(CANDIDATES), "--rules-only"]) == 0
    out, err = capsys.readouterr()
    rows = [json.loads(line) for line in out.splitlines()]
    assert any(r.get("unjudged") for r in rows) and any(r.get("stage") == "rules" for r in rows)
    assert "0 by the model" in err


# ---------------------------------------------------------------- the sports and the players


def test_every_sport_gives_the_judge_a_few_lines_of_its_own():
    for sport in (*sports.ALL, *sports.GROUPS):
        hints = sport.content_hints()
        assert isinstance(hints, str) and len(hints) < 1200, sport.key
        assert hints and "milestone" in hints.lower(), sport.key
    assert sports.OTHER.content_hints() == ""


def test_socials_are_set_checked_and_cleared():
    page = SimpleNamespace(socials=[])
    found = players.Found(players=[page])  # type: ignore[list-item]
    players.set_socials(found, [("x", "@imVkohli"), ("instagram", "virat.kohli"), ("x", "imVkohli")])
    assert page.socials == [{"platform": "x", "handle": "imVkohli"}, {"platform": "instagram", "handle": "virat.kohli"}]
    with pytest.raises(players.Refused, match="platform"):
        players.set_socials(found, [("myspace", "tom")])
    with pytest.raises(players.Refused, match="handle"):
        players.set_socials(found, [("x", "not a handle")])
    with pytest.raises(players.Refused, match="page"):
        players.set_socials(players.Found(), [("x", "a")])
    players.set_socials(found, [])
    assert page.socials == []
    # What's set is what the judge reads as the player's own.
    assert curate.Social.model_validate({"platform": "x", "handle": "imVkohli"})
