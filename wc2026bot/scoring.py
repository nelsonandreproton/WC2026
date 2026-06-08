"""Pure scoring logic for the WC2026 prediction bot.

No database or I/O dependencies — every function here is deterministic and
takes only plain values, so it is trivially unit-testable.

Scoring rules (agreed with the user):
- A prediction is a final score, e.g. 2-1.
- Direction (1/X/2): a draw counts as its own direction.
- Correct direction        -> +3 points
- Correct exact score      -> +2 extra points (only if direction also correct,
                              which it always is when the score is exact)
- Otherwise                -> 0 points
- Knockout stage uses the score at 90' (extra time / penalties ignored). That
  decision lives in the caller — this module just scores whatever score it gets.

Champion bet:
- Correct champion -> +100 points, applied only after the final.
"""

from __future__ import annotations

from typing import Final

DIRECTION_POINTS: Final[int] = 3
EXACT_BONUS_POINTS: Final[int] = 2
CHAMPION_BONUS_POINTS: Final[int] = 100


def _direction(home: int, away: int) -> int:
    """Return the sign of the result from the home team's perspective.

    1  -> home win
    0  -> draw
    -1 -> away win
    """
    if home > away:
        return 1
    if home < away:
        return -1
    return 0


def score_prediction(
    pred_home: int,
    pred_away: int,
    real_home: int,
    real_away: int,
) -> int:
    """Score a single match prediction against the real (90') score.

    Returns 0, 3, or 5.
    """
    for value in (pred_home, pred_away, real_home, real_away):
        if value < 0:
            raise ValueError("scores cannot be negative")

    if _direction(pred_home, pred_away) != _direction(real_home, real_away):
        return 0

    points = DIRECTION_POINTS
    if pred_home == real_home and pred_away == real_away:
        points += EXACT_BONUS_POINTS
    return points


def score_champion_bet(predicted_team: str, actual_champion: str) -> int:
    """Score the champion bet. Returns CHAMPION_BONUS_POINTS or 0.

    Comparison is case-insensitive and whitespace-insensitive so that minor
    formatting differences between the user's pick and the provider's team
    name don't wrongly void a correct bet. The caller is responsible for
    feeding canonical team names where possible.
    """
    if not predicted_team or not actual_champion:
        return 0
    if predicted_team.strip().casefold() == actual_champion.strip().casefold():
        return CHAMPION_BONUS_POINTS
    return 0
