"""Edge confidences and call-resolution tiers as data (PRD §9.1, decision 0005).

Calibration refits them on repos outside the benchmark and records the result in a decision
record; ``TIERS_VERSION`` changes whenever a value here does. Version 2 holds the values fitted
in decision 0017 (T1, T2, T3, T5, T6, and tested-by through imports); T4 and tested-by by
convention had too few judged edges and keep their initial values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

TIERS_VERSION = 2


class Tier(StrEnum):
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"
    T5 = "T5"
    T6 = "T6"


@dataclass(frozen=True, slots=True)
class TierSpec:
    tier: Tier
    situation: str
    confidence: float  # T6 splits this total across its candidates


TIER_TABLE: tuple[TierSpec, ...] = (
    TierSpec(
        Tier.T1,
        "Callee bound by import to a resolved symbol, or defined in the same file scope",
        0.94,
    ),
    TierSpec(
        Tier.T2,
        "alias.func() where alias is an imported module, namespace, or class with that member",
        0.99,
    ),
    TierSpec(
        Tier.T3,
        "self.m(), this.m(), cls.m() resolved on the enclosing class or its resolved bases",
        0.99,
    ),
    TierSpec(Tier.T4, "super().m() / super.m() resolved on a base class", 0.80),
    TierSpec(Tier.T5, "obj.m() where exactly one class or module in the repo defines m", 0.25),
    TierSpec(Tier.T6, "obj.m() with 2-5 candidates: one edge per candidate", 0.29),
)
TIERS: dict[Tier, TierSpec] = {spec.tier: spec for spec in TIER_TABLE}

MAX_CANDIDATES = 5  # more candidates than this: an unresolved call, no edges
MAX_UNRESOLVED_CANDIDATES = 20  # candidate keys kept on an unresolved call


def tier_confidence(tier: Tier, candidates: int = 1) -> float:
    """The confidence of one edge resolved at ``tier`` (T6 divides by the candidate count)."""
    spec = TIERS[tier]
    return spec.confidence / candidates if tier == Tier.T6 else spec.confidence


CONTAINS_CONFIDENCE = 1.0
IMPORT_CONFIDENCE = 1.0
INHERITS_CONFIDENCE = 0.95
TESTED_BY_IMPORT_CONFIDENCE = 0.98
TESTED_BY_CONVENTION_CONFIDENCE = 0.6
ROUTES_TO_CONFIDENCE = 0.95
