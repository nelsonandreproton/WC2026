"""Telegram handlers — thin wrappers over wc2026bot.service.

The session factory and Settings live in application.bot_data so handlers stay
stateless. All domain errors (ServiceError) are caught and shown to the user;
unexpected errors are logged and a generic message is shown.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
import html
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from wc2026bot.bot.formatting import (
    HELP,
    WELCOME,
    fmt_match_line,
    fmt_my_predictions,
    fmt_standings,
)
from wc2026bot.standings import compute_standings
from wc2026bot.service import (
    ServiceError,
    change_nickname,
    get_player,
    my_predictions,
    open_matches,
    open_matches_for_player,
    register_player,
    set_champion_bet,
    upsert_prediction,
)

logger = logging.getLogger(__name__)

# Conversation states
ASK_NICK, PICK_MATCH, ASK_SCORE = range(3)
ASK_CHAMPION = 10
ASK_NEW_NICK = 20


CANCEL_DATA = "cancel_flow"


def _session(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["session_factory"]()


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["settings"]


def _cancel_kb(extra_rows: list[list[InlineKeyboardButton]] | None = None):
    """Inline keyboard with a Cancel button, optionally above other rows."""
    rows = list(extra_rows or [])
    rows.append([InlineKeyboardButton("✖️ Cancelar", callback_data=CANCEL_DATA)])
    return InlineKeyboardMarkup(rows)


async def on_cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inline '✖️ Cancelar' handler — ends any conversation without saving."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Cancelado. Nada foi guardado.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /start  (asks for nickname if new)
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with _session(context) as session:
        player = get_player(session, update.effective_user.id)
    if player is not None:
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)
        return ConversationHandler.END
    # No cancel button on first-time registration: a nickname is required to
    # use the bot, so there's nothing useful to cancel into.
    await update.message.reply_text(
        "Bem-vindo! Escolhe o teu <b>nickname</b> (3–20 caracteres, letras/números/_):",
        parse_mode=ParseMode.HTML,
    )
    return ASK_NICK


async def on_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        with _session(context) as session:
            player = register_player(
                session, update.effective_user.id, update.message.text
            )
    except ServiceError as exc:
        await update.message.reply_text(str(exc))
        return ASK_NICK
    await update.message.reply_text(
        f"Registado como <b>{html.escape(player.nickname)}</b>! 🎉\n\n{WELCOME}",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /nick
# --------------------------------------------------------------------------- #

async def cmd_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """`/nick Name` changes directly; `/nick` alone opens a cancellable prompt."""
    with _session(context) as session:
        if get_player(session, update.effective_user.id) is None:
            await update.message.reply_text("Usa /start primeiro.")
            return ConversationHandler.END

    if context.args:
        # Direct one-shot: /nick Name — apply and end, no retry loop.
        await _apply_nick(update, context, " ".join(context.args))
        return ConversationHandler.END

    await update.message.reply_text(
        "Escreve o teu novo <b>nickname</b> (3–20 caracteres, letras/números/_):",
        parse_mode=ParseMode.HTML,
        reply_markup=_cancel_kb(),
    )
    return ASK_NEW_NICK


async def on_new_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Interactive flow: on error, stay in the prompt so the user can retry.
    ok = await _apply_nick(update, context, update.message.text)
    return ConversationHandler.END if ok else ASK_NEW_NICK


async def _apply_nick(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Apply a nickname change. Returns True on success, False on ServiceError."""
    try:
        with _session(context) as session:
            player = change_nickname(session, update.effective_user.id, text)
    except ServiceError as exc:
        await update.message.reply_text(str(exc))
        return False
    await update.message.reply_text(
        f"Nickname alterado para <b>{html.escape(player.nickname)}</b>.",
        parse_mode=ParseMode.HTML,
    )
    return True


# --------------------------------------------------------------------------- #
# /proximos
# --------------------------------------------------------------------------- #

