"""Signing in with an emailed code, what a signed-in request is, signing out and deleting an account,
through the API with the fakes of test_api (Redis, the queue, sessions) and accounts.py's database work
replaced by a small in-memory stand-in. What that database work does to real rows is test_accounts_db.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from sports_follow import accounts, admin, google_signin, mail, server
from tests.test_admin import admin_cookie, password  # noqa: F401  (password: the fixture)
from tests.test_admin import sign_in as admin_sign_in
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
        self.blocked_ids: set[int] = set()  # blocked by an admin (admin.py)
        self.dropped = 0  # the device's teams that don't fit in the account (teams.merge)
        self.dropped_players = self.dropped_games = 0  # and its players and games (accounts.adopt)
        self.fresh: set[int] = set()  # accounts made today; the rest stood a day before (accounts.standing)

    def find_or_create(self, db, email):
        found = next((i for i, e in self.emails.items() if e == email), None)
        if found is None:
            found = len(self.emails) + 1
            self.emails[found] = email
            self.fresh.add(found)
        return found

    def sign_in(self, db, account_id, anon, ending=None):
        self.sessions = {h: s for h, s in self.sessions.items() if s.session_id != ending}
        self.made += 1
        token = f"token-{self.made}"
        signed = accounts.Signed(account_id=account_id, fan_id=f"acct-{account_id}", session_id=100 + self.made)
        self.sessions[accounts.token_hash(token)] = signed
        if anon:
            self.adopted.append((anon, account_id))
        return accounts.SignedIn(token=token, session_id=signed.session_id, fan_id=signed.fan_id, players=2 if anon else 0, teams_dropped=self.dropped if anon else 0,
                                 players_dropped=self.dropped_players if anon else 0, games_dropped=self.dropped_games if anon else 0)

    def session_for(self, db, token_hash):
        self.lookups += 1
        return self.sessions.get(token_hash)

    def find_or_create_google(self, db, sub, email):
        linked = getattr(self, "google", {})
        if sub in linked:
            return linked[sub]
        account_id = self.find_or_create(db, email)
        if account_id in linked.values():
            return None  # this address's account has another Google account
        self.google = {**linked, sub: account_id}
        return account_id

    def blocked(self, db, email=None, account_id=None):
        if account_id is None:
            account_id = next((i for i, e in self.emails.items() if e == email), None)
        return account_id in self.blocked_ids

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

    def standing(self, db, email):
        return any(e == email and i not in self.fresh for i, e in self.emails.items())

    def delete_account(self, db, account_id):
        hashes = self.end_sessions(db, account_id)
        return accounts.Deleted(fan_id=f"acct-{account_id}", email=self.emails.pop(account_id), token_hashes=hashes)

    def fan_of(self, db, account_id):
        return f"acct-{account_id}" if account_id in self.emails else None


@pytest.fixture
def store(api, monkeypatch):
    fake = Accounts()
    for name in ("find_or_create", "find_or_create_google", "sign_in", "session_for", "describe", "bump", "end_sessions", "delete_account", "blocked", "standing",
                 "fan_of"):
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


def test_asking_for_a_code_doesnt_say_whether_an_address_is_blocked(api, store, outbox):
    """BUG-102: asking for a code answered 403 "blocked" for a blocked account's address, so anyone could find out
    whether an address was one. It answers as for any address; the code it sends says so to whoever holds the
    inbox, and signs nobody in."""
    store.emails.update({1: "blocked@example.com", 2: "fan@example.com"})
    store.blocked_ids.add(1)
    blocked = api.call("POST", "/api/auth/email/start", body={"email": "blocked@example.com"}, address=PUBLIC)
    other = api.call("POST", "/api/auth/email/start", body={"email": "fan@example.com"}, address=PUBLIC)
    assert blocked.status_code == other.status_code == 200 and blocked.json() == other.json()
    refused = api.call("POST", "/api/auth/email/verify", body={"email": "blocked@example.com", "code": outbox["blocked@example.com"]}, address=PUBLIC)
    assert refused.status_code == 403 and refused.json()["detail"] == server.BLOCKED
    assert "sf_session" not in set_cookies(refused) and store.sessions == {}


@pytest.mark.parametrize("email", ["", "fan", "fan@", "@example.com", "fan@example", "a b@example.com", "x" * 250 + "@example.com",
                                   "fan\x01x@example.com", "fan\u200bx@example.com"])  # a control or an invisible character (BUG-096)
def test_an_address_that_isnt_one_is_refused(api, store, outbox, email):
    assert api.call("POST", "/api/auth/email/start", body={"email": email}).status_code == 400
    assert outbox == {}


def wrong_to(right: str) -> str:
    return "000000" if right != "000000" else "111111"


def verify(api: Api, code: str, address: str = PUBLIC):
    return api.call("POST", "/api/auth/email/verify", body={"email": EMAIL, "code": code}, address=address)


def test_five_wrong_codes_end_the_code_where_they_came_from(api, store, outbox):
    api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC)
    right = outbox.pop("fan@example.com")
    for _ in range(server.CODE_TRIES):
        answer = verify(api, wrong_to(right))
        assert answer.status_code == 400 and "isn't right" in answer.json()["detail"]
    for _ in range(2):
        answer = verify(api, right)
        assert answer.status_code == 429 and answer.json()["detail"] == "Too many wrong codes. Ask for a new one."  # even the right one
    assert store.sessions == {}
    api.redis.data.pop(f"auth-wait:{accounts.email_key('fan@example.com')}")  # a minute later
    api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC)
    assert verify(api, outbox["fan@example.com"]).status_code == 200  # a new code starts over


ELSEWHERE = "81.2.69.200"  # someone else's network, who knows the fan's address


def test_wrong_codes_from_another_network_neither_end_the_fans_code_nor_shut_them_out_for_the_day(api, store, outbox):
    """BUG-092 (BHA-414): wrong codes were counted per email address, whoever sent them, so anyone who knew a fan's
    address could end the code in their inbox, and with twenty in a day shut them out of signing in until UTC
    midnight. Now each network's wrong codes are its own."""
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 200
    code = outbox.pop("fan@example.com")
    assert [verify(api, wrong_to(code), ELSEWHERE).status_code for _ in range(server.CODE_TRIES + 1)] == [400] * server.CODE_TRIES + [429]
    assert verify(api, code).status_code == 200  # the fan's code still works where they asked for it

    # The rest of a day's wrong codes from there, to codes that network asked for (and the fan was sent).
    who = accounts.email_key("fan@example.com")
    for _ in range(server.FAILS_PER_DAY // server.CODE_TRIES - 1):
        for key in [k for k in api.redis.data if k.startswith(("auth-wait:", "auth-email:"))]:
            del api.redis.data[key]  # a minute, and an hour, later
        assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=ELSEWHERE).status_code == 200
        right = outbox.pop("fan@example.com")
        assert all(verify(api, wrong_to(right), ELSEWHERE).status_code == 400 for _ in range(server.CODE_TRIES))
    api.redis.data.pop(f"auth-wait:{who}")
    refused = api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=ELSEWHERE)
    assert refused.status_code == 429 and refused.json()["detail"] == "Too many wrong codes for this address today. Try again tomorrow."
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 200  # but not the fan
    assert verify(api, outbox["fan@example.com"]).status_code == 200


