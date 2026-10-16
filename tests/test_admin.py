"""The admin panel (admin.py, controls.py): sports and sources turned off and on while the app runs, within
what the server's settings allow; what a source may spend, changed and put back; and the panel's own
password and authenticator code, the only way in, each sign-in counted for the alert that emails it. What it does to accounts needs Postgres (test_admin_db.py).
Redis, the database and the job queue are fakes here (test_api.Api)."""

from __future__ import annotations

import pytest

import base64
import time

from sports_follow import admin, adapters, controls, metrics, server, sports
from sports_follow.adapters import ADAPTERS, KNOWN, budget
from tests.test_api import Api, api  # noqa: F401  (the fixture)

PASSWORD = "correct horse battery staple"


def sport_with_sources() -> sports.Sport:
    """A running sport with a running source (which one depends on the tokens this machine has)."""
    return next(s for s in sports.ENABLED if any(a.sport == s.key for a in ADAPTERS.values()))


# ---------------------------------------------------------------- sports and sources, live


def test_a_sport_turned_off_stops_running_with_its_sources_and_comes_back():
    sport = sport_with_sources()
    theirs = [system for system, a in ADAPTERS.items() if a.sport == sport.key]
    controls.turn_sport(sport.key, False)
    assert sport not in sports.ENABLED and not sports.on(sport.key)
    assert not any(system in ADAPTERS for system in theirs)
    assert sports.shown(sport, True)["on"] is False
    controls.turn_sport(sport.key, True)
    assert sport in sports.ENABLED and all(system in ADAPTERS for system in theirs)


def test_a_source_turned_off_stops_running_and_the_next_one_serves_its_sport():
    first = next(iter(ADAPTERS.values()))
    controls.turn_source(first.system, False)
    assert first.system not in ADAPTERS and ADAPTERS.get(first.system) is None
    assert adapters.for_sport(first.sport) is not first
    controls.turn_source(first.system, True)
    assert ADAPTERS.get(first.system) is first


def test_a_sport_the_servers_settings_leave_out_stays_off(monkeypatch):
    kept, left_out = sports.ALL[0], sports.ALL[1]
    monkeypatch.setattr(sports, "ALLOWED", (kept,))  # SPORTS_FOLLOW_SPORTS=<kept>
    controls.turn_sport(left_out.key, False)
    controls.turn_sport(left_out.key, True)
    assert list(sports.ENABLED) == [kept]
    shown = next(s for s in admin.sports_view() if s["key"] == left_out.key)
    assert (shown["allowed"], shown["on"], shown["running"], shown["sources"]) == (False, True, False, [])


def test_a_source_the_servers_settings_leave_out_stays_off(monkeypatch):
    listed, other = adapters.ALL[0], next(a for a in adapters.ALL[1:] if a.system != adapters.ALL[0].system)
    monkeypatch.setattr(adapters, "SOURCES", {listed.system})  # SPORTS_FOLLOW_SOURCES=<listed>
    controls.turn_source(other.system, False)
    controls.turn_source(other.system, True)
    assert list(ADAPTERS) == [listed.system]


def test_a_loop_over_the_running_sources_sees_one_whole_set():
    systems = list(ADAPTERS)
    seen = []
    for i, system in enumerate(ADAPTERS):
        seen.append(system)
        if i == 0:
            controls.turn_source(systems[-1], False)  # mid-loop: no "dictionary changed size", no gap
    assert seen == systems
    assert systems[-1] not in ADAPTERS


def test_a_tests_patch_of_the_running_sources_holds_until_the_panel_changes_them(monkeypatch):
    marker = object()
    monkeypatch.setitem(ADAPTERS, "made_up", marker)
    assert ADAPTERS["made_up"] is marker and "made_up" in list(ADAPTERS)
    monkeypatch.delitem(ADAPTERS, "made_up")
    assert "made_up" not in ADAPTERS


