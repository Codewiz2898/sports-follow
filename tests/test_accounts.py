"""Signing in with an emailed code, what a signed-in request is, signing out and deleting an account,
through the API with the fakes of test_api (Redis, the queue, sessions) and accounts.py's database work
replaced by a small in-memory stand-in. What that database work does to real rows is test_accounts_db.
"""

from __future__ import annotations

from datetime import datetime, timezone
from http.cookies import SimpleCookie

import pytest

from sports_follow import accounts, mail, server
from tests.test_api import PUBLIC, Api, api  # noqa: F401  (api: the fixture)

EMAIL = "Fan@Example.com"


class Accounts:
    """accounts.py's database side, in memory."""

    def __init__(self):
        self.emails: dict[int, str] = {}
        self.sessions: dict[str, accounts.Signed] = {}  # token hash: session
        self.adopted: list[tuple[str, int]] = []
        self.lookups = 0
        self.made = 0
        self.bumped = False

    def find_or_create(self, db, email):
        found = next((i for i, e in self.emails.items() if e == email), None)
        if found is None:
            found = len(self.emails) + 1
            self.emails[found] = email
        return found

    def sign_in(self, db, account_id, anon, ending=None):
        self.sessions = {h: s for h, s in self.sessions.items() if s.session_id != ending}
        self.made += 1
        token = f"token-{self.made}"
        signed = accounts.Signed(account_id=account_id, fan_id=f"acct-{account_id}", session_id=100 + self.made)
        self.sessions[accounts.token_hash(token)] = signed
        if anon:
            self.adopted.append((anon, account_id))
        return accounts.SignedIn(token=token, session_id=signed.session_id, fan_id=signed.fan_id, players=2 if anon else 0)

    def session_for(self, db, token_hash):
        self.lookups += 1
        return self.sessions.get(token_hash)

    def describe(self, db, account_id):
        email = self.emails.get(account_id)
        return {"signed_in": True, "email": email, "google": False, "created_at": "2026-10-13T00:00:00+00:00"} if email else None

    def bump(self, db, session_id):
        return self.bumped

    def end_sessions(self, db, account_id, session_id=None):
        ended = [h for h, s in self.sessions.items() if s.account_id == account_id and session_id in (None, s.session_id)]
        for h in ended:
            del self.sessions[h]
        return ended

    def delete_account(self, db, account_id):
        hashes = self.end_sessions(db, account_id)
        return accounts.Deleted(fan_id=f"acct-{account_id}", email=self.emails.pop(account_id), token_hashes=hashes)


@pytest.fixture
def store(api, monkeypatch):
    fake = Accounts()
    for name in ("find_or_create", "sign_in", "session_for", "describe", "bump", "end_sessions", "delete_account"):
        monkeypatch.setattr(accounts, name, getattr(fake, name))
    return fake


@pytest.fixture
def outbox(monkeypatch):
    """Codes "emailed", by address."""
    sent: dict[str, str] = {}
    monkeypatch.setattr(mail, "configured", lambda https: True)
    monkeypatch.setattr(mail, "send_code", lambda email, code: sent.__setitem__(email, code))
    return sent


def cookies(fan: str | None = None, session: str | None = None, raw_fan: str | None = None) -> dict[str, str]:
    jar = [f"sf_fan={server.sign_fan(fan)}"] if fan else [f"sf_fan={raw_fan}"] if raw_fan else []
    jar += [f"sf_session={session}"] if session else []
    return {"Cookie": "; ".join(jar)} if jar else {}


def set_cookies(response) -> dict[str, tuple[str, bool]]:
    """Each cookie a response sets: its value, and whether it's being deleted."""
    out = {}
    for header in response.headers.get_list("set-cookie"):
        for name, morsel in SimpleCookie(header).items():
            out[name] = (morsel.value, morsel["max-age"] == "0" or bool(morsel["expires"] and "1970" in morsel["expires"]))
    return out


def sign_in(api: Api, outbox: dict, *, email: str = EMAIL, headers: dict | None = None, address: str = PUBLIC):
    assert api.call("POST", "/api/auth/email/start", body={"email": email}, headers=headers, address=address).status_code == 200
    code = outbox[accounts.normal_email(email)]
    return api.call("POST", "/api/auth/email/verify", body={"email": email, "code": code}, headers=headers, address=address)


# ---------------------------------------------------------------- the emailed code


def test_a_code_signs_in_and_the_first_sign_in_makes_the_account(api, store, outbox):
    answer = sign_in(api, outbox)
    assert answer.status_code == 200
    assert answer.json()["signed_in"] is True and answer.json()["email"] == "fan@example.com"  # kept lower case
    jar = set_cookies(answer)
    assert jar["sf_session"][0] and not jar["sf_session"][1]
    assert jar["sf_fan"][1]  # the device's anonymous cookie goes
    assert sign_in(api, outbox, email=" FAN@example.com ").status_code == 200
    assert store.emails == {1: "fan@example.com"}  # the same account


