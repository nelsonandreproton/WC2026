"""Tests for pure formatting + score parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wc2026bot.bot.formatting import (
    fmt_countdown,
    fmt_my_predictions,
    fmt_result_dm,
    fmt_standings,
)
from wc2026bot.standings import StandingRow
from wc2026bot.bot.handlers import parse_score
from wc2026bot.db.models import Match, MatchStatus
from wc2026bot.service import PredictionView

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def a_match(home_score=None, away_score=None) -> Match:
    return Match(
        ext_id=1, matchday=1, stage="GROUP_STAGE", home="Portugal", away="Spain",
        kickoff_utc=NOW, lock_utc=NOW, status=MatchStatus.FINISHED,
        home_score=home_score, away_score=away_score,
    )


class TestParseScore:
    def test_dash(self):
        assert parse_score("2-1") == (2, 1)

    def test_colon(self):
        assert parse_score("2:1") == (2, 1)

    def test_space(self):
        assert parse_score("2 1") == (2, 1)

    def test_whitespace(self):
        assert parse_score("  3-0  ") == (3, 0)

    def test_invalid_letters(self):
        assert parse_score("a-b") is None

    def test_invalid_single(self):
        assert parse_score("2") is None

    def test_invalid_negative(self):
        assert parse_score("-1-2") is None


class TestCountdown:
    def test_minutes(self):
        assert fmt_countdown(NOW + timedelta(minutes=30), NOW) == "fecha em 30m"

    def test_hours(self):
        assert fmt_countdown(NOW + timedelta(hours=2, minutes=15), NOW) == "fecha em 2h 15m"

    def test_days(self):
        assert "d" in fmt_countdown(NOW + timedelta(days=2), NOW)

    def test_closed(self):
        assert fmt_countdown(NOW - timedelta(minutes=1), NOW) == "fechado"


class TestResultDM:
    def test_exact(self):
        v = PredictionView(match=a_match(2, 1), pred_home=2, pred_away=1, points=5)
        msg = fmt_result_dm(v)
        assert "🎯" in msg and "5 pts" in msg and "2-1" in msg

    def test_direction(self):
        v = PredictionView(match=a_match(2, 0), pred_home=1, pred_away=0, points=3)
        msg = fmt_result_dm(v)
        assert "✅" in msg and "3 pts" in msg

    def test_miss(self):
        v = PredictionView(match=a_match(0, 2), pred_home=2, pred_away=1, points=0)
        msg = fmt_result_dm(v)
        assert "❌" in msg and "0 pts" in msg

    def test_no_prediction(self):
        v = PredictionView(match=a_match(1, 1), pred_home=None, pred_away=None, points=None)
        msg = fmt_result_dm(v)
        assert "sem previsão" in msg


class TestStandings:
    def _rows(self):
        return [
            StandingRow(1, 10, "Alice", 12, 2),
            StandingRow(2, 20, "Bob", 8, 1),
            StandingRow(3, 30, "Cara", 5, 0),
        ]

    def test_medals_and_points(self):
        out = fmt_standings(self._rows(), "Classificação")
        assert "🥇 Alice — 12 pts" in out
        assert "🥈 Bob — 8 pts" in out
        assert "🥉 Cara — 5 pts" in out

    def test_highlight_own_row(self):
        out = fmt_standings(self._rows(), "Classificação", highlight_id=20)
        assert "➤ *🥈 Bob — 8 pts*" in out

    def test_rank_number_after_three(self):
        rows = self._rows() + [StandingRow(4, 40, "Dan", 2, 0)]
        out = fmt_standings(rows, "T")
        assert "4. Dan — 2 pts" in out

    def test_round_points_shown(self):
        rows = [StandingRow(1, 10, "Alice", 12, 2, round_points=5)]
        out = fmt_standings(rows, "Ronda", show_round=True)
        assert "(+5 nesta ronda)" in out

    def test_empty(self):
        assert "sem jogadores" in fmt_standings([], "T")

    def test_nickname_markdown_escaped(self):
        rows = [StandingRow(1, 10, "a_b_c", 5, 0)]
        out = fmt_standings(rows, "T")
        # underscores escaped so they don't italicise in Telegram Markdown
        assert "a\\_b\\_c" in out


class TestEscaping:
    def test_team_name_with_markdown_escaped(self):
        # External team names with markdown chars must not break formatting.
        v = PredictionView(match=a_match(2, 1), pred_home=2, pred_away=1, points=5)
        v.match.home = "A*B"
        out = fmt_result_dm(v)
        assert "A\\*B" in out


class TestMyPredictions:
    def test_empty(self):
        assert "Ainda não" in fmt_my_predictions([])

    def test_with_result(self):
        v = PredictionView(match=a_match(2, 1), pred_home=2, pred_away=1, points=5)
        out = fmt_my_predictions([v])
        assert "Portugal vs Spain" in out and "2-1" in out
