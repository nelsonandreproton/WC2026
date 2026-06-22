"""One-off corrective re-scoring for a single match.

Use when a match's stored 90' score was wrong (e.g. a provider glitch recorded
5-0 instead of 4-0) and predictions were *already* scored against the bad score.

The normal scoring job (scheduler.scoring_job.score_finished_matches) is
deliberately idempotent: it only scores predictions where points IS NULL. That
makes it safe to re-run, but it also means it will NOT repair points that were
computed against a wrong score. This module does the repair explicitly.

Pure logic over a session — no Telegram I/O. The returned deltas drive a
separate, explicit correction message; this function never re-fires the normal
post-match result broadcast.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from wc2026bot.db.models import Match, MatchStatus, Prediction
from wc2026bot.scoring import score_prediction


@dataclass(frozen=True)
class RescoreDelta:
    """One player's point change from a re-score."""

    telegram_id: int
    pred_home: int
    pred_away: int
    old_points: int | None
    new_points: int


@dataclass(frozen=True)
class RescoreResult:
    match_id: int
    old_home: int | None
    old_away: int | None
    new_home: int
    new_away: int
    # One entry per prediction on the match (changed or not). Tuple so the
    # frozen result is genuinely immutable (a list field would still be mutable).
    deltas: tuple[RescoreDelta, ...]
    # True when nothing was written (dry_run): caller can inspect deltas first.
    dry_run: bool = False

    @property
    def changed(self) -> list[RescoreDelta]:
        """Only the players whose points actually changed."""
        return [d for d in self.deltas if d.old_points != d.new_points]


def rescore_match(
    session: Session,
    match_id: int,
    new_home: int,
    new_away: int,
    dry_run: bool = False,
) -> RescoreResult:
    """Correct a match's 90' score and recompute points for ALL its predictions.

    Unlike the scheduler job, this recomputes every prediction's points, not
    just the unscored ones — that is the whole point of a correction.

    Returns a RescoreResult capturing the old/new score and every per-player
    old->new point delta, so the caller can DM only the affected players.

    dry_run=True computes and returns the deltas WITHOUT writing anything
    (expires the session to discard the in-memory mutations) — use it on a live
    DB to verify the deltas before committing the real correction.

    Raises ValueError if the match does not exist, the new score is negative,
    or the match is not FINISHED (correcting an unfinished match would corrupt
    tournament state).
    """
    if new_home < 0 or new_away < 0:
        raise ValueError("scores cannot be negative")

    match = session.get(Match, match_id)
    if match is None:
        raise ValueError(f"match {match_id} not found")
    if match.status != MatchStatus.FINISHED:
        raise ValueError(
            f"match {match_id} is not FINISHED (status={match.status.value})"
        )

    old_home, old_away = match.home_score, match.away_score
    match.home_score = new_home
    match.away_score = new_away

    preds = session.scalars(
        select(Prediction).where(Prediction.match_id == match_id)
    ).all()

    deltas: list[RescoreDelta] = []
    for pred in preds:
        old_points = pred.points
        new_points = score_prediction(
            pred.pred_home, pred.pred_away, new_home, new_away
        )
        pred.points = new_points
        deltas.append(
            RescoreDelta(
                telegram_id=pred.player_id,
                pred_home=pred.pred_home,
                pred_away=pred.pred_away,
                old_points=old_points,
                new_points=new_points,
            )
        )

    if dry_run:
        # Roll back the pending (uncommitted) mutations; nothing reaches the DB.
        session.rollback()
    else:
        session.commit()

    return RescoreResult(
        match_id=match_id,
        old_home=old_home,
        old_away=old_away,
        new_home=new_home,
        new_away=new_away,
        deltas=tuple(deltas),
        dry_run=dry_run,
    )
