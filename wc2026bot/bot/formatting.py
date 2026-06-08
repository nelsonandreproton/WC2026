"""Pure message-formatting helpers. No Telegram objects, fully testable."""

from __future__ import annotations

from datetime import UTC, datetime

from telegram.helpers import escape_markdown

from wc2026bot.service import PredictionView
from wc2026bot.standings import StandingRow

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _md(text: str) -> str:
    """Escape user-controlled text for Telegram legacy Markdown (v1)."""
    return escape_markdown(text, version=1)

WELCOME = (
    "⚽ *Bólão Mundial 2026*\n\n"
    "Prevê o resultado de cada jogo. Pontuação:\n"
    "• Acertar a direção (V/E/D): *+3*\n"
    "• Acertar o resultado exato: *+2* extra (total 5)\n"
    "• Apostar no campeão certo: *+100* no fim\n\n"
    "As previsões fecham uns minutos antes de cada jogo e ninguém vê as tuas.\n\n"
    "Comandos: /proximos /prever /minhas /campeao /tabela /nick /ajuda"
)

HELP = (
    "*Comandos*\n"
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
    return f"{_md(home)} vs {_md(away)} — _{fmt_countdown(lock_utc, now)}_"


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
        f"{emoji} *{_md(m.home)} {real} {_md(m.away)}*\n"
        f"A tua previsão: {pred} · *{pts} pts*"
    )


def fmt_standings(
    rows: list[StandingRow],
    title: str,
    highlight_id: int | None = None,
    show_round: bool = False,
) -> str:
    """Standings table. The viewer's own row is bolded and marked with ➤."""
    if not rows:
        return f"*{title}*\n(sem jogadores ainda)"
    lines = [f"*{title}*"]
    for r in rows:
        marker = MEDALS.get(r.rank, f"{r.rank}.")
        round_part = (
            f" (+{r.round_points} nesta ronda)"
            if show_round and r.round_points is not None
            else ""
        )
        line = f"{marker} {_md(r.nickname)} — {r.total_points} pts{round_part}"
        if highlight_id is not None and r.telegram_id == highlight_id:
            line = f"➤ *{line}*"
        lines.append(line)
    return "\n".join(lines)


def fmt_my_predictions(views: list[PredictionView]) -> str:
    if not views:
        return "Ainda não fizeste previsões. Usa /prever."
    lines = ["*As tuas previsões*"]
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
        lines.append(f"• {_md(m.home)} vs {_md(m.away)}: {pred}{real}")
    return "\n".join(lines)