def test_networks_other_than_the_one_that_asked_share_a_few_tries_at_a_code(api, store, outbox):
    """However many networks guess at a code, they get CODE_TRIES_ELSEWHERE tries between them; the network that
    asked for it keeps its own."""
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 200
    code = outbox["fan@example.com"]
    tries = [verify(api, wrong_to(code), f"81.2.69.{20 + i // 4}").status_code for i in range(server.CODE_TRIES_ELSEWHERE + 2)]
    assert tries == [400] * server.CODE_TRIES_ELSEWHERE + [429, 429]  # four a network: each under its own five
    assert verify(api, code).status_code == 200


def test_one_code_signs_in_once_however_many_requests_bring_it_at_once(api, store, outbox, monkeypatch):
    """BUG-095: the code was read, checked and only then deleted, so two Verifies sent together (a double tap, or
    someone else holding the code) each signed in, and one left a year's session nobody held. Now the code is used
    up as it's checked: the second request finds it gone."""
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 200
    code = outbox["fan@example.com"]
    counted, other = server._code_tries, []

    async def together(tries, network, asked_here):
        allowed = await counted(tries, network, asked_here)
        if not other:  # the first request has read the code: the second comes in before it goes on
            other.append(None)
            other[0] = await asyncio.to_thread(verify, api, code)
        return allowed

    monkeypatch.setattr(server, "_code_tries", together)
    first = verify(api, code)
    assert sorted([first.status_code, other[0].status_code]) == [200, 400]
    assert len(store.sessions) == 1
    late = first if first.status_code == 400 else other[0]
    assert late.json()["detail"] == "That code has run out. Ask for a new one." and "sf_session" not in set_cookies(late)


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


def test_one_network_gets_twenty_codes_a_day_however_the_hours_fall(api, store, outbox):
    def start(i):
        for key in [k for k in api.redis.data if k.startswith("auth-address:")]:
            del api.redis.data[key]  # another hour
        return api.call("POST", "/api/auth/email/start", body={"email": f"fan{i}@example.com"}, address=PUBLIC)

    assert [start(i).status_code for i in range(server.CODES_PER_ADDRESS_DAY)] == [200] * server.CODES_PER_ADDRESS_DAY
    refused = start(99)
    assert refused.status_code == 429 and refused.json()["detail"] == "Your network has asked for a lot of codes today. Try again tomorrow."