def test_rows_of_the_wrong_shape_turn_nothing_off():
    assert controls.parse({"sports_off": "football", "sources_off": [1, None], "budget:x": [1]}) == controls.Controls()


# ---------------------------------------------------------------- what a source may spend


SCARCE = budget.Budget("scarce-test", windows=(budget.Window(3600, 10, margin=1),), reserve=3, fan=2)


@pytest.fixture
def scarce(monkeypatch):
    monkeypatch.setattr(budget, "known", lambda: [SCARCE])
    return SCARCE


def test_a_new_allowance_is_spent_at_once_and_going_back_restores_the_declared_one(scarce):
    for _ in range(6):
        budget.take(scarce, lane_name="refresh")  # 10 less the margin less the reserve
    with pytest.raises(budget.RateLimited):
        budget.take(scarce, lane_name="refresh")
    admin.set_limits(scarce.name, {"windows": [{"seconds": 3600, "requests": 100, "margin": 1}], "reserve": 3, "fan": 2, "pause": 60})
    budget.take(scarce, lane_name="refresh")
    assert budget.standing(scarce)["windows"][0]["allowance"] == 100
    assert budget.effective(scarce).windows[0].requests == 100
    admin.set_limits(scarce.name, None)
    assert budget.effective(scarce) == scarce
    with pytest.raises(budget.RateLimited):
        budget.take(scarce, lane_name="refresh")


def test_the_fans_share_and_the_pause_follow_the_panel(scarce):
    admin.set_limits(scarce.name, {"windows": [{"seconds": 3600, "requests": 10}], "reserve": 0, "fan": 1, "pause": 5})
    budget.take(scarce, lane_name="fan")
    with pytest.raises(budget.RateLimited, match="fans' share"):
        budget.take(scarce, lane_name="fan")
    assert budget.effective(scarce).pause == 5 and budget.effective(scarce).fan == 1


def test_a_source_without_a_published_limit_can_be_given_one_and_back(monkeypatch):
    counted = budget.Budget("counted-test")
    monkeypatch.setattr(budget, "known", lambda: [counted])
    admin.set_limits(counted.name, {"windows": [{"seconds": 60, "requests": 2}], "reserve": 0, "fan": None, "pause": 60})
    budget.take(counted, lane_name="refresh")
    budget.take(counted, lane_name="refresh")
    with pytest.raises(budget.RateLimited):
        budget.take(counted, lane_name="refresh")
    admin.set_limits(counted.name, {"windows": [], "reserve": 0, "fan": None, "pause": 60})
    for _ in range(50):
        budget.take(counted, lane_name="refresh")


@pytest.mark.parametrize("body, wrong", [
    ({"windows": [{"seconds": 120, "requests": 5}]}, "a minute, an hour or a day"),
    ({"windows": [{"seconds": 60, "requests": 5}, {"seconds": 60, "requests": 9}]}, "each at most once"),
    ({"windows": [{"seconds": 60, "requests": 0}]}, "allowance"),
    ({"windows": [{"seconds": 60, "requests": 5, "margin": 5}]}, "margin"),
    ({"windows": [{"seconds": 60, "requests": 5, "margin": 1}], "reserve": 4}, "reserve"),
    ({"windows": [{"seconds": 60, "requests": 5}], "fan": 6}, "fans' share"),
    ({"windows": [{"seconds": 60, "requests": 5}], "pause": 0}, "pause"),
    ({"windows": [{"seconds": 60, "requests": "5"}]}, "allowance"),
    ({"windows": [{"seconds": 60, "requests": True}]}, "allowance"),
    ({}, "list"),
    ({"windows": []}, "at least one window"),  # its plan has a limit
])
def test_an_allowance_that_cant_work_is_refused_saying_why(scarce, body, wrong):
    with pytest.raises(ValueError, match=wrong):
        admin.checked_limits(scarce.name, body)


