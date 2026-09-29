"""The player-follow agent.

The model is reached through the local llm-providers gateway (app `sports-follow`), which holds
the provider keys. Claude researches the player with the local `web_search` / `fetch_url` tools
and finishes by calling a `submit_*` tool whose input is our schema. Every tool call is yielded
as progress so the UI can show what the agent is doing.
"""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from dotenv import dotenv_values
from llm_providers import Client, LlmError
from pydantic import BaseModel, ValidationError

from .schema import LiveUpdate, PlayerReport

log = logging.getLogger("sports_follow.agent")
from .tools import TOOL_DEFS, ToolError, describe, run_tool

# `:nitro` asks OpenRouter for its fastest hosts; the default routing picks by price and some hosts stall.
MODEL = os.environ.get("SPORTS_FOLLOW_MODEL", "openrouter:moonshotai/kimi-k2.6:nitro")
GATEWAY_URL = os.environ.get("LLM_GATEWAY_URL", "http://127.0.0.1:8787")
TOKEN_VAR = "SPORTS_FOLLOW_GATEWAY_TOKEN"
KEYS_FILE = Path(os.environ.get("LLM_PROVIDERS_KEYS_FILE", "~/.config/llm-providers/keys.env")).expanduser()
MAX_TURNS = 16
# The final call writes the whole report in one reply, so it gets more room than a research turn needs.
CALL_TIMEOUT_MS = 300_000
RETRYABLE = {"timeout", "server", "network", "rate_limit"}

SYSTEM_PROMPT = """\
You are a sports-follow agent. Given a player's name, you find out who they are and \
what is happening with them right now, using the web_search and fetch_url tools. You cover \
every sport: football, cricket, tennis, chess, basketball, F1, golf, athletics, and so on.

How to work:
- Identify the athlete first. If the name is ambiguous (e.g. "Ronaldo"), pick the most \
prominent currently newsworthy athlete and say who else it could mean in `disambiguation`.
- Use web_search kind='news' for the latest news. Search results are only leads: open \
(fetch_url) fixture and live pages before stating a score, date or stat.
- Public JSON APIs are often the most reliable live source and can be fetched directly, e.g. \
ESPN (https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard, \
.../summary?event={id}; leagues like soccer/eng.1, soccer/ksa.1, soccer/uefa.champions, \
basketball/nba, tennis/atp) and Lichess (https://lichess.org/api/broadcast/top). \
Cricbuzz and ESPNcricinfo pages work for cricket.
- Think about what "match", "score" and "stats" mean for the sport. Team sports: club and \
national-team fixtures. Tennis/golf/F1: the tournament, draw position, next round. Chess: \
the event, round pairings, the current game (result or move number), ratings and tournament \
score. Cricket: format, innings score, batting/bowling figures.
- Only mark a game as live if a source shows it in progress now; give the source URL and \
its timestamp. If the player is not playing right now, set is_live to false and leave the \
live fields empty. Never invent scores, fixtures, dates or quotes; omit what you could not verify.
- Retired players have no upcoming fixtures; say so in the summary and focus on news.
- Times go in ISO 8601 UTC (convert from local kick-off times).
- Be quick: the user is watching a spinner. Run independent searches and fetches in parallel \
in one turn, and aim to finish in about 5 rounds of tool calls. A good report with a few gaps \
beats a perfect one that takes minutes.

When you are done, call the submit tool exactly once with everything you found."""


class AgentError(RuntimeError):
    pass


def _token() -> str | None:
    """The app's gateway token: from the environment, else that one variable from the shared keys file."""
    token = os.environ.get(TOKEN_VAR)
    if not token and KEYS_FILE.is_file():
        token = dotenv_values(KEYS_FILE).get(TOKEN_VAR)
    return token or None


def gateway() -> Client:
    return Client(GATEWAY_URL, token=_token(), timeout=CALL_TIMEOUT_MS / 1000 + 20)


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits $defs/$ref; inline them and drop titles so any provider accepts the schema."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out: dict[str, Any] = {}
            for key, value in node.items():
                # Drop the "title" annotation, never a field called title (UpcomingEvent.title).
                if key == "$defs" or (key == "title" and isinstance(value, str)):
                    continue
                if key == "properties" and isinstance(value, dict):
                    out[key] = {name: walk(prop) for name, prop in value.items()}
                else:
                    out[key] = walk(value)
            # Ask for every field even though validation tolerates omissions: a model told a field is
            # optional tends to skip it (a report without stats is a worse page, not an error).
            if isinstance(out.get("properties"), dict):
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _submit_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": _inline_refs(model.model_json_schema())}


def _progress(message: str) -> dict[str, Any]:
    return {"type": "progress", "message": message}


