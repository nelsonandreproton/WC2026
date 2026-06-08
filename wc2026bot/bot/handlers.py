"""Telegram handlers — thin wrappers over wc2026bot.service.

The session factory and Settings live in application.bot_data so handlers stay
stateless. All domain errors (ServiceError) are caught and shown to the user;
unexpected errors are logged and a generic message is shown.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown
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
    register_player,
    set_champion_bet,
    upsert_prediction,
)

logger = logging.getLogger(__name__)

# Conversation states
ASK_NICK, PICK_MATCH, ASK_SCORE = range(3)
ASK_CHAMPION = 10


def _session(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["session_factory"]()


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["settings"]


# --------------------------------------------------------------------------- #
# /start  (asks for nickname if new)
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with _session(context) as session:
        player = get_player(session, update.effective_user.id)
    if player is not None:
        await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END
    await update.message.reply_text(
        "Bem-vindo! Escolhe o teu *nickname* (3–20 caracteres, letras/números/_):",
        parse_mode=ParseMode.MARKDOWN,
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
        f"Registado como *{escape_markdown(player.nickname, version=1)}*! 🎉\n\n{WELCOME}",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /nick
# --------------------------------------------------------------------------- #

async def cmd_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Uso: /nick <novo_nickname>")
        return
    try:
        with _session(context) as session:
            player = change_nickname(
                session, update.effective_user.id, " ".join(context.args)
            )
    except ServiceError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"Nickname alterado para *{escape_markdown(player.nickname, version=1)}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


# --------------------------------------------------------------------------- #
# /proximos
# --------------------------------------------------------------------------- #

async def cmd_proximos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _session(context) as session:
        matches = open_matches(session)
    if not matches:
        await update.message.reply_text("Não há jogos abertos para previsão agora.")
        return
    lines = ["*Jogos abertos*"]
    for m in matches[:20]:
        lines.append("• " + fmt_match_line(m.home, m.away, m.lock_utc))
    lines.append("\nUsa /prever para fazer uma previsão.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# --------------------------------------------------------------------------- #
# /prever  (conversation: pick match -> enter score)
# --------------------------------------------------------------------------- #

async def cmd_prever(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    with _session(context) as session:
        if get_player(session, update.effective_user.id) is None:
            await update.message.reply_text("Usa /start primeiro.")
            return ConversationHandler.END
        matches = open_matches(session)
    if not matches:
        await update.message.reply_text("Não há jogos abertos para previsão agora.")
        return ConversationHandler.END

    buttons = [
        [InlineKeyboardButton(f"{m.home} vs {m.away}", callback_data=f"match:{m.id}")]
        for m in matches[:20]
    ]
    await update.message.reply_text(
        "Escolhe o jogo:", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return PICK_MATCH


async def on_match_picked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    match_id = int(query.data.split(":", 1)[1])
    context.user_data["match_id"] = match_id
    await query.edit_message_text(
        "Escreve a tua previsão no formato *golos-golos* (ex.: `2-1`):",
        parse_mode=ParseMode.MARKDOWN,
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
        await update.message.reply_text("Formato inválido. Tenta algo como `2-1`.",
                                        parse_mode=ParseMode.MARKDOWN)
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
        f"✅ Previsão guardada: *{parsed[0]}-{parsed[1]}*. "
        "Podes editar até ao fecho com /prever.",
        parse_mode=ParseMode.MARKDOWN,
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
        fmt_my_predictions(views), parse_mode=ParseMode.MARKDOWN
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
        "Em que seleção apostas para *campeã do Mundial*? Escreve o nome do país.\n"
        "(Vale +100 pontos no fim. Editável até ao jogo de abertura.)",
        parse_mode=ParseMode.MARKDOWN,
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
        f"🏆 Aposta registada: *{escape_markdown(bet.team, version=1)}*.",
        parse_mode=ParseMode.MARKDOWN,
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
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END


def build_handlers() -> list:
    """Return the handler list to register on the Application."""
    start_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_nickname)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    prever_conv = ConversationHandler(
        entry_points=[CommandHandler("prever", cmd_prever)],
        states={
            PICK_MATCH: [CallbackQueryHandler(on_match_picked, pattern=r"^match:")],
            ASK_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_score)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    campeao_conv = ConversationHandler(
        entry_points=[CommandHandler("campeao", cmd_campeao)],
        states={ASK_CHAMPION: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_champion)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
    return [
        start_conv,
        prever_conv,
        campeao_conv,
        CommandHandler("nick", cmd_nick),
        CommandHandler("proximos", cmd_proximos),
        CommandHandler("minhas", cmd_minhas),
        CommandHandler("tabela", cmd_tabela),
        CommandHandler(["ajuda", "help"], cmd_ajuda),
    ]