def test_clients_without_a_public_address_share_only_the_hourly_bucket(api, store, outbox):
    """This machine, or a proxy whose forwarded address isn't trusted: one shared key a day would lock out everyone."""
    def start(i):
        for key in [k for k in api.redis.data if k.startswith("auth-address:")]:
            del api.redis.data[key]
        return api.call("POST", "/api/auth/email/start", body={"email": f"fan{i}@example.com"}, address="127.0.0.1")

    assert all(start(i).status_code == 200 for i in range(server.CODES_PER_ADDRESS_DAY + 5))


def test_codes_stop_at_the_mail_plans_daily_allowance(api, store, outbox, monkeypatch):
    """Past it the mail provider refuses anyway: the fan hears "tomorrow" instead of a failed send."""
    monkeypatch.setattr(server, "MAIL_CODES_PER_DAY", 3)
    store.emails.update({i + 1: f"fan{i}@example.com" for i in range(4)})  # fans with accounts: the whole allowance is theirs
    answers = [api.call("POST", "/api/auth/email/start", body={"email": f"fan{i}@example.com"}, address=f"81.2.69.{10 + i}") for i in range(4)]
    assert [a.status_code for a in answers] == [200, 200, 200, 429]
    assert "sent all the sign-in codes it can today" in answers[-1].json()["detail"] and len(outbox) == 3
    # Tapped again within the minute: refused again, not told "the code sent then is the one".
    again = api.call("POST", "/api/auth/email/start", body={"email": "fan3@example.com"}, address="81.2.69.13")
    assert again.status_code == 429


def test_an_address_with_twenty_wrong_codes_today_from_one_network_gets_no_more_there(api, store, outbox):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    api.redis.data[f"auth-fails:{accounts.email_key('fan@example.com')}:{PUBLIC}:{day}"] = server.FAILS_PER_DAY
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 429
    assert outbox == {}
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address="81.2.69.143").status_code == 200


def test_made_up_addresses_cant_spend_the_days_codes_kept_for_fans_with_accounts(api, store, outbox, monkeypatch):
    """BUG-093 (BHA-415): the day's codes (the mail plan's) were everyone's, so a few networks asking codes for made-up
    addresses could leave none for the day, and nobody could sign in. Part of them is kept for addresses that have an
    account (KEPT_FOR_ACCOUNTS); the answer is the same for both until the rest is spent."""
    monkeypatch.setattr(server, "MAIL_CODES_PER_DAY", 10)
    store.emails.update({i + 1: f"fan{i}@example.com" for i in range(4)})
    start = lambda email, i: api.call("POST", "/api/auth/email/start", body={"email": email}, address=f"81.2.69.{10 + i}").status_code  # noqa: E731
    assert [start(f"made-up{i}@example.com", i) for i in range(8)] == [200] * 7 + [429]
    assert [start(f"fan{i}@example.com", 20 + i) for i in range(4)] == [200, 200, 200, 429]  # then the whole day's are spent


def test_accounts_made_today_cant_spend_the_codes_kept_for_fans_with_accounts(api, store, outbox, monkeypatch):
    """BUG-093, reopened: the codes kept for fans with accounts could be spent by accounts made for it minutes before,
    since any account counted. They're kept for accounts that have stood a day (accounts.standing): a newer one asks
    from the rest of the day's, as a new address does."""
    monkeypatch.setattr(server, "MAIL_CODES_PER_DAY", 10)
    store.emails.update({1: "fan@example.com", 2: "made-today@example.com", 3: "made-today-too@example.com"})
    store.fresh.update({2, 3})
    start = lambda email, i: api.call("POST", "/api/auth/email/start", body={"email": email}, address=f"81.2.69.{10 + i}").status_code  # noqa: E731
    assert [start(f"made-up{i}@example.com", i) for i in range(7)] == [200] * 7  # all the day's codes but the three kept
    assert [start("made-today@example.com", 7), start("made-today-too@example.com", 8)] == [429, 429]
    assert start("fan@example.com", 9) == 200  # an account that has stood a day still gets one


