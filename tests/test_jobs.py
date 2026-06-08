"""Integration tests for the orchestration jobs, with fakes (no network, no bot).

We fake the PTB context: bot_data holds the session factory, a fake provider,
and a Throttler with instant sleep. context.bot.send_message is recorded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from wc2026bot.db.models import (
    ChampionBet,
    Match,
    MatchStatus,
    Player,
    Prediction,
    Round,
)
from wc2026bot.notifier import Throttler
from wc2026bot.scheduler.jobs import (
    _maybe_apply_champion,
    _publish_complete_rounds,
)

NOW = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append((chat_id, text))


def make_context(session_factory):
    bot = FakeBot()
    app = SimpleNamespace(
        bot_data={
            "session_factory": session_factory,
            "throttler": Throttler(interval_seconds=0, sleep=_no_sleep),
        }
    )
    return SimpleNamespace(application=app, bot=bot), bot


async def _no_sleep(_):
    return None


@pytest.fixture
def factory(db_session):
    # Reuse the engine/session from the db_session fixture's bind.
    bind = db_session.get_bind()
    from wc2026bot.db.session import make_session_factory
    return make_session_factory(bind)


def seed_round(session, round_key, name, complete=True):
    session.add(Round(round_key=round_key, name=name, sort_order=1))
    status = MatchStatus.FINISHED if complete else MatchStatus.IN_PLAY
    m = Match(
        ext_id=hash(round_key) % 100000, matchday=1, stage="GROUP_STAGE",
        round_key=round_key, home="A", away="B", kickoff_utc=NOW,
        lock_utc=NOW - timedelta(minutes=15), status=status,
        home_score=2, away_score=1,
    )
    session.add(m)
    session.commit()
    return m


def add_player_pred(session, tid, nick, match_id, points):
    session.add(Player(telegram_id=tid, nickname=nick, nickname_ci=nick.lower()))
    session.add(Prediction(player_id=tid, match_id=match_id,
                           pred_home=2, pred_away=1, points=points))
    session.commit()


class TestPublishCompleteRounds:
    @pytest.mark.asyncio
    async def test_publishes_once_when_complete(self, db_session, factory):
        m = seed_round(db_session, "GROUP-1", "Jornada 1", complete=True)
        add_player_pred(db_session, 1, "Ana", m.id, 5)
        add_player_pred(db_session, 2, "Rui", m.id, 3)

        context, bot = make_context(factory)
        await _publish_complete_rounds(context)

        # One DM per player.
        assert len(bot.sent) == 2
        # table_sent_at is now set -> second run sends nothing.
        await _publish_complete_rounds(context)
        assert len(bot.sent) == 2

    @pytest.mark.asyncio
    async def test_does_not_publish_incomplete(self, db_session, factory):
        m = seed_round(db_session, "GROUP-1", "Jornada 1", complete=False)
        add_player_pred(db_session, 1, "Ana", m.id, None)
        context, bot = make_context(factory)
        await _publish_complete_rounds(context)
        assert bot.sent == []

    @pytest.mark.asyncio
    async def test_final_round_not_published_here(self, db_session, factory):
        # The FINAL table is owned by _maybe_apply_champion, not this job.
        m = seed_round(db_session, "FINAL", "Final", complete=True)
        add_player_pred(db_session, 1, "Ana", m.id, 5)
        context, bot = make_context(factory)
        await _publish_complete_rounds(context)
        assert bot.sent == []


class TestMaybeApplyChampion:
    @pytest.mark.asyncio
    async def test_awards_and_dms_once(self, db_session, factory):
        # Final finished, Brazil won.
        session = db_session
        session.add(Round(round_key="FINAL", name="Final", sort_order=500))
        session.add(Match(
            ext_id=7001, matchday=0, stage="FINAL", round_key="FINAL",
            home="Brazil", away="France", kickoff_utc=NOW, lock_utc=NOW,
            status=MatchStatus.FINISHED, home_score=1, away_score=1,
            winner="HOME_TEAM",
        ))
        session.add(Player(telegram_id=1, nickname="Ana", nickname_ci="ana"))
        session.add(ChampionBet(player_id=1, team="Brazil"))
        session.commit()

        context, bot = make_context(factory)
        await _maybe_apply_champion(context)

        # Bonus applied + final standings DM sent.
        assert len(bot.sent) == 1
        bet = session.get(ChampionBet, 1)
        session.refresh(bet)
        assert bet.points == 100

        # Second run is a no-op (bets already scored).
        await _maybe_apply_champion(context)
        assert len(bot.sent) == 1

    @pytest.mark.asyncio
    async def test_no_final_no_action(self, db_session, factory):
        context, bot = make_context(factory)
        await _maybe_apply_champion(context)
        assert bot.sent == []

    @pytest.mark.asyncio
    async def test_runs_once_even_with_zero_bets(self, db_session, factory):
        # Guard is FINAL.table_sent_at, not bet count — final standings still
        # go out exactly once when nobody bet on the champion.
        session = db_session
        session.add(Round(round_key="FINAL", name="Final", sort_order=500))
        session.add(Match(
            ext_id=7002, matchday=0, stage="FINAL", round_key="FINAL",
            home="Brazil", away="France", kickoff_utc=NOW, lock_utc=NOW,
            status=MatchStatus.FINISHED, home_score=2, away_score=0,
            winner="HOME_TEAM",
        ))
        session.add(Player(telegram_id=1, nickname="Ana", nickname_ci="ana"))
        session.commit()

        context, bot = make_context(factory)
        await _maybe_apply_champion(context)
        assert len(bot.sent) == 1
        # Second run: FINAL already stamped -> no-op.
        await _maybe_apply_champion(context)
        assert len(bot.sent) == 1
