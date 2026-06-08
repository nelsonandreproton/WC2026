"""Idempotent sync of provider fixtures into the local DB.

Pure-ish: takes a session and a list of FixtureDTO. No network here, so it is
fully testable with hand-built DTOs.

Idempotency key is FixtureDTO.ext_id (the provider's match id). Re-running with
the same data is a no-op; changed kickoff times, team names (knockout fill-in)
or statuses are updated in place. lock_utc is always recomputed from kickoff so
a rescheduled match locks at the right time.

Rounds are keyed by the canonical round_key (see wc2026bot.rounds), NOT by
matchday — knockout fixtures have no matchday from the provider.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from wc2026bot.db.models import Match, MatchStatus, Round
from wc2026bot.providers.base import FixtureDTO
from wc2026bot.rounds import round_key, round_name

DEFAULT_LOCK_MINUTES = 15

# Display order: group matchdays first (1..3), then knockouts in bracket order.
_KNOCKOUT_ORDER = {
    "LAST_16": 100,
    "ROUND_OF_16": 100,
    "QUARTER_FINALS": 200,
    "SEMI_FINALS": 300,
    "THIRD_PLACE": 400,
    "FINAL": 500,
}


def _sort_order(stage: str, matchday: int) -> int:
    key = round_key(stage, matchday)
    if key.startswith("GROUP-"):
        return matchday or 0
    return _KNOCKOUT_ORDER.get(key, 999)


def _coerce_status(raw: str) -> MatchStatus:
    try:
        return MatchStatus(raw)
    except ValueError:
        return MatchStatus.SCHEDULED


def sync_fixtures(
    session: Session,
    fixtures: list[FixtureDTO],
    lock_minutes: int = DEFAULT_LOCK_MINUTES,
) -> dict[str, int]:
    """Upsert fixtures and ensure a Round row per canonical round.

    Returns counts: {"created": n, "updated": n, "rounds": n}.
    Does NOT overwrite a stored score with NULL (a provider blip returning no
    score must never wipe a recorded result).
    """
    created = updated = 0
    lock_delta = timedelta(minutes=lock_minutes)
    seen_rounds: set[str] = set()

    existing = {m.ext_id: m for m in session.scalars(select(Match)).all()}

    for dto in fixtures:
        lock_utc = dto.kickoff_utc - lock_delta
        status = _coerce_status(dto.status)
        rkey = round_key(dto.stage, dto.matchday)
        match = existing.get(dto.ext_id)

        if match is None:
            match = Match(
                ext_id=dto.ext_id,
                matchday=dto.matchday,
                stage=dto.stage,
                round_key=rkey,
                home=dto.home,
                away=dto.away,
                kickoff_utc=dto.kickoff_utc,
                lock_utc=lock_utc,
                status=status,
            )
            _apply_result(match, dto)
            session.add(match)
            created += 1
        else:
            match.matchday = dto.matchday
            match.stage = dto.stage
            match.round_key = rkey
            match.home = dto.home
            match.away = dto.away
            match.kickoff_utc = dto.kickoff_utc
            match.lock_utc = lock_utc
            match.status = status
            _apply_result(match, dto)
            updated += 1

        seen_rounds.add(rkey)

    _ensure_rounds(session, fixtures)
    session.commit()
    return {"created": created, "updated": updated, "rounds": len(seen_rounds)}


def _apply_result(match: Match, dto: FixtureDTO) -> None:
    """Copy 90' score and winner only when the provider reports them.

    Never clears an existing stored score/winner with a None from the provider.
    """
    if dto.home_score is not None and dto.away_score is not None:
        match.home_score = dto.home_score
        match.away_score = dto.away_score
    if dto.winner is not None:
        match.winner = dto.winner


def _ensure_rounds(session: Session, fixtures: list[FixtureDTO]) -> None:
    existing = {r.round_key for r in session.scalars(select(Round)).all()}
    # Representative (stage, matchday) per round_key for naming/ordering.
    repr_by_key: dict[str, tuple[str, int]] = {}
    for dto in fixtures:
        repr_by_key.setdefault(
            round_key(dto.stage, dto.matchday), (dto.stage, dto.matchday)
        )

    for rkey, (stage, matchday) in repr_by_key.items():
        if rkey in existing:
            continue
        session.add(
            Round(
                round_key=rkey,
                name=round_name(stage, matchday),
                sort_order=_sort_order(stage, matchday),
            )
        )