def _run_tool_safely(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    try:
        return run_tool(name, args), False
    except ToolError as exc:
        return str(exc), True


T = TypeVar("T", bound=BaseModel)


def _run(
    llm: Client,
    prompt: str,
    *,
    submit_name: str,
    submit_description: str,
    result_model: type[T],
    effort: str,
    label: str,
    soft_turns: int,
) -> Iterator[dict[str, Any]]:
    """Agent loop. Yields progress events, then a final {"type": "result", "data": ...}."""
    tools = [*TOOL_DEFS, _submit_tool(submit_name, submit_description, result_model)]
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    with ThreadPoolExecutor(max_workers=6) as pool:
        for turn in range(MAX_TURNS):
            yield _progress("Thinking…" if turn else f"Asking {MODEL.split(':', 1)[-1]}…")
            for attempt in range(2):
                try:
                    r = llm.complete(
                        model=MODEL,
                        system=SYSTEM_PROMPT,
                        messages=messages,
                        tools=tools,
                        max_tokens=16000,  # OpenRouter reserves credit for the whole budget up front
                        thinking="adaptive",
                        effort=effort,
                        cache_system=True,
                        timeout_ms=CALL_TIMEOUT_MS,
                        label=label,
                    )
                    break
                except LlmError as exc:
                    # One stalled or failed host shouldn't throw away minutes of research: retry once.
                    if attempt == 0 and exc.kind in RETRYABLE:
                        yield _progress("The model was slow to answer, retrying…")
                        continue
                    raise AgentError(f"Model call failed ({exc.kind}): {exc}") from exc

            if r.finish == "refusal":
                raise AgentError("The model declined this request.")
            if r.finish == "length":
                raise AgentError("The model's reply was cut off (max tokens).")

            messages.append(r.message)

            if not r.tool_calls:
                messages.append({"role": "user", "content": f"Please call {submit_name} now with what you found."})
                continue

            submit = next((c for c in r.tool_calls if c.name == submit_name), None)
            if submit is not None:
                try:
                    result = result_model.model_validate(submit.args)
                except ValidationError as exc:
                    # Each resubmit costs a full report's worth of output, so leave a trail of why.
                    log.warning("%s: %s rejected, %d errors, first: %s", label, submit_name, exc.error_count(), exc.errors()[0])
                    yield _progress("Tidying the report…")
                    # Answer every call in the turn; only the submit carries the error.
                    for c in r.tool_calls:
                        content = f"Input did not match the schema, please resubmit: {exc}" if c is submit else "Skipped."
                        messages.append({"role": "tool", "tool_call_id": c.id, "content": content})
                    continue
                yield {"type": "result", "data": result.model_dump(mode="json")}
                return

            for c in r.tool_calls:
                yield _progress(describe(c.name, c.args))
            outputs = list(pool.map(lambda c: _run_tool_safely(c.name, c.args), r.tool_calls))
            for c, (content, failed) in zip(r.tool_calls, outputs):
                messages.append({"role": "tool", "tool_call_id": c.id, "content": f"Error: {content}" if failed else content})
            failures = sum(failed for _, failed in outputs)
            if failures:
                yield _progress(f"{failures} of {len(outputs)} lookups failed, working around it")
            if turn + 1 >= soft_turns:
                # Research budget spent: finish with what's known rather than keep the user waiting.
                messages.append({"role": "user", "content": f"Time is up. Call {submit_name} now with what you have."})

    raise AgentError("The agent did not finish within its turn limit.")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")


def follow_player(llm: Client, player: str, known: str | None = None) -> Iterator[dict[str, Any]]:
    """Full report: profile, live game, news, upcoming fixtures, recent results, stats.

    known pins the exact athlete when a live source already identified them ("Nikola Jokić,
    football, FK Jedinstvo Ub. Their page on ESPN: …"), so a famous namesake can't take over.
    """
    prompt = (
        f"Current time: {_now()}.\n\n"
        f"Player: {player}\n"
        + (f"Exactly this athlete: {known}\n" if known else "")
        + "\n"
        "Build the full report: who they are, whether they are playing right now (live score "
        "and their live stats), the latest news (up to 6 items from the last few weeks), "
        "upcoming matches/events for every team or tournament they're in (up to 6), recent "
        "results (up to 5) with their contribution, and key current-season stats."
    )
    yield from _run(
        llm,
        prompt,
        submit_name="submit_player_report",
        submit_description="Submit the finished player report. Call exactly once, at the end.",
        result_model=PlayerReport,
        effort=os.environ.get("SPORTS_FOLLOW_EFFORT", "medium"),
        label="player-report",
        soft_turns=6,
    )


def live_update(llm: Client, player: str, sport: str, teams: list[str]) -> Iterator[dict[str, Any]]:
    """Quick refresh of just the live game, used for auto-refresh in the UI."""
    team_text = ", ".join(teams) if teams else "none (individual sport)"
    prompt = (
        f"Current time: {_now()}.\n\n"
        f"Player: {player} ({sport}). Teams: {team_text}.\n\n"
        "Only check whether this player is playing right now. If so, get the current score, "
        "clock/state and the player's live stats from a live source. If not, give their next "
        "scheduled match/event. Be quick: two or three lookups at most."
    )
    yield from _run(
        llm,
        prompt,
        submit_name="submit_live_update",
        submit_description="Submit the live status. Call exactly once, at the end.",
        result_model=LiveUpdate,
        effort="low",
        label="live-update",
        soft_turns=2,
    )