def test_plus_addresses_of_one_inbox_share_its_codes_an_hour(api, store, outbox):
    """BUG-296: me+1@ and me+2@ reach one inbox, and each was an address of its own, with an address's codes an hour:
    one inbox made any number of accounts, and their codes could spend those kept for fans with accounts (BUG-093).
    An address's codes an hour are its inbox's now (accounts.inbox: its +tag dropped, and Gmail's dots)."""
    addresses = ["me+1@gmail.com", "me+2@gmail.com", "m.e@gmail.com", "me+spurs@googlemail.com", "me@googlemail.com", "m.e+x@gmail.com"]
    store.emails.update({i + 1: e for i, e in enumerate(addresses)})  # accounts that have stood a day
    answers = [api.call("POST", "/api/auth/email/start", body={"email": e}, address=f"81.2.69.{10 + i}") for i, e in enumerate(addresses)]
    assert [a.status_code for a in answers] == [200] * server.CODES_PER_EMAIL + [429]
    assert answers[-1].json()["detail"] == "That's a lot of codes for one address. Try again in an hour."
    assert api.call("POST", "/api/auth/email/start", body={"email": "you+1@gmail.com"}, address="81.2.69.30").status_code == 200  # another inbox
    # Each address is still its own account: its code signs in to it.
    answer = api.call("POST", "/api/auth/email/verify", body={"email": "me+2@gmail.com", "code": outbox["me+2@gmail.com"]}, address="81.2.69.11")
    assert answer.status_code == 200 and answer.json()["email"] == "me+2@gmail.com"


def test_an_inbox_is_an_address_without_its_tag_and_gmails_dots_and_an_account_is_still_its_address():
    """BUG-296: what counts accounts counts inboxes (accounts.inbox); who an account is stays its whole address, so an
    account made with a +tag signs in as itself, as it always has."""
    assert accounts.normal_email(" Me+Spurs@Gmail.com ") == "me+spurs@gmail.com"
    assert {accounts.inbox(e) for e in ("me+spurs@gmail.com", "m.e@gmail.com", "me@googlemail.com", "m.e+1@googlemail.com", "me@gmail.com")} == {"me@gmail.com"}
    assert accounts.inbox("first.last+news@example.com") == "first.last@example.com"  # dots are Gmail's alone to drop
    assert accounts.inbox("+only@example.com") == "+only@example.com"  # nothing before the tag: the address as it is


CARRIER = "2405:201:ab00"  # an IPv6 /48 of a phone network's: its subscribers each have a /64 in it


def carrier(i: int, site: str = CARRIER) -> str:
    """An address in the i-th /64 of a /48."""
    return f"{site}:{i:x}::10"


def test_fans_with_accounts_on_one_ipv6_48_each_have_their_own_64s_codes_a_day(api, store, outbox):
    """BUG-326: an IPv6 client's codes a day were capped by its /48, at forty, and a phone network or an ISP shares a
    /48 among many subscribers: after forty codes nobody there could sign in that day. A /64 (a household's, or a
    phone's) has its own day now, as an IPv4 address has, and fans whose accounts have stood a day aren't held back by
    their /48's other subscribers."""
    store.emails.update({i + 1: f"fan{i}@example.com" for i in range(41)})  # accounts that have stood a day
    start = lambda i: api.call("POST", "/api/auth/email/start", body={"email": f"fan{i}@example.com"}, address=carrier(i)).status_code  # noqa: E731
    assert [start(i) for i in range(41)] == [200] * 41  # forty-one subscribers, a /64 each

    def from_one_64(i):
        for key in [k for k in api.redis.data if k.startswith("auth-address:")]:
            del api.redis.data[key]  # another hour
        return api.call("POST", "/api/auth/email/start", body={"email": f"home{i}@example.com"}, address=carrier(7, "2405:201:ab02"))

    assert [from_one_64(i).status_code for i in range(server.CODES_PER_ADDRESS_DAY)] == [200] * server.CODES_PER_ADDRESS_DAY
    assert from_one_64(99).json()["detail"] == "Your network has asked for a lot of codes today. Try again tomorrow."  # a /64's day, as an IPv4 address's