def test_asking_for_a_code_says_the_same_whether_or_not_the_address_has_an_account(api, store, outbox):
    store.emails[1] = "known@example.com"
    known = api.call("POST", "/api/auth/email/start", body={"email": "known@example.com"}, address=PUBLIC)
    unknown = api.call("POST", "/api/auth/email/start", body={"email": "nobody@example.com"}, address=PUBLIC)
    assert known.status_code == unknown.status_code == 200 and known.json() == unknown.json()
    assert store.emails == {1: "known@example.com"}  # asking makes nothing


@pytest.mark.parametrize("email", ["", "fan", "fan@", "@example.com", "fan@example", "a b@example.com", "x" * 250 + "@example.com"])
def test_an_address_that_isnt_one_is_refused(api, store, outbox, email):
    assert api.call("POST", "/api/auth/email/start", body={"email": email}).status_code == 400
    assert outbox == {}


def test_five_wrong_codes_end_the_code(api, store, outbox):
    api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC)
    right = outbox["fan@example.com"]
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(server.CODE_TRIES):
        answer = api.call("POST", "/api/auth/email/verify", body={"email": EMAIL, "code": wrong})
        assert answer.status_code == 400 and "isn't right" in answer.json()["detail"]
    answer = api.call("POST", "/api/auth/email/verify", body={"email": EMAIL, "code": right})
    assert answer.status_code == 429  # even the right one: the code is gone
    assert api.call("POST", "/api/auth/email/verify", body={"email": EMAIL, "code": right}).json()["detail"] == "That code has run out. Ask for a new one."
    assert store.sessions == {}


def test_a_new_code_waits_a_minute_and_an_address_gets_five_an_hour(api, store, outbox):
    start = lambda: api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC)  # noqa: E731
    wait = f"auth-wait:{accounts.email_key('fan@example.com')}"
    assert start().status_code == 200
    first = outbox.pop("fan@example.com")
    again = start()  # within the minute: the code already sent is the one
    assert again.status_code == 200 and again.json()["resend_in"] == 42 and outbox == {}
    assert api.call("POST", "/api/auth/email/verify", body={"email": EMAIL, "code": first}).status_code == 200
    assert wait not in api.redis.data  # a code that was used doesn't hold up the next sign-in
    for _ in range(server.CODES_PER_EMAIL - 1):  # the second to the fifth
        api.redis.data.pop(wait, None)  # a minute later
        assert start().status_code == 200
    api.redis.data.pop(wait, None)
    assert start().status_code == 429  # the sixth code this hour


def test_one_network_gets_twenty_codes_an_hour_whichever_addresses_they_go_to(api, store, outbox):
    answers = [api.call("POST", "/api/auth/email/start", body={"email": f"fan{i}@example.com"}, address=PUBLIC) for i in range(server.CODES_PER_ADDRESS + 1)]
    assert [a.status_code for a in answers] == [200] * server.CODES_PER_ADDRESS + [429]
    assert api.call("POST", "/api/auth/email/start", body={"email": "other@example.com"}, address="81.2.69.143").status_code == 200


def test_an_address_with_twenty_wrong_codes_today_gets_no_more(api, store, outbox):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    api.redis.data[f"auth-fails:{accounts.email_key('fan@example.com')}:{day}"] = server.FAILS_PER_DAY
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}).status_code == 429
    assert outbox == {}


def test_a_code_that_couldnt_be_sent_can_be_asked_for_again_at_once(api, store, monkeypatch):
    monkeypatch.setattr(mail, "configured", lambda https: True)

    def down(email, code):
        raise mail.MailError("Resend answered 500")

    monkeypatch.setattr(mail, "send_code", down)
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}).status_code == 502
    assert not any(k.startswith(("auth-code:", "auth-wait:")) for k in api.redis.data)


def test_without_a_mail_key_email_sign_in_is_off_and_logging_codes_needs_plain_http(api, monkeypatch):
    monkeypatch.setattr(mail, "_key", lambda: None)
    monkeypatch.setattr(mail, "MAIL_LOG", False)
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}).status_code == 503
    assert api.call("GET", "/api/config").json()["email_sign_in"] is False
    monkeypatch.setattr(mail, "MAIL_LOG", True)
    assert mail.configured(https=False) is True
    assert mail.configured(https=True) is False  # a real server never logs codes


# ---------------------------------------------------------------- signing in brings the device's things


def test_signing_in_brings_this_devices_fan_and_counts_its_researches(api, store, outbox):
    api.redis.data[server.player_search.research_key("anon-1")] = 2
    answer = sign_in(api, outbox, headers=cookies(fan="anon-1"))
    assert answer.json()["players_added"] == 2
    assert store.adopted == [("anon-1", 1)]
    assert api.redis.data[server.player_search.research_key("acct-1")] == 2  # no three more for signing in
    assert server.player_search.research_key("anon-1") not in api.redis.data
    assert api.queue.jobs[-1][:3] == ("adopt_stragglers", "anon-1", 1) and api.queue.options == {"_defer_by": 60}


