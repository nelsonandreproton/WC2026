"""The /prever match picker is paginated so the message stays short and the
first (soonest) match remains visible at the top, instead of Telegram
scrolling to the bottom of a long inline keyboard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from wc2026bot.bot.handlers import PREVER_PAGE_SIZE, _build_prever_view


def _make_views(n: int):
    views = []
    for i in range(n):
        match = SimpleNamespace(
            id=i,
            home=f"H{i}",
            away=f"A{i}",
            kickoff_utc=datetime(2026, 6, 21, 12, 0, tzinfo=UTC),
        )
        views.append(
            SimpleNamespace(
                match=match,
                pred_home=None,
                pred_away=None,
                has_prediction=False,
            )
        )
    return views


def _match_buttons(markup):
    """Flatten the keyboard and return only the match-picking buttons."""
    return [
        b
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("match:")
    ]


def _callbacks(markup):
    return [
        b.callback_data
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data
    ]


def test_first_page_shows_only_page_size_matches():
    views = _make_views(PREVER_PAGE_SIZE * 3)
    _, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=0)
    buttons = _match_buttons(markup)
    assert len(buttons) == PREVER_PAGE_SIZE
    # The soonest matches (start of the list) are the ones shown.
    assert buttons[0].callback_data == "match:0"
    assert buttons[-1].callback_data == f"match:{PREVER_PAGE_SIZE - 1}"


def test_first_page_has_next_but_no_prev():
    views = _make_views(PREVER_PAGE_SIZE * 2)
    _, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=0)
    cbs = _callbacks(markup)
    assert "preverpage:pending:1" in cbs
    assert not any(c.startswith("preverpage") and c.endswith(":-1") for c in cbs)
    assert not any("preverpage:pending:0" == c for c in cbs)  # no "previous" on first page


def test_middle_page_has_both_nav():
    views = _make_views(PREVER_PAGE_SIZE * 3)
    _, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=1)
    cbs = _callbacks(markup)
    assert "preverpage:pending:0" in cbs
    assert "preverpage:pending:2" in cbs


def test_last_page_has_prev_but_no_next():
    views = _make_views(PREVER_PAGE_SIZE * 2 + 1)
    last = (len(views) + PREVER_PAGE_SIZE - 1) // PREVER_PAGE_SIZE - 1
    _, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=last)
    cbs = _callbacks(markup)
    assert f"preverpage:pending:{last - 1}" in cbs
    assert f"preverpage:pending:{last + 1}" not in cbs
    # The remainder match is shown on the last page.
    assert len(_match_buttons(markup)) == 1


def test_no_pagination_when_list_fits():
    views = _make_views(PREVER_PAGE_SIZE)
    header, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=0)
    cbs = _callbacks(markup)
    assert not any(c.startswith("preverpage") for c in cbs)
    assert "página" not in header


def test_out_of_range_page_is_clamped():
    views = _make_views(PREVER_PAGE_SIZE + 1)
    # Requesting a page far beyond the end falls back to the last page.
    _, markup = _build_prever_view(views, show_all=False, pending_count=len(views), page=999)
    assert len(_match_buttons(markup)) == 1


def test_show_all_mode_uses_all_in_callback():
    views = _make_views(PREVER_PAGE_SIZE * 2)
    _, markup = _build_prever_view(views, show_all=True, pending_count=3, page=0)
    cbs = _callbacks(markup)
    assert "preverpage:all:1" in cbs
