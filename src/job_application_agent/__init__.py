"""Job application agent package."""

from job_application_agent.decision import DecisionInput, DecisionPolicy, DecisionResult
from job_application_agent.deduplication import DeduplicationService, DuplicateMatch
from job_application_agent.filters import (
    FilterResult,
    HardFilterCriteria,
    HardFilterService,
)
from job_application_agent.models import (
    CandidateProfile,
    CompensationRange,
    JobPosting,
    JobSource,
    JobStatus,
)
from job_application_agent.parser import JobParser
from job_application_agent.scoring import FitScorer, ScoreBreakdown, ScoringCriteria
from job_application_agent.storage import ApplicationLedger, ApplicationStore

__all__ = [
    "__version__",
    "ApplicationLedger",
    "ApplicationStore",
    "CandidateProfile",
    "CompensationRange",
    "DecisionInput",
    "DecisionPolicy",
    "DecisionResult",
    "DeduplicationService",
    "DuplicateMatch",
    "FilterResult",
    "FitScorer",
    "HardFilterCriteria",
    "HardFilterService",
    "JobParser",
    "JobPosting",
    "JobSource",
    "JobStatus",
    "ScoreBreakdown",
    "ScoringCriteria",
]

__version__ = "0.1.0"
