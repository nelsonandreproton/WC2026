"""Tests for the throttled notifier — fake sleep + fake send, no real I/O."""

from __future__ import annotations

import pytest

from wc2026bot.notifier import OutboundDM, Throttler


@pytest.mark.asyncio
async def test_sends_all_and_spaces_between():
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    sent: list[tuple[int, str]] = []

    async def send_one(chat_id, text):
        sent.append((chat_id, text))

    t = Throttler(interval_seconds=0.2, sleep=fake_sleep)
    msgs = [OutboundDM(1, "a"), OutboundDM(2, "b"), OutboundDM(3, "c")]
    result = await t.send_all(send_one, msgs)

    assert result == {"sent": 3, "failed": 0}
    assert sent == [(1, "a"), (2, "b"), (3, "c")]
    # 3 messages -> 2 gaps between them
    assert slept == [0.2, 0.2]


@pytest.mark.asyncio
async def test_failure_does_not_abort_batch():
    async def fake_sleep(_):
        return None

    async def send_one(chat_id, text):
        if chat_id == 2:
            raise RuntimeError("user blocked bot")

    t = Throttler(interval_seconds=0, sleep=fake_sleep)
    msgs = [OutboundDM(1, "a"), OutboundDM(2, "b"), OutboundDM(3, "c")]
    result = await t.send_all(send_one, msgs)

    assert result == {"sent": 2, "failed": 1}


@pytest.mark.asyncio
async def test_empty_batch():
    async def fake_sleep(_):
        return None

    async def send_one(chat_id, text):
        return None

    t = Throttler(interval_seconds=1, sleep=fake_sleep)
    result = await t.send_all(send_one, [])
    assert result == {"sent": 0, "failed": 0}
