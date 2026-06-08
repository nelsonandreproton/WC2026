"""Tests for idempotent fixture sync."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from wc2026bot.db.models import Match, MatchStatus, Round
from wc2026bot.providers.base import FixtureDTO
from wc2026bot.providers.sync import sync_fixtures


def dto(
    ext_id=2001,
    matchday=1,
    stage="GROUP_STAGE",
    home="Portugal",
    away="Spain",
    kickoff=None,
    status="SCHEDULED",
    home_score=None,
    away_score=None,
) -> FixtureDTO:
    return FixtureDTO(
        ext_id=ext_id,
        matchday=matchday,
        stage=stage,
        home=home,
        away=away,
        kickoff_utc=kickoff or datetime(2026, 6, 11, 19, 0, tzinfo=UTC),
        status=status,
        home_score=home_score,
        away_score=away_score,
    )


class TestSyncCreate:
    def test_creates_match_and_round(self, db_session):
        res = sync_fixtures(db_session, [dto()])
        assert res == {"created": 1, "updated": 0, "rounds": 1}
        m = db_session.scalars(select(Match)).one()
        assert m.home == "Portugal"
        assert db_session.scalars(select(Round)).one().name == "Jornada 1"

    def test_lock_computed_15_min_before(self, db_session):
        ko = datetime(2026, 6, 11, 19, 0, tzinfo=UTC)
        sync_fixtures(db_session, [dto(kickoff=ko)])
        m = db_session.scalars(select(Match)).one()
        assert m.lock_utc == ko - timedelta(minutes=15)

    def test_custom_lock_minutes(self, db_session):
        ko = datetime(2026, 6, 11, 19, 0, tzinfo=UTC)
        sync_fixtures(db_session, [dto(kickoff=ko)], lock_minutes=30)
        m = db_session.scalars(select(Match)).one()
        assert m.lock_utc == ko - timedelta(minutes=30)


class TestSyncIdempotent:
    def test_resync_same_data_is_noop(self, db_session):
        sync_fixtures(db_session, [dto()])
        res = sync_fixtures(db_session, [dto()])
        assert res["created"] == 0
        assert res["updated"] == 1
        assert db_session.scalars(select(Match)).all().__len__() == 1

    def test_kickoff_change_recomputes_lock(self, db_session):
        sync_fixtures(db_session, [dto()])
        new_ko = datetime(2026, 6, 11, 21, 0, tzinfo=UTC)
        sync_fixtures(db_session, [dto(kickoff=new_ko)])
        m = db_session.scalars(select(Match)).one()
        assert m.kickoff_utc == new_ko
        assert m.lock_utc == new_ko - timedelta(minutes=15)

    def test_knockout_team_fill_in_updates(self, db_session):
        # Knockout match created with TBD teams, later resolved.
        sync_fixtures(db_session, [dto(ext_id=3001, stage="LAST_16",
                                       matchday=4, home="TBD", away="TBD")])
        sync_fixtures(db_session, [dto(ext_id=3001, stage="LAST_16",
                                       matchday=4, home="Brazil", away="France")])
        m = db_session.scalars(select(Match)).one()
        assert (m.home, m.away) == ("Brazil", "France")


class TestSyncScores:
    def test_status_and_score_updated(self, db_session):
        sync_fixtures(db_session, [dto()])
        sync_fixtures(
            db_session,
            [dto(status="FINISHED", home_score=2, away_score=1)],
        )
        m = db_session.scalars(select(Match)).one()
        assert m.status == MatchStatus.FINISHED
        assert (m.home_score, m.away_score) == (2, 1)

    def test_null_score_never_wipes_stored_score(self, db_session):
        # Score recorded, then a later sync returns no score (provider blip).
        sync_fixtures(db_session, [dto(status="FINISHED",
                                       home_score=3, away_score=0)])
        sync_fixtures(db_session, [dto(status="FINISHED",
                                       home_score=None, away_score=None)])
        m = db_session.scalars(select(Match)).one()
        assert (m.home_score, m.away_score) == (3, 0)


class TestRoundNaming:
    def test_knockout_rounds_distinct_despite_matchday_zero(self, db_session):
        # Reality: football-data returns matchday=0/null for knockouts. Round
        # identity must come from stage, so each knockout stage is its own round.
        sync_fixtures(db_session, [
            dto(ext_id=4001, matchday=0, stage="LAST_16"),
            dto(ext_id=4002, matchday=0, stage="QUARTER_FINALS"),
            dto(ext_id=4003, matchday=0, stage="FINAL"),
        ])
        rounds = {r.round_key: r.name for r in db_session.scalars(select(Round)).all()}
        assert rounds["LAST_16"] == "Oitavos de Final"
        assert rounds["QUARTER_FINALS"] == "Quartos de Final"
        assert rounds["FINAL"] == "Final"

    def test_group_rounds_keyed_by_matchday(self, db_session):
        sync_fixtures(db_session, [
            dto(ext_id=5001, matchday=1, stage="GROUP_STAGE"),
            dto(ext_id=5002, matchday=2, stage="GROUP_STAGE"),
        ])
        rounds = {r.round_key: r.name for r in db_session.scalars(select(Round)).all()}
        assert rounds["GROUP-1"] == "Jornada 1"
        assert rounds["GROUP-2"] == "Jornada 2"
