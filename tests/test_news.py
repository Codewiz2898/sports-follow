"""The feed's news from publishers' RSS (news.py, adapters/publisher_rss.py, BHA-325): a feed read as its stories,
asked again conditionally through the source cache; stories linked to the followed players they name, a common
surname only with the player's side; the same story from several outlets one group; and what each sport reads.
Every feed, publisher, story and player here is made up."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sports_follow import controls, news, rank, sports
from sports_follow.adapters import NEWS, AdapterError, Article, budget, publisher_rss
from sports_follow.candidates import Followed

FEED = "https://feeds.example.com/cricket.xml"
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Daily Wicket | Cricket</title>
<item>
  <title>Asha Verma&#8217;s hundred sinks Riverside</title>
  <link>http://Daily.Example.com/story/123/?utm_source=rss&amp;at_medium=RSS&amp;page=2#comments</link>
  <pubDate>Tue, 20 Oct 2026 16:13:27 BST</pubDate>
  <description><![CDATA[<p>The captain made <b>112</b> off 98 balls &amp; carried her side home.</p>]]></description>
  <content:encoded><![CDATA[<p>The whole story, which is never read.</p>]]></content:encoded>
</item>
<item>
  <title>A story with a guid for a link</title>
  <guid isPermaLink="true">https://daily.example.com/story/124/</guid>
  <dc:date>2026-10-20T10:00:00+05:30</dc:date>
  <description>Short.</description>
</item>
<item><title>No link, left out</title><description>Nothing to open.</description></item>
<item><title>A guid that isn't a link</title><guid isPermaLink="false">abc-125</guid></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Board Notes</title>
<entry><title type="html">Kiran Rao &lt;i&gt;wins&lt;/i&gt; the open</title>
  <link rel="alternate" href="https://notes.example.org/rao-wins"/><link rel="self" href="https://notes.example.org/feed/1"/>
  <published>2026-10-19T08:30:00Z</published><summary>Rao beat the top seed in the final round.</summary></entry>
</feed>"""

RDF = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Old Style</title></channel>
<item><title>An old-style story</title><link>https://old.example.net/a</link><dc:date>2026-10-18</dc:date><description>Old.</description></item>
</rdf:RDF>"""


# ---------------------------------------------------------------- reading a feed


def test_a_feed_is_read_as_its_stories_headline_link_time_and_a_short_description():
    stories = publisher_rss.parse(RSS, FEED, "Daily Wicket")
    assert [s.title for s in stories] == ["Asha Verma’s hundred sinks Riverside", "A story with a guid for a link"]
    first = stories[0]
    assert first.url == "https://daily.example.com/story/123?page=2"  # tracking, fragment and the last slash dropped
    assert first.published == datetime(2026, 10, 20, 15, 13, 27, tzinfo=timezone.utc)  # BST is an hour ahead
    assert first.description == "The captain made 112 off 98 balls & carried her side home."  # plain text, never content:encoded
    assert (first.outlet, first.feed) == ("Daily Wicket", FEED)
    assert stories[1].url == "https://daily.example.com/story/124" and stories[1].published == datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc)


def test_atom_and_rss_1_are_read_alike():
    (rao,) = publisher_rss.parse(ATOM, "https://notes.example.org/feed", "Board Notes")
    assert (rao.title, rao.url, rao.description) == ("Kiran Rao wins the open", "https://notes.example.org/rao-wins", "Rao beat the top seed in the final round.")
    assert rao.published == datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc)
    (old,) = publisher_rss.parse(RDF, "https://old.example.net/rss", "Old Style")
    assert (old.url, old.published) == ("https://old.example.net/a", datetime(2026, 10, 18, tzinfo=timezone.utc))


def test_a_page_that_isnt_a_feed_is_an_error_not_an_empty_feed():
    with pytest.raises(AdapterError, match="isn't RSS or Atom"):
        publisher_rss.parse("<html><body>Subscribe!</body></html>", FEED, "Daily Wicket")
    with pytest.raises(AdapterError, match="isn't readable"):
        publisher_rss.parse("not xml at all", FEED, "Daily Wicket")


def test_a_long_description_is_cut_at_a_word():
    words = publisher_rss.plain("<p>" + "word " * 200 + "</p>", publisher_rss.DESCRIPTION_MAX)
    assert len(words) <= publisher_rss.DESCRIPTION_MAX + 1 and words.endswith("word…")


@pytest.mark.parametrize("value, expected", [
    ("Tue, 20 Oct 2026 16:13:27 GMT", datetime(2026, 10, 20, 16, 13, 27, tzinfo=timezone.utc)),
    ("Tue, 20 Oct 2026 18:04:02 IST", datetime(2026, 10, 20, 12, 34, 2, tzinfo=timezone.utc)),
    ("Mon, 19 Oct 2026 18:35:00 -0700", datetime(2026, 10, 20, 1, 35, tzinfo=timezone.utc)),
    ("2026-10-20T22:40:29+05:30", datetime(2026, 10, 20, 17, 10, 29, tzinfo=timezone.utc)),
    ("whenever", None),
    ("", None),
])  # fmt: skip
def test_a_feeds_dates_are_read_in_utc(value, expected):
    assert publisher_rss.when(value) == expected


def test_a_stories_address_is_written_one_way():
    assert publisher_rss.canonical("http://WWW.Example.com/a/b/?b=2&utm_campaign=x&a=1&ex_cid=OTC-RSS#top") == "https://www.example.com/a/b?a=1&b=2"
    assert publisher_rss.canonical("https://example.com/") == "https://example.com/"


class Publisher:
    """A made-up publisher's feed server, which answers 304 to a request that has its ETag."""

    def __init__(self):
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text=RSS, headers={"etag": '"v1"', "last-modified": "Tue, 20 Oct 2026 16:00:00 GMT"})


