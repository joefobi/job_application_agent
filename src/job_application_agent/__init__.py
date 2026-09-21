"""Job application agent package."""

from job_application_agent.application_worker import (
    ApplicationPreparation,
    ApplicationWorker,
)
from job_application_agent.decision import DecisionInput, DecisionPolicy, DecisionResult
from job_application_agent.deduplication import DeduplicationService, DuplicateMatch
from job_application_agent.discovery import DiscoveryQuery, build_discovery_queries
from job_application_agent.filters import (
    FilterResult,
    HardFilterCriteria,
    HardFilterService,
)
from job_application_agent.models import (
    CandidateProfile,
    CompensationRange,
    FollowUpReminder,
    FollowUpStatus,
    JobPosting,
    JobSource,
    JobStatus,
    SubmissionConfirmation,
)
from job_application_agent.parser import JobParser
from job_application_agent.scoring import FitScorer, ScoreBreakdown, ScoringCriteria
from job_application_agent.storage import ApplicationLedger, ApplicationStore
from job_application_agent.tracking import (
    FollowUpTracker,
    SubmissionConfirmationRecorder,
    SubmissionConfirmationRequest,
)

__all__ = [
    "__version__",
    "ApplicationLedger",
    "ApplicationPreparation",
    "ApplicationStore",
    "ApplicationWorker",
    "CandidateProfile",
    "CompensationRange",
    "DecisionInput",
    "DecisionPolicy",
    "DecisionResult",
    "DeduplicationService",
    "DiscoveryQuery",
    "DuplicateMatch",
    "FilterResult",
    "FitScorer",
    "FollowUpReminder",
    "FollowUpStatus",
    "FollowUpTracker",
    "HardFilterCriteria",
    "HardFilterService",
    "JobParser",
    "JobPosting",
    "JobSource",
    "JobStatus",
    "ScoreBreakdown",
    "ScoringCriteria",
    "SubmissionConfirmation",
    "SubmissionConfirmationRecorder",
    "SubmissionConfirmationRequest",
    "build_discovery_queries",
]

__version__ = "0.1.0"
