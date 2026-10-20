"""The registry and read models (docs/backend-design.html, section 6).

Milestone 1 fills player, event, event_state, player_line, news_item, follow and player_card
from the agent's report. player_identity, source_binding, team and membership exist for the
resolver and adapters that come next, so the schema does not have to change for them.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Player(Base):
    __tablename__ = "player"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Until the resolver exists, the registry is keyed by the normalized query ("virat-kohli").
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    sport: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # active | retired | unknown
    nationality: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(200))
    teams: Mapped[list] = mapped_column(JSONB, default=list)
    # Their own accounts, [{"platform": "x", "handle": "imVkohli"}]: the Feed's judge (curate.py) takes a share
    # from one of them as the player's own. Set with `python -m sports_follow.players socials`.
    socials: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    wikidata_qid: Mapped[str | None] = mapped_column(String(20))
    disambiguation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # The database cascades the delete (ON DELETE CASCADE); without this the ORM would try to null the card's key first.
    card: Mapped["PlayerCard | None"] = relationship(back_populates="player", uselist=False, cascade="all, delete-orphan", passive_deletes=True)


class PlayerAlias(Base):
    """Every normalized name a fan has typed for this player. "Kohli" and "Virat Kohli" both land here,
    so every fan shares one player and one card however they spelled the name."""

    __tablename__ = "player_alias"

    alias: Mapped[str] = mapped_column(String(120), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)


class PlayerIdentity(Base):
    __tablename__ = "player_identity"
    __table_args__ = (UniqueConstraint("system", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    system: Mapped[str] = mapped_column(String(60))  # espncricinfo, fide, lichess, transfermarkt…
    external_id: Mapped[str] = mapped_column(String(200))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_url: Mapped[str | None] = mapped_column(Text)


class Athlete(Base):
    """The player registry: every athlete in the covered sports, imported from Wikidata (registry.py).

    One row per person and sport, whether or not anyone follows them. Search answers from here first;
    `ids` holds the person's id in each source ("espn_cricket", "lichess_chess", "transfermarkt"…), so
    following one binds the live source by id. A followed athlete's player carries a "wikidata" identity.
    """

    __tablename__ = "athlete"
    __table_args__ = (UniqueConstraint("qid", "sport"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Wikidata id; "pandascore:<id>" for an esports pro, imported from PandaScore instead.
    qid: Mapped[str] = mapped_column(String(20), index=True)
    sport: Mapped[str] = mapped_column(String(20))  # football | cricket | basketball | tennis | chess | esports
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    # " virat kohli | king kohli" folded to plain ASCII words; trigram-indexed for search as you type.
    search_text: Mapped[str] = mapped_column(Text)
    birth_date: Mapped[datetime | None] = mapped_column(Date)
    # How much of birth_date Wikidata knows: 11 the day, 10 the month, 9 the year (stored as 1 January).
    # None for rows imported before it was read, and for esports pros.
    birth_precision: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(120))
    teams: Mapped[list] = mapped_column(JSONB, default=list)  # current teams, club first
    league: Mapped[str | None] = mapped_column(String(20))  # nba | wnba | atp | wta; an esports pro's game (valorant)
    title: Mapped[str | None] = mapped_column(String(10))  # chess: GM, IM, WGM…
    ids: Mapped[dict] = mapped_column(JSONB, default=dict)
    image: Mapped[str | None] = mapped_column(String(300))  # Wikimedia Commons file name
    sitelinks: Mapped[int] = mapped_column(Integer, default=0)  # Wikipedia editions: the popularity signal
    current: Mapped[bool] = mapped_column(Boolean, default=True)  # plays now, as far as Wikidata says
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AthleteOverride(Base):
    """What an operator decided about one registry athlete (players.py), which every import respects.

    keep: an athlete added by hand, or one pinned so an import never drops them; imports may refresh the
    row but never delete it. remove: taken out, with their page; an import never writes the row again,
    and search and following leave out their ids in the sources.
    """

    __tablename__ = "athlete_override"
    __table_args__ = (UniqueConstraint("qid", "sport"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    qid: Mapped[str] = mapped_column(String(20))  # the athlete's registry id; "player:<id>" for a page no registry row had
    sport: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(10))  # keep | remove
    name: Mapped[str] = mapped_column(String(200))  # who, as they were called then
    ids: Mapped[dict] = mapped_column(JSONB, default=dict)  # their ids in the sources: {"espn_cricket": "253802"}
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class AthleteUnmatch(Base):
    """An operator's word that a registry athlete isn't the Wikidata person roster_wikidata.py matched them
    to for their photo (players unmatch): no run matches them to that person again, or to anyone (person
    None). Apart from athlete_override, whose one decision per athlete is keep or remove: a kept athlete can
    be unmatched too, and removing an athlete leaves this, so restoring them doesn't bring the face back.
    """

    __tablename__ = "athlete_unmatch"
    __table_args__ = (UniqueConstraint("qid", "sport", "person", postgresql_nulls_not_distinct=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    qid: Mapped[str] = mapped_column(String(20))  # the athlete's registry id: "pandascore:20401"
    sport: Mapped[str] = mapped_column(String(20))
    person: Mapped[str | None] = mapped_column(String(20))  # the Wikidata person they aren't: "Q113450352"; None: anyone
    name: Mapped[str] = mapped_column(String(200))  # who, as they were called then
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Starter(Base):
    """A registry athlete offered to a new fan, who follows nobody yet, in their sport's starter picker
    (starters.py; the operator sets them with `players starter`). Removing the athlete takes the row too."""

    __tablename__ = "starter"
    __table_args__ = (UniqueConstraint("qid", "sport"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    qid: Mapped[str] = mapped_column(String(20))  # the athlete's registry id
    sport: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)  # the order within the sport, first shown first
    created_at: Mapped[datetime] = _now()


class Campaign(Base):
    """A campaign page at /<slug> (stann.in/gukesh): a big event's players, or whole teams, to follow in one tap,
    its games and a few lines of copy (campaigns.py; the operator sets them with `python -m
    sports_follow.campaigns`). A campaign is a row, never code: a new event is a new row."""

    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)  # its address: "gukesh" is stann.in/gukesh
    sport: Mapped[str] = mapped_column(String(20))  # the registry's sport key: its players', and its colour
    title: Mapped[str] = mapped_column(String(80))  # "Gukesh v Sindarov", in big type
    event: Mapped[str | None] = mapped_column(String(120))  # "World Chess Championship", above it
    lines: Mapped[list] = mapped_column(JSONB, default=list)  # a few lines of copy, in order
    players: Mapped[list] = mapped_column(JSONB, default=list)  # registry ids (Athlete.qid) of its sport, in order
    # Whole teams, each as its source lists it and its roster as last read: {"system", "team_id", "name",
    # "game", "players": [{"athlete_id", "name", "league"}], "read_at"}.
    teams: Mapped[list] = mapped_column(JSONB, default=list)
    games: Mapped[list] = mapped_column(JSONB, default=list)  # its schedule: {"start_utc", "label"}, in order
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class FanSource(Base):
    """Where a fan came from: the tags on the first link they opened (s=, utm_source, utm_medium,
    utm_campaign) and the page it led to, kept with their fan id (server.visit_source), and moved to their
    account when they sign in. What BHA-239's launch numbers count by."""

    __tablename__ = "fan_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), unique=True)
    tag: Mapped[str | None] = mapped_column(String(60))  # s=: "share", or a post's own
    utm_source: Mapped[str | None] = mapped_column(String(60))
    utm_medium: Mapped[str | None] = mapped_column(String(60))
    utm_campaign: Mapped[str | None] = mapped_column(String(60))
    landing: Mapped[str] = mapped_column(String(200))  # the page's path: "/gukesh"
    created_at: Mapped[datetime] = _now()


