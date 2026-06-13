"""Tests for the business logic layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from zoneinfo import ZoneInfo

from wc2026bot.db.models import Match, MatchStatus
from wc2026bot.service import (
    ServiceError,
    change_nickname,
    get_champion_bet,
    get_match,
    is_locked,
    matches_today,
    my_predictions,
    open_matches,
    open_matches_for_player,
    register_player,
    set_champion_bet,
    upsert_prediction,
    validate_nickname,
)

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
_LISBON = ZoneInfo("Europe/Lisbon")


def add_match(
    session, ext_id=5001, kickoff=None, status=MatchStatus.SCHEDULED, matchday=1
) -> Match:
    ko = kickoff or (NOW + timedelta(hours=2))
    m = Match(
        ext_id=ext_id,
        matchday=matchday,
        stage="GROUP_STAGE",
        round_key=f"GROUP-{matchday}",
        home="Portugal",
        away="Spain",
        kickoff_utc=ko,
        lock_utc=ko - timedelta(minutes=15),
        status=status,
    )
    session.add(m)
    session.commit()
    return m


class TestNicknameValidation:
    @pytest.mark.parametrize("nick", ["Zico", " a_b_c", "Player_99", "abc"])
    def test_valid(self, nick):
        assert validate_nickname(nick) == nick.strip()

    @pytest.mark.parametrize("nick", ["ab", "a" * 21, "has space", "emoji😀", "bad!"])
    def test_invalid(self, nick):
        with pytest.raises(ServiceError):
            validate_nickname(nick)


class TestRegister:
    def test_register_ok(self, db_session):
        p = register_player(db_session, 1, "Zico")
        assert p.nickname == "Zico"

    def test_duplicate_telegram_id(self, db_session):
        register_player(db_session, 1, "Zico")
        with pytest.raises(ServiceError, match="Já estás registado"):
            register_player(db_session, 1, "Other")

    def test_duplicate_nickname_ci(self, db_session):
        register_player(db_session, 1, "Zico")
        with pytest.raises(ServiceError, match="já está em uso"):
            register_player(db_session, 2, "ZICO")


class TestChangeNickname:
    def test_change_ok(self, db_session):
        register_player(db_session, 1, "Zico")
        p = change_nickname(db_session, 1, "Pele")
        assert p.nickname == "Pele"

    def test_change_to_taken(self, db_session):
        register_player(db_session, 1, "Zico")
        register_player(db_session, 2, "Pele")
        with pytest.raises(ServiceError, match="já está em uso"):
            change_nickname(db_session, 1, "Pele")

    def test_keep_own_nickname_case_change(self, db_session):
        register_player(db_session, 1, "Zico")
        p = change_nickname(db_session, 1, "ZICO")
        assert p.nickname == "ZICO"

    def test_change_unregistered(self, db_session):
        with pytest.raises(ServiceError, match="Não estás registado"):
            change_nickname(db_session, 99, "Ghost")


class TestOpenMatches:
    def test_lists_future_and_orders(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=5))
        add_match(db_session, ext_id=2, kickoff=NOW + timedelta(hours=2))
        result = open_matches(db_session, now=NOW)
        assert [m.ext_id for m in result] == [2, 1]

    def test_excludes_locked(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW + timedelta(minutes=10))  # locked
        add_match(db_session, ext_id=2, kickoff=NOW + timedelta(hours=2))
        result = open_matches(db_session, now=NOW)
        assert [m.ext_id for m in result] == [2]

    def test_excludes_finished(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=2),
                  status=MatchStatus.FINISHED)
        assert open_matches(db_session, now=NOW) == []


class TestOpenMatchesForPlayer:
    def test_only_unpredicted_hides_predicted(self, db_session):
        register_player(db_session, 1, "Zico")
        m1 = add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=2))
        m2 = add_match(db_session, ext_id=2, kickoff=NOW + timedelta(hours=3))
        upsert_prediction(db_session, 1, m1.id, 2, 1, now=NOW)
        views = open_matches_for_player(db_session, 1, only_unpredicted=True, now=NOW)
        assert [v.match.id for v in views] == [m2.id]

    def test_show_all_includes_predicted_with_flag(self, db_session):
        register_player(db_session, 1, "Zico")
        m1 = add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=2))
        m2 = add_match(db_session, ext_id=2, kickoff=NOW + timedelta(hours=3))
        upsert_prediction(db_session, 1, m1.id, 2, 1, now=NOW)
        views = {v.match.id: v for v in
                 open_matches_for_player(db_session, 1, only_unpredicted=False, now=NOW)}
        assert views[m1.id].has_prediction is True
        assert (views[m1.id].pred_home, views[m1.id].pred_away) == (2, 1)
        assert views[m2.id].has_prediction is False

    def test_other_players_predictions_dont_leak(self, db_session):
        register_player(db_session, 1, "Zico")
        register_player(db_session, 2, "Pele")
        m1 = add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=2))
        upsert_prediction(db_session, 2, m1.id, 3, 0, now=NOW)  # Pele predicts
        # Zico hasn't predicted -> match still shows for Zico as unpredicted.
        views = open_matches_for_player(db_session, 1, only_unpredicted=True, now=NOW)
        assert [v.match.id for v in views] == [m1.id]
        assert views[0].has_prediction is False


class TestMatchesToday:
    def test_includes_only_current_day(self, db_session):
        # NOW = 2026-06-11 12:00 UTC (Lisbon = 13:00, same calendar day).
        today = add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=3))
        add_match(db_session, ext_id=2, kickoff=NOW + timedelta(days=1))
        add_match(db_session, ext_id=3, kickoff=NOW - timedelta(days=1))
        result = matches_today(db_session, _LISBON, now=NOW)
        assert [m.ext_id for m in result] == [today.ext_id]

    def test_includes_finished_matches(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW - timedelta(hours=2),
                  status=MatchStatus.FINISHED)
        result = matches_today(db_session, _LISBON, now=NOW)
        assert [m.ext_id for m in result] == [1]

    def test_ordered_by_kickoff(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW + timedelta(hours=5))
        add_match(db_session, ext_id=2, kickoff=NOW + timedelta(hours=2))
        result = matches_today(db_session, _LISBON, now=NOW)
        assert [m.ext_id for m in result] == [2, 1]

    def test_empty_when_no_games(self, db_session):
        add_match(db_session, ext_id=1, kickoff=NOW + timedelta(days=2))
        assert matches_today(db_session, _LISBON, now=NOW) == []


class TestGetMatch:
    def test_found_and_missing(self, db_session):
        m = add_match(db_session)
        assert get_match(db_session, m.id).id == m.id
        assert get_match(db_session, 99999) is None


class TestLock:
    def test_is_locked_true_after_lock(self, db_session):
        m = add_match(db_session, kickoff=NOW + timedelta(minutes=10))
        assert is_locked(m, now=NOW) is True

    def test_is_locked_false_before(self, db_session):
        m = add_match(db_session, kickoff=NOW + timedelta(hours=2))
        assert is_locked(m, now=NOW) is False


class TestPredictions:
    def test_create_prediction(self, db_session):
        register_player(db_session, 1, "Zico")
        m = add_match(db_session)
        pred = upsert_prediction(db_session, 1, m.id, 2, 1, now=NOW)
        assert (pred.pred_home, pred.pred_away) == (2, 1)

    def test_update_prediction(self, db_session):
        register_player(db_session, 1, "Zico")
        m = add_match(db_session)
        upsert_prediction(db_session, 1, m.id, 2, 1, now=NOW)
        pred = upsert_prediction(db_session, 1, m.id, 0, 0, now=NOW)
        assert (pred.pred_home, pred.pred_away) == (0, 0)
        assert len(my_predictions(db_session, 1)) == 1

    def test_locked_match_rejected(self, db_session):
        register_player(db_session, 1, "Zico")
        m = add_match(db_session, kickoff=NOW + timedelta(minutes=10))
        with pytest.raises(ServiceError, match="fechadas"):
            upsert_prediction(db_session, 1, m.id, 1, 0, now=NOW)

    def test_negative_rejected(self, db_session):
        register_player(db_session, 1, "Zico")
        m = add_match(db_session)
        with pytest.raises(ServiceError):
            upsert_prediction(db_session, 1, m.id, -1, 0, now=NOW)

    def test_unregistered_rejected(self, db_session):
        m = add_match(db_session)
        with pytest.raises(ServiceError, match="Não estás registado"):
            upsert_prediction(db_session, 99, m.id, 1, 0, now=NOW)


class TestChampionBet:
    def test_set_and_get(self, db_session):
        register_player(db_session, 1, "Zico")
        lock = NOW + timedelta(days=1)
        set_champion_bet(db_session, 1, "Brazil", lock, now=NOW)
        assert get_champion_bet(db_session, 1).team == "Brazil"

    def test_update_before_lock(self, db_session):
        register_player(db_session, 1, "Zico")
        lock = NOW + timedelta(days=1)
        set_champion_bet(db_session, 1, "Brazil", lock, now=NOW)
        set_champion_bet(db_session, 1, "France", lock, now=NOW)
        assert get_champion_bet(db_session, 1).team == "France"

    def test_team_too_long_rejected(self, db_session):
        register_player(db_session, 1, "Zico")
        lock = NOW + timedelta(days=1)
        with pytest.raises(ServiceError, match="demasiado longo"):
            set_champion_bet(db_session, 1, "x" * 61, lock, now=NOW)

    def test_rejected_after_lock(self, db_session):
        register_player(db_session, 1, "Zico")
        lock = NOW - timedelta(minutes=1)
        with pytest.raises(ServiceError, match="fechadas"):
            set_champion_bet(db_session, 1, "Brazil", lock, now=NOW)

    def test_locked_flag_blocks_update(self, db_session):
        register_player(db_session, 1, "Zico")
        lock = NOW + timedelta(days=1)
        bet = set_champion_bet(db_session, 1, "Brazil", lock, now=NOW)
        bet.locked = True
        db_session.commit()
        with pytest.raises(ServiceError, match="fechadas"):
            set_champion_bet(db_session, 1, "France", lock, now=NOW)
