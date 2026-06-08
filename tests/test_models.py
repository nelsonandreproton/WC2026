"""Tests for the ORM models and their constraints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from wc2026bot.db.models import (
    ChampionBet,
    Match,
    MatchStatus,
    Player,
    Prediction,
    Round,
)


def _kickoff(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def make_player(session, telegram_id=1, nickname="Zico") -> Player:
    p = Player(telegram_id=telegram_id, nickname=nickname, nickname_ci=nickname.lower())
    session.add(p)
    session.commit()
    return p


def make_match(session, ext_id=1001, matchday=1) -> Match:
    ko = _kickoff()
    m = Match(
        ext_id=ext_id,
        matchday=matchday,
        stage="GROUP_STAGE",
        round_key=f"GROUP-{matchday}",
        home="Portugal",
        away="Spain",
        kickoff_utc=ko,
        lock_utc=ko - timedelta(minutes=15),
    )
    session.add(m)
    session.commit()
    return m


class TestPlayer:
    def test_create_player(self, db_session):
        p = make_player(db_session)
        assert p.is_active is True
        assert p.joined_at is not None

    def test_nickname_ci_unique(self, db_session):
        make_player(db_session, telegram_id=1, nickname="Zico")
        dup = Player(telegram_id=2, nickname="ZICO", nickname_ci="zico")
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_same_display_different_ci_not_enforced_here(self, db_session):
        # ci uniqueness is on nickname_ci; two players may share display only if
        # ci differs (won't happen in practice, but the constraint is on _ci).
        make_player(db_session, telegram_id=1, nickname="Ze")
        p2 = Player(telegram_id=2, nickname="Ze", nickname_ci="ze2")
        db_session.add(p2)
        db_session.commit()  # no raise


class TestMatch:
    def test_ext_id_unique(self, db_session):
        make_match(db_session, ext_id=1001)
        dup = Match(
            ext_id=1001,
            matchday=1,
            stage="GROUP_STAGE",
            round_key="GROUP-1",
            home="A",
            away="B",
            kickoff_utc=_kickoff(),
            lock_utc=_kickoff(),
        )
        db_session.add(dup)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_default_status_scheduled(self, db_session):
        m = make_match(db_session)
        assert m.status == MatchStatus.SCHEDULED

    def test_scores_both_or_neither_constraint(self, db_session):
        m = make_match(db_session)
        m.home_score = 2  # away_score still NULL -> violates check
        with pytest.raises(IntegrityError):
            db_session.commit()


class TestPrediction:
    def test_create_prediction(self, db_session):
        p = make_player(db_session)
        m = make_match(db_session)
        pred = Prediction(
            player_id=p.telegram_id, match_id=m.id, pred_home=2, pred_away=1
        )
        db_session.add(pred)
        db_session.commit()
        assert pred.points is None
        assert pred.created_at is not None

    def test_one_prediction_per_player_match(self, db_session):
        p = make_player(db_session)
        m = make_match(db_session)
        db_session.add(
            Prediction(player_id=p.telegram_id, match_id=m.id, pred_home=2, pred_away=1)
        )
        db_session.commit()
        db_session.add(
            Prediction(player_id=p.telegram_id, match_id=m.id, pred_home=0, pred_away=0)
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_negative_prediction_rejected(self, db_session):
        p = make_player(db_session)
        m = make_match(db_session)
        db_session.add(
            Prediction(
                player_id=p.telegram_id, match_id=m.id, pred_home=-1, pred_away=0
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_cascade_delete_player_removes_predictions(self, db_session):
        p = make_player(db_session)
        m = make_match(db_session)
        db_session.add(
            Prediction(player_id=p.telegram_id, match_id=m.id, pred_home=1, pred_away=1)
        )
        db_session.commit()
        db_session.delete(p)
        db_session.commit()
        assert db_session.query(Prediction).count() == 0


class TestChampionBet:
    def test_one_bet_per_player(self, db_session):
        p = make_player(db_session)
        db_session.add(ChampionBet(player_id=p.telegram_id, team="Brazil"))
        db_session.commit()
        # Second bet for same player -> PK violation
        db_session.add(ChampionBet(player_id=p.telegram_id, team="France"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_defaults(self, db_session):
        p = make_player(db_session)
        bet = ChampionBet(player_id=p.telegram_id, team="Argentina")
        db_session.add(bet)
        db_session.commit()
        assert bet.locked is False
        assert bet.points is None


class TestRound:
    def test_create_round(self, db_session):
        db_session.add(Round(round_key="GROUP-1", name="Jornada 1", sort_order=1))
        db_session.commit()
        r = db_session.get(Round, "GROUP-1")
        assert r.name == "Jornada 1"
        assert r.table_sent_at is None
