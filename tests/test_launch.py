"""Launch settings: which sources run (SPORTS_FOLLOW_SOURCES, licensed ones with a token) and the Lichess token."""

from __future__ import annotations

from sports_follow.adapters import ALL, lichess_chess, liquipedia, pandascore, pick, pubg, sportmonks
from sports_follow.adapters.espn_cricket import EspnCricket
from sports_follow.adapters.espn_soccer import EspnSoccer
from sports_follow.adapters.sportmonks_cricket import SportmonksCricket
from sports_follow.adapters.lichess_chess import LichessChess
from sports_follow.adapters.sportmonks_football import SportmonksFootball
from sports_follow.config import user_agent
from sports_follow.notify import vapid_subject


def test_every_source_runs_unless_the_build_lists_its_licensed_ones():
    everything = (SportmonksFootball(), EspnSoccer(), LichessChess())
    assert list(pick(everything, set())) == ["sportmonks_football", "espn_soccer", "lichess_chess"]
    licensed = pick(everything, {"lichess_chess", "sportmonks_football", "sportmonks_tennis"})
    assert list(licensed) == ["sportmonks_football", "lichess_chess"]  # names of sources not built yet are ignored, not errors


def test_licensed_sources_run_only_with_their_token():
    systems = [a.system for a in ALL]
    assert [s for s in systems if not s.startswith(("sportmonks_", "pandascore_", "liquipedia_", "pubg_"))] == ["espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis", "lichess_chess"]
    assert ({"sportmonks_football", "sportmonks_cricket"} <= set(systems)) == bool(sportmonks.token())
    assert ("pandascore_esports" in systems) == bool(pandascore.token())
    assert ("liquipedia_esports" in systems) == bool(liquipedia.token())
    assert ("pubg_esports" in systems) == bool(pubg.token())


def test_football_prefers_sportmonks_and_cricket_keeps_espn_until_the_plan_covers_it():
    everything = pick((SportmonksFootball(), EspnSoccer(), EspnCricket(), SportmonksCricket()), set())
    first = lambda sport, adapters: next(a.system for a in adapters.values() if a.sport == sport)  # noqa: E731  as for_sport()
    assert (first("football", everything), first("cricket", everything)) == ("sportmonks_football", "espn_cricket")
    public = pick((SportmonksFootball(), EspnSoccer(), EspnCricket(), SportmonksCricket()), {"sportmonks_football", "sportmonks_cricket"})
    assert first("cricket", public) == "sportmonks_cricket"


def test_the_lichess_token_goes_to_lichess_only(monkeypatch):
    monkeypatch.setenv("SPORTS_FOLLOW_LICHESS_TOKEN", "lip_example")
    lichess_chess.token.cache_clear()
    try:
        assert lichess_chess._headers("https://lichess.org/api/broadcast/-/-/abc", text=False) == {"authorization": "Bearer lip_example"}
        assert lichess_chess._headers("https://lichess.org/fide/1503014", text=True) == {"accept": "*/*", "authorization": "Bearer lip_example"}
        assert lichess_chess._headers("https://example.com/api", text=False) == {}
    finally:
        lichess_chess.token.cache_clear()


def test_no_token_means_anonymous_requests(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_FOLLOW_LICHESS_TOKEN", raising=False)
    monkeypatch.setattr(lichess_chess, "TOKEN_FILE", tmp_path / "missing")
    lichess_chess.token.cache_clear()
    try:
        assert lichess_chess._headers("https://lichess.org/api/broadcast", text=False) == {}
    finally:
        lichess_chess.token.cache_clear()


def test_sources_and_push_services_are_told_the_site_never_the_private_repo():
    assert user_agent("https://app.example.com", "hello@example.com") == "sports-follow/0.1 (https://app.example.com; hello@example.com)"
    assert user_agent("", "hello@example.com") == "sports-follow/0.1 (hello@example.com)"
    assert user_agent("", "") == "sports-follow/0.1"
    assert vapid_subject("https://app.example.com") == "https://app.example.com"
    assert vapid_subject("") == vapid_subject("http://localhost:8421") == "https://localhost"  # push wants https or mailto
