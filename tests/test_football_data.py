"""Tests for the football-data.org parser — no network, hand-built JSON."""

from __future__ import annotations

from datetime import UTC, datetime

from wc2026bot.providers.football_data import (
    _extract_90_score,
    _parse_iso_utc,
    _to_dto,
)


def test_parse_iso_utc():
    assert _parse_iso_utc("2026-06-11T19:00:00Z") == datetime(
        2026, 6, 11, 19, 0, tzinfo=UTC
    )


class TestExtract90Score:
    def test_prefers_regular_time_for_knockout(self):
        # Knockout: 1-1 at 90', decided 3-1 after extra time. We want 1-1.
        score = {
            "regularTime": {"home": 1, "away": 1},
            "fullTime": {"home": 3, "away": 1},
        }
        assert _extract_90_score(score) == (1, 1)

    def test_falls_back_to_full_time_for_group(self):
        score = {"regularTime": {"home": None, "away": None},
                 "fullTime": {"home": 2, "away": 0}}
        assert _extract_90_score(score) == (2, 0)

    def test_no_score_yet(self):
        score = {"regularTime": {"home": None, "away": None},
                 "fullTime": {"home": None, "away": None}}
        assert _extract_90_score(score) == (None, None)

    def test_missing_keys(self):
        assert _extract_90_score({}) == (None, None)


class TestToDto:
    def test_full_match(self):
        match = {
            "id": 12345,
            "matchday": 1,
            "stage": "GROUP_STAGE",
            "homeTeam": {"name": "Portugal"},
            "awayTeam": {"name": "Spain"},
            "utcDate": "2026-06-11T19:00:00Z",
            "status": "FINISHED",
            "score": {"fullTime": {"home": 2, "away": 1}},
        }
        d = _to_dto(match)
        assert d.ext_id == 12345
        assert d.home == "Portugal"
        assert d.away == "Spain"
        assert d.status == "FINISHED"
        assert (d.home_score, d.away_score) == (2, 1)
        assert d.kickoff_utc == datetime(2026, 6, 11, 19, 0, tzinfo=UTC)

    def test_tbd_knockout_match(self):
        match = {
            "id": 999,
            "matchday": 4,
            "stage": "LAST_16",
            "homeTeam": {"name": None},
            "awayTeam": {"name": None},
            "utcDate": "2026-07-04T19:00:00Z",
            "status": "SCHEDULED",
            "score": {},
        }
        d = _to_dto(match)
        assert d.home == "TBD"
        assert d.away == "TBD"
        assert (d.home_score, d.away_score) == (None, None)
