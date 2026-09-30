"""Launch settings: which sources run (SPORTS_FOLLOW_SOURCES, licensed ones with a token) and the Lichess token."""

from __future__ import annotations

from sports_follow.adapters import ALL, lichess_chess, pick, sportmonks
from sports_follow.adapters.espn_cricket import EspnCricket
from sports_follow.adapters.espn_soccer import EspnSoccer
from sports_follow.adapters.sportmonks_cricket import SportmonksCricket
from sports_follow.adapters.lichess_chess import LichessChess
from sports_follow.adapters.sportmonks_football import SportmonksFootball


def test_every_source_runs_unless_the_build_lists_its_licensed_ones():
    everything = (SportmonksFootball(), EspnSoccer(), LichessChess())
    assert list(pick(everything, set())) == ["sportmonks_football", "espn_soccer", "lichess_chess"]
    licensed = pick(everything, {"lichess_chess", "sportmonks_football", "sportmonks_tennis"})
    assert list(licensed) == ["sportmonks_football", "lichess_chess"]  # names of sources not built yet are ignored, not errors


def test_licensed_sources_run_only_with_their_token():
    systems = [a.system for a in ALL]
    assert [s for s in systems if not s.startswith("sportmonks_")] == ["espn_soccer", "espn_cricket", "espn_basketball", "espn_tennis", "lichess_chess"]
    assert ({"sportmonks_football", "sportmonks_cricket"} <= set(systems)) == bool(sportmonks.token())


def test_football_prefers_sportmonks_and_cricket_keeps_espn_until_the_plan_covers_it():
    everything = pick((SportmonksFootball(), EspnSoccer(), EspnCricket(), SportmonksCricket()), set())
    first = lambda sport, adapters: next(a.system for a in adapters.values() if sport in a.sports)  # noqa: E731  as for_sport()
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