class Photo(Base):
    """A player's photo from Wikimedia Commons, by the registry's file name (Athlete.image): what Commons
    says about it, and its thumbnail once its licence lets fans see it (photos.py)."""

    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(primary_key=True)
    file: Mapped[str] = mapped_column(String(300), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))  # whose photo it is, as the Credits page names them
    status: Mapped[str] = mapped_column(String(10))  # ok | unfree | missing | failed
    reason: Mapped[str | None] = mapped_column(Text)  # why it isn't shown: "licence not kept: GFDL"
    licence: Mapped[str | None] = mapped_column(String(100))  # "CC BY-SA 4.0", "public domain"
    licence_url: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(200))
    page: Mapped[str | None] = mapped_column(Text)  # the file's page on Commons, where its credit links
    source_sha1: Mapped[str | None] = mapped_column(String(40))  # the upload's: a new one is a new photo
    digest: Mapped[str | None] = mapped_column(String(32), index=True)  # of the thumbnail: its address
    media_type: Mapped[str | None] = mapped_column(String(30))
    data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)  # the WIDTH-pixel thumbnail
    asked_width: Mapped[int | None] = mapped_column(Integer)  # the WIDTH it was asked at: another is fetched again
    # Its size in pixels once the job has looked for the face in it (faces.py; 0 if it didn't decode), and
    # the face's box, {x, y, w, h} as fractions of the width and height; None when it found none.
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    face: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Team(Base):
    """A team fans can follow (teams.py, BHA-77): one source's team, by that source's id for it, imported weekly
    from the source's list (Adapter.teams) for the sports whose athletes turn out for teams, or added by an
    operator (teams add). Removing one (teams remove) takes its follows, its games' links and its results with
    it, and leaves a TeamOverride, so no import writes it again."""

    __tablename__ = "team"
    __table_args__ = (UniqueConstraint("system", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sport: Mapped[str] = mapped_column(String(60))  # its Sport's key: "football", "valorant"
    competitions: Mapped[list] = mapped_column(JSONB, default=list)  # what it plays in: ["Premier League"]
    system: Mapped[str] = mapped_column(String(60))  # its source: "sportmonks_football"
    external_id: Mapped[str] = mapped_column(String(60))  # its id there (SourceTeam.team_id)
    short: Mapped[str] = mapped_column(String(20), default="")  # the source's code for it: "ARS"
    country: Mapped[str | None] = mapped_column(String(120))
    tier: Mapped[int] = mapped_column(Integer, default=0)  # how big the source makes it (SourceTeam.tier): search's tie-break
    # " arsenal | ars" folded to plain ASCII words, as an athlete's: trigram-indexed for search as you type.
    search_text: Mapped[str] = mapped_column(Text, default="")
    # What its page shows (teams.card): its games to come and its last results, from its side, and its squad.
    card: Mapped[dict] = mapped_column(JSONB, default=dict)
    health: Mapped[dict] = mapped_column(JSONB, default=dict)  # its refreshes: last_ok, failures, checked_at, error
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # its games last read
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the last import that listed it
    created_at: Mapped[datetime] = _now()


class TeamOverride(Base):
    """What an operator decided about one source's team (teams.py), which every import respects. keep: added by
    hand, or pinned, so an import never drops it. remove: taken out, with its follows; an import never writes it
    again, and search leaves it out."""

    __tablename__ = "team_override"
    __table_args__ = (UniqueConstraint("system", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(60))
    external_id: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(10))  # keep | remove
    name: Mapped[str] = mapped_column(String(200))  # which team, as it was called then
    sport: Mapped[str] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Membership(Base):
    __tablename__ = "membership"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    from_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceBinding(Base):
    __tablename__ = "source_binding"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # fixtures | live | stats | news
    adapter: Mapped[str] = mapped_column(String(60))  # "agent" until real adapters exist
    locator: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    health: Mapped[dict] = mapped_column(JSONB, default=dict)  # last_ok, failures, checked_at
    history: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # older results read so far (history.py)
    created_at: Mapped[datetime] = _now()


class Event(Base):
    """The unit of polling: one game, shared by every player in it."""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Dedupe key: sport + normalized title + date. Real adapters will use the source's own id.
    key: Mapped[str] = mapped_column(String(64), unique=True)
    sport: Mapped[str] = mapped_column(String(60))
    competition: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300))
    # Its sides' ids in its source, as the last fixture read named them, a player's refresh or a team's (ESPN
    # basketball's team ids carry the league): event_team, not this, says which followed teams play in it.
    participants: Mapped[list] = mapped_column(JSONB, default=list)
    start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)
    # scheduled | armed | live | final | postponed
    live_binding: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EventPlayer(Base):
    """Which followed players are in an event, so arming an event reaches all of them."""

    __tablename__ = "event_player"

    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), primary_key=True, index=True)
    # Once the event is over: the result from this player's side ({title, result, date,
    # player_contribution, competition, source_url, scorecard}), kept for their results history.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class EventTeam(Base):
    """Which followed teams play in an event (teams.refresh), as event_player is for players: its polls reach
    their fans as a followed game's do (matches.py), and its result from each one's side is kept for the team's
    page."""

    __tablename__ = "event_team"

    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), primary_key=True, index=True)
    # Once the event is over: the result from this team's side ({title, result, date, start_utc, competition,
    # event_id, match}), for its last results.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class EventState(Base):
    """Append-only. The latest row is the live score; older rows are the timeline."""

    __tablename__ = "event_state"
    __table_args__ = (Index("ix_event_state_event_asof", "event_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    score_label: Mapped[str | None] = mapped_column(String(200))
    clock_label: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_url: Mapped[str | None] = mapped_column(Text)
    snapshot_ref: Mapped[str | None] = mapped_column(Text)


class PlayerLine(Base):
    """The player's own numbers in one event, at one time."""

    __tablename__ = "player_line"
    __table_args__ = (Index("ix_player_line_player_event", "player_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)


class NewsItem(Base):
    __tablename__ = "news_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_hash: Mapped[str] = mapped_column(String(64), unique=True)
    headline: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(Text)
    published: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str | None] = mapped_column(String(30))  # milestone | injury | transfer | interview | report
    created_at: Mapped[datetime] = _now()


class NewsPlayer(Base):
    """One item can belong to several players."""

    __tablename__ = "news_player"

    news_id: Mapped[int] = mapped_column(ForeignKey("news_item.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), primary_key=True)


class FeedCandidate(Base):
    """Something shared about a followed player that the feed may show (a video that names them), as the curator
    fetched it (candidates.py): one row per player and item, which goes with the player. The later stages fill
    their own columns: the judge's verdict, then a score and the moment it's about (rank.py)."""

    __tablename__ = "feed_candidates"
    __table_args__ = (
        UniqueConstraint("player_id", "external_id"),
        Index("ix_feed_candidates_source_external", "source", "external_id"),
        Index("ix_feed_candidates_unjudged", "fetched_at", postgresql_where=text("verdict IS NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(40))  # the video source's system (VideoSource.system)
    external_id: Mapped[str] = mapped_column(String(64))  # the source's id for it
    url: Mapped[str] = mapped_column(Text)
    # What was fetched, as the source gave it: title, description, channel, published, thumbnail, and which of
    # the player's names it was kept for.
    candidate: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Numbers for ranking: views, likes, seconds, and reads of views and likes with when: the first and the latest two.
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    fetched_at: Mapped[datetime] = _now()
    checked_at: Mapped[datetime] = _now()  # when the source last said it's still there and still plays here
    verdict: Mapped[dict | None] = mapped_column(JSONB)  # the judge's (L1), once it has read it
    # The ranking's number (rank.py), from the verdict, the features and the channel's trust; NULL: never served.
    score: Mapped[float | None] = mapped_column(Float)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The player's moment it's about (published within two days after it), so the Feed groups it there.
    moment_id: Mapped[int | None] = mapped_column(ForeignKey("moment.id", ondelete="SET NULL"))
    # An admin's word on it (rank.REVIEWS: approved, hidden, pinned), which stands over the judge's.
    review: Mapped[str | None] = mapped_column(String(10))


class FeedSearch(Base):
    """A search for fans' videos of a followed player's big moment (searches.py): one per player and game, which a
    later moment of theirs in the game takes over until its last run (a brace over the goal before it). It runs a
    few hours after the moment, and again a day after; it goes with the player, the game and the moment."""

    __tablename__ = "feed_search"
    __table_args__ = (UniqueConstraint("player_id", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"))
    moment_id: Mapped[int] = mapped_column(ForeignKey("moment.id", ondelete="CASCADE"), index=True)  # the latest it's for
    query: Mapped[str] = mapped_column(String(300))  # the player's name and the sport's words for the moment
    priority: Mapped[int] = mapped_column(Integer)  # its moments' highest (moments.PRIORITY): the biggest run first
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # its first moment's: videos published since
    first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when its first run ran
    second_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # and its second, the last
    kept: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # candidates its runs kept


class Sidelined(Base):
    """A spell a player is out of their team's games, injured or suspended, as a source listed it at its
    reads (sidelined.py), whoever follows them: by the source's ids, for the spell and the player, never a
    player row, so a fan's player is found by the identity they're bound by (PlayerIdentity). When a read
    found it news that they're out, and that they're back, are the Wire's facts (week.py). A row no read has
    listed for a fortnight goes (retention.py)."""

    __tablename__ = "sidelined"
    __table_args__ = (UniqueConstraint("system", "source_id"), Index("ix_sidelined_athlete", "system", "athlete_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(60))  # the source: "sportmonks_football"
    source_id: Mapped[str] = mapped_column(String(60))  # its id for the spell
    athlete_id: Mapped[str] = mapped_column(String(60))  # its id for the player (PlayerIdentity.external_id)
    team_id: Mapped[str] = mapped_column(String(60))  # and for their team, whose list it's on
    sport: Mapped[str] = mapped_column(String(20))  # football
    name: Mapped[str] = mapped_column(String(200))  # the player, as the source names them
    team: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20))  # injury | suspension | other (Absence.kind)
    what: Mapped[str] = mapped_column(String(200), default="")  # "Back Injury", as the source says it
    since: Mapped[date | None] = mapped_column(Date)
    until: Mapped[date | None] = mapped_column(Date)  # when they're expected back, if the source says
    fame: Mapped[int] = mapped_column(Integer, default=0)  # the registry's Wikipedia editions for them, at the last read
    out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # told they're out: a read found it new
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # found over: off the list, or past its end
    back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # told they're back
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the last read that listed it
    created_at: Mapped[datetime] = _now()


class Follow(Base):
    """The only fan-specific row in the pipeline."""

    __tablename__ = "follow"
    __table_args__ = (UniqueConstraint("fan_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    alert_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()


class TeamFollow(Base):
    """A team a fan follows (teams.py, BHA-77): each of its games is followed as a game followed by itself is
    (matches.py), with its moments from both sides by the fan's level, and its result told from the team's side.
    A game of it they mute stays muted; unfollowing the team stops all of them."""

    __tablename__ = "team_follow"
    __table_args__ = (UniqueConstraint("fan_id", "team_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"), index=True)
    alert_rules: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"level": ...}, as a player follow's
    created_at: Mapped[datetime] = _now()


class MatchFollow(Base):
    """A game a fan follows by itself (matches.py: the match page's Follow), whoever plays in it: its key
    moments from both sides reach them, by their level, until it ends. Then the follow lapses on its own
    (until): what the game says after that isn't theirs. Kept a month after, for the activity list."""

    __tablename__ = "match_follow"
    __table_args__ = (UniqueConstraint("fan_id", "event_id"), Index("ix_match_follow_keys", "keys", postgresql_using="gin"))

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), index=True)
    alert_rules: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"level": ...}, as a player follow's
    # Every address the game has here, as event keys: a chess board is a fixture of each of its players, so a
    # follow made at one is found at the other, and moves to the event of a player the fan follows (matches.settle).
    keys: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _now()
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when it lapsed: the game ended (or never came)


class PlayerCard(Base):
    """The read model: what the app renders. Mirrored to Redis."""

    __tablename__ = "player_card"

    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="building")  # building | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    card: Mapped[dict] = mapped_column(JSONB, default=dict)
    freshness: Mapped[dict] = mapped_column(JSONB, default=dict)  # live, fixtures, stats, news: ISO timestamps
    is_live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    live_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    player: Mapped[Player] = relationship(back_populates="card")


class Moment(Base):
    """Something worth telling a player's fans (moments.py): a goal, a fifty, a set, a result, an
    injury. The key makes each one happen once, however many polls see it. A game's own moment, for the
    fans following the game itself (matches.py, Sport.game_moments), has no player: it says whom it's about."""

    __tablename__ = "moment"
    __table_args__ = (Index("ix_moment_unsent", "created_at", postgresql_where=text("sent_at IS NULL")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)  # None: the game's own
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(30))  # start | lineup | final | goal | assist | red | on | fifty | hundred | correction | ...
    level: Mapped[str] = mapped_column(String(10))  # key | minor
    priority: Mapped[int] = mapped_column(Integer, default=50, server_default="50")  # 0-100, moments.PRIORITY
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(300), default="/")
    created_at: Mapped[datetime] = _now()
    # When a sender claimed it (notify.send). Unclaimed moments are swept up by the tick.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The moment a correction takes back (a goal VAR rules out): it goes only to the fans who were sent that one.
    corrects: Mapped[int | None] = mapped_column(ForeignKey("moment.id", ondelete="CASCADE"), index=True)
    # A game's own moment: the players it's about, by their ids in its source (a goal's scorer), or ["*"], the
    # whole game (the result). A fan who follows one of them hears it once, as their player's (alerts.plan).
    about: Mapped[list | None] = mapped_column(JSONB)


class Notification(Base):
    """One push to one fan (alerts.py plans it, notify.py sends it to each of their devices): one or
    more moments of one game (their players', or a game they follow), or one player's news. The log the noise rules read (how often this fan's
    phone buzzed, the game's story so far) and the open rate is measured from."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_fan_created", "fan_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id", ondelete="SET NULL"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"))  # the lead moment's; None: the game's own
    moment_ids: Mapped[list] = mapped_column(JSONB, default=list)
    kind: Mapped[str] = mapped_column(String(30))  # the lead moment's
    lines: Mapped[list] = mapped_column(JSONB, default=list)  # every moment's title, lead first
    loud: Mapped[bool] = mapped_column(Boolean)  # with sound (before each device's quiet hours)
    devices: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _now()
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationMute(Base):
    """A game a fan muted from a notification ("Mute this game"): nothing more is pushed from it."""

    __tablename__ = "notification_mute"

    fan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = _now()


class FeedFeedback(Base):
    """What a fan said about one item on a player's Feed (feed.py): not interested, not about the player, or a
    report. The item leaves that fan's feed at once; the judge learns from all of them later."""

    __tablename__ = "feed_feedback"

    fan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), primary_key=True, index=True)
    item_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # feed.item_id: of the item's address
    verdict: Mapped[str] = mapped_column(String(20))  # feed.VERDICTS
    created_at: Mapped[datetime] = _now()


class PushSubscription(Base):
    """One device a fan turned notifications on for, with its quiet hours: a browser or installed web app
    (Web Push), or the Android app (FCM, fcm.py)."""

    __tablename__ = "push_subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    transport: Mapped[str] = mapped_column(String(16), default="webpush", server_default="webpush")  # "webpush" or "fcm"
    # Where its pushes go: a Web Push endpoint (a push service's URL), or the Android app's FCM token.
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str | None] = mapped_column(String(200))  # Web Push's keys; an FCM token has none
    auth: Mapped[str | None] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    quiet_start: Mapped[str | None] = mapped_column(String(5))  # "22:00", in the device's timezone
    quiet_end: Mapped[str | None] = mapped_column(String(5))  # "07:00"
    created_at: Mapped[datetime] = _now()
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    # The Android app's live cards (livecards.py), as the phone says (server.register_fcm, stop_live_card): whether
    # it shows them (Alerts' Live scores), and the games whose card it stopped, newest last: none goes to it.
    live_cards: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    live_stopped: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The signed-in session that turned it on: signing that device out removes it (accounts.py).
    session_id: Mapped[int | None] = mapped_column(ForeignKey("account_session.id", ondelete="CASCADE"), index=True)


class Account(Base):
    """A fan who signed in (accounts.py). Their follows, levels, mutes and devices are kept under
    `fan_id`, as an anonymous fan's are under their cookie's, so every route works the same for both."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), unique=True)  # "acct-…", never in a cookie
    email: Mapped[str] = mapped_column(String(254), unique=True)  # lowercased
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = _now()
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When an admin blocked it (admin.py): it can't sign in, and its sessions ended then.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountSession(Base):
    """One signed-in device. Its cookie holds a random token; only the token's hash is kept here."""

    __tablename__ = "account_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    last_seen_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Control(Base):
    """One thing the admin panel changed on the running app (controls.py): the sports and sources turned
    off, a source's allowance. Every process reads them, so a change needs no restart."""

    __tablename__ = "control"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)  # "sports_off", "sources_off", "budget:sportmonks"
    value: Mapped[dict | list] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _now()
