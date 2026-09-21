# Job Application Agent Design

## Goal

Build an agentic system that searches for relevant jobs, scores them against a candidate profile, prepares truthful application materials, fills application forms, and keeps a durable record of every job it has seen or applied to so it does not re-apply.

The system should start with human approval before final submission. Full auto-apply can be added later, but only after the tracker, duplicate detection, and safety checks are reliable.

## Core Components

### 1. Profile and Preferences

Stores the candidate's canonical information and search constraints.

Examples:

- Resume versions
- Work history
- Skills
- Education
- Salary preferences
- Location and remote preferences
- Work authorization and visa information
- Target roles and seniority
- Companies or industries to avoid
- Answers to common application questions

### 2. Job Discovery

Finds jobs from selected sources.

Possible sources:

- Greenhouse boards
- Lever boards
- Ashby boards
- Company career pages
- Niche job boards
- Search alerts
- LinkedIn or Indeed later, if the project can handle their brittleness and terms

Start with Greenhouse and Lever because they are more structured than broad job boards.

### 3. Job Parsing and Normalization

Converts messy postings into structured data that downstream systems can compare and store.

Example normalized job:

```json
{
  "job_id": "greenhouse:exampleco:12345",
  "title": "Backend Engineer",
  "company": "ExampleCo",
  "location": "Remote US",
  "salary_range": "$140k-$180k",
  "requirements": ["Python", "AWS", "Postgres"],
  "nice_to_haves": ["LLMs", "Kubernetes"],
  "application_url": "https://boards.greenhouse.io/exampleco/jobs/12345",
  "source": "greenhouse"
}
```

The parser can use an LLM for extraction, but output should be validated with schemas before being saved.

### 4. Application Ledger and Deduplication

This is a first-class component. The agent must persist every discovered job and every submitted application so it can avoid duplicate applications.

The agent should never apply directly from search results. It should always pass through the ledger first.

Recommended flow:

```text
Discover job
  -> Normalize job data
  -> Check if already seen or applied
  -> Score job
  -> Human or policy approval
  -> Check again before submit
  -> Apply
  -> Record application
```

Recommended statuses:

```text
discovered
rejected
saved
needs_review
approved_to_apply
started_application
applied
failed
withdrawn
interviewing
closed
```

Example application record:

```json
{
  "job_id": "greenhouse:exampleco:12345",
  "company": "ExampleCo",
  "title": "Backend Engineer",
  "location": "Remote",
  "application_url": "https://boards.greenhouse.io/exampleco/jobs/12345",
  "canonical_url": "https://boards.greenhouse.io/exampleco/jobs/12345",
  "source": "greenhouse",
  "status": "applied",
  "applied_at": "2026-09-21T10:30:00Z",
  "resume_version": "backend_v3",
  "cover_letter_version": "exampleco_2026_09_21",
  "confirmation_number": "ABC123",
  "notes": "Submitted through Greenhouse"
}
```

Duplicate detection should use multiple signals:

- Exact ATS job ID when available
- Canonical URL with tracking parameters removed
- Company, title, and location fingerprint
- Optional semantic duplicate check for similar postings across sources

Before submitting an application, the agent should perform a second duplicate check. This protects against race conditions if multiple workers or agents are running.

Policy sketch:

```python
if job.status == "applied":
    skip("Already applied")

if similar_applied_job_exists(job):
    send_to_review("Possible duplicate")

if job.status in ["started_application", "approved_to_apply"]:
    resume_or_review()
```

### 5. Fit Scoring

Ranks jobs against the profile.

Signals:

- Required skill overlap
- Seniority match
- Location and remote compatibility
- Salary compatibility
- Work authorization compatibility
- Company preference fit
- Whether the role is worth customizing materials for

Use deterministic filters for hard constraints and LLM assistance for nuanced explanations.

### 6. Application Decision Policy

Decides the next action for each job:

- Reject
- Save
- Ask for review
- Approve for application
- Generate tailored materials first
- Fill application and stop before final submit

Early versions should require human approval before submission.

### 7. Resume and Cover Letter Customization

Generates tailored materials from the candidate profile, resume history, job description, and company context.

Rules:

- Never invent experience.
- Reorder, emphasize, and rephrase only truthful information.
- Keep generated materials tied to specific resume and cover letter versions.
- Store generated artifacts with the application record.

### 8. Application Form Filler

Uses browser automation to fill application forms.

Recommended approach:

- Use Playwright.
- Start with one ATS provider.
- Fill known fields.
- Upload selected resume and cover letter files.
- Stop before final submit until the system is trusted.
- Hand off captchas, ambiguous questions, and sensitive questions to the user.

Suggested provider order:

1. Greenhouse
2. Lever
3. Ashby
4. Workday much later, if at all

### 9. Human Review UI

Provides visibility and control.

Minimum useful views:

- New matched jobs
- Score and match explanation
- Possible duplicates
- Applications awaiting approval
- Generated resume and cover letter preview
- Submitted applications
- Follow-up reminders

### 10. Safety and Compliance Layer

The system should:

- Never lie.
- Avoid applying to jobs the candidate clearly does not want.
- Respect applicable site rules and terms.
- Avoid spam behavior.
- Ask for approval on sensitive questions.
- Keep logs of what was submitted and when.

## High-Level Architecture

```text
Job Sources
  -> Discovery Worker
  -> Job Parser
  -> Database
  -> Deduplication Service
  -> Fit Scorer
  -> Decision Policy
  -> Material Generator
  -> Human Review UI
  -> Browser Application Agent
  -> Application Tracker
```

## Suggested Initial Tech Stack

- Python for backend workers
- SQLite for the first local version, Postgres later
- Pydantic for schemas
- Playwright for browser automation
- FastAPI for an API
- React or server-rendered HTML for a dashboard
- OpenAI or another LLM provider for parsing, ranking, and writing
- Cron, Celery, or RQ for scheduled discovery

## MVP Milestones

### Milestone 1: Discover and Score

Given a candidate profile and a list of Greenhouse or Lever company career URLs, find relevant jobs, normalize them, deduplicate them, score them, and show the top matches.

### Milestone 2: Generate Materials

For an approved job, generate a tailored resume variant or resume summary and a cover letter. Store the generated versions and connect them to the application record.

### Milestone 3: Assisted Apply

Open the application page, fill known fields, upload files, and stop before submitting. Record `started_application` and later `applied` only after confirmed submission.

### Milestone 4: Safer Automation

Add stronger duplicate detection, retries, form-specific adapters, and policy controls for when the agent can submit without manual approval.
