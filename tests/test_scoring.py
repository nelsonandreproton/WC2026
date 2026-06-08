"""Tests for the pure scoring logic.

These are the contract for scoring. They were written before the
implementation was trusted, and they encode every agreed rule:
direction +3, exact +2 extra (total 5), draw is its own direction,
champion bet +100.
"""

import pytest

from wc2026bot.scoring import (
    CHAMPION_BONUS_POINTS,
    DIRECTION_POINTS,
    EXACT_BONUS_POINTS,
    score_champion_bet,
    score_prediction,
)


class TestExactScore:
    def test_exact_home_win_gives_five(self):
        assert score_prediction(2, 1, 2, 1) == 5

    def test_exact_away_win_gives_five(self):
        assert score_prediction(0, 3, 0, 3) == 5

    def test_exact_draw_gives_five(self):
        assert score_prediction(1, 1, 1, 1) == 5

    def test_exact_zero_zero_draw_gives_five(self):
        assert score_prediction(0, 0, 0, 0) == 5

    def test_exact_equals_direction_plus_bonus(self):
        assert score_prediction(2, 1, 2, 1) == DIRECTION_POINTS + EXACT_BONUS_POINTS


class TestDirectionOnly:
    def test_right_home_win_wrong_score_gives_three(self):
        # predicted home win, was home win, but different score
        assert score_prediction(2, 0, 3, 1) == 3

    def test_right_away_win_wrong_score_gives_three(self):
        assert score_prediction(0, 1, 1, 2) == 3

    def test_right_draw_wrong_score_gives_three(self):
        # predicted 1-1 draw, was 2-2 draw -> direction correct, not exact
        assert score_prediction(1, 1, 2, 2) == 3

    def test_predicted_draw_different_draw_score(self):
        assert score_prediction(0, 0, 3, 3) == 3


class TestWrongDirection:
    def test_predicted_home_win_was_away_win(self):
        assert score_prediction(2, 1, 0, 1) == 0

    def test_predicted_win_was_draw(self):
        assert score_prediction(2, 1, 1, 1) == 0

    def test_predicted_draw_was_win(self):
        assert score_prediction(1, 1, 2, 0) == 0

    def test_predicted_away_win_was_home_win(self):
        assert score_prediction(0, 2, 3, 0) == 0


class TestKnockoutSemantics:
    """Knockout uses the 90' score. From scoring's point of view that is just
    a normal score; the caller decides which numbers to pass in. A 1-1 at 90'
    that goes to penalties is scored as a draw."""

    def test_draw_at_90_scored_as_draw_even_if_penalties_decided_it(self):
        # real 90' score is 1-1; whoever predicted a 1-1 draw gets exact
        assert score_prediction(1, 1, 1, 1) == 5
        # predicted a home win -> wrong direction at 90'
        assert score_prediction(2, 1, 1, 1) == 0


class TestValidation:
    @pytest.mark.parametrize(
        "args",
        [(-1, 0, 0, 0), (0, -1, 0, 0), (0, 0, -1, 0), (0, 0, 0, -1)],
    )
    def test_negative_scores_rejected(self, args):
        with pytest.raises(ValueError):
            score_prediction(*args)

    def test_large_scores_allowed(self):
        assert score_prediction(7, 0, 7, 0) == 5


class TestChampionBet:
    def test_correct_champion_gives_100(self):
        assert score_champion_bet("Portugal", "Portugal") == CHAMPION_BONUS_POINTS

    def test_wrong_champion_gives_zero(self):
        assert score_champion_bet("Spain", "Portugal") == 0

    def test_case_insensitive(self):
        assert score_champion_bet("portugal", "PORTUGAL") == 100

    def test_whitespace_insensitive(self):
        assert score_champion_bet("  Portugal  ", "Portugal") == 100

    def test_empty_prediction_gives_zero(self):
        assert score_champion_bet("", "Portugal") == 0

    def test_empty_actual_gives_zero(self):
        assert score_champion_bet("Portugal", "") == 0
