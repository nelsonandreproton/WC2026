"""Scheduled jobs, wired onto PTB's JobQueue (shares the bot's event loop).

Each job is thin: it opens a session, calls already-tested logic, and uses the
Throttler to fan out DMs. The heavy correctness lives in service/sync/scoring/
standings/scoring_job, all unit-tested. These jobs are the glue.
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from wc2026bot.bot.formatting import fmt_result_dm, fmt_standings
from wc2026bot.notifier import OutboundDM, Throttler
from wc2026bot.providers.sync import sync_fixtures
from wc2026bot.scheduler.scoring_job import score_finished_matches
from wc2026bot.standings import (
    apply_champion_bonus,
    champion_from_final,
    compute_standings,
    is_round_complete,
    round_points,
)
from wc2026bot.db.models import Round, utcnow
from sqlalchemy import select

logger = logging.getLogger(__name__)


def _factory(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["session_factory"]


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["settings"]


def _throttler(context: ContextTypes.DEFAULT_TYPE) -> Throttler:
    return context.application.bot_data["throttler"]


async def _send_one(context, chat_id: int, text: str) -> None:
    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN
    )


# --------------------------------------------------------------------------- #

async def job_sync_fixtures(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily: refresh fixtures, kickoff times, knockout team fill-ins."""
    settings = _settings(context)
    provider = context.application.bot_data["provider"]
    try:
        fixtures = provider.fetch_fixtures()
    except Exception as exc:  # noqa: BLE001
        logger.warning("sync_fixtures fetch failed: %s", exc)
        return
    with _factory(context)() as session:
        result = sync_fixtures(session, fixtures, lock_minutes=settings.lock_minutes)
    logger.info("sync_fixtures: %s", result)


async def job_poll_and_score(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every N minutes: pull results, persist scores, score predictions, DM
    each player their result, then publish any newly-complete round table.

    Registered with max_instances=1 + coalesce=True so a slow run never
    overlaps the next tick (also avoids SQLite 'database is locked')."""
    provider = context.application.bot_data["provider"]
    settings = _settings(context)
    try:
        results = provider.fetch_results()
    except Exception as exc:  # noqa: BLE001
        logger.warning("poll fetch failed: %s", exc)
        return

    with _factory(context)() as session:
        sync_fixtures(session, results, lock_minutes=settings.lock_minutes)
        notifications = score_finished_matches(session)

    # DM each player their result (throttled).
    dms = [
        OutboundDM(chat_id=n.telegram_id, text=fmt_result_dm(n.view))
        for n in notifications
    ]
    if dms:
        await _throttler(context).send_all(
            lambda cid, txt: _send_one(context, cid, txt), dms
        )

    await _publish_complete_rounds(context)
    await _maybe_apply_champion(context)


async def _publish_complete_rounds(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the standings table for any round that just completed (once)."""
    factory = _factory(context)
    with factory() as session:
        pending = session.scalars(
            select(Round).where(Round.table_sent_at.is_(None)).order_by(
                Round.sort_order
            )
        ).all()
        # Skip FINAL: its authoritative table (with the +100 champion bonus) is
        # sent by _maybe_apply_champion, so we don't double-DM the final.
        to_publish = [
            r.round_key
            for r in pending
            if r.round_key != "FINAL" and is_round_complete(session, r.round_key)
        ]

    for rkey in to_publish:
        with factory() as session:
            rnd = session.get(Round, rkey)
            round_title = f"Classificação — {rnd.name}"  # capture before close
            rp = round_points(session, rkey)
            standings = compute_standings(session)
        # Attach this round's points to each row for display.
        rows = [
            type(s)(
                rank=s.rank, telegram_id=s.telegram_id, nickname=s.nickname,
                total_points=s.total_points, exact_hits=s.exact_hits,
                round_points=rp.get(s.telegram_id, 0),
            )
            for s in standings
        ]
        title = round_title
        dms = [
            OutboundDM(
                chat_id=s.telegram_id,
                text=fmt_standings(rows, title, highlight_id=s.telegram_id,
                                   show_round=True),
            )
            for s in rows
        ]
        await _throttler(context).send_all(
            lambda cid, txt: _send_one(context, cid, txt), dms
        )
        with factory() as session:
            rnd = session.get(Round, rkey)
            rnd.table_sent_at = utcnow()
            session.commit()
        logger.info("Published standings for round %s", rkey)


async def _maybe_apply_champion(context: ContextTypes.DEFAULT_TYPE) -> None:
    """After the final, award +100 to correct champion bets (once) and DM
    the final standings."""
    factory = _factory(context)
    with factory() as session:
        champion = champion_from_final(session)
        if champion is None:
            return
        # Single-run guard: FINAL.table_sent_at is the source of truth. Once the
        # final standings DM goes out we stamp it, so this runs exactly once
        # regardless of how many champion bets exist (even zero).
        final_round = session.get(Round, "FINAL")
        if final_round is not None and final_round.table_sent_at is not None:
            return
        winners = apply_champion_bonus(session, champion)
        standings = compute_standings(session)

    logger.info("Champion=%s, %d champion-bonus winners", champion, len(winners))
    dms = [
        OutboundDM(
            chat_id=s.telegram_id,
            text=fmt_standings(
                standings,
                f"🏆 Classificação Final — Campeão: {champion}",
                highlight_id=s.telegram_id,
            ),
        )
        for s in standings
    ]
    if dms:
        await _throttler(context).send_all(
            lambda cid, txt: _send_one(context, cid, txt), dms
        )

    # Stamp FINAL as handled so this never re-sends and _publish_complete_rounds
    # never sends a second (pre-bonus) table for the final.
    with factory() as session:
        final_round = session.get(Round, "FINAL")
        if final_round is not None and final_round.table_sent_at is None:
            final_round.table_sent_at = utcnow()
            session.commit()
