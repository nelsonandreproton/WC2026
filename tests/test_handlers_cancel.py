"""Tests for the cancel-flow inline button — fakes, no Telegram I/O."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.ext import ConversationHandler

from wc2026bot.bot.handlers import CANCEL_DATA, _cancel_kb, on_cancel_button


class FakeQuery:
    def __init__(self):
        self.answered = False
        self.edited_text = None

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edited_text = text


def test_cancel_kb_has_cancel_button():
    kb = _cancel_kb()
    # last row is the cancel button
    btn = kb.inline_keyboard[-1][0]
    assert btn.callback_data == CANCEL_DATA
    assert "Cancelar" in btn.text


def test_cancel_kb_keeps_extra_rows_above():
    from telegram import InlineKeyboardButton
    extra = [[InlineKeyboardButton("Jogo", callback_data="match:1")]]
    kb = _cancel_kb(extra)
    assert kb.inline_keyboard[0][0].callback_data == "match:1"
    assert kb.inline_keyboard[-1][0].callback_data == CANCEL_DATA


@pytest.mark.asyncio
async def test_on_cancel_button_ends_and_clears():
    query = FakeQuery()
    update = SimpleNamespace(callback_query=query)
    user_data = {"match_id": 42}
    context = SimpleNamespace(user_data=user_data)

    result = await on_cancel_button(update, context)

    assert result == ConversationHandler.END
    assert query.answered is True
    assert "Cancelado" in query.edited_text
    assert user_data == {}  # cleared
