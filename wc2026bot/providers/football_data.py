"""football-data.org implementation of FootballProvider.

API docs: https://www.football-data.org/documentation/quickstart
Auth: header `X-Auth-Token: <API key>`.

Free tier is rate-limited (10 req/min at time of writing). We therefore:
- retry transient failures with tenacity, but NEVER retry HTTP 429
  (retrying a rate-limit just extends the ban — project rule).
- keep polling windowed (caller's job), not 24/7.

Score-at-90' note: football-data.org exposes score.regularTime and
score.fullTime. For knockout matches fullTime includes extra time, so we
prefer regularTime when present. For group games regularTime is usually null
and fullTime == 90' score, so we fall back to fullTime.
"""

from __future__ import annotations

import os
from datetime import datetime

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from wc2026bot.providers.base import FixtureDTO, FootballProvider

BASE_URL = "https://api.football-data.org/v4"
# World Cup competition code on football-data.org.
DEFAULT_COMPETITION = "WC"

# Statuses that carry a usable result for polling.
RESULT_STATUSES = {"IN_PLAY", "PAUSED", "FINISHED"}


def _is_not_rate_limit(exc: BaseException) -> bool:
    """tenacity predicate: retry anything except a 429 rate-limit."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code != 429
    return isinstance(exc, requests.RequestException)


def _parse_iso_utc(value: str) -> datetime:
    """Parse football-data's ISO-8601 UTC string (e.g. '2026-06-11T19:00:00Z')."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _extract_90_score(score: dict) -> tuple[int | None, int | None]:
    """Pull the regular-time (90') score, preferring regularTime over fullTime."""
    regular = score.get("regularTime") or {}
    if regular.get("home") is not None and regular.get("away") is not None:
        return regular["home"], regular["away"]
    full = score.get("fullTime") or {}
    return full.get("home"), full.get("away")


def _to_dto(match: dict) -> FixtureDTO:
    score = match.get("score") or {}
    home_score, away_score = _extract_90_score(score)
    return FixtureDTO(
        ext_id=match["id"],
        matchday=match.get("matchday") or 0,
        stage=match.get("stage") or "UNKNOWN",
        home=(match.get("homeTeam") or {}).get("name") or "TBD",
        away=(match.get("awayTeam") or {}).get("name") or "TBD",
        kickoff_utc=_parse_iso_utc(match["utcDate"]),
        status=match.get("status") or "SCHEDULED",
        home_score=home_score,
        away_score=away_score,
        # winner reflects who advanced (penalties included) — source of truth
        # for the champion, independent of the 90' score we use for scoring.
        winner=score.get("winner"),
    )


class FootballDataProvider(FootballProvider):
    def __init__(
        self,
        api_key: str | None = None,
        competition: str = DEFAULT_COMPETITION,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("FOOTBALL_DATA_API_KEY", "")
        if not self.api_key:
            raise ValueError("FOOTBALL_DATA_API_KEY is required")
        self.competition = competition
        self.timeout = timeout

    @retry(
        retry=retry_if_exception(_is_not_rate_limit),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _get_matches(self) -> list[dict]:
        url = f"{BASE_URL}/competitions/{self.competition}/matches"
        resp = requests.get(
            url,
            headers={"X-Auth-Token": self.api_key},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("matches", [])

    def fetch_fixtures(self) -> list[FixtureDTO]:
        return [_to_dto(m) for m in self._get_matches()]

    def fetch_results(self) -> list[FixtureDTO]:
        return [
            _to_dto(m)
            for m in self._get_matches()
            if (m.get("status") in RESULT_STATUSES)
        ]