def test_new_addresses_from_one_ipv6_48_get_half_the_days_codes_for_new_addresses_at_most(api, store, outbox, caplog, monkeypatch):
    """BUG-093, with BUG-326's fix: a /48's ceiling was twice the day's mail, so a free tunnel's /48, asking from a few
    of its /64s, could spend every code for new addresses and leave nobody else able to sign up that day. New
    addresses from one /48's /64s together get half of those at most (with the mail plan's allowance as it is set),
    and a /48 at that ceiling is logged, once; a fan with an account there still gets a code."""
    caplog.set_level(logging.WARNING, "sports_follow.limits")
    monkeypatch.setattr(server, "_ceilings_logged", {}, raising=False)
    new = server.MAIL_CODES_PER_DAY - round(server.MAIL_CODES_PER_DAY * server.KEPT_FOR_ACCOUNTS)
    ceiling = min(server.CODES_PER_NETWORK_DAY, new // 2)
    assert (server.MAIL_CODES_PER_DAY, ceiling) == (100, 35)  # the default allowance: seventy for new addresses
    start = lambda email, address: api.call("POST", "/api/auth/email/start", body={"email": email}, address=address)  # noqa: E731
    answers = [start(f"new{i}@example.com", carrier(i)) for i in range(ceiling + 2)]
    assert [a.status_code for a in answers] == [200] * ceiling + [429, 429]
    assert answers[-1].json()["detail"] == "Your network has asked for a lot of codes today. Try again tomorrow."
    assert [r.getMessage() for r in caplog.records if r.name == "sports_follow.limits"] == [
        f"limits: a /48 reached its ceiling for auth-address-day ({hashlib.sha256(b'2405:201:ab00::/48').hexdigest()[:12]})"]
    assert start("someone-else@example.com", carrier(1, "2405:201:ab01")).status_code == 200  # others still sign up
    store.emails[1] = "fan@example.com"  # an account that has stood a day, on the /48 at its ceiling
    assert start("fan@example.com", carrier(ceiling + 5)).status_code == 200


def test_a_subscriber_sharing_the_fans_48_can_neither_end_their_code_nor_shut_them_out_for_the_day(api, store, outbox):
    """BUG-092 on phone networks (BUG-326): wrong codes were counted by the /48 they came from, which a phone network
    shares among its subscribers, so another subscriber who knew a fan's address could end the code in their inbox,
    and with twenty in a day shut them out of signing in until UTC midnight. A /64's wrong codes are its own now, as
    an IPv4 address's are."""
    fan, other = carrier(1), carrier(0x9F)
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=fan).status_code == 200
    code = outbox.pop("fan@example.com")
    assert [verify(api, wrong_to(code), other).status_code for _ in range(server.CODE_TRIES + 1)] == [400] * server.CODE_TRIES + [429]
    assert verify(api, code, fan).status_code == 200  # the fan's code still works where they asked for it

    # The rest of a day's wrong codes from the other subscriber, to codes asked for there (and sent to the fan).
    who = accounts.email_key("fan@example.com")
    for _ in range(server.FAILS_PER_DAY // server.CODE_TRIES - 1):
        for key in [k for k in api.redis.data if k.startswith(("auth-wait:", "auth-email:"))]:
            del api.redis.data[key]  # a minute, and an hour, later
        assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=other).status_code == 200
        right = outbox.pop("fan@example.com")
        assert all(verify(api, wrong_to(right), other).status_code == 400 for _ in range(server.CODE_TRIES))
    api.redis.data.pop(f"auth-wait:{who}")
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=other).status_code == 429
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=fan).status_code == 200  # but not the fan
    assert verify(api, outbox["fan@example.com"], fan).status_code == 200


def test_a_code_that_couldnt_be_sent_can_be_asked_for_again_at_once(api, store, monkeypatch):
    monkeypatch.setattr(mail, "configured", lambda https: True)

    def down(email, code):
        raise mail.MailError("Resend answered 500")

    monkeypatch.setattr(mail, "send_code", down)
    assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}).status_code == 502
    assert not any(k.startswith(("auth-code:", "auth-wait:")) for k in api.redis.data)


def test_an_address_the_mail_service_wont_send_to_is_the_fans_to_change(api, store, monkeypatch, caplog):
    """Resend refuses some addresses (example.com's): a 400 that says so, not a server error (a 5xx counts
    against the API's alert). Its other refusals and failures stay 502s."""
    monkeypatch.setattr(mail, "_key", lambda: "re_test")
    monkeypatch.setattr(mail, "MAIL_FROM", "signin@stann.in")
    answers: list[tuple[int, str]] = []

    def post(url, headers, json):
        status, text = answers.pop(0)
        return httpx.Response(status, text=text)

    monkeypatch.setattr(mail._client, "post", post)
    caplog.set_level(logging.INFO, "sports_follow.mail")
    refused = '{"statusCode":422,"name":"validation_error","message":"Invalid `to` field. Please use our testing email address instead of domains like `example.com`."}'
    answers.append((422, refused))
    answer = api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC)
    assert answer.status_code == 400 and "that address" in answer.json()["detail"]
    assert not any(k.startswith(("auth-code:", "auth-wait:")) for k in api.redis.data)  # another address may follow at once
    ours = [
        (422, '{"statusCode":422,"name":"missing_required_field","message":"Missing `to` field."}'),  # our request's fault
        (422, '{"statusCode":422,"name":"validation_error","message":"The `from` field is invalid."}'),
        (422, "not json"),
        (500, ""),
    ]
    for status, text in ours:
        answers.append((status, text))
        assert api.call("POST", "/api/auth/email/start", body={"email": EMAIL}, address=PUBLIC).status_code == 502
    levels = [r.levelno for r in caplog.records if r.name == "sports_follow.mail"]
    assert levels == [logging.INFO] + [logging.WARNING] * len(ours)  # only the server's own failures are warnings


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
    assert api.queue.jobs[-1][:3] == ("adopt_stragglers", "anon-1", 1)
    assert api.queue.options == {"_defer_by": 60, "trace": {"rid": answer.headers["x-request-id"]}}  # logged under the sign-in's id