def test_every_known_allowance_is_shown_with_who_spends_it(scarce):
    view = admin.sources_view()
    assert [b["name"] for b in view["budgets"]] == [scarce.name]
    assert {s["system"] for s in view["sources"]} == set(KNOWN)
    assert view["budgets"][0]["declared"] == {"windows": [{"seconds": 3600, "requests": 10, "margin": 1}], "reserve": 3, "fan": 2, "pause": 60}


# ---------------------------------------------------------------- the password


def test_only_the_password_matches_its_hash():
    stored = admin.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert admin.check_password(PASSWORD, stored)
    assert not admin.check_password(PASSWORD + "!", stored)
    assert not admin.check_password(PASSWORD, "plain text")
    assert admin.hash_password(PASSWORD) != stored  # salted


# ---------------------------------------------------------------- the API


@pytest.fixture
def password(tmp_path, monkeypatch):
    path = tmp_path / "admin-password"
    path.write_text(admin.hash_password(PASSWORD) + "\n")
    monkeypatch.setenv("SPORTS_FOLLOW_ADMIN_PASSWORD_FILE", str(path))
    return path


def sign_in(api: Api, password_tried: str = PASSWORD, address: str = "81.2.69.142"):
    return api.call("POST", "/api/admin/login", address=address, body={"password": password_tried})


def admin_cookie(answer) -> dict[str, str]:
    token = answer.cookies.get(admin.COOKIE)
    assert token
    return {"Cookie": f"{admin.COOKIE}={token}"}


def test_without_a_password_set_the_panel_isnt_there(api, tmp_path, monkeypatch):
    monkeypatch.setenv("SPORTS_FOLLOW_ADMIN_PASSWORD_FILE", str(tmp_path / "none"))
    assert sign_in(api).status_code == 404
    assert api.call("GET", "/api/admin/sports").status_code == 404


# ---------------------------------------------------------------- the authenticator code

KEY = base64.b32encode(b"12345678901234567890").decode()  # RFC 6238's test key


