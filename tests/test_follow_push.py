"""A follow, an unfollow or a level changed on one device reaches the fan's apps open on others (BUG-165): once the
change is saved, the route says so on the fan's own channel (bus.follows_channel), which each of their open streams
answers by sending their list again (tests/test_streams.py). A change refused says nothing. Redis and the database
are fakes."""

from __future__ import annotations

from fastapi.routing import APIRoute

from sports_follow import matches, server, teams

from .test_api import PUBLIC, api  # noqa: F401  (the fixture)

# Every route that changes what the fan follows, or a level: what the fan's list (/api/me/following, a stream's
# first event) carries.
CHANGES = {
    ("POST", "/api/follows"), ("DELETE", "/api/follows/{player_id}"), ("PUT", "/api/follows/{player_id}/alerts"),
    ("POST", "/api/campaigns/{slug}/follow"),
    ("PUT", "/api/teams/{team_id}/follow"), ("DELETE", "/api/teams/{team_id}/follow"), ("PUT", "/api/teams/{team_id}/alerts"),
    ("PUT", "/api/match/{system}/{game}/follow"), ("DELETE", "/api/match/{system}/{game}/follow"),
    ("DELETE", "/api/me/matches/{event_id}"), ("PUT", "/api/me/matches/{event_id}/alerts"),
    ("PUT", "/api/me/sports"),
}


def told(api) -> list[str]:  # noqa: F811
    """The channels the API publishes on, from now."""
    said: list[str] = []

    async def publish(channel, message):
        said.append(channel)
        api.log.append("told")
        return 1

    api.redis.publish = publish
    return said


def test_every_route_that_changes_the_fans_list_tells_their_devices():
    routes = {(m, r.path): r for r in server.app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert CHANGES <= routes.keys()
    assert [key for key in sorted(CHANGES) if server._tell_devices not in [d.call for d in routes[key].dependant.dependencies]] == []


def test_a_change_saved_tells_the_fans_channel_and_one_refused_doesnt(api, monkeypatch):  # noqa: F811
    said = told(api)
    assert api.call("POST", "/api/follows", fan="fan", address=PUBLIC, body={"player_id": 2}).status_code == 404  # no such player
    assert said == []
    assert api.call("POST", "/api/follows", fan="fan", address=PUBLIC, body={"player_id": 1}).status_code == 200
    assert said == ["follows:fan"] and api.log[-3:] == ["commit", "told", "response"]  # saved, then told, then answered
    api.follows.add(("fan", 1))
    assert api.call("PUT", "/api/follows/1/alerts", fan="fan", body={"level": "loud"}).status_code == 400
    assert api.call("PUT", "/api/follows/1/alerts", fan="fan", body={"level": "off"}).status_code == 200
    assert api.call("DELETE", "/api/follows/1", fan="fan").status_code == 200
    # A team's and a game's, sync routes all.
    monkeypatch.setattr(teams, "unfollow", lambda db, fan, team_id: None)
    monkeypatch.setattr(matches, "unfollow_event", lambda db, fan, event_id: None)
    assert api.call("DELETE", "/api/teams/3/follow", fan="fan").status_code == 200
    assert api.call("DELETE", "/api/me/matches/5", fan="fan").status_code == 200
    assert said == ["follows:fan"] * 5
    assert api.call("POST", "/api/follows", fan="another", address=PUBLIC, body={"player_id": 1}).status_code == 200
    assert said[-1] == "follows:another"  # each fan's own


def test_a_change_saved_stands_when_redis_cant_say_so(api):  # noqa: F811
    async def down(channel, message):
        raise ConnectionError("Redis is restarting")

    api.redis.publish = down
    assert api.call("POST", "/api/follows", fan="fan", address=PUBLIC, body={"player_id": 1}).status_code == 200
