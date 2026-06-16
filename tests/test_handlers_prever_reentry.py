"""Regression test: /prever must restart cleanly even when the user is already
in a conversation state (ASK_SCORE or PICK_MATCH) without having cancelled.

Without allow_reentry=True on prever_conv, a /prever command issued while a
user is stuck mid-conversation is silently dropped by the ConversationHandler,
making the command appear broken for those users.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.ext import ConversationHandler

from wc2026bot.bot.handlers import build_handlers, cmd_prever


class FakeMessage:
    def __init__(self):
        self.replied_text = None
        self.replied_markup = None

    async def reply_text(self, text, **kwargs):
        self.replied_text = text
        self.replied_markup = kwargs.get("reply_markup")


def _make_fake_session(player_exists: bool):
    """Return a session context-manager fake that may or may not have a player."""
    from wc2026bot.db.models import Player

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, model, pk):
            if model is Player and player_exists:
                p = SimpleNamespace(telegram_id=pk, nickname="Tester", is_active=True)
                return p
            return None

        def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

    return FakeSession()


def _make_context(user_data: dict, player_exists: bool = True):
    fake_session = _make_fake_session(player_exists)

    class FakeSessionFactory:
        def __call__(self):
            return fake_session

    bot_data = {"session_factory": FakeSessionFactory()}
    return SimpleNamespace(
        user_data=user_data,
        application=SimpleNamespace(bot_data=bot_data),
    )


@pytest.mark.asyncio
async def test_cmd_prever_clears_stale_user_data():
    """cmd_prever must wipe user_data so stale match_id from a stuck session
    doesn't carry over into the new conversation."""
    stale_data = {"match_id": 99}
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        message=message,
    )
    context = _make_context(stale_data, player_exists=False)

    await cmd_prever(update, context)

    assert stale_data == {}, "user_data should be cleared on /prever re-entry"


def test_prever_conv_allows_reentry():
    """prever_conv must have allow_reentry=True so /prever can restart even
    when the user is stuck in PICK_MATCH or ASK_SCORE state."""
    handlers = build_handlers()
    prever_conv = next(
        h for h in handlers
        if hasattr(h, "entry_points")
        and any(
            getattr(ep, "callback", None).__name__ == "cmd_prever"
            for ep in h.entry_points
            if hasattr(ep, "callback")
        )
    )
    assert prever_conv.allow_reentry is True, (
        "prever_conv must have allow_reentry=True; without it, users stuck in "
        "ASK_SCORE or PICK_MATCH can never restart /prever"
    )