def test_an_accounts_fan_id_is_never_taken_from_a_fan_cookie(api, store, monkeypatch):
    """Ids from before cookies were signed are taken unsigned if they hold something; an account's never is."""
    monkeypatch.setattr(server, "_owns_anything", lambda db, fan: True)
    taken = server._signed(api.call("GET", "/api/me/research", cookie="acct-AbCdEfGhIjKlMnOpQrStUvWx").cookies["sf_fan"])
    assert taken and not taken[0].startswith("acct-")  # a new anonymous fan instead
    assert server._signed(api.call("GET", "/api/me/research", cookie="an-old-unsigned-id").cookies["sf_fan"])[0] == "an-old-unsigned-id"


def test_a_cookie_the_server_didnt_sign_brings_nothing(api, store, outbox):
    sign_in(api, outbox, headers=cookies(raw_fan="someone-elses-id"))
    assert store.adopted == []


def test_a_signed_in_request_is_the_accounts_fan_and_its_session_is_looked_up_once(api, store, outbox):
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    api.follows.add(("acct-1", 1))

    async def read(player_id):
        return {"player_id": 1, "version": 1, "status": "ready", "live": {}}

    server.read_card, original = read, server.read_card
    try:
        for _ in range(3):
            answer = api.call("GET", "/api/players/1/card", headers=cookies(session=token))
            assert answer.json()["following"] is True
            assert "sf_fan" not in set_cookies(answer)  # no anonymous fan is made for a signed-in device
    finally:
        server.read_card = original
    assert store.lookups == 1  # then from Redis


def test_signing_in_again_replaces_the_devices_session(api, store, outbox):
    first = set_cookies(sign_in(api, outbox))["sf_session"][0]
    second = set_cookies(sign_in(api, outbox, headers=cookies(session=first)))["sf_session"][0]
    assert second != first
    assert list(store.sessions) == [accounts.token_hash(second)]
    assert api.call("GET", "/api/me/account", headers=cookies(session=first)).json()["signed_in"] is False


# ---------------------------------------------------------------- who am I


def test_the_app_hears_who_it_is_signed_in_as_and_when_it_was_signed_out(api, store, outbox):
    assert api.call("GET", "/api/me/account").json() == {"signed_in": False}
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    me = api.call("GET", "/api/me/account", headers=cookies(session=token))
    assert me.json()["email"] == "fan@example.com" and "sf_session" not in set_cookies(me)
    store.bumped = True  # a day later
    assert set_cookies(api.call("GET", "/api/me/account", headers=cookies(session=token)))["sf_session"] == (token, False)
    store.sessions.clear()  # signed out on another device
    api.redis.data.pop(f"session:{accounts.token_hash(token)}")
    gone = api.call("GET", "/api/me/account", headers=cookies(session=token))
    assert gone.json() == {"signed_in": False, "reason": "signed_out"}
    assert set_cookies(gone)["sf_session"][1]  # the dead cookie goes


# ---------------------------------------------------------------- signing out


@pytest.mark.parametrize("everywhere", [False, True])
def test_signing_out_ends_the_session_and_leaves_a_new_empty_fan(api, store, outbox, everywhere):
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    other = set_cookies(sign_in(api, outbox, address="81.2.69.143"))["sf_session"][0]  # another device
    answer = api.call("POST", "/api/auth/signout", body={"everywhere": everywhere}, headers=cookies(session=token))
    jar = set_cookies(answer)
    assert answer.json() == {"signed_in": False} and jar["sf_session"][1]
    fresh = server._signed(jar["sf_fan"][0])
    assert fresh and fresh[0] != "acct-1"  # a new anonymous fan
    assert api.call("GET", "/api/me/account", headers=cookies(session=token)).json()["signed_in"] is False  # not from the cache either
    assert api.call("GET", "/api/me/account", headers=cookies(session=other)).json()["signed_in"] is not everywhere


# ---------------------------------------------------------------- deleting the account


def test_deleting_needs_the_account_and_a_confirmation(api, store, outbox):
    assert api.call("DELETE", "/api/me/account", body={"confirm": True}).status_code == 401
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    assert api.call("DELETE", "/api/me/account", body={}, headers=cookies(session=token)).status_code == 400
    assert store.emails


def test_deleting_the_account_ends_every_session_and_forgets_its_keys(api, store, outbox):
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    api.redis.data[server.player_search.research_key("acct-1")] = 1
    answer = api.call("DELETE", "/api/me/account", body={"confirm": True}, headers=cookies(session=token))
    assert answer.json() == {"deleted": True}
    assert store.emails == {} and store.sessions == {}
    assert not [k for k in api.redis.data if k.startswith(("session:", "research:acct-1", "auth-code:", "auth-tries:"))]
    jar = set_cookies(answer)
    assert jar["sf_session"][1] and server._signed(jar["sf_fan"][0])
