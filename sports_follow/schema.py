"""Structured shapes the agent must return.

These Pydantic models are the single source of truth: their JSON schema is sent
to Claude as the input schema of the `submit_*` tools, and the tool input is
validated back into them before it reaches the web UI.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    # additionalProperties: false on every object, as strict tool schemas require.
    model_config = ConfigDict(extra="forbid")


class Stat(_Strict):
    label: str = Field(description='Short label, e.g. "Goals", "Classical rating", "ODI runs", "Aces"')
    value: str = Field(description='A short value, at most ~12 characters: "15,080", "2835", "54*", "7-6(4)". Put context in the label, not here.')


class PlayerProfile(_Strict):
    name: str  # common name, e.g. "Cristiano Ronaldo"
    sport: str  # e.g. "Football", "Chess", "Tennis", "Cricket"
    status: Literal["active", "retired", "unknown"]
    nationality: str | None
    role: str | None = Field(description='Position or discipline in a few words: "Forward", "Top-order batter", "Grandmaster"')
    teams: list[str]  # current club + national team; empty for individual sports
    summary: str = Field(description="One or two short sentences on where the player is right now. Details belong in the other sections.")
    disambiguation: str | None = Field(
        description="Only when the query genuinely could mean another well-known athlete (e.g. 'Ronaldo'): who else it could be. Otherwise null."
    )


class NewsItem(_Strict):
    headline: str
    summary: str
    source: str
    url: str
    published: str | None  # ISO 8601 date or datetime when known


class UpcomingEvent(_Strict):
    title: str  # "Al-Nassr vs Al-Hilal", "Norway Chess – Round 6", "R32 vs J. Sinner"
    competition: str
    team: str | None  # which of the player's teams is involved
    start_utc: str | None  # ISO 8601 UTC, e.g. "2026-10-04T18:00:00Z"
    venue: str | None
    notes: str | None  # e.g. "Player doubtful – hamstring"


class LiveStatus(_Strict):
    is_live: bool
    event: str | None  # what is being played right now
    score: str | None  # "Al-Nassr 2 – 1 Al-Hilal", "IND 287/4 (48.2)", "6-4 3-2", "½–½ after 34 moves"
    clock: str | None  # "72'", "2nd set", "Day 2, Session 3", "Move 34"
    player_stats: list[Stat]  # the player's numbers in this game
    source_url: str | None
    as_of: str | None  # when the source was last updated, ISO 8601 if known


class RecentResult(_Strict):
    title: str
    result: str  # "Won 3–1", "Draw", "Lost 4-6 6-7"
    date: str | None
    player_contribution: str | None  # "1 goal, 1 assist", "82 (64)", "Beat Nakamura in 41 moves"


class PlayerReport(_Strict):
    player: PlayerProfile
    live: LiveStatus
    news: list[NewsItem]
    upcoming: list[UpcomingEvent]
    recent_results: list[RecentResult]
    season_stats: list[Stat]
    sources: list[str]


class LiveUpdate(_Strict):
    live: LiveStatus
    next_event: UpcomingEvent | None  # handy when nothing is live
