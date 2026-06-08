"""Provider abstraction so the data source can be swapped.

The rest of the codebase depends only on this interface and on FixtureDTO,
never on football-data.org's JSON shape. To switch to API-Football, write a
new class implementing FootballProvider — nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FixtureDTO:
    """A single match as exposed to the rest of the app.

    Provider-agnostic. `home_score`/`away_score` are the score at 90'
    (regular time) when known, else None. The provider implementation is
    responsible for extracting the *regular-time* score, not the
    after-extra-time/penalties score.
    """

    ext_id: int
    matchday: int
    stage: str
    home: str
    away: str
    kickoff_utc: datetime
    status: str
    home_score: int | None
    away_score: int | None
    # Outcome at full result (HOME_TEAM / AWAY_TEAM / DRAW / None). Unlike the
    # 90' score, this reflects who actually advanced (penalties included), so
    # it is the source of truth for the champion (final winner).
    winner: str | None = None


class FootballProvider(ABC):
    """Read-only access to World Cup fixtures and results."""

    @abstractmethod
    def fetch_fixtures(self) -> list[FixtureDTO]:
        """Return all known fixtures for the competition.

        Used by the daily sync. Includes scheduled, in-play and finished
        matches; the caller decides what to do with each.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_results(self) -> list[FixtureDTO]:
        """Return fixtures that are in-play or finished (for result polling).

        May be the same data as fetch_fixtures() filtered by status; kept
        separate so a provider can hit a cheaper/faster endpoint for live
        scores if one exists.
        """
        raise NotImplementedError
