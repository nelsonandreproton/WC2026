"""Throttled outbound DM sender.

Telegram limits how fast a bot can send messages (~30/s overall, less per
chat). We spread DMs out with a fixed interval so a result/standings broadcast
to many players never hits the limit. A failure to one chat (e.g. user blocked
the bot) is logged and skipped — it never aborts the batch.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutboundDM:
    chat_id: int
    text: str


class Throttler:
    """Sleeps `interval` seconds between sends. Injectable sleep for tests."""

    def __init__(self, interval_seconds: float, sleep=asyncio.sleep) -> None:
        self.interval = interval_seconds
        self._sleep = sleep

    async def send_all(self, send_one, messages: Iterable[OutboundDM]) -> dict[str, int]:
        """Send each message via `send_one(chat_id, text)` coroutine, spacing
        them out. Returns {"sent": n, "failed": n}."""
        sent = failed = 0
        first = True
        for msg in messages:
            if not first:
                await self._sleep(self.interval)
            first = False
            try:
                await send_one(msg.chat_id, msg.text)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - never abort the batch
                failed += 1
                logger.warning("DM to %s failed: %s", msg.chat_id, exc)
        return {"sent": sent, "failed": failed}
