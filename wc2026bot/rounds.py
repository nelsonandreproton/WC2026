"""Canonical round identity, derived from (stage, matchday).

Why this exists: football-data.org returns matchday only for the GROUP_STAGE;
knockout fixtures come back with matchday = null (→ 0 after our coercion). So
matchday alone cannot identify a knockout round — every knockout match would
collapse into "round 0". Round identity therefore comes from the *stage* for
knockouts and from the matchday for the group stage.

`round_key` is a stable string used to group matches into rounds. `round_name`
is the human label shown to players.
"""

from __future__ import annotations

# Knockout stages in order, mapped to display names.
KNOCKOUT_NAMES = {
    "LAST_16": "Oitavos de Final",
    "ROUND_OF_16": "Oitavos de Final",
    "QUARTER_FINALS": "Quartos de Final",
    "QUARTER_FINAL": "Quartos de Final",
    "SEMI_FINALS": "Meias-Finais",
    "SEMI_FINAL": "Meias-Finais",
    "THIRD_PLACE": "3.º e 4.º Lugar",
    "FINAL": "Final",
}

GROUP_STAGES = {"GROUP_STAGE", "GROUP_STAGE_1", "GROUPS"}


def round_key(stage: str, matchday: int) -> str:
    """Stable key grouping matches into a single round.

    Group stage: one round per matchday -> 'GROUP-1', 'GROUP-2', 'GROUP-3'.
    Knockouts: one round per stage -> 'LAST_16', 'QUARTER_FINALS', ...
    """
    stage_up = (stage or "").upper()
    if stage_up in GROUP_STAGES:
        return f"GROUP-{matchday or 0}"
    return stage_up or "UNKNOWN"


def round_name(stage: str, matchday: int) -> str:
    stage_up = (stage or "").upper()
    if stage_up in GROUP_STAGES:
        return f"Jornada {matchday}" if matchday else "Fase de Grupos"
    return KNOCKOUT_NAMES.get(stage_up, stage_up.replace("_", " ").title())


def is_final(stage: str) -> bool:
    return (stage or "").upper() == "FINAL"
