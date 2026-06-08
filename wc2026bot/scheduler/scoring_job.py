"""Score finished matches. Pure logic over a session; testable without I/O.

Idempotent: only predictions with points IS NULL on a FINISHED match with a
recorded score are scored. Re-running does nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from wc2026bot.db.models import Match, MatchStatus, Prediction
from wc2026bot.scoring import score_prediction
from wc2026bot.service import PredictionView
from wc2026bot.db.models import utcnow


@dataclass(frozen=True)
class ScoredNotification:
    telegram_id: int
    view: PredictionView


def score_finished_matches(session: Session) -> list[ScoredNotification]:
    """Score all unscored predictions on finished matches.

    Returns one ScoredNotification per newly-scored prediction, for the
    notifier to DM. Also stamps Match.finished_at the first time a match is
    fully scored.
    """
    stmt = (
        select(Match)
        .where(Match.status == MatchStatus.FINISHED)
        .where(Match.home_score.is_not(None))
        .where(Match.away_score.is_not(None))
    )
    finished = session.scalars(stmt).all()

    notifications: list[ScoredNotification] = []
    for match in finished:
        unscored = session.scalars(
            select(Prediction)
            .where(Prediction.match_id == match.id)
            .where(Prediction.points.is_(None))
        ).all()
        if not unscored:
            continue

        for pred in unscored:
            pred.points = score_prediction(
                pred.pred_home, pred.pred_away, match.home_score, match.away_score
            )
            notifications.append(
                ScoredNotification(
                    telegram_id=pred.player_id,
                    view=PredictionView(
                        match=match,
                        pred_home=pred.pred_home,
                        pred_away=pred.pred_away,
                        points=pred.points,
                    ),
                )
            )

        if match.finished_at is None:
            match.finished_at = utcnow()

    session.commit()
    return notifications
