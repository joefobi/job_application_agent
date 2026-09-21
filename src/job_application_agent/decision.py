"""Decision policy contracts for post-scoring application flow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from job_application_agent.filters import FilterResult
from job_application_agent.models import JobStatus
from job_application_agent.scoring import ScoreBreakdown


class DecisionAction(StrEnum):
    """Policy actions for the next job state."""

    REJECT = "reject"
    SAVE = "save"
    REVIEW = "review"
    APPROVE_TO_APPLY = "approve_to_apply"
    SKIP = "skip"


@dataclass(frozen=True)
class DecisionInput:
    """Inputs required by the decision policy.

    This consumes the concrete hard-filter and fit-scoring outputs from steps
    5-8 while keeping the final status transition deterministic.
    """

    job_id: int
    ledger_status: JobStatus
    filter_result: FilterResult | None = None
    score_breakdown: ScoreBreakdown | None = None
    reason_codes: tuple[str, ...] = ()
    risk_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecisionResult:
    """Result produced by the decision policy."""

    job_id: int
    action: DecisionAction
    target_status: JobStatus
    reason_codes: tuple[str, ...]
    explanation: str


class DecisionPolicy:
    """Deterministic policy that converts scoring signals into a job action."""

    def __init__(self, approve_threshold: int = 85, review_threshold: int = 65) -> None:
        """Create a decision policy.

        Args:
            approve_threshold: Minimum score for auto-approval to apply.
            review_threshold: Minimum score for human review instead of rejection.
        """

        if not 0 <= review_threshold <= approve_threshold <= 100:
            raise ValueError(
                "Policy thresholds must satisfy 0 <= review <= approve <= 100."
            )
        self.approve_threshold = approve_threshold
        self.review_threshold = review_threshold

    def decide(self, decision_input: DecisionInput) -> DecisionResult:
        """Choose the next action for a job.

        Args:
            decision_input: Ledger, filter, and scoring signals for a job.

        Returns:
            A deterministic policy decision.
        """

        status = decision_input.ledger_status
        if status == JobStatus.APPLIED:
            return _result(
                decision_input,
                DecisionAction.SKIP,
                JobStatus.APPLIED,
                "already_applied",
                "The job was already applied to.",
            )
        if status == JobStatus.DUPLICATE_POSSIBLE:
            return _result(
                decision_input,
                DecisionAction.REVIEW,
                JobStatus.NEEDS_REVIEW,
                "duplicate_possible",
                "The job may duplicate an existing application.",
            )
        if (
            decision_input.filter_result is not None
            and not decision_input.filter_result.passed
        ):
            return _result(
                decision_input,
                DecisionAction.REJECT,
                JobStatus.REJECTED,
                "hard_filter_rejected",
                "A hard filter rejected the job.",
            )
        score_breakdown = decision_input.score_breakdown
        if score_breakdown is not None and score_breakdown.rejected_by_hard_filter:
            return _result(
                decision_input,
                DecisionAction.REJECT,
                JobStatus.REJECTED,
                "score_rejected_by_hard_filter",
                "The scorer rejected the job via hard filters.",
            )
        if score_breakdown is None:
            return _result(
                decision_input,
                DecisionAction.REVIEW,
                JobStatus.NEEDS_REVIEW,
                "missing_fit_score",
                "No fit score is available yet.",
            )
        if decision_input.risk_codes:
            return _result(
                decision_input,
                DecisionAction.REVIEW,
                JobStatus.NEEDS_REVIEW,
                "risks_present",
                "The score has risk flags that require review.",
            )
        if score_breakdown.total >= self.approve_threshold:
            return _result(
                decision_input,
                DecisionAction.APPROVE_TO_APPLY,
                JobStatus.APPROVED_TO_APPLY,
                "score_above_approve_threshold",
                "The job is above the approval threshold.",
            )
        if score_breakdown.total >= self.review_threshold:
            return _result(
                decision_input,
                DecisionAction.REVIEW,
                JobStatus.NEEDS_REVIEW,
                "score_above_review_threshold",
                "The job is promising enough for review.",
            )
        return _result(
            decision_input,
            DecisionAction.REJECT,
            JobStatus.REJECTED,
            "score_below_review_threshold",
            "The job score is below the review threshold.",
        )


def _result(
    decision_input: DecisionInput,
    action: DecisionAction,
    target_status: JobStatus,
    reason_code: str,
    explanation: str,
) -> DecisionResult:
    """Build a decision result with inherited reason and risk codes."""

    return DecisionResult(
        job_id=decision_input.job_id,
        action=action,
        target_status=target_status,
        reason_codes=decision_input.reason_codes
        + decision_input.risk_codes
        + (reason_code,),
        explanation=explanation,
    )
