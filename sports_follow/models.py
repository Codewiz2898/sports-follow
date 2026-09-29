"""The registry and read models (docs/backend-design.html, section 6).

Milestone 1 fills player, event, event_state, player_line, news_item, follow and player_card
from the agent's report. player_identity, source_binding, team and membership exist for the
resolver and adapters that come next, so the schema does not have to change for them.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
    qid: Mapped[str] = mapped_column(String(20), index=True)
    sport: Mapped[str] = mapped_column(String(20))  # football | cricket | basketball | tennis | chess
    name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    # " virat kohli | king kohli" folded to plain ASCII words; trigram-indexed for search as you type.
    search_text: Mapped[str] = mapped_column(Text)
    birth_date: Mapped[datetime | None] = mapped_column(Date)
    country: Mapped[str | None] = mapped_column(String(120))
    teams: Mapped[list] = mapped_column(JSONB, default=list)  # current teams, club first
    league: Mapped[str | None] = mapped_column(String(20))  # nba | wnba | atp | wta
    title: Mapped[str | None] = mapped_column(String(10))  # chess: GM, IM, WGM…
    ids: Mapped[dict] = mapped_column(JSONB, default=dict)
    image: Mapped[str | None] = mapped_column(String(300))  # Wikimedia Commons file name
    sitelinks: Mapped[int] = mapped_column(Integer, default=0)  # Wikipedia editions: the popularity signal
    current: Mapped[bool] = mapped_column(Boolean, default=True)  # plays now, as far as Wikidata says
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sport: Mapped[str] = mapped_column(String(60))
    competitions: Mapped[list] = mapped_column(JSONB, default=list)


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


class Follow(Base):
    """The only fan-specific row in the pipeline."""

    __tablename__ = "follow"
    __table_args__ = (UniqueConstraint("fan_id", "player_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    alert_rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()


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
    injury. The key makes each one happen once, however many polls see it."""

    __tablename__ = "moment"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("event.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(30))  # start | final | goal | assist | red | on | fifty | hundred | ...
    level: Mapped[str] = mapped_column(String(10))  # key | minor
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(300), default="/")
    created_at: Mapped[datetime] = _now()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(Base):
    """One device a fan turned notifications on for (Web Push), with its quiet hours."""

    __tablename__ = "push_subscription"

    id: Mapped[int] = mapped_column(primary_key=True)
    fan_id: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    quiet_start: Mapped[str | None] = mapped_column(String(5))  # "22:00", in the device's timezone
    quiet_end: Mapped[str | None] = mapped_column(String(5))  # "07:00"
    created_at: Mapped[datetime] = _now()
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failures: Mapped[int] = mapped_column(Integer, default=0)
