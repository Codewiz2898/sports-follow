"""Linear, where this project's work is tracked, as the admin panel shows it: the workspace's plan and how
many issues it holds against the Free plan's limit, past which Linear refuses new issues (as it did on
17 Oct 2026), so whoever runs the app sees it coming.

It's read with a personal API key of Linear's with the Read permission alone, in
SPORTS_FOLLOW_LINEAR_KEY_FILE (else ~/.config/sports-follow/linear-key); without one the panel says how to
add it. Each API process asks Linear at most once an hour, and after a failed read waits 5 minutes before
asking again, showing the last reading meanwhile: the page never waits on Linear but now and then, and
never breaks for it.

What Linear's API says (its schema: github.com/linear/linear, packages/sdk/src/schema.graphql):
- The plan is `organization.subscription.type`, the plan and its billing ("basic_monthly_12" on 17 Oct 2026; the
  schema says "basic, business, enterprise"), shown by its first word. A workspace with no paid subscription
  is on the Free plan.
- `Team.issueCount` counts a team's issues without the archived ones (`includeArchived: true` counts those
  too). The limit isn't in the API (its `quotas` "are not subscription limits"), so it's a setting here,
  SPORTS_FOLLOW_LINEAR_FREE_ISSUES. Linear's pricing page says "250 issues"; its older wording, "Issues
  (excluding Archive)", left archived ones out, so they're not counted against it, and are shown beside.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("sports_follow.linear")

API = "https://api.linear.app/graphql"
KEY_PATH = Path.home() / ".config" / "sports-follow" / "linear-key"
FREE_ISSUES = int(os.environ.get("SPORTS_FOLLOW_LINEAR_FREE_ISSUES", 250))
NEAR = 0.8  # from this share of the limit on, the panel warns
FRESH = 3600  # seconds a reading is shown before Linear is asked again
RETRY = 300  # seconds after a failed read before Linear is asked again
# A Free workspace has at most 2 teams, so one page of them is every team.
QUERY = """query AdminPlan {
  organization { name urlKey subscription { type } trialEndsAt }
  teams(first: 100) { nodes { key issueCount all: issueCount(includeArchived: true) } }
}"""

_client = httpx.Client(timeout=10)


class LinearError(Exception):
    """Linear didn't answer, refused the key, or answered something else: said on the panel as it is."""


@dataclass(frozen=True)
class Reading:
    workspace: str
    url: str
    plan: str | None  # "free", "basic", "business", "enterprise"; None: the key may not read it
    trial_ends_at: str | None
    issues: int  # without the archived ones
    archived: int
    at: float


def _key() -> str | None:
    path = Path(os.environ.get("SPORTS_FOLLOW_LINEAR_KEY_FILE") or KEY_PATH)
    try:
        return path.read_text().strip() or None
    except OSError:
        return None


ODD = "Linear's answer wasn't what this app expects"


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise LinearError(ODD)
    return int(value)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LinearError(ODD)
    return value


def read(key: str, now: float) -> Reading:
    """Ask Linear, once."""
    try:
        answer = _client.post(API, headers={"authorization": key}, json={"query": QUERY})
    except httpx.HTTPError as exc:
        raise LinearError(f"Linear didn't answer ({type(exc).__name__})") from exc
    try:
        body = answer.json()
    except ValueError:
        body = None
    data = body.get("data") if isinstance(body, dict) else None
    errors = [e for e in _list(body.get("errors")) if isinstance(e, dict)] if isinstance(body, dict) else []
    org = data.get("organization") if isinstance(data, dict) else None
    teams = data.get("teams") if isinstance(data, dict) else None
    if answer.status_code >= 300 or not isinstance(org, dict) or not isinstance(teams, dict):
        said = str(errors[0].get("message") or "")[:160] if errors else ""
        log.warning("linear: answered %s: %s", answer.status_code, said or answer.text[:200])
        raise LinearError(f"Linear answered {answer.status_code}{': ' + said if said else ''}")
    # A key that may read issues but not billing gets the counts with an error on the subscription.
    hidden = any(isinstance(e.get("path"), list) and e["path"][:2] == ["organization", "subscription"] for e in errors)
    subscription = org.get("subscription")
    if hidden:
        plan = None
    elif isinstance(subscription, dict):
        plan = str(subscription.get("type") or "").lower().split("_")[0] or None
    else:
        plan = "free"
    issues = everything = 0
    for team in _list(teams.get("nodes")):
        if not isinstance(team, dict):
            raise LinearError(ODD)
        issues += _count(team.get("issueCount"))
        everything += _count(team.get("all"))
    url_key = str(org.get("urlKey") or "")
    return Reading(
        workspace=str(org.get("name") or ""),
        url=f"https://linear.app/{url_key}" if url_key else "https://linear.app",
        plan=plan,
        trial_ends_at=org.get("trialEndsAt") if isinstance(org.get("trialEndsAt"), str) else None,
        issues=issues,
        archived=max(everything - issues, 0),
        at=now,
    )


_lock = threading.Lock()
_last: Reading | None = None
_last_key = ""
_failed: tuple[float, str] | None = None  # when the last read failed, and why


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def status(now: float | None = None) -> dict[str, Any]:
    """What the panel shows: whether there's a key, then the last reading (asking Linear when it's due) and
    the last failure since it. `limit` is the Free plan's, or None on a paid plan; `state` is "ok", "near"
    (from NEAR of the limit on) or "full" (Linear refuses new issues)."""
    global _last, _last_key, _failed
    now = time.time() if now is None else now
    key = _key()
    if key is None:
        return {"configured": False, "limit": FREE_ISSUES}
    with _lock:  # one read at a time: the requests that wait for it get its answer
        if key != _last_key:  # a new key: what the old one read, or failed to, isn't this one's
            _last, _last_key, _failed = None, key, None
        due = _last is None or now - _last.at >= FRESH
        if due and (_failed is None or now - _failed[0] >= RETRY):
            try:
                _last, _failed = read(key, now), None
            except LinearError as exc:
                _failed = (now, str(exc))
        last, failed = _last, _failed
    out: dict[str, Any] = {"configured": True, "limit": FREE_ISSUES, "error": failed[1] if failed else None, "failed_at": _iso(failed[0]) if failed else None}
    if last is None:
        return out
    limit = FREE_ISSUES if last.plan in ("free", None) else None
    state = "ok" if limit is None or last.issues < NEAR * limit else "near" if last.issues < limit else "full"
    return {
        **out,
        "workspace": last.workspace,
        "url": last.url,
        "plan": last.plan,
        "trial_ends_at": last.trial_ends_at,
        "issues": last.issues,
        "archived": last.archived,
        "limit": limit,
        "state": state,
        "read_at": _iso(last.at),
    }


def forget() -> None:
    """Drop the last reading and failure, so the next status asks Linear (tests)."""
    global _last, _last_key, _failed
    with _lock:
        _last, _last_key, _failed = None, "", None
