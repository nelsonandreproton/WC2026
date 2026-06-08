"""Business logic layer — pure functions over a DB session.

Handlers (Telegram I/O) and jobs (scheduler) are thin wrappers around this.
Everything here is testable without a running bot. All time comparisons use
timezone-aware UTC; the `now` argument is injectable for deterministic tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from wc2026bot.db.models import (
    ChampionBet,
    Match,
    MatchStatus,
    Player,
    Prediction,
)

NICKNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


class ServiceError(Exception):
    """User-facing domain error; message is safe to show in chat."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Players / nicknames
# --------------------------------------------------------------------------- #

def validate_nickname(nickname: str) -> str:
    """Return the cleaned nickname or raise ServiceError."""
    cleaned = nickname.strip()
    if not NICKNAME_RE.match(cleaned):
        raise ServiceError(
            "Nickname inválido. Usa 3–20 caracteres: letras, números ou _."
        )
    return cleaned


def register_player(session: Session, telegram_id: int, nickname: str) -> Player:
    """Create a player, or raise if the nickname is taken or already registered."""
    cleaned = validate_nickname(nickname)
    ci = cleaned.casefold()

    existing = session.get(Player, telegram_id)
    if existing is not None:
        raise ServiceError("Já estás registado. Usa /nick para mudar o nickname.")

    taken = session.scalar(select(Player).where(Player.nickname_ci == ci))
    if taken is not None:
        raise ServiceError("Esse nickname já está em uso. Escolhe outro.")

    player = Player(telegram_id=telegram_id, nickname=cleaned, nickname_ci=ci)
    session.add(player)
    session.commit()
    return player


def change_nickname(session: Session, telegram_id: int, nickname: str) -> Player:
    cleaned = validate_nickname(nickname)
    ci = cleaned.casefold()
    player = session.get(Player, telegram_id)
    if player is None:
        raise ServiceError("Não estás registado. Usa /start primeiro.")

    taken = session.scalar(
        select(Player).where(Player.nickname_ci == ci, Player.telegram_id != telegram_id)
    )
    if taken is not None:
        raise ServiceError("Esse nickname já está em uso. Escolhe outro.")

    player.nickname = cleaned
    player.nickname_ci = ci
    session.commit()
    return player


def get_player(session: Session, telegram_id: int) -> Player | None:
    return session.get(Player, telegram_id)


# --------------------------------------------------------------------------- #
# Match listing
# --------------------------------------------------------------------------- #

def open_matches(session: Session, now: datetime | None = None) -> list[Match]:
    """Matches still open for prediction (lock in the future), soonest first."""
    moment = _now(now)
    stmt = (
        select(Match)
        .where(Match.lock_utc > moment)
        .where(Match.status.in_([MatchStatus.SCHEDULED, MatchStatus.TIMED]))
        .order_by(Match.kickoff_utc.asc())
    )
    return list(session.scalars(stmt).all())


def is_locked(match: Match, now: datetime | None = None) -> bool:
    return _now(now) >= match.lock_utc


# --------------------------------------------------------------------------- #
# Predictions
# --------------------------------------------------------------------------- #

def upsert_prediction(
    session: Session,
    telegram_id: int,
    match_id: int,
    pred_home: int,
    pred_away: int,
    now: datetime | None = None,
) -> Prediction:
    """Create or update a prediction. Raises if player unknown, match unknown,
    scores invalid, or the match is locked."""
    if pred_home < 0 or pred_away < 0:
        raise ServiceError("Os golos não podem ser negativos.")
    if pred_home > 99 or pred_away > 99:
        raise ServiceError("Golos a mais. Sê realista. 🙂")

    player = session.get(Player, telegram_id)
    if player is None:
        raise ServiceError("Não estás registado. Usa /start primeiro.")

    match = session.get(Match, match_id)
    if match is None:
        raise ServiceError("Jogo não encontrado.")
    if is_locked(match, now):
        raise ServiceError("As previsões para este jogo já estão fechadas.")

    pred = session.scalar(
        select(Prediction).where(
            Prediction.player_id == telegram_id, Prediction.match_id == match_id
        )
    )
    if pred is None:
        pred = Prediction(
            player_id=telegram_id,
            match_id=match_id,
            pred_home=pred_home,
            pred_away=pred_away,
        )
        session.add(pred)
    else:
        pred.pred_home = pred_home
        pred.pred_away = pred_away
    session.commit()
    return pred


@dataclass(frozen=True)
class PredictionView:
    match: Match
    pred_home: int | None
    pred_away: int | None
    points: int | None


def my_predictions(session: Session, telegram_id: int) -> list[PredictionView]:
    """All of a player's predictions, most recent matches first."""
    stmt = (
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(Prediction.player_id == telegram_id)
        .order_by(Match.kickoff_utc.desc())
    )
    rows = session.execute(stmt).all()
    return [
        PredictionView(
            match=m, pred_home=p.pred_home, pred_away=p.pred_away, points=p.points
        )
        for p, m in rows
    ]


# --------------------------------------------------------------------------- #
# Champion bet
# --------------------------------------------------------------------------- #

def set_champion_bet(
    session: Session,
    telegram_id: int,
    team: str,
    champion_lock_utc: datetime,
    now: datetime | None = None,
) -> ChampionBet:
    """Set/update the champion pick. Locked once the opening match kicks off."""
    team_clean = team.strip()
    if not team_clean:
        raise ServiceError("Indica uma equipa.")
    if len(team_clean) > 60:
        raise ServiceError("Nome de equipa demasiado longo (máx. 60 caracteres).")
    if _now(now) >= champion_lock_utc:
        raise ServiceError("As apostas no campeão já estão fechadas.")

    player = session.get(Player, telegram_id)
    if player is None:
        raise ServiceError("Não estás registado. Usa /start primeiro.")

    bet = session.get(ChampionBet, telegram_id)
    if bet is None:
        bet = ChampionBet(player_id=telegram_id, team=team_clean)
        session.add(bet)
    else:
        if bet.locked:
            raise ServiceError("As apostas no campeão já estão fechadas.")
        bet.team = team_clean
    session.commit()
    return bet


def get_champion_bet(session: Session, telegram_id: int) -> ChampionBet | None:
    return session.get(ChampionBet, telegram_id)
