"""Tests for scoring finished matches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wc2026bot.db.models import Match, MatchStatus, Player, Prediction
from wc2026bot.scheduler.scoring_job import score_finished_matches

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def player(session, tid, nick):
    p = Player(telegram_id=tid, nickname=nick, nickname_ci=nick.lower())
    session.add(p)
    session.commit()
    return p


def match(session, ext_id=1, status=MatchStatus.FINISHED, hs=2, as_=1):
    m = Match(
        ext_id=ext_id, matchday=1, stage="GROUP_STAGE", round_key="GROUP-1",
        home="Portugal", away="Spain",
        kickoff_utc=NOW, lock_utc=NOW - timedelta(minutes=15), status=status,
        home_score=hs, away_score=as_,
    )
    session.add(m)
    session.commit()
    return m


def prediction(session, tid, mid, ph, pa):
    p = Prediction(player_id=tid, match_id=mid, pred_home=ph, pred_away=pa)
    session.add(p)
    session.commit()
    return p


class TestScoreFinished:
    def test_scores_exact_and_direction(self, db_session):
        player(db_session, 1, "Exact")
        player(db_session, 2, "Dir")
        player(db_session, 3, "Miss")
        m = match(db_session, hs=2, as_=1)
        prediction(db_session, 1, m.id, 2, 1)   # exact -> 5
        prediction(db_session, 2, m.id, 1, 0)   # direction -> 3
        prediction(db_session, 3, m.id, 0, 2)   # wrong -> 0

        notes = score_finished_matches(db_session)
        points = {n.telegram_id: n.view.points for n in notes}
        assert points == {1: 5, 2: 3, 3: 0}

    def test_idempotent(self, db_session):
        player(db_session, 1, "P")
        m = match(db_session)
        prediction(db_session, 1, m.id, 2, 1)
        first = score_finished_matches(db_session)
        second = score_finished_matches(db_session)
        assert len(first) == 1
        assert second == []  # nothing left to score

    def test_stamps_finished_at(self, db_session):
        player(db_session, 1, "P")
        m = match(db_session)
        prediction(db_session, 1, m.id, 0, 0)
        score_finished_matches(db_session)
        assert m.finished_at is not None

    def test_ignores_unfinished_matches(self, db_session):
        player(db_session, 1, "P")
        m = Match(
            ext_id=9, matchday=1, stage="GROUP_STAGE", round_key="GROUP-1",
            home="A", away="B",
            kickoff_utc=NOW, lock_utc=NOW, status=MatchStatus.IN_PLAY,
        )
        db_session.add(m)
        db_session.commit()
        prediction(db_session, 1, m.id, 1, 0)
        assert score_finished_matches(db_session) == []

    def test_persists_points(self, db_session):
        player(db_session, 1, "P")
        m = match(db_session, hs=3, as_=0)
        prediction(db_session, 1, m.id, 3, 0)
        score_finished_matches(db_session)
        pred = db_session.query(Prediction).one()
        assert pred.points == 5
