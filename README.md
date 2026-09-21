# Job Application Agent

Job Application Agent is a local-first tool for running a job search like an
auditable workflow. It stores a candidate profile, discovers and scores jobs,
tracks every posting in a ledger, prepares application materials, and helps a
user review application form plans before anything is marked submitted.

The app is designed around safety and traceability: jobs pass through explicit
statuses, duplicate checks happen before application work, and a job is only
marked `applied` after a submission confirmation is recorded.

## Capabilities

- Candidate profile management for contact info, target roles, salary,
  location/remote preferences, work authorization, skills, and companies or
  industries to avoid.
- Local React dashboard with Google sign-in support and an explicit local-dev
  auth mode.
- Discovery query planning from target roles and location preferences.
- Greenhouse and Lever ingestion/parsing support for structured job postings.
- Job normalization for titles, companies, locations, canonical URLs, salary,
  remote status, seniority, requirements, and nice-to-have skills.
- Deterministic deduplication using ATS identity, canonical URL, and
  company/title/location fingerprints.
- Hard filters for non-negotiable constraints such as location, remote
  preference, salary, title keywords, excluded companies, and work
  authorization.
- Fit scoring that weighs target role/title match, required skills, seniority,
  location, salary, work authorization, and company preferences.
- Decision policy that moves jobs through `discovered`, `needs_review`,
  `approved_to_apply`, `started_application`, `applied`, and related ledger
  states.
- Application worker for approved jobs that generates truthful draft materials,
  selects the right ATS form planner, builds a reviewable field plan, and stops
  before final submit.
- Greenhouse and Lever form-fill planning for known fields, uploads, reusable
  answers, and cover-letter review.
- Submission confirmation recording so applications become `applied` only after
  a real confirmation is captured.
- Follow-up reminder tracking for submitted applications.

## Current Safety Model

The project intentionally stops before final submission. The dashboard can
prepare an application and show the generated cover letter, short answers, and
form-field plan, but the user must review the artifacts and confirm that the
application was actually submitted before the ledger records it as applied.

This keeps the early workflow useful without silently applying to jobs or
inventing candidate history.

## Local Development

Install Python dependencies into the project virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

Install frontend dependencies:

```bash
npm install
```

Run the API and frontend in local development mode:

```bash
npm run dev:api:local
npm run dev:local
```

The API runs on `http://127.0.0.1:8000` and the Vite frontend runs on
`http://127.0.0.1:5173`.

## Verification

```bash
.venv/bin/python -m pytest
.venv/bin/python -m mypy
.venv/bin/python -m black --check src tests
.venv/bin/python -m isort --check-only src tests
npm run build
```

## More Detail

See [design.md](design.md) for the architecture notes, safety model, and
component-by-component design.
