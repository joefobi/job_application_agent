"""Job application agent package."""

from job_application_agent.models import (
    CandidateProfile,
    JobPosting,
    JobSource,
    JobStatus,
)
from job_application_agent.storage import ApplicationLedger, ApplicationStore

__all__ = [
    "__version__",
    "ApplicationLedger",
    "ApplicationStore",
    "CandidateProfile",
    "JobPosting",
    "JobSource",
    "JobStatus",
]

__version__ = "0.1.0"
