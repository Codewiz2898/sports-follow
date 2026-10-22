"""The feed's news against Postgres (news.py, BHA-325): a poll of made-up publishers' feeds keeps one row per player
and story, however many feeds list it; a common surname in the registry needs the player's side; the judge and the
ranking serve the best story of a group with the number of outlets that ran it, as the Feed's news, the player
page's and the Wire's, in place of the card's; removing a player takes their stories, and the weekly prune drops
old ones but an admin's evergreen ones. Every feed, publisher, story and player is made up. Skipped without a
throwaway database at alembic head (tests/test_corrections_db.py says how to make one)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.orm import sessionmaker

from sports_follow import bus, candidates, curate, db as db_module, feed, news, rank, week
from sports_follow.adapters import publisher_rss
from sports_follow.models import Athlete, FeedCandidate, Follow, Moment, NewsItem, Player, PlayerCard, SourceBinding

URL = os.environ.get("SPORTS_FOLLOW_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs SPORTS_FOLLOW_TEST_DATABASE_URL: a throwaway database at alembic head")

NOW = datetime.now(timezone.utc).replace(microsecond=0)
WICKET, GAZETTE = "https://wicket.example.com/rss", "https://gazette.example.org/cricket/feed"
CARD = {"player": {"name": "Asha Verma"}, "news": [{"headline": "Verma named captain", "url": "https://agent.example.com/captain", "source": "Somewhere",
                                                    "published": (NOW - timedelta(days=2)).isoformat(), "summary": ""}]}  # fmt: skip


def rss(*items: tuple[str, str, str, float]) -> str:
    """A feed of (title, link, description, hours ago) items."""
    body = "".join(f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
                   f"<pubDate>{(NOW - timedelta(hours=h)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>" for t, link, d, h in items)  # fmt: skip
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Made up</title>{body}</channel></rss>'


@pytest.fixture
def world(monkeypatch):
    engine = create_engine(URL or "postgresql+psycopg://")
    with engine.begin() as c:
        c.execute(text("TRUNCATE feed_candidates, feed_feedback, follow, moment, news_player, news_item, player_card, player_alias, player_identity, player, athlete RESTART IDENTITY CASCADE"))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", maker)
    feeds: dict[str, str] = {}
    monkeypatch.setattr(publisher_rss, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=feeds[str(r.url)]) if str(r.url) in feeds else httpx.Response(404))))
    monkeypatch.setattr(news, "feeds", lambda source: {WICKET: news.Listed("Daily Wicket", {"cricket"}, "cricket"), GAZETTE: news.Listed("Cricket Gazette", {"cricket"}, "cricket")})
    monkeypatch.setattr(rank, "listed_channels", lambda: {WICKET, GAZETTE})
    yield maker, feeds
    engine.dispose()


def player(name: str, teams: list[str], followed: bool = True, card: dict | None = None) -> int:
    with db_module.session() as s:
        p = Player(slug=name.lower().replace(" ", "-"), name=name, sport="cricket", teams=teams)
        s.add(p)
        s.flush()
        if card is not None:
            s.add(PlayerCard(player_id=p.id, status="ready", card=card))
        if followed:
            s.add(Follow(fan_id="fan", player_id=p.id, alert_rules={}))
        return p.id


def stories() -> list[tuple[int, str, str]]:
    with db_module.session() as s:
        return [tuple(r) for r in s.execute(select(FeedCandidate.player_id, FeedCandidate.url, FeedCandidate.candidate["channel"].astext).order_by(FeedCandidate.id)).all()]


def test_a_poll_keeps_one_row_per_player_and_story_however_many_feeds_list_it(world):
    _, feeds = world
    asha = player("Asha Verma", ["Riverside XI"])
    player("Kiran Rao", ["Eastern Stars"], followed=False)  # nobody follows her: never looked for
    feeds[WICKET] = rss(
        ("Asha Verma hits a century", "https://wicket.example.com/a/1?utm_source=rss", "112 not out.", 2),
        ("Kiran Rao takes five", "https://wicket.example.com/a/2", "Five for the spinner.", 3),
        ("Asha Verma's old record", "https://wicket.example.com/a/0", "From last season.", 24 * 5),  # too old to be news
    )
    feeds[GAZETTE] = rss(("Verma century, as it happened", "https://wicket.example.com/a/1#live", "A syndicated copy.", 1))
    done = news.poll(NOW)
    assert done == {"cricket": {"feeds": 2, "unread": 0, "read": 3, "matched": 2, "kept": 1}}
    assert stories() == [(asha, "https://wicket.example.com/a/1", "Daily Wicket")]
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.source == "publisher_rss" and row.external_id == news.external_id(row.url) and row.verdict is None
        assert row.candidate["title"] == "Asha Verma hits a century" and row.candidate["description"] == "112 not out." and row.candidate["channel_id"] == WICKET
        assert row.features == {"outlets": 1}
    assert news.poll(NOW + timedelta(minutes=20))["cricket"]["kept"] == 0  # read again: nothing new


def test_a_common_surname_in_the_registry_names_a_player_only_with_their_side(world):
    _, feeds = world
    smith = player("Dev Smith", ["Eastern Stars"])
    with db_module.session() as s:
        s.add_all(Athlete(qid=f"Q{i}", sport="cricket", name=n, search_text=n.lower()) for i, n in enumerate(["Dev Smith", "Ann Smith", "Joe Smith", "Ravi Kumar"]))
    feeds[WICKET] = rss(("Smith ruled out", "https://wicket.example.com/s/1", "A hamstring.", 2),
                        ("Smith back for Eastern Stars", "https://wicket.example.com/s/2", "Fit again.", 1))  # fmt: skip
    feeds[GAZETTE] = rss()
    news.poll(NOW)
    assert stories() == [(smith, "https://wicket.example.com/s/2", "Daily Wicket")]


def said(**kw) -> dict:
    return {"about_player": "subject", "by": "outlet", "kind": "news", "interest": 0.6, "risks": [], "sponsored": False, "reason": "News of her hundred.", "stage": "model", **kw}


def test_the_best_story_of_a_group_is_served_with_its_outlets_as_the_feeds_the_pages_and_the_wires_news(world, monkeypatch):
    maker, feeds = world
    asha = player("Asha Verma", ["Riverside XI"], card=CARD)
    with db_module.session() as s:  # the agent's story, told on the Wire before the curator read any feed
        story = NewsItem(dedupe_hash="x", headline="Verma injured", summary="", source="Somewhere", url="https://agent.example.com/injury", kind="injury")
        s.add(story)
        s.flush()
        s.add(Moment(key=f"-:{asha}:news:n{story.id}", player_id=asha, kind="news", level="key", priority=80, title="Asha Verma: injury news",
                     url="https://agent.example.com/injury", created_at=NOW - timedelta(hours=5)))  # fmt: skip
    feeds[WICKET] = rss(("Verma's unbeaten century powers Riverside to victory", "https://wicket.example.com/c/1", "Asha Verma struck 112 not out against Eastern Stars", 3),
                        ("Verma signs bat sponsorship deal", "https://wicket.example.com/c/2", "The captain joins a new equipment brand", 2))  # fmt: skip
    feeds[GAZETTE] = rss(("Riverside win as Verma hits century against Eastern Stars", "https://gazette.example.org/c/9", "An unbeaten knock from the captain", 2.5))
    news.poll(NOW)
    asked: list[curate.Candidate] = []

    def judge_many(cands):
        cands = list(cands)
        asked.extend(cands)
        return [curate.Verdict.model_validate(said(kind="promo", sponsored=True) if "sponsorship" in c.title else said(interest=0.8 if "gazette" in c.url else 0.6)) for c in cands]

    monkeypatch.setattr(curate, "judge_many", judge_many)
    assert rank.judge(NOW)["judged"] == 3
    assert {(c.kind, c.source, c.author_kind, c.author_name) for c in asked} == {("article", "rss", "outlet", "Daily Wicket"), ("article", "rss", "outlet", "Cricket Gazette")}
    assert rank.rescore(NOW)["served"] == 1  # the century, once; the sponsorship deal is a promo
    with db_module.session() as s:
        got = {r.url: r for r in s.scalars(select(FeedCandidate))}
    best, other = got["https://gazette.example.org/c/9"], got["https://wicket.example.com/c/1"]
    assert best.score and other.score is None and best.features["outlets"] == other.features["outlets"] == 2

    s = maker()
    try:
        page = feed.feed_for(s, asha, "fan")
        assert page is not None
        items = [i for g in page.groups for i in g.items]
        assert [(i.kind, i.title, i.source, i.outlets, i.summary) for i in items] == [
            ("news", "Riverside win as Verma hits century against Eastern Stars", "Cricket Gazette", 2, "An unbeaten knock from the captain")]  # the card's news gives way
        wire = week.wire(s, "fan", NOW)  # the story, and the agent's injury line still
        assert [(r["tag"], r["text"], r["source"]) for r in wire] == [("News", "Riverside win as Verma hits century against Eastern Stars", "Cricket Gazette"),
                                                                       ("Injury", "Verma injured", "Somewhere")]  # fmt: skip
    finally:
        s.close()

    # The curator puts the served stories on the card, beside the agent's news, and publishes it as any card is:
    # a page, a stream and the Android app all get the same card.
    sent: list[tuple[int, dict]] = []
    monkeypatch.setattr(bus, "store_card", lambda pid, card: sent.append((pid, card)))
    assert rank.cards(NOW) == {"cards": 1}
    (pid, card), = sent
    assert pid == asha and card["news"] == CARD["news"] and card["version"] == 1
    assert [st | {"published": None} for st in card["stories"]] == [{
        "headline": "Riverside win as Verma hits century against Eastern Stars", "url": "https://gazette.example.org/c/9", "source": "Cricket Gazette",
        "published": None, "summary": "An unbeaten knock from the captain", "outlets": 2}]  # fmt: skip
    with db_module.session() as s3:
        stored = s3.get(PlayerCard, asha)
        assert stored is not None and stored.card["stories"] == card["stories"] and stored.version == 1
    assert rank.cards(NOW) == {"cards": 0}  # nothing changed: nothing written
    with db_module.session() as s3:  # an admin hides the story: the other outlet's takes its place
        s3.execute(update(FeedCandidate).where(FeedCandidate.url == "https://gazette.example.org/c/9").values(review="hidden"))
    rank.rescore(NOW)
    assert rank.cards(NOW) == {"cards": 1} and [st["url"] for st in sent[-1][1]["stories"]] == ["https://wicket.example.com/c/1"]
    with db_module.session() as s3:  # and with that one hidden too, the stories leave the card; the agent's news stays
        s3.execute(update(FeedCandidate).where(FeedCandidate.url == "https://wicket.example.com/c/1").values(review="hidden"))
    rank.rescore(NOW)
    assert rank.cards(NOW) == {"cards": 1} and "stories" not in sent[-1][1] and sent[-1][1]["news"] == CARD["news"]
    # The weekly check of videos leaves stories alone, however long since they were read.
    with db_module.session() as s2:
        s2.execute(update(FeedCandidate).values(checked_at=NOW - timedelta(days=40)))
    assert candidates.check(NOW)["dropped"] == 0


def test_a_headline_the_publisher_corrects_at_the_same_address_reaches_the_card_and_the_wire(world, monkeypatch):
    # BUG-245: the first wording was kept for good, so fans read "ruled out of the series" after the publisher had
    # corrected it. A change that tells another story goes back to the judge; a small one keeps its verdict; another
    # feed's copy of the story at the same address changes nothing.
    maker, feeds = world
    monkeypatch.setattr(publisher_rss, "FEED_TTL", 1e-6)  # every poll reads the feeds again
    asha = player("Asha Verma", ["Riverside XI"], card={"player": {"name": "Asha Verma"}, "news": []})
    link = "https://wicket.example.com/v/1"
    feeds[WICKET] = rss(("Verma ruled out of the series with a back injury", link, "The opener will miss all three Tests.", 3))
    feeds[GAZETTE] = rss()
    news.poll(NOW)
    judged: list[str] = []

    def judge_many(cands):
        cands = list(cands)
        judged.extend(c.title for c in cands)
        return [curate.Verdict.model_validate(said()) for c in cands]

    monkeypatch.setattr(curate, "judge_many", judge_many)
    monkeypatch.setattr(bus, "store_card", lambda pid, card: None)

    def run() -> tuple[list[str], list[str]]:
        rank.judge(NOW)
        rank.rescore(NOW)
        rank.cards(NOW)
        s = maker()
        try:
            card = s.get(PlayerCard, asha)
            assert card is not None
            return [st["headline"] for st in card.card.get("stories") or []], [r["text"] for r in week.wire(s, "fan", NOW)]
        finally:
            s.close()

    assert run() == (["Verma ruled out of the series with a back injury"], ["Verma ruled out of the series with a back injury"])
    feeds[WICKET] = rss(("Verma to miss only the first Test", link, "Scans showed a minor strain; she is expected back for the second Test.", 3))
    assert news.poll(NOW + timedelta(minutes=20))["cricket"]["kept"] == 0  # no new row: the same one, changed
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.candidate["title"] == "Verma to miss only the first Test" and row.verdict is None and row.score is None
    assert run() == (["Verma to miss only the first Test"], ["Verma to miss only the first Test"])
    assert judged == ["Verma ruled out of the series with a back injury", "Verma to miss only the first Test"]

    # A small change says the same story: its verdict stands, and fans read the new words at once.
    feeds[WICKET] = rss(("Verma to miss only the first Test, board says", link, "Scans showed a minor strain; she is expected back for the second Test.", 3))
    news.poll(NOW + timedelta(minutes=40))
    with db_module.session() as s:
        row = s.scalar(select(FeedCandidate))
        assert row is not None and row.verdict is not None and row.score is not None
    assert run() == (["Verma to miss only the first Test, board says"], ["Verma to miss only the first Test, board says"])
    assert len(judged) == 2
    # Another feed's copy of it, worded its own way, isn't the publisher's correction.
    feeds[GAZETTE] = rss(("Verma blow eases", link, "A syndicated copy.", 2))
    news.poll(NOW + timedelta(minutes=60))
    assert stories() == [(asha, link, "Daily Wicket")] and run()[0] == ["Verma to miss only the first Test, board says"]


def test_a_bound_players_card_loses_the_agents_old_news_at_the_curators_next_run(world, monkeypatch):
    # BUG-247: a player bound to a live source after the research agent wrote their page kept its news for good (the
    # agent never runs for them again), so their Today tile read an October story as their status. The curator's
    # card pass takes it off; a player no live source has keeps theirs.
    old = {"headline": "Verma set for the launch of a new league", "url": "https://agent.example.com/launch", "source": "Somewhere",
           "published": (NOW - timedelta(days=30)).isoformat(), "summary": ""}  # fmt: skip
    bound = player("Asha Verma", ["Riverside XI"], card={"player": {"name": "Asha Verma"}, "news": [old], "freshness": {"news": "2026-09-20T00:00:00+00:00"}})
    unbound = player("Kiran Rao", ["Eastern Stars"], card={"player": {"name": "Kiran Rao"}, "news": [old]})
    with db_module.session() as s:
        s.add(SourceBinding(player_id=bound, purpose="fixtures", adapter="espn_cricket", locator={"system": "espn_cricket", "athlete_id": "1"}))
    sent: list[tuple[int, dict]] = []
    monkeypatch.setattr(bus, "store_card", lambda pid, card: sent.append((pid, card)))
    assert rank.cards(NOW) == {"cards": 1}
    (pid, card), = sent
    assert pid == bound and card["news"] == [] and "news" not in card["freshness"] and card["version"] == 1
    with db_module.session() as s:
        kept = s.get(PlayerCard, bound)
        theirs = s.get(PlayerCard, unbound)
        assert kept is not None and kept.card["news"] == [] and theirs is not None and theirs.card["news"] == [old]
    assert rank.cards(NOW) == {"cards": 0}  # nothing left to take off


# Stories from three publishers' real feeds of 16-21 Oct 2026 (BUG-249), their words retold here, their facts (who,
# when, the figures) kept: (player, outlet, published, headline, description). Few words of the same story are the
# same from one outlet to the next ("Bavuma's 179" from three, "give up hope" from two); a series' words are in all
# its stories ("Australia", "Gqeberha", "second Test"), whichever day they tell.
GUARDIAN, CRICINFO, HINDU = "The Guardian", "ESPNcricinfo", "The Hindu"
TOLD = {
    "bavuma day 2, live": ("Temba Bavuma", GUARDIAN, "2026-10-19T16:17", "South Africa reach 97-5 against Australia at stumps on day two of the men's second Test after rain delays – as it happened",
                           "Australia pick up three wickets late on but Temba Bavuma is unbeaten on 41. The radar doesn't look promising for tomorrow either…"),
    "bavuma day 2": ("Temba Bavuma", CRICINFO, "2026-10-19T16:45", "Bavuma resists but Boland's four puts Australia in control",
                     "With 146 overs lost to rain in Gqeberha so far, Australia believe they have time to win the Test and make it 2-0"),
    "bavuma 179, live": ("Temba Bavuma", GUARDIAN, "2026-10-20T15:51", "Australia limp to 3-1 after South Africa declare on 425-9 on day three of the men's second Test – as it happened",
                         "Bavuma's brilliant 179 took South Africa to 425-9 declared before Rabada struck late to leave Australia 3-1 at stumps. 47th over: South Africa 106-5 (Bavuma 42, Mulder 10). Bavuma looks set to settle in; Cummins tries a couple of bouncers…"),
    "bavuma 179": ("Temba Bavuma", GUARDIAN, "2026-10-20T16:21", "Australia on the back foot as Bavuma plays a captain's innings for South Africa",
                   "A superb 179 puts the hosts in charge on day three, and Maddison falls for a duck before the close. In Durban it was Australia's lower order that frustrated South Africa; here in Gqeberha…"),
    "bavuma 179, cricinfo": ("Temba Bavuma", CRICINFO, "2026-10-20T16:51", "Bavuma's flawless 179 turns the Test on its head",
                             "Jansen and Harmer also made half-centuries, giving South Africa a chance to push for a win that levels the series"),
    "bavuma 179, hindu": ("Temba Bavuma", HINDU, "2026-10-21T05:16", "AUS vs SA 2nd Test: Bavuma fights adversity to put South Africa in control",
                          "The captain's defiant innings showed off his batting as South Africa recovered from 110-6 in the morning session of the third day to declare on 425-"),
    "bavuma dreams": ("Temba Bavuma", CRICINFO, "2026-10-21T19:17", "'We want to go for the win' - Bavuma's 179 gives South Africa a chance to dream",
                      "South Africa are the only side who can realistically win, though enforcing the follow-on may decide the outcome"),
    "mandhana era": ("Smriti Mandhana", CRICINFO, "2026-10-16T13:18", "Raipur the stage for the start of India's Mandhana era",
                     "Zimbabwe, yet to win a T20I this year, play their first bilateral series against India"),
    "mandhana toss": ("Smriti Mandhana", CRICINFO, "2026-10-16T13:31", "Niki Prasad debuts for India as Mandhana chooses to chase against Zimbabwe",
                      "India went in with Anushka Sharma at No. 3, Harmanpreet Kaur at No. 4 and Prasad at No. 5"),
    "mandhana series": ("Smriti Mandhana", HINDU, "2026-10-19T06:17", "Shafali, Mandhana shine as India beat Zimbabwe by nine wickets for an unbeatable 2-0 series lead",
                        "Shafali and captain Smriti Mandhana (47 not out) shared a rousing opening stand as India chased 103 in 8.5 overs"),
    "mandhana series, cricinfo": ("Smriti Mandhana", CRICINFO, "2026-10-19T07:37", "Bowlers, Shafali and Mandhana power India to series win",
                                  "India cruised home after holding Zimbabwe to 102 for 8"),
    "shami hope": ("Mohammed Shami", HINDU, "2026-10-20T13:17", "Shami not giving up hope of an India recall",
                   "The veteran pacer's absence from Bengal's Ranji game against Services had set off more talk about his fitness"),
    "shami hope, cricinfo": ("Mohammed Shami", CRICINFO, "2026-10-20T17:02", "Shami: 'I'm still at my peak, why would I give up hope of playing for India?'",
                             "Left out of the tour of New Zealand, Shami insists he is fully fit and bowling with good rhythm"),
    "rabada back": ("Kagiso Rabada", CRICINFO, "2026-10-17T18:31", "Rabada's back to bolster South Africa as Australia chase a series win",
                    "South Africa get a big boost with their leading quick fit, but both sides wait to name their XIs with the weather uncertain"),
    "rabada smith": ("Kagiso Rabada", GUARDIAN, "2026-10-17T23:50", "'Testosterone to burn': Kagiso Rabada plays down his 2018 clash with Australia's Steve Smith",
                     "The bowler says there's no hate as he returns to St George's Park, where the pair clashed during a heated series in Gqeberha"),
    "rabada toss": ("Kagiso Rabada", CRICINFO, "2026-10-18T08:08", "Lyon left out as Australia go all-pace and bowl first on a damp day",
                    "Kagiso Rabada returns for South Africa, with Keshav Maharaj squeezed out as they look to level the series"),
}  # fmt: skip


def test_the_same_story_from_several_outlets_is_one_group_and_the_days_other_stories_are_not(world):
    # BUG-249: the same story from two outlets was almost never grouped, so the Feed and the page repeated it, each
    # from "1 outlet": numbers didn't count ("179"), "Bavuma's" wasn't Bavuma, and a series' words drowned the rest.
    maker, _ = world
    ids = {name: player(name, [side]) for name, side in (("Temba Bavuma", "South Africa"), ("Smriti Mandhana", "India"),
                                                         ("Mohammed Shami", "India"), ("Kagiso Rabada", "South Africa"))}  # fmt: skip
    with db_module.session() as s:
        for key, (who, outlet, at, title, description) in TOLD.items():
            s.add(FeedCandidate(player_id=ids[who], source="publisher_rss", external_id=news.external_id(f"https://{key}"), url=f"https://example.com/{key}",
                                candidate={"title": title, "description": description, "channel": outlet, "channel_id": f"https://{outlet}/rss",
                                           "published": f"{at}:00+00:00"}, features={"outlets": 1}, verdict=said()))  # fmt: skip
    s = maker()
    try:
        rows = s.execute(select(FeedCandidate.id, FeedCandidate.player_id, FeedCandidate.url, FeedCandidate.candidate)).all()
        published = {r.id: datetime.fromisoformat(r.candidate["published"]) for r in rows}
        found = rank._stories(s, rows, published)  # pyright: ignore[reportPrivateUsage]
    finally:
        s.close()
    key = {r.id: r.url.removeprefix("https://example.com/") for r in rows}
    groups = {frozenset(key[g] for g in group) for _, group in found.values()}
    together = [{"bavuma 179, live", "bavuma 179", "bavuma 179, cricinfo", "bavuma 179, hindu"}, {"mandhana series", "mandhana series, cricinfo"},
                {"shami hope", "shami hope, cricinfo"}]  # fmt: skip
    assert groups == {*map(frozenset, together), *(frozenset([k]) for k in TOLD if not any(k in t for t in together))}
    outlets = {key[r]: n for r, (n, _) in found.items()}
    assert (outlets["bavuma 179, hindu"], outlets["shami hope"], outlets["bavuma day 2"], outlets["mandhana era"]) == (3, 2, 1, 1)


def test_removing_a_player_takes_their_stories_and_the_prune_keeps_only_the_evergreen_old_ones(world):
    _, feeds = world
    asha = player("Asha Verma", ["Riverside XI"])
    rao = player("Kiran Rao", ["Eastern Stars"])
    feeds[WICKET] = rss(("Asha Verma and Kiran Rao star", "https://wicket.example.com/p/1", "Both.", 1), ("Kiran Rao again", "https://wicket.example.com/p/2", "Her.", 1))
    feeds[GAZETTE] = rss(("Asha Verma's week", "https://gazette.example.org/p/3", "Her week.", 1))
    news.poll(NOW)
    with db_module.session() as s:
        s.execute(delete(Player).where(Player.id == rao))
    assert stories() == [(asha, "https://wicket.example.com/p/1", "Daily Wicket"), (asha, "https://gazette.example.org/p/3", "Cricket Gazette")]
    with db_module.session() as s:
        s.execute(update(FeedCandidate).values(fetched_at=NOW - timedelta(days=31)))
        s.execute(update(FeedCandidate).where(FeedCandidate.url == "https://gazette.example.org/p/3").values(review="pinned"))
    assert news.prune(NOW) == {"pruned": 1}
    assert stories() == [(asha, "https://gazette.example.org/p/3", "Cricket Gazette")]
