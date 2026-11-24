# Architecture

## Overview

```text
 Browser (React SPA)
     │  same-origin HTTPS, session cookie + X-Requested-With header
     ▼
 Starlette app (backend/app/main.py)
  ├─ Security middleware (headers, CSRF) ─ error handlers (what / why / what still works / what to do)
  ├─ api/auth.py ─────────► auth/providers.py (PasswordAuthProvider, MagicLinkAuthProvider, EmailProvider)
  ├─ api/projects.py, api/files.py, api/analysis.py  (every route calls require_project() → project isolation)
  │        │
  │        ├─► services/ingestion.py ─► parsers/* ─► analytics/profiling.py (normalise + data quality)
  │        │        └─► StorageProvider (original file, normalised dataset copy)
  │        ├─► services/jobs.py (background thread pool)
  │        ├─► services/analysis_service.py ─► analytics/loader.py ─► analytics/pipeline.py ─► engines
  │        ├─► analytics/ask.py (Ask Your Data) ─► ai/provider.py (optional, off by default)
  │        └─► reports/builder.py ─► reports/exporters.py (PDF / DOCX / XLSX) ─► StorageProvider
  ▼
 PostgreSQL (Neon) via db.py (psycopg 3, parameterised SQL, SQL migrations)
```

## Pipeline (spec §37) and where each step lives

| Step | Code |
|---|---|
| Upload, validation (type sniffing, size, safe names) | `api/files.py`, `parsers/__init__.py`, `services/ingestion.store_upload` |
| Extraction | `parsers/tabular.py`, `pdf.py`, `office.py`, `text.py`, `image.py` |
| Normalisation | `analytics/profiling.normalize_table`, `reshape_wide` |
| Data quality check | `analytics/profiling.normalize_table` → `data_quality_issues` |
| Source indexing | `document_chunks`, `datasets`, `dataset_columns` (+ row numbers in `__row__`) |
| Project data model | `analytics/loader.load_project_data` → `ProjectData` |
| Learning objectives, question generation | `analytics/questions.py`, `capabilities.py` |
| Trend / comparison / gap / opportunity / impact | `trends.py`, `comparisons.py`, `gaps.py`, `opportunities.py`, `impact.py` |
| Marketing & ROAS, strategy, documents, anomalies | `marketing.py`, `documents.py`, `anomalies.py` |
| Research-gap analysis | `research.py` |
| Insight ranking | `questions.rank_findings` |
| Source traceability | `model.Evidence` (file, dataset, chunk, rows, columns, filters) → `finding_evidence` |
| Results, report generation | `services/analysis_service.persist`, `reports/*` |

## Key design decisions

* **Deterministic first.** Every number is computed with pandas/SciPy from the uploaded data. AI is an optional
  re-wording layer (`ai/provider.py`) that only receives computed facts.
* **Provider abstractions.** `StorageProvider` (local, S3), `AuthProvider` (password, magic link), `EmailProvider`
  (console, SMTP), `AIProvider` (none, OpenAI-compatible). Swapping one does not touch the analytics.
* **Originals are immutable.** The uploaded file is stored as-is; analysis uses a normalised copy (`derived/*.csv.gz`)
  and every transformation is recorded in `datasets.transformations_json`.
* **Partial results over failure.** Each parser and each engine is isolated; a failure produces a plain-language
  error and the rest of the analysis continues (run status `partial`).
* **Starlette instead of FastAPI.** Same performance and async model (FastAPI is a layer on Starlette); validation is
  done with Pydantic models explicitly. Moving to FastAPI later is straightforward.
* **Single deployable service.** In production the backend also serves the built React app (`frontend/dist`), so one
  Render web service and one origin are enough (simplest secure cookie setup).
* **Background work** uses an in-process thread pool (`services/jobs.py`). Statuses are stored in the database so the
  UI can poll. Interrupted jobs are marked on restart. A durable queue (e.g. a worker reading `analysis_runs`) can be
  added without changing the API.
* **Database portability.** SQL is written for PostgreSQL; the test-suite translates the DDL to SQLite so tests run
  offline. Production must use PostgreSQL (Neon).

## Frontend

`frontend/src`: `main.tsx` (app shell, routing), `router.tsx` (hash router), `api.ts` (fetch client + types),
`ui.tsx` (components), `Chart.tsx` (SVG charts from engine chart specs, with tooltips, legends and table view),
`findings.tsx` (finding cards, source viewer), `pages/*` (Auth, Dashboard, Project overview, Sources, Analyse,
Results, Ask, Reports, Settings).
