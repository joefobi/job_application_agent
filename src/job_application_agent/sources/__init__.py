"""Job source ingestion adapters."""

from job_application_agent.sources.greenhouse import GreenhouseIngestor
from job_application_agent.sources.lever import LeverIngestor

__all__ = ["GreenhouseIngestor", "LeverIngestor"]