def test_what_a_sign_in_brings_shows_in_the_accounts_apps_open_on_other_devices(api, store, outbox):
    """The players, teams and games a device brought as it signed in showed in the account's apps open elsewhere only
    at their stream's next connect (BUG-165): once they're in, the sign-in says so on the account's own channel, which
    each of its open streams answers by sending the list again (tests/test_streams.py)."""
    said: list[tuple[str, list]] = []

    async def publish(channel, message):
        said.append((channel, list(store.adopted)))
        return 1

    api.redis.publish = publish
    sign_in(api, outbox)  # a device with nothing of its own to bring
    assert said == []
    assert sign_in(api, outbox, headers=cookies(fan="anon-1")).status_code == 200
    assert said == [("follows:acct-1", [("anon-1", 1)])]  # said once they're in

    async def down(channel, message):
        raise ConnectionError("Redis is restarting")

    api.redis.publish = down  # the sign-in stands all the same: the apps catch up as their streams connect again
    assert sign_in(api, outbox, headers=cookies(fan="anon-2")).json()["players_added"] == 2


def test_an_accounts_fan_id_is_never_taken_from_a_fan_cookie(api, store, monkeypatch):
    """Ids from before cookies were signed are taken unsigned if they hold something; an account's never is."""
    monkeypatch.setattr(server, "_owns_anything", lambda db, fan: True)
    taken = server._signed(api.call("POST", "/api/me/source", body={}, cookie="acct-AbCdEfGhIjKlMnOpQrStUvWx").cookies["sf_fan"])
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


def test_signing_out_when_already_signed_out_keeps_the_devices_fan(api, store, outbox):
    """BUG-094 (BHA-416): "Sign out" in a tab left from before (signed out in another, or everywhere from the phone)
    gave the device a new, empty anonymous fan, so what it had followed since was gone. Signed in to nothing, it
    keeps its fan; a session cookie that ended elsewhere goes."""
    answer = api.call("POST", "/api/auth/signout", body={}, headers=cookies(fan="anon-1"))
    assert answer.json() == {"signed_in": False} and set_cookies(answer) == {}
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    store.sessions.clear()  # signed out everywhere from another device
    answer = api.call("POST", "/api/auth/signout", body={}, headers=cookies(fan="anon-2", session=token))
    jar = set_cookies(answer)
    assert answer.json() == {"signed_in": False} and jar["sf_session"][1] and "sf_fan" not in jar


ENDINGS = {  # each way a session ends that another device or the admin panel sets off
    "signed out everywhere": lambda api, other, panel: api.call("POST", "/api/auth/signout", body={"everywhere": True}, headers=cookies(session=other)),
    "account deleted": lambda api, other, panel: api.call("DELETE", "/api/me/account", body={"confirm": True}, headers=cookies(session=other)),
    "deleted by an admin": lambda api, other, panel: api.call("DELETE", "/api/admin/users/1", body={"confirm": True}, headers=panel),
    "signed out by an admin": lambda api, other, panel: api.call("POST", "/api/admin/users/1/signout", headers=panel),
    "blocked by an admin": lambda api, other, panel: api.call("PUT", "/api/admin/users/1/block", body={"blocked": True}, headers=panel),
}


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_request_under_way_when_the_session_ends_doesnt_sign_the_device_back_in(api, store, outbox, password, monkeypatch, ending):
    """A request looks its session up, then caches it. If the session ends in between, that late copy
    mustn't keep the device signed in for SESSION_CACHE."""
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    other = set_cookies(sign_in(api, outbox, address="81.2.69.143"))["sf_session"][0]  # another device
    panel = admin_cookie(admin_sign_in(api))
    monkeypatch.setattr(admin, "sign_out", store.end_sessions)
    monkeypatch.setattr(admin, "block", lambda db, account_id, blocked: (store.blocked_ids.add(account_id), store.end_sessions(db, account_id))[1])
    looked_up, pending = accounts.session_for, [ENDINGS[ending]]

    def under_way(db, token_hash):
        found = looked_up(db, token_hash)
        if token_hash == accounts.token_hash(token) and pending:  # read it; before it's cached, the session ends
            assert pending.pop()(api, other, panel).status_code == 200
        return found

    monkeypatch.setattr(accounts, "session_for", under_way)
    during = api.call("POST", "/api/me/source", body={}, headers=cookies(session=token))
    assert not pending and "sf_fan" not in set_cookies(during)  # the request under way was still signed in
    after = api.call("POST", "/api/me/source", body={}, headers=cookies(session=token))
    assert "sf_fan" in set_cookies(after)  # the next one isn't: a new anonymous fan


