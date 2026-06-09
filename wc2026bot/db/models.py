"""SQLAlchemy 2.x ORM models for the WC2026 prediction bot.

Conventions (per project rules):
- DateTime columns (never sqlalchemy.Timestamp).
- All timestamps are timezone-aware UTC (datetime.now(UTC)).
- No mutation helpers here; models are plain data holders.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """DateTime that always stores/returns timezone-aware UTC.

    SQLite's DateTime(timezone=True) does NOT actually persist the tz offset:
    it stores a naive string and reads it back naive, so a stored aware value
    compares unequal to the original. This decorator normalizes every input to
    UTC before storing and re-attaches UTC on the way out, so callers always
    get aware UTC datetimes regardless of backend.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # Treat naive input as already-UTC rather than guessing local tz.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class MatchStatus(str, enum.Enum):
    """Mirrors the subset of football-data.org statuses we care about."""

    SCHEDULED = "SCHEDULED"
    TIMED = "TIMED"
    IN_PLAY = "IN_PLAY"
    PAUSED = "PAUSED"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    CANCELLED = "CANCELLED"


class Player(Base):
    __tablename__ = "players"

    telegram_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    # Stored lowercased for case-insensitive uniqueness; display value kept too.
    nickname: Mapped[str] = mapped_column(String(20), nullable=False)
    nickname_ci: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="player", cascade="all, delete-orphan"
    )
    champion_bet: Mapped["ChampionBet | None"] = relationship(
        back_populates="player", cascade="all, delete-orphan", uselist=False
    )


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint(
            "(home_score IS NULL) = (away_score IS NULL)",
            name="ck_scores_both_or_neither",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Provider's match id; idempotency key for sync.
    ext_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    matchday: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    # Canonical round identity (see wc2026bot.rounds). Group: GROUP-1.. ;
    # knockouts: LAST_16, QUARTER_FINALS, ... Indexed for round queries.
    round_key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    home: Mapped[str] = mapped_column(String(60), nullable=False)
    away: Mapped[str] = mapped_column(String(60), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False
    )
    lock_utc: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=20),
        default=MatchStatus.SCHEDULED,
        nullable=False,
    )
    # Score at 90' (knockout extra time / penalties are ignored for scoring).
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Who advanced: HOME_TEAM / AWAY_TEAM / DRAW / None. Source of truth for the
    # champion (final winner), since penalties can decide a 90'-draw.
    winner: Mapped[str | None] = mapped_column(String(12), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    # Set once the "match starts in 30 min" DM batch is sent.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    # Set once the result DM batch is sent to all active players.
    result_broadcast_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )

    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("player_id", "match_id", name="uq_player_match"),
        CheckConstraint("pred_home >= 0 AND pred_away >= 0", name="ck_pred_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.telegram_id", ondelete="CASCADE"), nullable=False
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    pred_home: Mapped[int] = mapped_column(Integer, nullable=False)
    pred_away: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    # NULL until the match is scored; then 0/3/5.
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    player: Mapped[Player] = relationship(back_populates="predictions")
    match: Mapped[Match] = relationship(back_populates="predictions")


class ChampionBet(Base):
    __tablename__ = "champion_bets"

    # One bet per player -> player_id is the PK.
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.telegram_id", ondelete="CASCADE"), primary_key=True
    )
    team: Mapped[str] = mapped_column(String(60), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    locked: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 100 or 0, filled only after the final.
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    player: Mapped[Player] = relationship(back_populates="champion_bet")


class Round(Base):
    __tablename__ = "rounds"

    # Canonical round identity (see wc2026bot.rounds), e.g. 'GROUP-1', 'FINAL'.
    round_key: Mapped[str] = mapped_column(
        String(40), primary_key=True, autoincrement=False
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # Sort order for displaying rounds in sequence (group MDs, then knockouts).
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Set when the standings DM batch for this round has been sent.
    table_sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
