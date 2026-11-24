# Data model

PostgreSQL (Neon). Defined in `backend/migrations/0001_initial.sql`. All primary keys are UUIDs; all tables have
timestamps; foreign keys cascade so deleting a project removes everything that belongs to it.

## Accounts
| Table | Purpose |
|---|---|
| `users` | E-mail (unique), display name, scrypt password hash, provider, active/verified flags, settings |
| `sessions` | Sign-in sessions: only a **hash** of the cookie token is stored; expiry; revocation |
| `auth_tokens` | Single-use tokens for password reset / magic link / e-mail verification (hashed, expiring) |
| `audit_logs` | Who did what and when (logins, uploads, deletions, exports…) — kept after project deletion |

## Projects & sources
| Table | Purpose |
|---|---|
| `projects` | Name, description, industry, geography, time period, business objective, focus company/brand, learning objectives |
| `project_members` | User ↔ project with role `owner` / `editor` / `viewer` (SaaS-ready sharing model) |
| `files` | Uploaded file metadata: names, type, size, SHA-256, storage key, role (e.g. `proposed_strategy`), period label, processing status, extraction summary, errors |
| `document_chunks` | Extracted text blocks (heading/paragraph/list/caption/footnote/note/OCR) with locators (page, slide, section, paragraph, line) and an approximate flag |
| `datasets` | Each table found in a file (sheet, PDF table, slide chart…) with locator, row/column counts, profile, recorded transformations and the storage key of its normalised copy |
| `dataset_columns` | Original and normalised column names, data type, detected meaning (`semantic_role` e.g. revenue, spend, channel, time), unit, missing/distinct counts, statistics |
| `data_quality_issues` | Data Quality Report rows: type, field, severity, description, suggested resolution, locator (rows) |
| `research_sources` | URLs/citations attached to a project (optional fetch) |

## Analysis
| Table | Purpose |
|---|---|
| `analysis_runs` | Mode (quick/deep/research), status, progress/stage, files included, full sectioned result (`result_json`), errors |
| `analysis_questions` | User and auto-generated questions with detected intents and answers (linked finding ids or "insufficient" + data needed) |
| `findings` | Category, title, **fact, interpretation, implication, hypothesis, validation**, confidence, approximate flag, score, rank, tags, chart spec, details |
| `finding_evidence` | Source traceability: file, dataset, chunk, human-readable source label, locator (sheet/page/slide, rows, columns, filters), excerpt, values |
| `trends` | Measure, dimension/value, direction, start/end period and values, absolute/% change, CAGR, slope, R², acceleration, series |
| `comparisons` | Type (time / segment / plan_vs_actual / source / strategy), subjects A & B, measure, values, difference |
| `gaps` | Gap type, gap, evidence, why it matters, significance, missing information, recommended investigation, potential action |
| `opportunities` | Subject, classification (e.g. `potential_opportunity` vs `low_competition_weak_demand`), rationale, USP, competition level, demand indicators, risks, validation, confidence |
| `risks` | Risk, horizon, likelihood, impact, mitigation |
| `recommendations` | Recommendation, type (action / carry_forward / reconsider / validate / research), priority, rationale |
| `research_questions` | Future and research questions, rationale, methodologies, priority |
| `ask_messages` | Ask Your Data history with full answers |

## Reports
| Table | Purpose |
|---|---|
| `reports` | A frozen snapshot (`content_json`) of a run's results, so reports stay stable even after new analyses |
| `report_exports` | Each generated PDF/DOCX/XLSX file (storage key, size) |

## Files in storage
```
projects/<project_id>/files/<file_id>/original/<safe name>      ← the untouched upload
projects/<project_id>/files/<file_id>/derived/<dataset_id>.csv.gz ← normalised analysis copy (+ __row__ = source row)
projects/<project_id>/reports/<report_id>/report.<pdf|docx|xlsx>
```