def test_codes_are_the_ones_authenticator_apps_show():
    # RFC 6238's SHA-1 test vectors (8 digits there; an app shows the last 6).
    for at, eight in ((59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")):
        assert admin.code_at(KEY, at // admin.CODE_STEP) == eight[-6:]


def test_a_code_works_a_step_either_side_for_a_clock_a_little_off_and_no_further():
    now = 1_000_000_000.0
    step = int(now // admin.CODE_STEP)
    for off in (-1, 0, 1):
        assert admin.code_step(KEY, admin.code_at(KEY, step + off), now) == step + off
    for off in (-2, 2):
        assert admin.code_step(KEY, admin.code_at(KEY, step + off), now) is None
    for typed in ("", "12345", "1234567", "abcdef", None, 123456):
        assert admin.code_step(KEY, typed, now) is None


@pytest.fixture
def with_code(password):
    password.write_text(admin.Keys(password.read_text().strip(), KEY).text())
    return password


def code_now() -> str:
    return admin.code_at(KEY, int(time.time() // admin.CODE_STEP))


def test_with_an_authenticator_key_the_password_alone_doesnt_open_the_panel(api, with_code, monkeypatch):
    counted = []
    monkeypatch.setattr(metrics, "admin_sign_in", counted.append)
    assert api.call("GET", "/api/admin/login").json() == {"code": True}
    alone = sign_in(api)
    assert alone.status_code == 401 and alone.json()["detail"] == "That password or code isn't right."  # never says which
    wrong_code = api.call("POST", "/api/admin/login", address="81.2.69.142", body={"password": PASSWORD, "code": "000000" if code_now() != "000000" else "111111"})
    assert wrong_code.status_code == 401
    code = code_now()
    assert api.call("POST", "/api/admin/login", address="81.2.69.142", body={"password": "wrong", "code": code}).status_code == 401
    answer = api.call("POST", "/api/admin/login", address="81.2.69.142", body={"password": PASSWORD, "code": code})
    assert answer.status_code == 200
    assert api.call("GET", "/api/admin/me", headers=admin_cookie(answer)).json() == {"admin": True, "code": True}
    # A code works once: seen over a shoulder, or sent twice, it's refused.
    assert api.call("POST", "/api/admin/login", address="81.2.69.142", body={"password": PASSWORD, "code": code}).status_code == 401
    assert counted == ["wrong", "wrong", "wrong", "ok", "wrong"]


def test_adding_an_authenticator_key_signs_every_admin_out(api, password):
    cookie = admin_cookie(sign_in(api))
    assert api.call("GET", "/api/admin/me", headers=cookie).json() == {"admin": True, "code": False}  # the panel says so
    assert api.call("GET", "/api/admin/login").json() == {"code": False}
    password.write_text(admin.Keys(password.read_text().strip(), KEY).text())
    assert api.call("GET", "/api/admin/me", headers=cookie).status_code == 401


def test_the_code_command_keeps_a_key_only_once_a_code_from_the_app_checks_out(password, monkeypatch, capsys):
    hashed = password.read_text().strip()
    shown = {}

    def app_reads_the_key(prompt):
        if "key" not in shown:  # the key it printed, typed into the app
            shown["key"] = "".join(capsys.readouterr().out.split("Add this key")[1].split("\n\n")[1].split())
        return admin.code_at(shown["key"], int(time.time() // admin.CODE_STEP))

    assert admin.set_code(ask=lambda prompt: "12345x") == 1  # never a right code: nothing kept
    assert password.read_text().strip() == hashed
    capsys.readouterr()
    assert admin.set_code(ask=app_reads_the_key) == 0
    keys = admin.stored()
    assert keys is not None and keys.password == hashed and keys.totp == shown["key"]
    assert oct(password.stat().st_mode & 0o777) == "0o600"
    # A new password keeps the key, and the key needs a password first.
    monkeypatch.setattr(admin.getpass, "getpass", lambda prompt: "a brand new long password")
    assert admin.set_password() == 0
    renewed = admin.stored()
    assert renewed is not None and renewed.totp == shown["key"] and admin.check_password("a brand new long password", renewed.password)
    password.unlink()
    assert admin.set_code(ask=lambda prompt: "123456") == 1


def test_a_file_without_a_password_on_its_first_line_is_no_panel(password):
    password.write_text(f"totp:{KEY}\n")
    assert admin.stored() is None


def test_search_engines_are_told_not_to_list_the_admin_page():
    assert server._not_indexed("admin") == server._not_indexed("admin/users/3") == {"X-Robots-Tag": "noindex, nofollow"}
    assert server._not_indexed("") == server._not_indexed("player/7") == server._not_indexed("administrator") == {}


def test_the_panel_opens_with_its_password_only(api, password):
    assert api.call("GET", "/api/admin/me").status_code == 401
    assert api.call("GET", "/api/admin/sports", headers={"Cookie": f"{admin.COOKIE}=made-up"}).status_code == 401
    assert api.call("GET", "/api/admin/sports", fan="a-fan").status_code == 401  # a fan's cookie isn't an admin's
    assert sign_in(api, "wrong").status_code == 401
    answer = sign_in(api)
    assert answer.status_code == 200
    set_cookie = answer.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie and "path=/api/admin" in set_cookie
    assert api.call("GET", "/api/admin/me", headers=admin_cookie(answer)).json() == {"admin": True, "code": False}


def test_a_new_password_signs_every_admin_out(api, password):
    cookie = admin_cookie(sign_in(api))
    password.write_text(admin.hash_password("another long password") + "\n")
    assert api.call("GET", "/api/admin/me", headers=cookie).status_code == 401


def test_signing_out_ends_the_session(api, password):
    cookie = admin_cookie(sign_in(api))
    assert api.call("POST", "/api/admin/logout", headers=cookie).status_code == 200
    assert api.call("GET", "/api/admin/me", headers=cookie).status_code == 401


def test_tries_are_limited_from_an_address_and_wrong_ones_from_everyone(api, password):
    answers = [sign_in(api, "wrong").status_code for _ in range(admin.ATTEMPTS_PER_ADDRESS + 1)]
    assert answers == [401] * admin.ATTEMPTS_PER_ADDRESS + [429]
    assert sign_in(api).status_code == 429  # even the right one, from there, for now
    for i in range(admin.FAILURES_PER_HOUR):
        sign_in(api, "wrong", address=f"81.2.{i // 200}.{i % 200 + 1}")
    assert sign_in(api, address="81.3.0.1").status_code == 429


def test_the_panel_turns_a_sport_off_for_the_app_too(api, password):
    cookie = admin_cookie(sign_in(api))
    sport = sport_with_sources()
    answer = api.call("PUT", f"/api/admin/sports/{sport.key}", headers=cookie, body={"on": False})
    assert answer.status_code == 200
    row = next(s for s in answer.json()["sports"] if s["key"] == sport.key)
    assert row["allowed"] and not row["on"] and not row["running"]
    shown = next(s for s in api.call("GET", "/api/sports").json()["sports"] if s["key"] == sport.key)
    assert shown["on"] is False
    assert api.call("PUT", "/api/admin/sports/curling", headers=cookie, body={"on": False}).status_code == 404
    assert api.call("PUT", f"/api/admin/sports/{sport.key}", headers=cookie, body={"on": "no"}).status_code == 400
    assert api.call("PUT", f"/api/admin/sports/{sport.key}", body={"on": True}).status_code == 401
    assert sport not in sports.ENABLED


def test_the_panel_sets_and_resets_an_allowance(api, password, scarce):
    cookie = admin_cookie(sign_in(api))
    good = {"windows": [{"seconds": 60, "requests": 30, "margin": 2}], "reserve": 10, "fan": 5, "pause": 120}
    answer = api.call("PUT", f"/api/admin/budgets/{scarce.name}", headers=cookie, body=good)
    assert answer.status_code == 200
    shown = answer.json()["budgets"][0]
    assert shown["limits"] == good and shown["changed"] is True
    refused = api.call("PUT", f"/api/admin/budgets/{scarce.name}", headers=cookie, body={**good, "reserve": 28})
    assert refused.status_code == 400 and "reserve" in refused.json()["detail"]
    assert api.call("PUT", "/api/admin/budgets/nobody", headers=cookie, body=good).status_code == 404
    reset = api.call("DELETE", f"/api/admin/budgets/{scarce.name}", headers=cookie).json()["budgets"][0]
    assert reset["changed"] is False and reset["limits"] == reset["declared"]


def test_a_source_the_panel_turns_off_is_off_for_search_and_follows(api, password):
    cookie = admin_cookie(sign_in(api))
    system = next(iter(ADAPTERS))
    answer = api.call("PUT", f"/api/admin/sources/{system}", headers=cookie, body={"on": False})
    row = next(s for s in answer.json()["sources"] if s["system"] == system)
    assert row["on"] is False and row["running"] is False
    assert system not in server.ADAPTERS
    assert api.call("PUT", "/api/admin/sources/nowhere", headers=cookie, body={"on": False}).status_code == 404


def test_a_blocked_account_gets_no_code(api, monkeypatch):
    monkeypatch.setattr(server.mail, "configured", lambda secure: True)
    monkeypatch.setattr(server.accounts, "blocked", lambda db, email=None, account_id=None: email == "blocked@example.com")
    sent = []
    monkeypatch.setattr(server.mail, "send_code", lambda email, code: sent.append(email))
    refused = api.call("POST", "/api/auth/email/start", address="81.2.69.142", body={"email": "Blocked@example.com"})
    assert refused.status_code == 403 and refused.json()["detail"] == server.BLOCKED
    assert api.call("POST", "/api/auth/email/start", address="81.2.69.142", body={"email": "fan@example.com"}).status_code == 200
    assert sent == ["fan@example.com"]
