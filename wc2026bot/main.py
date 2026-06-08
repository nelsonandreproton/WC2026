"""Entry point: build the Application, wire handlers + jobs, run polling.

Jobs run on PTB's JobQueue (shares the bot's event loop and lifecycle), per the
integration design. The poll job is registered with max_instances=1 +
coalesce=True so a slow tick never overlaps the next (and SQLite never locks).
"""

from __future__ import annotations

import logging
import os
from datetime import time, timezone

from telegram.ext import Application

from wc2026bot.bot.handlers import build_handlers
from wc2026bot.config import Settings
from wc2026bot.db.session import init_db, make_engine, make_session_factory
from wc2026bot.notifier import Throttler
from wc2026bot.providers.football_data import FootballDataProvider
from wc2026bot.scheduler.jobs import job_poll_and_score, job_sync_fixtures

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def build_application(settings: Settings) -> Application:
    # Ensure the DB directory exists before opening the SQLite file.
    db_dir = os.path.dirname(settings.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    engine = make_engine(settings.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)

    provider = FootballDataProvider(
        api_key=settings.football_data_api_key, competition=settings.competition
    )

    app = Application.builder().token(settings.telegram_token).build()
    app.bot_data.update(
        {
            "settings": settings,
            "session_factory": session_factory,
            "provider": provider,
            "throttler": Throttler(interval_seconds=settings.dm_interval_seconds),
        }
    )

    for handler in build_handlers():
        app.add_handler(handler)

    jq = app.job_queue
    # Daily fixture sync at 04:00 UTC (low-traffic, refreshes kickoff times).
    jq.run_daily(job_sync_fixtures, time=time(hour=4, tzinfo=timezone.utc))
    # Result polling every N minutes; non-overlapping.
    jq.run_repeating(
        job_poll_and_score,
        interval=settings.poll_interval_minutes * 60,
        first=10,
        job_kwargs={"max_instances": 1, "coalesce": True},
    )
    # One sync shortly after startup so a fresh DB is populated immediately.
    # `when` must clear the ~4s Telegram connect before the scheduler starts;
    # misfire_grace_time lets it still run if startup is slow (otherwise
    # APScheduler silently drops a "missed" one-off job and the DB stays empty
    # until the next daily sync).
    jq.run_once(
        job_sync_fixtures,
        when=15,
        job_kwargs={"misfire_grace_time": 120},
    )

    return app


def main() -> None:
    settings = Settings.from_env()
    app = build_application(settings)
    logger.info("Starting WC2026 bot (poll every %dm, lock %dm before kickoff)",
                settings.poll_interval_minutes, settings.lock_minutes)
    app.run_polling()


if __name__ == "__main__":
    main()
