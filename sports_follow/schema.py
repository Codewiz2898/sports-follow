"""Structured shapes the agent must return.

These Pydantic models are the single source of truth: their JSON schema is sent
to the model as the input schema of the `submit_*` tools, and the tool input is
validated back into them before it reaches the app.
"""

from __future__ import annotations

from typing import Literal

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Lenient(BaseModel):
    # A rejected report costs a whole resubmission (a minute or more), so accept near-misses that lose
    # nothing: numbers where text is expected ("value": 523), omitted optional fields, and stray keys.
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)

    @model_validator(mode="before")
    @classmethod
    def _null_text_is_empty(cls, data: Any) -> Any:
        # Told every field is required, models fill unknown text with null ("result": null).
        if isinstance(data, dict):
            nulls = [name for name, field in cls.model_fields.items() if field.annotation is str and name in data and data[name] is None]
            if nulls:
                data = {**data, **{name: "" for name in nulls}}
        return data


def _title_from(data: Any, *parts: str) -> Any:
    """Models sometimes leave `title` out and put the match name in other fields (seen: notes="2nd ODI")."""
    if isinstance(data, dict) and not data.get("title"):
        title = " – ".join(str(data[p]) for p in parts if data.get(p))
        if title:
            return {**data, "title": title}
    return data


class Stat(_Lenient):
    label: str = Field(description='Short label, e.g. "Goals", "Classical rating", "ODI runs", "Aces"')
    value: str = Field(description='A short value, at most ~12 characters: "15,080", "2835", "54*", "7-6(4)". Put context in the label, not here.')


class PlayerProfile(_Lenient):
    name: str  # common name, e.g. "Cristiano Ronaldo"
    sport: str  # e.g. "Football", "Chess", "Tennis", "Cricket"
    status: Literal["active", "retired", "unknown"]
    nationality: str | None = None
    role: str | None = Field(default=None, description='Position or discipline in a few words: "Forward", "Top-order batter", "Grandmaster"')
    teams: list[str] = []  # current club + national team; empty for individual sports
    summary: str = Field(description="One or two short sentences on where the player is right now. Details belong in the other sections.")
    disambiguation: str | None = Field(
        default=None,
        description="Only when the query genuinely could mean another well-known athlete (e.g. 'Ronaldo'): who else it could be. Otherwise null."
    )


class NewsItem(_Lenient):
    headline: str
    summary: str
    source: str
    url: str
    published: str | None = None  # ISO 8601 date or datetime when known


class UpcomingEvent(_Lenient):
    _fill_title = model_validator(mode="before")(lambda data: _title_from(data, "competition", "notes"))

    title: str  # "Al-Nassr vs Al-Hilal", "Norway Chess – Round 6", "R32 vs J. Sinner"
    competition: str
    team: str | None = None  # which of the player's teams is involved
    start_utc: str | None = None  # ISO 8601 UTC, e.g. "2026-10-04T18:00:00Z"
    venue: str | None = None
    notes: str | None = None  # e.g. "Player doubtful – hamstring"


class LiveStatus(_Lenient):
    is_live: bool
    event: str | None = None  # what is being played right now
    score: str | None = None  # "Al-Nassr 2 – 1 Al-Hilal", "IND 287/4 (48.2)", "6-4 3-2", "½–½ after 34 moves"
    clock: str | None = None  # "72'", "2nd set", "Day 2, Session 3", "Move 34"
    player_stats: list[Stat] = []  # the player's numbers in this game
    source_url: str | None = None
    as_of: str | None = None  # when the source was last updated, ISO 8601 if known


class RecentResult(_Lenient):
    _fill_title = model_validator(mode="before")(lambda data: _title_from(data, "date", "result"))

    title: str
    result: str  # "Won 3–1", "Draw", "Lost 4-6 6-7"
    date: str | None = None
    player_contribution: str | None = None  # "1 goal, 1 assist", "82 (64)", "Beat Nakamura in 41 moves"


class PlayerReport(_Lenient):
    player: PlayerProfile
    live: LiveStatus
    news: list[NewsItem] = []
    upcoming: list[UpcomingEvent] = []
    recent_results: list[RecentResult] = []
    season_stats: list[Stat] = []
    sources: list[str] = []


class LiveUpdate(_Lenient):
    live: LiveStatus
    next_event: UpcomingEvent | None = None  # handy when nothing is live