@pytest.mark.parametrize("ending", [*ENDINGS, "signed out here"])
def test_ending_sessions_tells_the_accounts_open_streams(api, store, outbox, password, monkeypatch, ending):
    """BUG-162, round two: an app left open in front on a device signed out elsewhere was never told: its stream kept
    the account it connected as, and nothing was said when sessions ended. Each way they end says so on the account's
    own channel, once their cached copies say signed out, and each of its open streams asks for its session again
    (tests/test_streams.py): one whose session ended tells its app, which starts over, and ends."""
    token = set_cookies(sign_in(api, outbox))["sf_session"][0]
    other = set_cookies(sign_in(api, outbox, address="81.2.69.143"))["sf_session"][0]  # another device
    panel = admin_cookie(admin_sign_in(api))
    monkeypatch.setattr(admin, "sign_out", store.end_sessions)
    monkeypatch.setattr(admin, "block", lambda db, account_id, blocked: (store.blocked_ids.add(account_id), store.end_sessions(db, account_id))[1])
    told: list[tuple[str, object]] = []

    async def publish(channel, message):
        told.append((channel, api.redis.data.get(f"session:{accounts.token_hash(token)}")))
        return 1

    api.redis.publish = publish
    if ending == "signed out here":  # this device alone: its other tabs (the same cookie) are told too
        assert api.call("POST", "/api/auth/signout", body={}, headers=cookies(session=token)).status_code == 200
    else:
        assert ENDINGS[ending](api, other, panel).status_code == 200
    assert told == [("follows:acct-1", "")]  # said once the session reads as ended


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
    assert not [k for k in api.redis.data if k.startswith(("research:acct-1", "auth-code:", "auth-tries:"))]
    assert {v for k, v in api.redis.data.items() if k.startswith("session:")} == {""}  # cached as signed out
    jar = set_cookies(answer)
    assert jar["sf_session"][1] and server._signed(jar["sf_fan"][0])


# ---------------------------------------------------------------- Continue with Google


def id_token(**claims) -> str:
    """An ID token as Google's token endpoint returns it (its signature isn't checked: see google_signin)."""
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{part({'alg': 'RS256'})}.{part(claims)}.c2ln"


@pytest.fixture
def google(monkeypatch):
    """Google, with a fake token endpoint answering with `google.claims` once the code is right."""
    monkeypatch.setattr(google_signin, "CLIENT_ID", "client-1.apps.googleusercontent.com")
    monkeypatch.setattr(google_signin, "_secret", lambda: "client-secret")
    seen = {}

    def exchange(code, verifier, redirect_uri):
        seen.update(code=code, verifier=verifier, redirect_uri=redirect_uri)
        return {"id_token": id_token(**seen["claims"](seen["nonce"]))}

    monkeypatch.setattr(google_signin, "_exchange", exchange)
    return seen


def good_claims(**changed):
    return lambda nonce: {
        "iss": "https://accounts.google.com", "aud": "client-1.apps.googleusercontent.com", "exp": time.time() + 3600,
        "sub": "google-123", "email": "Fan@Gmail.com", "email_verified": True, "nonce": nonce, **changed,
    }


def go_to_google(api, google, claims=None, fan="anon-g"):
    """Start, then come back with a code. Returns (the start's query, the callback's answer)."""
    started = api.call("GET", "/api/auth/google/start", headers=cookies(fan=fan))
    assert started.status_code == 303
    query = {k: v[0] for k, v in parse_qs(urlsplit(started.headers["location"]).query).items()}
    google.update(nonce=query["nonce"], claims=claims or good_claims())
    remembered = set_cookies(started)["sf_oauth"][0]
    jar = f"sf_oauth={remembered}; " + cookies(fan=fan)["Cookie"]
    return query, api.call("GET", f"/api/auth/google/callback?state={query['state']}&code=the-code", headers={"Cookie": jar})


def test_continue_with_google_signs_in_and_brings_the_device(api, store, google):
    query, back = go_to_google(api, google)
    assert query["client_id"] == "client-1.apps.googleusercontent.com" and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "http://test/api/auth/google/callback" == google["redirect_uri"]
    assert base64.urlsafe_b64encode(hashlib.sha256(google["verifier"].encode()).digest()).decode().rstrip("=") == query["code_challenge"]  # PKCE
    assert back.status_code == 303 and back.headers["location"] == "/account?welcome=2"
    jar = set_cookies(back)
    assert jar["sf_session"][0] and jar["sf_oauth"][1] and jar["sf_fan"][1]
    assert store.emails == {1: "fan@gmail.com"} and store.adopted == [("anon-g", 1)]