async def cmd_proximos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _session(context) as session:
        matches = open_matches(session)
    if not matches:
        await update.message.reply_text("Não há jogos abertos para previsão agora.")
        return
    lines = ["<b>Jogos abertos</b>"]
    for m in matches[:20]:
        lines.append("• " + fmt_match_line(m.home, m.away, m.lock_utc))
    lines.append("\nUsa /prever para fazer uma previsão.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------------------- #
# /prever  (conversation: pick match -> enter score)
# --------------------------------------------------------------------------- #

def _build_prever_view(views, show_all: bool) -> tuple[str, InlineKeyboardMarkup]:
    """Build the match-picker text + keyboard. Predicted matches (in show-all
    mode) are flagged with ✏️ and their current score."""
    buttons = []
    for v in views[:20]:
        m = v.match
        if v.has_prediction:
            label = f"✏️ {m.home} {v.pred_home}-{v.pred_away} {m.away}"
        else:
            label = f"{m.home} vs {m.away}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"match:{m.id}")])

    if show_all:
        toggle = InlineKeyboardButton(
            "🔵 A mostrar todos — ver só por prever", callback_data="prever:pending"
        )
        header = "Escolhe o jogo (✏️ = já tens previsão):"
    else:
        toggle = InlineKeyboardButton(
            "👁 Ver todos (incl. já previstos)", callback_data="prever:all"
        )
        header = "Escolhe um jogo por prever:"

    extra = [[toggle]] + buttons
    return header, _cancel_kb(extra)


async def _send_prever(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       show_all: bool, edit: bool) -> int:
    uid = update.effective_user.id
    with _session(context) as session:
        if get_player(session, uid) is None:
            target = update.callback_query.message if edit else update.message
            await target.reply_text("Usa /start primeiro.")
            return ConversationHandler.END
        views = open_matches_for_player(session, uid, only_unpredicted=not show_all)

    if not views:
        msg = (
            "Não há jogos abertos para previsão agora."
            if show_all
            else "Já fizeste previsão para todos os jogos abertos! 🎉\n"
                 "Usa o botão abaixo para rever ou editar."
        )
        if not show_all:
            # Offer the toggle even when nothing is pending, so they can edit.
            kb = _cancel_kb([[InlineKeyboardButton(
                "👁 Ver todos (incl. já previstos)", callback_data="prever:all")]])
            if edit:
                await update.callback_query.edit_message_text(msg, reply_markup=kb)
            else:
                await update.message.reply_text(msg, reply_markup=kb)
            return PICK_MATCH
        if edit:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    header, kb = _build_prever_view(views, show_all)
    if edit:
        await update.callback_query.edit_message_text(header, reply_markup=kb)
    else:
        await update.message.reply_text(header, reply_markup=kb)
    return PICK_MATCH


async def cmd_prever(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Default: only matches without a prediction yet.
    return await _send_prever(update, context, show_all=False, edit=False)


async def on_prever_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    show_all = query.data == "prever:all"
    return await _send_prever(update, context, show_all=show_all, edit=True)


async def on_match_picked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    match_id = int(query.data.split(":", 1)[1])
    context.user_data["match_id"] = match_id
    await query.edit_message_text(
        "Escreve a tua previsão no formato <b>golos-golos</b> (ex.: <code>2-1</code>):",
        parse_mode=ParseMode.HTML,
        reply_markup=_cancel_kb(),
    )
    return ASK_SCORE


def parse_score(text: str) -> tuple[int, int] | None:
    """Parse '2-1' / '2 1' / '2:1' into (home, away). None if invalid."""
    cleaned = text.strip().replace(":", "-").replace(" ", "-")
    # Reject negative inputs like "-1-2" before the split swallows the sign.
    if cleaned.startswith("-"):
        return None
    parts = [p for p in cleaned.split("-") if p != ""]
    if len(parts) != 2:
        return None
    try:
        h, a = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if h < 0 or a < 0:
        return None
    return h, a


async def on_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    parsed = parse_score(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            "Formato inválido. Tenta algo como <code>2-1</code>.",
            parse_mode=ParseMode.HTML,
        )
        return ASK_SCORE
    match_id = context.user_data.get("match_id")
    try:
        with _session(context) as session:
            upsert_prediction(
                session, update.effective_user.id, match_id, parsed[0], parsed[1]
            )
    except ServiceError as exc:
        await update.message.reply_text(str(exc))
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ Previsão guardada: <b>{parsed[0]}-{parsed[1]}</b>. "
        "Podes editar até ao fecho com /prever.",
        parse_mode=ParseMode.HTML,
    )
    context.user_data.pop("match_id", None)
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /minhas
# --------------------------------------------------------------------------- #

async def cmd_minhas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _session(context) as session:
        views = my_predictions(session, update.effective_user.id)
    await update.message.reply_text(
        fmt_my_predictions(views), parse_mode=ParseMode.HTML
    )


# --------------------------------------------------------------------------- #
# /campeao  (conversation: ask team)
# --------------------------------------------------------------------------- #

async def cmd_campeao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with _session(context) as session:
        if get_player(session, update.effective_user.id) is None:
            await update.message.reply_text("Usa /start primeiro.")
            return ConversationHandler.END
    await update.message.reply_text(
        "Em que seleção apostas para <b>campeã do Mundial</b>? Escreve o nome do país.\n"
        "(Vale +100 pontos no fim. Editável até ao jogo de abertura.)",
        parse_mode=ParseMode.HTML,
        reply_markup=_cancel_kb(),
    )
    return ASK_CHAMPION


async def on_champion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    settings = _settings(context)
    try:
        with _session(context) as session:
            bet = set_champion_bet(
                session,
                update.effective_user.id,
                update.message.text,
                settings.champion_lock_utc,
            )
    except ServiceError as exc:
        await update.message.reply_text(str(exc))
        return ConversationHandler.END
    await update.message.reply_text(
        f"🏆 Aposta registada: <b>{html.escape(bet.team)}</b>.",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /ajuda  and  /cancel
# --------------------------------------------------------------------------- #

async def cmd_tabela(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _session(context) as session:
        rows = compute_standings(session)
    text = fmt_standings(
        rows, "Classificação", highlight_id=update.effective_user.id
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END


def build_handlers() -> list:
    """Return the handler list to register on the Application."""
    # The inline '✖️ Cancelar' button works in any state of each conversation,
    # so it lives in fallbacks alongside the /cancel command.
    cancel_button = CallbackQueryHandler(on_cancel_button, pattern=f"^{CANCEL_DATA}$")
    cancel_fallbacks = [cancel_button, CommandHandler("cancel", cmd_cancel)]

    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_nickname)]},
        fallbacks=cancel_fallbacks,
    )
    prever_conv = ConversationHandler(
        entry_points=[CommandHandler("prever", cmd_prever)],
        states={
            PICK_MATCH: [
                CallbackQueryHandler(on_prever_toggle, pattern=r"^prever:(all|pending)$"),
                CallbackQueryHandler(on_match_picked, pattern=r"^match:"),
            ],
            ASK_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_score)],
        },
        fallbacks=cancel_fallbacks,
    )
    campeao_conv = ConversationHandler(
        entry_points=[CommandHandler("campeao", cmd_campeao)],
        states={ASK_CHAMPION: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_champion)]},
        fallbacks=cancel_fallbacks,
    )
    nick_conv = ConversationHandler(
        entry_points=[CommandHandler("nick", cmd_nick)],
        states={ASK_NEW_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_new_nick)]},
        fallbacks=cancel_fallbacks,
    )
    return [
        start_conv,
        prever_conv,
        campeao_conv,
        nick_conv,
        CommandHandler("proximos", cmd_proximos),
        CommandHandler("minhas", cmd_minhas),
        CommandHandler("tabela", cmd_tabela),
        CommandHandler(["ajuda", "help"], cmd_ajuda),
    ]
