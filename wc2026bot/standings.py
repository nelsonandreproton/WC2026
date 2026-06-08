"""Standings computation and champion bonus. Pure logic over a session."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wc2026bot.db.models import (
    ChampionBet,
    Match,
    MatchStatus,
    Player,
    Prediction,
)
from wc2026bot.scoring import score_champion_bet


@dataclass(frozen=True)
class StandingRow:
    rank: int
    telegram_id: int
    nickname: str
    total_points: int
    exact_hits: int
    round_points: int | None = None  # only set in per-round tables


def _exact_hits_subquery(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(Prediction.player_id, func.count())
        .where(Prediction.points == 5)
        .group_by(Prediction.player_id)
    ).all()
    return {pid: n for pid, n in rows}


def compute_standings(session: Session) -> list[StandingRow]:
    """Cumulative standings across all scored matches + applied champion bonus.

    Sort: total desc, then exact hits desc, then nickname asc (stable, fair).
    """
    # Sum of match points per player (NULL points -> not yet scored -> ignored).
    match_points = dict(
        session.execute(
            select(Prediction.player_id, func.coalesce(func.sum(Prediction.points), 0))
            .group_by(Prediction.player_id)
        ).all()
    )
    # Applied champion bonus (points already written post-final).
    champ_points = dict(
        session.execute(
            select(ChampionBet.player_id, func.coalesce(ChampionBet.points, 0))
        ).all()
    )
    exact_hits = _exact_hits_subquery(session)

    players = session.scalars(select(Player).where(Player.is_active.is_(True))).all()
    rows = []
    for p in players:
        total = match_points.get(p.telegram_id, 0) + champ_points.get(p.telegram_id, 0)
        rows.append((p, total, exact_hits.get(p.telegram_id, 0)))

    rows.sort(key=lambda r: (-r[1], -r[2], r[0].nickname_ci))
    return [
        StandingRow(
            rank=i + 1,
            telegram_id=p.telegram_id,
            nickname=p.nickname,
            total_points=total,
            exact_hits=hits,
        )
        for i, (p, total, hits) in enumerate(rows)
    ]


def round_points(session: Session, round_key: str) -> dict[int, int]:
    """Points each player earned from matches in the given round."""
    rows = session.execute(
        select(Prediction.player_id, func.coalesce(func.sum(Prediction.points), 0))
        .join(Match, Prediction.match_id == Match.id)
        .where(Match.round_key == round_key)
        .where(Prediction.points.is_not(None))
        .group_by(Prediction.player_id)
    ).all()
    return {pid: pts for pid, pts in rows}


# A match is "concluded" for round-completion purposes when it's finished OR it
# won't be played (cancelled/postponed) — otherwise a cancelled game would block
# the round's standings table forever.
CONCLUDED_STATUSES = (
    MatchStatus.FINISHED,
    MatchStatus.CANCELLED,
    MatchStatus.POSTPONED,
)


def is_round_complete(session: Session, round_key: str) -> bool:
    """True if the round has matches and none are still pending/in-play.

    Cancelled/postponed matches count as concluded so they don't block the
    round's table indefinitely.
    """
    total = session.scalar(
        select(func.count()).select_from(Match).where(Match.round_key == round_key)
    )
    if not total:
        return False
    pending = session.scalar(
        select(func.count())
        .select_from(Match)
        .where(Match.round_key == round_key)
        .where(Match.status.not_in(CONCLUDED_STATUSES))
    )
    return pending == 0


def champion_from_final(session: Session) -> str | None:
    """Resolve the champion team name from the FINISHED final's winner.

    Returns the team name (home/away depending on winner), or None if the final
    isn't finished or has no decisive winner recorded yet.
    """
    final = session.scalar(
        select(Match).where(Match.round_key == "FINAL").where(
            Match.status == MatchStatus.FINISHED
        )
    )
    if final is None or not final.winner:
        return None
    if final.winner == "HOME_TEAM":
        return final.home
    if final.winner == "AWAY_TEAM":
        return final.away
    return None  # DRAW with no winner shouldn't happen for a final


def apply_champion_bonus(session: Session, actual_champion: str) -> list[int]:
    """Write champion-bet points after the final. Idempotent: only bets with
    points IS NULL are scored. Returns telegram_ids that won the +100."""
    bets = session.scalars(
        select(ChampionBet).where(ChampionBet.points.is_(None))
    ).all()
    winners = []
    for bet in bets:
        pts = score_champion_bet(bet.team, actual_champion)
        bet.points = pts
        bet.locked = True
        if pts > 0:
            winners.append(bet.player_id)
    session.commit()
    return winners
