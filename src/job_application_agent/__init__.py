"""Job application agent package."""

from job_application_agent.application_worker import (
    ApplicationPreparation,
    ApplicationWorker,
)
from job_application_agent.decision import DecisionInput, DecisionPolicy, DecisionResult
from job_application_agent.deduplication import DeduplicationService, DuplicateMatch
from job_application_agent.discovery import DiscoveryQuery, build_discovery_queries
from job_application_agent.extraction import (
    JobFactExtractor,
    LLMJobFactExtractor,
    LocalJobFactExtractor,
)
from job_application_agent.filters import (
    FilterResult,
    HardFilterCriteria,
    HardFilterService,
)
from job_application_agent.models import (
    CandidateProfile,
    CompensationRange,
    ExtractedJobFacts,
    FollowUpReminder,
    FollowUpStatus,
    JobPosting,
    JobSource,
    JobStatus,
    StatusSource,
    SubmissionConfirmation,
)
from job_application_agent.parser import JobParser
from job_application_agent.scoring import (
    FitScorer,
    RoleRelevanceResult,
    RoleRelevanceScorer,
    ScoreBreakdown,
    ScoringCriteria,
)
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
    "ExtractedJobFacts",
    "FilterResult",
    "FitScorer",
    "FollowUpReminder",
    "FollowUpStatus",
    "FollowUpTracker",
    "HardFilterCriteria",
    "HardFilterService",
    "JobFactExtractor",
    "JobParser",
    "JobPosting",
    "JobSource",
    "JobStatus",
    "LLMJobFactExtractor",
    "LocalJobFactExtractor",
    "RoleRelevanceResult",
    "RoleRelevanceScorer",
    "ScoreBreakdown",
    "ScoringCriteria",
    "StatusSource",
    "SubmissionConfirmation",
    "SubmissionConfirmationRecorder",
    "SubmissionConfirmationRequest",
    "build_discovery_queries",
]

__version__ = "0.1.0"
