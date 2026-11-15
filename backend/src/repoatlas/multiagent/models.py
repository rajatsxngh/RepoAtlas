"""What the stages of the pipeline hand each other (MAS-1 to MAS-5, decision 0023).

- The planner turns a question and a ``PlanContext`` into a ``Plan``: at most four ``Branch``es,
  each a sub-question with seed symbols and a ``BranchBudget``.
- An explorer turns a branch into a ``BranchResult``: its ledger, its ``BranchNotes``, and a
  status.
- ``MultiAgentConfig`` holds the limits of one answer (defaults: decision 0023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repoatlas.agent.guard import MIN_RESULT_TOKENS
from repoatlas.agent.ledger import Ledger
from repoatlas.answers.schema import ToolUse
from repoatlas.llm.types import Role, Usage
from repoatlas.retrieval.evidence import EvidenceRef

MAX_BRANCHES = 4  # the most a plan may have, whatever the settings say
MIN_BRANCH_STEPS = 2  # a search and a look at what it found
MAX_SEEDS = 5
MAX_SUB_QUESTION_CHARS = 500
MAX_FINDINGS = 8
MAX_FINDING_CHARS = 600


class MultiAgentConfig(BaseModel):
    """Limits of one multi-agent answer."""

    model_config = ConfigDict(frozen=True)

    max_branches: int = Field(default=MAX_BRANCHES, ge=1, le=MAX_BRANCHES)
    concurrency: int = Field(default=3, ge=1, le=MAX_BRANCHES)  # branches running at once
    total_steps: int = Field(default=24, ge=MIN_BRANCH_STEPS, le=200)  # all branches together
    branch_max_steps: int = Field(default=12, ge=MIN_BRANCH_STEPS, le=50)
    max_input_tokens: int = Field(default=400_000, ge=1_000)  # all branches together
    per_tool_result_tokens: int = Field(default=2_000, ge=MIN_RESULT_TOKENS)
    step_output_tokens: int = Field(default=2_048, ge=256)  # one exploration turn
    branch_timeout_s: float = Field(default=180.0, gt=0)
    evidence_tokens: int = Field(default=12_000, ge=500)  # evidence the writer is shown
    context_k: int = Field(default=10, ge=1, le=10)  # search results the planner is shown
    plan_output_tokens: int = Field(default=1_024, ge=256)
    notes_output_tokens: int = Field(default=1_024, ge=256)
    verify_output_tokens: int = Field(default=2_048, ge=256)
    plan_role: Role = "large"
    explore_role: Role = "small"
    write_role: Role = "large"
    verify_role: Role = "large"

    @model_validator(mode="after")
    def _steps_fit(self) -> Self:
        if self.total_steps < self.max_branches * MIN_BRANCH_STEPS:
            raise ValueError(
                f"total_steps must allow {MIN_BRANCH_STEPS} steps for each of "
                f"{self.max_branches} branches."
            )
        return self


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class ContextEntry(BaseModel):
    """One place the first search found: where, and which symbol. No code."""

    model_config = ConfigDict(frozen=True)

    path: str
    start_line: int
    end_line: int
    symbol_key: str | None = None


class PlanContext(BaseModel):
    """What the planner knows about the repository: the places one search for the question
    found, best first."""

    model_config = ConfigDict(frozen=True)

    entries: list[ContextEntry] = Field(default_factory=list)

    @classmethod
    def from_refs(cls, refs: list[EvidenceRef]) -> PlanContext:
        entries: list[ContextEntry] = []
        for ref in refs:
            entry = ContextEntry(
                path=ref.path,
                start_line=ref.start_line,
                end_line=ref.end_line,
                symbol_key=ref.symbol_key,
            )
            if entry not in entries:
                entries.append(entry)
        return cls(entries=entries)

    @property
    def symbol_keys(self) -> list[str]:
        return list(dict.fromkeys(e.symbol_key for e in self.entries if e.symbol_key))


class BranchBudget(BaseModel):
    """One branch's share of the answer's limits. ``max_cost_usd`` is set once the money for
    exploring is known (``Plan.funded``)."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(ge=1)
    max_input_tokens: int = Field(ge=1)
    max_cost_usd: float = Field(default=0.0, ge=0)


class Branch(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # b1, b2, …
    sub_question: str
    seed_symbols: list[str] = Field(default_factory=list)  # symbol keys to start from
    budget: BranchBudget


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    branches: list[Branch]
    fallback: bool = False  # the planner gave no usable branch: one branch with the question

    @property
    def steps(self) -> int:
        return sum(branch.budget.max_steps for branch in self.branches)

    def funded(self, explore_usd: float) -> Plan:
        """The plan with the exploration money divided by each branch's share of the steps."""
        total = self.steps
        branches = [
            branch.model_copy(
                update={
                    "budget": branch.budget.model_copy(
                        update={
                            "max_cost_usd": max(0.0, explore_usd) * branch.budget.max_steps / total
                        }
                    )
                }
            )
            for branch in self.branches
        ]
        return self.model_copy(update={"branches": branches})


class BranchDraft(BaseModel):
    """One branch as the planner writes it."""

    sub_question: str = Field(min_length=1, max_length=MAX_SUB_QUESTION_CHARS)
    seed_symbols: list[str] = Field(max_length=2 * MAX_SEEDS)
    steps: int = Field(ge=1, le=200)


class PlanDraft(BaseModel):
    """What the planner returns. More branches than allowed are cut, not rejected."""

    branches: list[BranchDraft] = Field(max_length=2 * MAX_BRANCHES)


# ---------------------------------------------------------------------------
# Exploring
# ---------------------------------------------------------------------------

# complete: the explorer ended on its own. partial: a limit stopped it. timeout: it passed its
# time limit. failed: it raised, or the API refused it.
BranchStatus = Literal["complete", "partial", "timeout", "failed"]


class Finding(BaseModel):
    """One thing a branch established, citing IDs of its own ledger."""

    text: str = Field(min_length=1, max_length=MAX_FINDING_CHARS)
    evidence_ids: list[str] = Field(max_length=8)


class BranchNotes(BaseModel):
    """What an explorer reports at the end of its branch."""

    findings: list[Finding] = Field(max_length=MAX_FINDINGS)
    unanswered: str | None = Field(default=None, max_length=MAX_FINDING_CHARS)


@dataclass
class BranchResult:
    """What one branch left behind. ``ledger`` holds everything its tools returned, also when
    the branch did not finish."""

    branch: Branch
    ledger: Ledger
    notes: BranchNotes
    status: BranchStatus
    stop: str | None = None  # why exploring stopped (an agent stop reason), when it ran
    error: str | None = None  # the kind of failure, for a failed branch
    steps: int = 0
    tool_uses: list[ToolUse] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
