"""Pure message-formatting helpers. No Telegram objects, fully testable."""

from __future__ import annotations

from datetime import UTC, datetime

import html

from wc2026bot.service import PredictionView
from wc2026bot.standings import StandingRow

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _esc(text: str) -> str:
    """Escape user/external text for Telegram HTML parse mode.

    We use HTML (not legacy Markdown v1), which is fragile and non-deterministic
    with punctuation adjacent to '*' — that exact bug rejected the /start
    welcome message. HTML entities are explicit and reliably escapable.
    """
    return html.escape(text)


WELCOME = (
    "⚽ <b>Bólão Mundial 2026</b>\n\n"
    "Prevê o resultado de cada jogo. Pontuação:\n"
    "• Acertar a direção (V/E/D): <b>+3</b>\n"
    "• Acertar o resultado exato: <b>+2</b> extra (total 5)\n"
    "• Apostar no campeão certo: <b>+100</b> no fim\n\n"
    "As previsões fecham uns minutos antes de cada jogo e ninguém vê as tuas.\n\n"
    "Comandos: /proximos /prever /minhas /campeao /tabela /nick /ajuda"
)

HELP = (
    "<b>Comandos</b>\n"
    "/proximos – jogos abertos para previsão\n"
    "/prever – fazer/editar uma previsão\n"
    "/minhas – as tuas previsões e pontos\n"
    "/campeao – apostar no campeão do Mundial (+100)\n"
    "/tabela – ver a tua posição atual\n"
    "/nick – mudar o teu nickname\n"
    "/ajuda – esta mensagem"
)


def fmt_countdown(lock_utc: datetime, now: datetime | None = None) -> str:
    moment = now or datetime.now(UTC)
    delta = lock_utc - moment
    total = int(delta.total_seconds())
    if total <= 0:
        return "fechado"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours >= 24:
        days = hours // 24
        return f"fecha em {days}d {hours % 24}h"
    if hours:
        return f"fecha em {hours}h {minutes}m"
    return f"fecha em {minutes}m"


def fmt_match_line(home: str, away: str, lock_utc: datetime,
                   now: datetime | None = None) -> str:
    return f"{_esc(home)} vs {_esc(away)} — <i>{fmt_countdown(lock_utc, now)}</i>"


def fmt_result_dm(view: PredictionView) -> str:
    """DM sent after a match is scored: real score + the player's prediction."""
    m = view.match
    real = f"{m.home_score}-{m.away_score}"
    if view.pred_home is None:
        pred = "sem previsão"
        pts = 0
    else:
        pred = f"{view.pred_home}-{view.pred_away}"
        pts = view.points or 0
    emoji = "🎯" if pts == 5 else ("✅" if pts == 3 else "❌")
    return (
        f"{emoji} <b>{_esc(m.home)} {real} {_esc(m.away)}</b>\n"
        f"A tua previsão: {pred} · <b>{pts} pts</b>"
    )


def fmt_standings(
    rows: list[StandingRow],
    title: str,
    highlight_id: int | None = None,
    show_round: bool = False,
) -> str:
    """Standings table. The viewer's own row is bolded and marked with ➤."""
    if not rows:
        return f"<b>{_esc(title)}</b>\n(sem jogadores ainda)"
    lines = [f"<b>{_esc(title)}</b>"]
    for r in rows:
        marker = MEDALS.get(r.rank, f"{r.rank}.")
        round_part = (
            f" (+{r.round_points} nesta ronda)"
            if show_round and r.round_points is not None
            else ""
        )
        line = f"{marker} {_esc(r.nickname)} — {r.total_points} pts{round_part}"
        if highlight_id is not None and r.telegram_id == highlight_id:
            line = f"➤ <b>{line}</b>"
        lines.append(line)
    return "\n".join(lines)


def fmt_my_predictions(views: list[PredictionView]) -> str:
    if not views:
        return "Ainda não fizeste previsões. Usa /prever."
    lines = ["<b>As tuas previsões</b>"]
    for v in views:
        m = v.match
        pred = (
            f"{v.pred_home}-{v.pred_away}"
            if v.pred_home is not None
            else "—"
        )
        if m.home_score is not None:
            real = f" (resultado {m.home_score}-{m.away_score}"
            real += f", {v.points or 0} pts)" if v.points is not None else ")"
        else:
            real = ""
        lines.append(f"• {_esc(m.home)} vs {_esc(m.away)}: {pred}{real}")
    return "\n".join(lines)
