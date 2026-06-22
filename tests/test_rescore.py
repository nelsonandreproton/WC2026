"""Tests for the one-off corrective re-scoring of a single match.

Prove-It: the wrong score (5-0) was already scored onto predictions. A plain
re-run of the idempotent scoring job leaves those points stale because
points IS NOT NULL. rescore_match must recompute them against the corrected
score and report per-player deltas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from wc2026bot.db.models import Match, MatchStatus, Player, Prediction
from wc2026bot.rescore import rescore_match
from wc2026bot.scheduler.scoring_job import score_finished_matches

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def _player(session, tid, nick):
    p = Player(telegram_id=tid, nickname=nick, nickname_ci=nick.lower())
    session.add(p)
    session.commit()
    return p


def _match(session, ext_id=1, hs=5, as_=0):
    m = Match(
        ext_id=ext_id, matchday=1, stage="GROUP_STAGE", round_key="GROUP-1",
        home="Spain", away="Saudi Arabia",
        kickoff_utc=NOW, lock_utc=NOW - timedelta(minutes=15),
        status=MatchStatus.FINISHED, home_score=hs, away_score=as_,
    )
    session.add(m)
    session.commit()
    return m


def _prediction(session, tid, mid, ph, pa):
    p = Prediction(player_id=tid, match_id=mid, pred_home=ph, pred_away=pa)
    session.add(p)
    session.commit()
    return p


class TestRescoreMatch:
    def test_idempotent_job_does_not_repair_wrong_points(self, db_session):
        """Prove the bug: re-running the scoring job leaves stale points.

        Match stored 5-0 (wrong). Player predicted 5-0, got 5 (exact). Truth is
        4-0, so the correct score is 3 (direction only). The idempotent job
        will NOT fix this because points is already set.
        """
        _player(db_session, 1, "Believer")
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 5, 0)
        score_finished_matches(db_session)
        pred = db_session.query(Prediction).one()
        assert pred.points == 5  # scored against wrong 5-0

        # Correct the stored score to 4-0 but re-run only the idempotent job.
        m.home_score, m.away_score = 4, 0
        db_session.commit()
        score_finished_matches(db_session)
        db_session.refresh(pred)
        assert pred.points == 5  # STILL wrong — the bug the rescore must fix

    def test_rescore_recomputes_all_predictions(self, db_session):
        """rescore_match repairs points in both directions and updates score."""
        _player(db_session, 1, "OverCredited")   # predicted 5-0
        _player(db_session, 2, "UnderCredited")  # predicted 4-0
        _player(db_session, 3, "Unchanged")      # predicted 0-1 (away win)
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 5, 0)
        _prediction(db_session, 2, m.id, 4, 0)
        _prediction(db_session, 3, m.id, 0, 1)
        # Initial (wrong) scoring against 5-0.
        score_finished_matches(db_session)

        result = rescore_match(db_session, m.id, 4, 0)

        assert (result.old_home, result.old_away) == (5, 0)
        assert (result.new_home, result.new_away) == (4, 0)
        # Stored score corrected.
        db_session.refresh(m)
        assert (m.home_score, m.away_score) == (4, 0)
        # Points recomputed for everyone.
        points = {
            p.player_id: p.points for p in db_session.query(Prediction).all()
        }
        assert points == {1: 3, 2: 5, 3: 0}

    def test_deltas_report_old_and_new(self, db_session):
        _player(db_session, 1, "Over")
        _player(db_session, 2, "Under")
        _player(db_session, 3, "Same")
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 5, 0)
        _prediction(db_session, 2, m.id, 4, 0)
        _prediction(db_session, 3, m.id, 0, 1)
        score_finished_matches(db_session)

        result = rescore_match(db_session, m.id, 4, 0)

        by_id = {d.telegram_id: d for d in result.deltas}
        assert (by_id[1].old_points, by_id[1].new_points) == (5, 3)
        assert (by_id[2].old_points, by_id[2].new_points) == (3, 5)
        assert (by_id[3].old_points, by_id[3].new_points) == (0, 0)
        # changed excludes the unchanged player.
        changed_ids = {d.telegram_id for d in result.changed}
        assert changed_ids == {1, 2}

    def test_rescore_unscored_predictions(self, db_session):
        """Predictions with points still NULL also get scored by a correction."""
        _player(db_session, 1, "Late")
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 4, 0)  # never ran the job -> points NULL
        result = rescore_match(db_session, m.id, 4, 0)
        assert result.deltas[0].old_points is None
        assert result.deltas[0].new_points == 5

    def test_no_predictions_is_safe(self, db_session):
        m = _match(db_session, hs=5, as_=0)
        result = rescore_match(db_session, m.id, 4, 0)
        assert result.deltas == ()
        assert result.changed == []
        db_session.refresh(m)
        assert (m.home_score, m.away_score) == (4, 0)

    def test_unknown_match_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            rescore_match(db_session, 9999, 4, 0)

    def test_negative_score_raises(self, db_session):
        m = _match(db_session, hs=5, as_=0)
        with pytest.raises(ValueError, match="negative"):
            rescore_match(db_session, m.id, -1, 0)

    def test_non_finished_match_raises(self, db_session):
        """Refuse to correct a match that hasn't ended — would corrupt state."""
        m = _match(db_session, hs=5, as_=0)
        m.status = MatchStatus.IN_PLAY
        db_session.commit()
        with pytest.raises(ValueError, match="not FINISHED"):
            rescore_match(db_session, m.id, 4, 0)

    def test_dry_run_computes_deltas_but_writes_nothing(self, db_session):
        """dry_run reports the correct deltas yet leaves the DB untouched."""
        _player(db_session, 1, "Over")
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 5, 0)
        score_finished_matches(db_session)  # scores 5 against wrong 5-0

        result = rescore_match(db_session, m.id, 4, 0, dry_run=True)

        # Deltas reflect what WOULD change.
        assert result.dry_run is True
        assert (result.old_home, result.old_away) == (5, 0)
        assert result.deltas[0].old_points == 5
        assert result.deltas[0].new_points == 3
        # But nothing was persisted: score and points unchanged in the DB.
        m2 = db_session.get(Match, m.id)
        assert (m2.home_score, m2.away_score) == (5, 0)
        assert db_session.query(Prediction).one().points == 5

    def test_dry_run_then_real_run(self, db_session):
        """A dry_run followed by a real run produces the same deltas and commits."""
        _player(db_session, 1, "Over")
        m = _match(db_session, hs=5, as_=0)
        _prediction(db_session, 1, m.id, 5, 0)
        score_finished_matches(db_session)

        preview = rescore_match(db_session, m.id, 4, 0, dry_run=True)
        applied = rescore_match(db_session, m.id, 4, 0)

        assert preview.deltas[0].new_points == applied.deltas[0].new_points == 3
        assert applied.dry_run is False
        assert db_session.get(Match, m.id).home_score == 4
        assert db_session.query(Prediction).one().points == 3