def test_a_feed_is_read_through_the_cache_and_asked_again_conditionally(monkeypatch):
    publisher = Publisher()
    monkeypatch.setattr(publisher_rss, "_client", httpx.Client(transport=httpx.MockTransport(publisher)))
    source = NEWS["publisher_rss"]
    first = source.articles(FEED, "Daily Wicket")
    assert len(first) == 2 and len(source.articles(FEED, "Daily Wicket")) == 2
    assert len(publisher.asked) == 1  # the second read is the cache's: a feed is read at most every FEED_TTL
    monkeypatch.setattr(publisher_rss, "FEED_TTL", 1e-6)  # past its time: asked again, with what it said last
    with budget.in_lane("feed"):
        again = source.articles(FEED, "Daily Wicket")
    assert [a.url for a in again] == [a.url for a in first] and len(publisher.asked) == 2
    asked = publisher.asked[-1].headers
    assert asked["if-none-match"] == '"v1"' and asked["if-modified-since"] == "Tue, 20 Oct 2026 16:00:00 GMT"


def test_a_feed_that_doesnt_answer_is_an_error_and_an_address_of_another_shape_is_never_asked(monkeypatch):
    monkeypatch.setattr(publisher_rss, "_client", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(AdapterError, match="HTTP 503"):
        NEWS["publisher_rss"].articles(FEED)
    with pytest.raises(AdapterError, match="not a feed address"):
        NEWS["publisher_rss"].articles("http://insecure.example.com/rss")


# ---------------------------------------------------------------- what each sport reads


def test_each_sport_lists_real_looking_feeds_and_a_sport_turned_off_has_none_read():
    source = NEWS["publisher_rss"]
    for sport in sports.ALL:
        for url, outlet in sport.news_feeds.items():
            assert source.feed_pattern.fullmatch(url) and outlet.strip(), (sport.key, url)
            assert "news.google." not in url, (sport.key, url)  # never a search engine's feed of other sites
    assert {s.key for s in sports.ALL if s.news_feeds} >= {"cricket", "football", "chess", "valorant"}
    listed = news.feeds(source)
    cricket = next(url for url, f in listed.items() if "cricket" in f.sports)
    assert listed[cricket].counted == "cricket"
    esports = next(f for f in listed.values() if "valorant" in f.sports)
    assert esports.counted == "esports" and "esports" in esports.sports  # every game's, and a pro whose game isn't known
    controls.turn_sport("cricket", False)
    assert not any("cricket" in f.sports for f in news.feeds(source).values())


# ---------------------------------------------------------------- linking stories to players


def story(title: str, description: str = "") -> Article:
    return Article(url=f"https://daily.example.com/{abs(hash(title))}", title=title, description=description, outlet="Daily Wicket", feed=FEED, published=NOW)


def players() -> list[Followed]:
    return [
        Followed(1, "cricket", "Asha Verma", aliases=["ashu"], teams=["Riverside XI", "India"]),
        Followed(2, "cricket", "Rohit Sharma", aliases=["hitman"], teams=["Mumbai Indians", "India"]),
        Followed(3, "cricket", "Abhishek Sharma", teams=["Sunrisers Hyderabad", "India"]),
        Followed(4, "cricket", "Dev Smith", teams=["Eastern Stars", "Australia"]),
    ]


def linked(*stories: Article, usual: set[tuple[str, str]] = frozenset()) -> list[tuple[int, str, str]]:  # type: ignore[assignment]
    group = players()
    return sorted((f.player_id, f.name, f.where) for f in news.link(stories, group, news.names(group), set(usual)))


def test_a_full_name_or_a_surname_no_one_else_has_names_a_player():
    assert linked(story("Asha Verma's hundred sinks the hosts")) == [(1, "asha verma", "title")]
    assert linked(story("Verma stars again", "A captain's knock.")) == [(1, "verma", "title")]  # her surname, hers alone
    assert linked(story("Captain's knock", "Asha Verma carried her side.")) == [(1, "asha verma", "description")]
    assert linked(story("#AshaVerma trends after the final")) == [(1, "ashaverma", "title")]


def test_a_surname_two_followed_players_share_names_the_one_whose_side_the_story_names():
    assert linked(story("Sharma ruled out of the final")) == []  # which Sharma?
    assert linked(story("Sharma ruled out as Mumbai Indians lose")) == [(2, "sharma", "title")]
    assert linked(story("Sharma ruled out", "India sweat on fitness")) == []  # both play for India: still a tie
    # A full name settles it, and its surname names nobody else.
    assert linked(story("Rohit Sharma ruled out of the final")) == [(2, "rohit sharma", "title")]


def test_a_common_surname_or_a_nickname_names_a_player_only_with_their_side():
    assert linked(story("Smith hits back")) == [(4, "smith", "title")]  # uncommon in the registry
    assert linked(story("Smith hits back"), usual={("cricket", "smith")}) == []  # a common surname, alone
    assert linked(story("Smith hits back for Eastern Stars"), usual={("cricket", "smith")}) == [(4, "smith", "title")]
    assert linked(story("Who is the hitman this season?")) == []  # a fan's nickname for him, alone
    assert linked(story("Hitman leads Mumbai Indians to the top")) == [(2, "hitman", "title")]


# ---------------------------------------------------------------- the same story


def s(sid: int, hours: float, title: str, description: str = "", name: str = "Asha Verma") -> news.Story:
    return news.Story(sid, NOW - timedelta(hours=hours), frozenset(news.story_words(title, description, [name])))


def test_the_same_story_within_a_day_is_one_group_and_another_story_is_not():
    groups = news.clusters([
        s(1, 10, "Verma's unbeaten century powers Riverside to victory", "Asha Verma struck 112 not out against Eastern Stars"),
        s(2, 9, "Riverside win as Verma hits century against Eastern Stars", "An unbeaten knock from the captain"),
        s(3, 8, "Verma signs bat sponsorship deal", "The captain joins a new equipment brand"),
        s(4, 40, "Unbeaten century from Verma powers Riverside past Eastern Stars", "Another victory"),  # two days earlier
    ])
    assert groups == [[4], [1, 2], [3]]


def test_a_story_is_scored_by_the_judges_interest_and_how_many_outlets_ran_it():
    verdict = {"about_player": "subject", "interest": 0.5}
    one, three, five, ten = (rank.story_score(verdict, n, 1.0) for n in (1, 3, 5, 10))
    assert 0 < one < three < five == ten <= 1.0
    assert rank.story_score({**verdict, "about_player": "mention"}, 3, 1.0) < three
    assert rank.tag({"kind": "match_moment", "by": "outlet"}, story=True) == "Match report" and rank.tag({"kind": "other"}, story=True) == "News"


def test_a_surname_after_another_capitalised_name_is_someone_elses():
    assert linked(story("Deepti Sharma tops the rankings", "India's women climb too")) == []  # not Rohit's, though India is his side
    assert linked(story("Deepti Sharma and Rohit Sharma top the rankings")) == [(2, "rohit sharma", "title")]
    assert linked(story("Captain Verma Leads From The Front")) == [(1, "verma", "title")]  # a title-case headline's words
    assert linked(story("Priya Verma joins Riverside XI")) == []  # someone else, at her own club


def test_a_word_that_isnt_a_name_or_is_part_of_another_name_names_nobody():
    group = [
        Followed(11, "cricket", "Travis Head", teams=["Australia"]), Followed(12, "cricket", "Joe Root", teams=["England"]),
        Followed(13, "cricket", "KL Rahul", teams=["India"]), Followed(14, "cricket", "Reece James", teams=["England"]),
        Followed(15, "valorant", "Less", teams=["LOUD"]), Followed(16, "bgmi", "Mortal", teams=["Soul"]), Followed(17, "cs2", "amster", teams=["6666"]),
    ]

    def hits(title: str) -> list[int]:
        return sorted(f.player_id for f in news.link([story(title)], group, news.names(group), set()))

    for title in ("Gambhir named head coach until 2027 World Cup", "The head coach speaks after defeat", "head-to-head record ahead of the final",
                  "England get to the root of their problems", "Rahul Dravid returns to Rajasthan Royals", "James Anderson retires",
                  "Riot says patch makes Vandal less accurate", "Mortal Kombat 1 esports returns at Evo"):  # fmt: skip
        assert hits(title) == [], title
    assert hits("Head and Root star as Australia and England draw") == [11, 12]
    assert hits("Less joins LOUD for another year") == [15] and hits("amster signs with 6666") == [17]
    assert hits("Rahul Hits A Century In Perth") == [13]  # title case: only the word before counts
    assert hits("Head Coach Speaks After Defeat") == [11]  # so title case can't tell this one: the judge does


def test_a_feed_that_names_its_encoding_only_in_its_xml_declaration_keeps_its_accents():
    body = '<?xml version="1.0" encoding="ISO-8859-1"?><rss><channel><item><title>Müller scores</title><link>https://x.example.com/1</link></item></channel></rss>'.encode("latin-1")
    assert publisher_rss.decode(body, None).count("Müller") == 1
    assert publisher_rss.decode("Müller".encode(), "utf-8") == "Müller"


def test_punctuation_between_words_keeps_them_apart():
    group = [Followed(21, "cricket", "Mitchell Starc", teams=["Australia"]), Followed(22, "cricket", "Jasprit Bumrah", teams=["India"])]

    def hits(title: str, description: str = "") -> list[int]:
        return sorted(f.player_id for f in news.link([story(title, description)], group, news.names(group), set()))

    assert hits("Ashes: Starc takes five in Perth") == [21] and hits("WATCH: Bumrah yorker") == [22]
    assert hits("Fitness news", "India collapsed in Perth. Bumrah then took four.") == [22]
    assert hits("Cummins, Starc and Hazlewood fit for Brisbane") == [21] and hits("Ravindra Jadeja, Bumrah star") == [22]
