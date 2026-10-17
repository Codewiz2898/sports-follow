"""Linear on the admin panel (linear.py): the workspace's plan and its issues against the Free plan's limit, read
with a read-only key at most once an hour, and a panel that keeps working without a key or with Linear down.
Linear is a fake here (httpx.MockTransport) answering as its GraphQL API does."""

from __future__ import annotations

import json

import httpx
import pytest

from sports_follow import linear
from tests.test_admin import admin_cookie, password, sign_in  # noqa: F401  (the fixture)
from tests.test_api import Api, api  # noqa: F401  (the fixture)

KEY = "lin_api_read_only_test_key"
NOW = 1_800_000_000.0


def answer(subscription: dict | None = None, teams: tuple[tuple[int, int], ...] = ((180, 182), (95, 95))) -> dict:
    """Linear's answer to linear.QUERY: each team's issues without and with the archived ones."""
    return {"data": {
        "organization": {"name": "Bharath Mourya", "urlKey": "bharathm", "subscription": subscription, "trialEndsAt": None},
        "teams": {"nodes": [{"key": f"T{i}", "issueCount": live, "all": every} for i, (live, every) in enumerate(teams)]},
    }}


class FakeLinear:
    """Answers each request with the next of `answers` (a dict as JSON, an httpx.Response, or an exception to raise)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        given = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(given, Exception):
            raise given
        return given if isinstance(given, httpx.Response) else httpx.Response(200, json=given)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    """Each test starts with no reading, and its key in a file of its own; no test reaches the real Linear."""
    key = tmp_path / "linear-key"
    key.write_text(KEY + "\n")
    monkeypatch.setenv("SPORTS_FOLLOW_LINEAR_KEY_FILE", str(key))
    monkeypatch.setattr(linear, "FREE_ISSUES", 250)
    linear.forget()
    yield key
    linear.forget()


def fake(monkeypatch, *answers) -> FakeLinear:
    linear_ = FakeLinear(*answers)
    monkeypatch.setattr(linear, "_client", httpx.Client(transport=httpx.MockTransport(linear_)))
    return linear_


def test_the_free_plans_issues_are_counted_without_archived_ones_against_its_limit(monkeypatch):
    linear_ = fake(monkeypatch, answer(subscription=None))
    shown = linear.status(NOW)
    assert shown["plan"] == "free" and shown["workspace"] == "Bharath Mourya" and shown["url"] == "https://linear.app/bharathm"
    assert (shown["issues"], shown["archived"], shown["limit"]) == (275, 2, 250)
    assert shown["state"] == "full" and shown["error"] is None
    # A personal key goes as it is (no "Bearer", which is for OAuth tokens), and only to Linear's API.
    sent = linear_.requests[0]
    assert str(sent.url) == linear.API and sent.headers["authorization"] == KEY
    assert "issueCount(includeArchived: true)" in json.loads(sent.content)["query"]


@pytest.mark.parametrize(("issues", "state"), [(0, "ok"), (199, "ok"), (200, "near"), (249, "near"), (250, "full"), (300, "full")])
def test_the_panel_warns_from_four_fifths_of_the_limit_and_says_new_issues_are_refused_at_it(monkeypatch, issues, state):
    fake(monkeypatch, answer(teams=((issues, issues),)))
    assert linear.status(NOW)["state"] == state


def test_a_paid_plan_has_no_issue_limit(monkeypatch):
    fake(monkeypatch, answer(subscription={"type": "basic"}, teams=((900, 1200),)))
    shown = linear.status(NOW)
    assert (shown["plan"], shown["limit"], shown["state"], shown["issues"], shown["archived"]) == ("basic", None, "ok", 900, 300)


def test_a_key_that_cant_read_the_plan_still_counts_against_the_free_limit(monkeypatch):
    hidden = answer()
    hidden["errors"] = [{"message": "Forbidden", "path": ["organization", "subscription"]}]
    fake(monkeypatch, hidden)
    shown = linear.status(NOW)
    assert (shown["plan"], shown["limit"], shown["issues"], shown["state"]) == (None, 250, 275, "full")


def test_without_a_key_the_panel_says_so_and_never_asks_linear(monkeypatch, fresh):
    linear_ = fake(monkeypatch, answer())
    fresh.unlink()
    assert linear.status(NOW) == {"configured": False, "limit": 250}
    fresh.write_text("  \n")  # an empty file is no key either
    assert linear.status(NOW)["configured"] is False
    assert linear_.requests == []


def test_linear_is_asked_at_most_once_an_hour(monkeypatch):
    linear_ = fake(monkeypatch, answer(teams=((10, 10),)), answer(teams=((11, 11),)))
    assert linear.status(NOW)["issues"] == 10
    assert linear.status(NOW + linear.FRESH - 1)["issues"] == 10
    assert len(linear_.requests) == 1
    assert linear.status(NOW + linear.FRESH)["issues"] == 11
    assert len(linear_.requests) == 2


def test_linear_down_is_said_on_the_panel_and_tried_again_after_a_while_not_on_every_load(monkeypatch):
    linear_ = fake(monkeypatch, httpx.ConnectError("no route"), answer())
    shown = linear.status(NOW)
    assert shown["configured"] is True and "issues" not in shown
    assert shown["error"] == "Linear didn't answer (ConnectError)" and shown["limit"] == 250
    assert linear.status(NOW + linear.RETRY - 1)["error"]
    assert len(linear_.requests) == 1
    assert linear.status(NOW + linear.RETRY)["issues"] == 275


def test_a_failed_read_keeps_the_last_reading_on_the_panel_with_why_it_wasnt_renewed(monkeypatch):
    fake(monkeypatch, answer(teams=((240, 240),)), httpx.Response(503, text="upstream down"))
    linear.status(NOW)
    shown = linear.status(NOW + linear.FRESH)
    assert shown["issues"] == 240 and shown["state"] == "near"
    assert shown["error"] == "Linear answered 503" and shown["read_at"] < shown["failed_at"]


def test_a_refused_key_says_what_linear_said_and_never_the_key(monkeypatch, caplog):
    refused = httpx.Response(400, json={"errors": [{"message": "Authentication required, not authenticated", "extensions": {"code": "AUTHENTICATION_ERROR"}}]})
    fake(monkeypatch, refused)
    with caplog.at_level("WARNING", logger="sports_follow.linear"):
        shown = linear.status(NOW)
    assert shown["error"] == "Linear answered 400: Authentication required, not authenticated"
    assert "Authentication required" in caplog.text  # the refusal is logged, the request and its key aren't
    assert KEY not in json.dumps(shown) and KEY not in caplog.text


@pytest.mark.parametrize("odd", [
    {"data": None},
    {"data": {"organization": {"name": "x"}, "teams": {"nodes": [{"key": "T", "issueCount": "many", "all": 1}]}}},
    {"data": {"organization": {"name": "x"}, "teams": {"nodes": [{"key": "T", "issueCount": -1, "all": 1}]}}},
    ["not", "an", "object"],
    {"errors": 5},
    {"data": {"organization": {"name": "x"}, "teams": {"nodes": 5}}},
    httpx.Response(200, content=b'{"data": {"organization": {"name": "x"}, "teams": {"nodes": [{"key": "T", "issueCount": 1e400, "all": 1}]}}}'),
    httpx.Response(200, text="<html>a proxy's error page</html>"),
])
def test_an_answer_this_app_doesnt_expect_is_an_error_on_the_panel_not_a_crash(monkeypatch, odd):
    fake(monkeypatch, odd)
    shown = linear.status(NOW)
    assert shown["error"] and "issues" not in shown


def test_a_new_key_reads_again_at_once(monkeypatch, fresh):
    linear_ = fake(monkeypatch, answer(teams=((10, 10),)), answer(teams=((12, 12),)))
    linear.status(NOW)
    fresh.write_text("lin_api_another_key")
    assert linear.status(NOW + 1)["issues"] == 12
    assert [r.headers["authorization"] for r in linear_.requests] == [KEY, "lin_api_another_key"]


# ---------------------------------------------------------------- the API


def test_the_admin_route_shows_it_only_to_a_signed_in_admin(api, password, monkeypatch):
    fake(monkeypatch, answer())
    assert api.call("GET", "/api/admin/linear").status_code == 401
    shown = api.call("GET", "/api/admin/linear", headers=admin_cookie(sign_in(api)))
    assert shown.status_code == 200
    assert (shown.json()["plan"], shown.json()["issues"], shown.json()["limit"], shown.json()["state"]) == ("free", 275, 250, "full")


def test_without_an_admin_password_the_route_isnt_there(api, tmp_path, monkeypatch):
    monkeypatch.setenv("SPORTS_FOLLOW_ADMIN_PASSWORD_FILE", str(tmp_path / "none"))
    assert api.call("GET", "/api/admin/linear").status_code == 404
