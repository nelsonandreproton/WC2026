"""Tests for standings, round completion and champion bonus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wc2026bot.db.models import (
    ChampionBet,
    Match,
    MatchStatus,
    Player,
    Prediction,
)
from wc2026bot.standings import (
    apply_champion_bonus,
    champion_from_final,
    compute_standings,
    is_round_complete,
    round_points,
)

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def player(session, tid, nick):
    p = Player(telegram_id=tid, nickname=nick, nickname_ci=nick.lower())
    session.add(p)
    session.commit()
    return p


def match(session, ext_id, matchday=1, status=MatchStatus.FINISHED, round_key=None):
    m = Match(
        ext_id=ext_id, matchday=matchday, stage="GROUP_STAGE",
        round_key=round_key or f"GROUP-{matchday}", home="A", away="B",
        kickoff_utc=NOW, lock_utc=NOW - timedelta(minutes=15), status=status,
        home_score=2, away_score=1,
    )
    session.add(m)
    session.commit()
    return m


def pred(session, tid, mid, points):
    p = Prediction(player_id=tid, match_id=mid, pred_home=2, pred_away=1, points=points)
    session.add(p)
    session.commit()
    return p


class TestComputeStandings:
    def test_orders_by_total_desc(self, db_session):
        player(db_session, 1, "Low")
        player(db_session, 2, "High")
        m = match(db_session, 1)
        pred(db_session, 1, m.id, 3)
        pred(db_session, 2, m.id, 5)
        rows = compute_standings(db_session)
        assert [r.nickname for r in rows] == ["High", "Low"]
        assert rows[0].rank == 1 and rows[0].total_points == 5

    def test_tiebreak_by_exact_hits(self, db_session):
        player(db_session, 1, "Aaa")
        player(db_session, 2, "Bbb")
        m1 = match(db_session, 1)
        m2 = match(db_session, 2, matchday=2)
        # both total 5, but player 2 got it as one exact (5), player 1 as 3+ ...
        # make both total 5: p1 = 5 exact, p2 = 3+? -> give p1 one exact(5),
        # p2 two preds 3+? no. Keep simple: p1 5 via exact, p2 5 via exact too,
        # tiebreak then nickname. Test exact-hits tiebreak explicitly:
        pred(db_session, 1, m1.id, 5)   # 1 exact hit, total 5
        pred(db_session, 2, m1.id, 3)   # total 3
        pred(db_session, 2, m2.id, 2)   # total 5, 0 exact hits (points=2 hypothetical)
        rows = compute_standings(db_session)
        # both totals: p1=5 (1 exact), p2=5 (0 exact) -> p1 first
        assert rows[0].nickname == "Aaa"

    def test_includes_champion_bonus(self, db_session):
        player(db_session, 1, "Champ")
        m = match(db_session, 1)
        pred(db_session, 1, m.id, 3)
        bet = ChampionBet(player_id=1, team="Brazil", points=100, locked=True)
        db_session.add(bet)
        db_session.commit()
        rows = compute_standings(db_session)
        assert rows[0].total_points == 103

    def test_unscored_predictions_ignored(self, db_session):
        player(db_session, 1, "P")
        m = match(db_session, 1, status=MatchStatus.SCHEDULED)
        pred(db_session, 1, m.id, None)
        rows = compute_standings(db_session)
        assert rows[0].total_points == 0


class TestRoundCompletion:
    def test_complete_when_all_finished(self, db_session):
        match(db_session, 1, matchday=1)
        match(db_session, 2, matchday=1)
        assert is_round_complete(db_session, "GROUP-1") is True

    def test_incomplete_with_unfinished(self, db_session):
        match(db_session, 1, matchday=1)
        match(db_session, 2, matchday=1, status=MatchStatus.IN_PLAY)
        assert is_round_complete(db_session, "GROUP-1") is False

    def test_empty_round_not_complete(self, db_session):
        assert is_round_complete(db_session, "GROUP-99") is False

    def test_cancelled_match_does_not_block_round(self, db_session):
        # A cancelled game must not freeze the round's standings forever.
        match(db_session, 1, matchday=1)
        match(db_session, 2, matchday=1, status=MatchStatus.CANCELLED)
        assert is_round_complete(db_session, "GROUP-1") is True

    def test_postponed_match_does_not_block_round(self, db_session):
        match(db_session, 1, matchday=1)
        match(db_session, 2, matchday=1, status=MatchStatus.POSTPONED)
        assert is_round_complete(db_session, "GROUP-1") is True

    def test_in_play_still_blocks_round(self, db_session):
        match(db_session, 1, matchday=1)
        match(db_session, 2, matchday=1, status=MatchStatus.IN_PLAY)
        assert is_round_complete(db_session, "GROUP-1") is False

    def test_knockout_rounds_are_separate(self, db_session):
        # LAST_16 and QUARTER_FINALS both have matchday 0 but distinct keys.
        match(db_session, 1, matchday=0, round_key="LAST_16")
        match(db_session, 2, matchday=0, round_key="QUARTER_FINALS",
              status=MatchStatus.IN_PLAY)
        assert is_round_complete(db_session, "LAST_16") is True
        assert is_round_complete(db_session, "QUARTER_FINALS") is False


class TestRoundPoints:
    def test_per_round_sum(self, db_session):
        player(db_session, 1, "P")
        m1 = match(db_session, 1, matchday=1)
        m2 = match(db_session, 2, matchday=2)
        pred(db_session, 1, m1.id, 5)
        pred(db_session, 1, m2.id, 3)
        assert round_points(db_session, "GROUP-1") == {1: 5}
        assert round_points(db_session, "GROUP-2") == {1: 3}


class TestChampionBonus:
    def test_awards_winners(self, db_session):
        player(db_session, 1, "Right")
        player(db_session, 2, "Wrong")
        db_session.add(ChampionBet(player_id=1, team="Brazil"))
        db_session.add(ChampionBet(player_id=2, team="Spain"))
        db_session.commit()
        winners = apply_champion_bonus(db_session, "Brazil")
        assert winners == [1]
        bet1 = db_session.get(ChampionBet, 1)
        bet2 = db_session.get(ChampionBet, 2)
        assert bet1.points == 100 and bet1.locked is True
        assert bet2.points == 0 and bet2.locked is True

    def test_idempotent(self, db_session):
        player(db_session, 1, "Right")
        db_session.add(ChampionBet(player_id=1, team="Brazil"))
        db_session.commit()
        apply_champion_bonus(db_session, "Brazil")
        second = apply_champion_bonus(db_session, "Brazil")
        assert second == []  # already scored

    def test_case_insensitive(self, db_session):
        player(db_session, 1, "Right")
        db_session.add(ChampionBet(player_id=1, team="brazil"))
        db_session.commit()
        winners = apply_champion_bonus(db_session, "BRAZIL")
        assert winners == [1]


class TestChampionFromFinal:
    def _final(self, session, winner, home="Brazil", away="France",
               status=MatchStatus.FINISHED):
        m = Match(
            ext_id=7001, matchday=0, stage="FINAL", round_key="FINAL",
            home=home, away=away, kickoff_utc=NOW, lock_utc=NOW,
            status=status, home_score=1, away_score=1, winner=winner,
        )
        session.add(m)
        session.commit()
        return m

    def test_home_team_wins(self, db_session):
        self._final(db_session, "HOME_TEAM")
        assert champion_from_final(db_session) == "Brazil"

    def test_away_team_wins_on_penalties(self, db_session):
        # 1-1 at 90', France win on penalties -> winner=AWAY_TEAM.
        self._final(db_session, "AWAY_TEAM")
        assert champion_from_final(db_session) == "France"

    def test_no_final_yet(self, db_session):
        assert champion_from_final(db_session) is None

    def test_final_not_finished(self, db_session):
        self._final(db_session, None, status=MatchStatus.IN_PLAY)
        assert champion_from_final(db_session) is None