def test_the_fan_is_told_how_many_of_the_devices_teams_didnt_fit_in_the_account(api, store, outbox, google):
    """BUG-228: an account follows at most teams.MAX teams, and a sign-in that brought more dropped the rest without a
    word. The answer says how many, and so does Google's way back to the app."""
    store.dropped = 15
    answer = sign_in(api, outbox, headers=cookies(fan="anon-1"))
    assert (answer.json()["players_added"], answer.json()["teams_dropped"]) == (2, 15)
    _, back = go_to_google(api, google)
    assert back.headers["location"] == "/account?welcome=2&teams_dropped=15"
    store.dropped = 0
    _, back = go_to_google(api, google, fan="anon-h")
    assert back.headers["location"] == "/account?welcome=2"


def test_the_fan_is_told_how_many_of_the_devices_players_and_games_didnt_fit_in_the_account(api, store, outbox, google):
    """BUG-328: an account follows at most follows.MAX players and matches.MAX games, and the device's that didn't fit
    went without a word. The answer says how many of each, and so does Google's way back to the app."""
    store.dropped_players, store.dropped_games = 4, 2
    answer = sign_in(api, outbox, headers=cookies(fan="anon-1")).json()
    assert (answer["players_added"], answer["teams_dropped"], answer["players_dropped"], answer["games_dropped"]) == (2, 0, 4, 2)
    _, back = go_to_google(api, google)
    assert back.headers["location"] == "/account?welcome=2&players_dropped=4&games_dropped=2"


@pytest.mark.parametrize("claims", [
    good_claims(aud="someone-elses-app"),
    good_claims(iss="https://evil.example"),
    good_claims(exp=time.time() - 10),
    good_claims(email_verified=False),
    good_claims(nonce="replayed"),  # a token from another sign-in
])
def test_an_id_token_that_isnt_right_signs_nobody_in(api, store, google, claims):
    _, back = go_to_google(api, google, claims=claims)
    assert back.headers["location"] == "/account?error=google"
    assert store.sessions == {} and "sf_session" not in set_cookies(back)


def test_coming_back_without_this_devices_attempt_is_refused(api, store, google):
    started = api.call("GET", "/api/auth/google/start")
    query = {k: v[0] for k, v in parse_qs(urlsplit(started.headers["location"]).query).items()}
    google.update(nonce=query["nonce"], claims=good_claims())
    # Someone else's link, opened on this device: no attempt remembered here.
    assert api.call("GET", f"/api/auth/google/callback?state={query['state']}&code=x").headers["location"] == "/account?error=google"
    # This device's attempt, but another state.
    jar = f"sf_oauth={set_cookies(started)['sf_oauth'][0]}"
    assert api.call("GET", "/api/auth/google/callback?state=forged&code=x", headers={"Cookie": jar}).headers["location"] == "/account?error=google"
    assert store.sessions == {}


def test_an_address_whose_account_has_another_google_account_is_sent_to_the_code(api, store, google):
    go_to_google(api, google)
    _, back = go_to_google(api, google, claims=good_claims(sub="google-999"))
    assert back.headers["location"] == "/account?error=google_linked"


def test_a_blocked_account_signs_in_neither_with_google_nor_with_a_code_from_before(api, store, google, outbox):
    store.emails[1] = "fan@gmail.com"
    assert api.call("POST", "/api/auth/email/start", body={"email": "fan@gmail.com"}, address=PUBLIC).status_code == 200
    store.blocked_ids.add(1)  # an admin blocks it while the code is on its way
    refused = api.call("POST", "/api/auth/email/verify", body={"email": "fan@gmail.com", "code": outbox["fan@gmail.com"]}, address=PUBLIC)
    assert refused.status_code == 403 and refused.json()["detail"] == server.BLOCKED
    assert "sf_session" not in set_cookies(refused)
    _, back = go_to_google(api, google)
    assert back.headers["location"] == "/account?error=blocked" and "sf_session" not in set_cookies(back)
    assert store.sessions == {}


def test_without_a_client_google_is_off(api, monkeypatch):
    monkeypatch.setattr(google_signin, "CLIENT_ID", "")
    assert api.call("GET", "/api/auth/google/start").headers["location"] == "/account?error=google_off"
    assert api.call("GET", "/api/config").json()["google_sign_in"] is False


def test_changing_your_mind_at_google_just_comes_back(api, store, google):
    assert api.call("GET", "/api/auth/google/callback?error=access_denied").headers["location"] == "/account"
