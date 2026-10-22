"""GET and PUT /api/me/sports (BHA-653): what a request may say, whose list it is, and that the fan's other devices
hear of a change. Redis and the database are fakes; what the list does to real rows is tests/test_fansports_db.py."""

from __future__ import annotations

import pytest

from sports_follow import fansports

from .test_api import api  # noqa: F401  (the fixture)
from .test_follow_push import told


@pytest.fixture
def lists(monkeypatch):
    """Each fan's sports, as fansports keeps them, without its SQL: the fake database can't run it."""
    kept: dict[str, list[str]] = {}

    def replace(db, fan, keys, shown=None):
        kept[fan] = [k for k in dict.fromkeys(keys) if k in fansports.offered()]
        return kept[fan]

    monkeypatch.setattr(fansports, "replace", replace)
    monkeypatch.setattr(fansports, "mine", lambda db, fan, shown=None: kept.get(fan, []))
    return kept


def test_a_fans_sports_are_saved_in_their_order_and_read_back(api, lists):  # noqa: F811
    assert api.call("GET", "/api/me/sports", fan="fan").json() == {"sports": []}
    saved = api.call("PUT", "/api/me/sports", fan="fan", body={"sports": ["chess", "golf", "cricket", "chess"]})
    assert saved.status_code == 200 and saved.json() == {"sports": ["chess", "cricket"]}  # golf isn't a sport here
    assert api.call("GET", "/api/me/sports", fan="fan").json() == {"sports": ["chess", "cricket"]}
    assert api.call("GET", "/api/me/sports", fan="another").json() == {"sports": []}


def test_a_new_device_saving_its_welcome_picks_keeps_a_fan_of_its_own(api, lists):  # noqa: F811
    """The welcome's Next is often a device's first write: the fan it saves under is the one its cookie holds after."""
    saved = api.call("PUT", "/api/me/sports", body={"sports": ["football"]})
    assert saved.status_code == 200 and "sf_fan=" in saved.headers.get("set-cookie", "")
    assert list(lists.values()) == [["football"]]


@pytest.mark.parametrize("body", [{}, {"sports": "chess"}, {"sports": [1]}, {"sports": [None]}, {"sports": ["chess"] * (fansports.GIVEN_MOST + 1)}])
def test_anything_but_a_list_of_sport_keys_is_refused_and_tells_nobody(api, lists, body):  # noqa: F811
    said = told(api)
    answer = api.call("PUT", "/api/me/sports", fan="fan", body=body)
    assert answer.status_code == 400 and answer.json()["detail"] == "sports must be a list of sport keys"
    assert lists == {} and said == []


def test_a_change_tells_the_fans_other_devices_once_saved(api, lists):  # noqa: F811
    said = told(api)
    assert api.call("PUT", "/api/me/sports", fan="fan", body={"sports": ["cricket"]}).status_code == 200
    assert said == ["follows:fan"] and api.log[:3] == ["commit", "told", "response"]  # saved, then told, then answered
