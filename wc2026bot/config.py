"""Runtime configuration loaded from environment / .env.

All secrets come from the environment (project rule). Import `settings` once
at startup; it fails fast if a required value is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value.strip()


def _parse_iso_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    football_data_api_key: str
    db_path: str
    competition: str
    lock_minutes: int
    # Kickoff of the opening match — champion bets lock at this instant.
    champion_lock_utc: datetime
    # Throttle between outbound DMs (seconds) to stay under Telegram limits.
    dm_interval_seconds: float
    poll_interval_minutes: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            telegram_token=_require("TELEGRAM_BOT_TOKEN"),
            football_data_api_key=_require("FOOTBALL_DATA_API_KEY"),
            db_path=os.environ.get("DB_PATH", "data/wc2026.db"),
            competition=os.environ.get("COMPETITION", "WC"),
            lock_minutes=int(os.environ.get("LOCK_MINUTES", "15")),
            champion_lock_utc=_parse_iso_utc(
                os.environ.get("CHAMPION_LOCK_UTC", "2026-06-11T16:00:00Z")
            ),
            dm_interval_seconds=float(os.environ.get("DM_INTERVAL_SECONDS", "0.2")),
            poll_interval_minutes=int(os.environ.get("POLL_INTERVAL_MINUTES", "5")),
        )
