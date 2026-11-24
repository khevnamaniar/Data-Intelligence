# Data Intelligence & Analysis Platform — Phase 1

Upload reports and datasets → say what you want to learn → the platform extracts, cleans and structures the
information, analyses it (trends, comparisons, gaps, opportunities, marketing/ROAS, strategy changes, implications),
explains what the findings mean, shows the evidence behind every finding, suggests what to investigate next, and
produces a professional report (PDF, Word, Excel).

It is **industry-agnostic** (marketing, sales, product, HR, operations…), runs on **free, open-source software**,
and **does not need any paid AI service**. Uploaded files are never sent to outside services.

---

## Contents
1. [What it does](#1-what-it-does)
2. [Technology](#2-technology)
3. [Run it on your computer](#3-run-it-on-your-computer)
4. [Create the Neon database](#4-create-the-neon-database)
5. [Configure your local environment](#5-configure-your-local-environment)
6. [Database migrations](#6-database-migrations)
7. [Run the tests](#7-run-the-tests)
8. [Try the demo project](#8-try-the-demo-project)
9. [Repository structure](#9-repository-structure)
10. [Required vs optional dependencies](#10-required-vs-optional-dependencies)
11. [Current limitations](#11-current-limitations)
12. [Phase 2 (deployment)](#12-phase-2-deployment)

---

## 1. What it does

| Area | What you get |
|---|---|
| **Projects** | A workspace per piece of work (name, industry, geography, period, objective, your company/brand, learning objectives). |
| **Sources** | Upload many files at once: PDF, Excel, CSV, Word, PowerPoint, TXT, Markdown, images/screenshots/scans. Add URLs. Tag each file's role (e.g. *previous strategy*, *marketing data*). |
| **Extraction** | Text with headings, captions, footnotes, speaker notes; tables from every format; exact data from native PowerPoint charts; OCR of images and scanned pages; *approximate* reading of bar charts in images (always labelled **Approximate / visually interpreted**). |
| **Cleaning & data quality** | Numbers, currencies, %, k/m/bn, dates in mixed formats, wide "2023 / 2024 / 2025" tables, label spelling variants. A **Data Quality Report** (issue, source, field, severity, suggested resolution). Originals are never changed; every transformation is recorded. |
| **What can I do with this data?** | After upload, the platform lists only the analyses your data supports, and what extra data would unlock more. |
| **Questions** | Enter several questions, or tick **"I don't know — find interesting insights"**. |
| **Modes** | **Quick**, **Deep**, **Research** (adds known/unknown, assumptions, hypotheses, research methods). |
| **Analyses** | Trends (change, %, CAGR, share, contribution, moving average, acceleration), time/segment/source/plan-vs-actual comparisons, concentration, anomalies, correlations (never called causation), marketing KPIs and **ROAS drivers** (mix vs efficiency decomposition), **product opportunity** (separates *low competition* from *genuine opportunity*), gap engine (14 gap types), strategy change (added/removed/changed) and **strategy-vs-data** (carry forward / reconsider / validate), impact ("if this continues"), research gaps, future questions. |
| **Every finding** | Fact · Interpretation · Implication · Hypothesis · Validation · Confidence (High/Medium/Low) · Evidence → Source (file, sheet/page/slide, rows, columns). |
| **Ask Your Data** | Follow-up questions answered by calculation, with facts, hypotheses, charts and evidence — or *"The available data is insufficient to determine this."* plus the data that would be needed. |
| **Reports** | Results page in the agreed structure; export to **PDF**, **DOCX** (editable) and **XLSX** (findings, metrics, comparisons, gaps, opportunities, evidence, data quality, supporting data). |
| **Security** | Accounts, password reset, project isolation enforced on the server, private file storage, CSRF protection, security headers, upload validation, audit log. |

## 2. Technology

| Part | Technology |
|---|---|
| Frontend | React + TypeScript + Vite + Tailwind CSS (charts are hand-written SVG — no chart library needed) |
| Backend | Python, Starlette (the framework FastAPI is built on), Uvicorn |
| Analytics | pandas, NumPy, SciPy, scikit-learn |
| Documents | pdfplumber, pypdfium2, python-docx, python-pptx, openpyxl, Pillow, OpenCV, Tesseract OCR (optional) |
| Exports | reportlab (PDF), python-docx (DOCX), openpyxl (XLSX), matplotlib (charts in exports) |
| Database | PostgreSQL on **Neon** (SQL migrations, UUIDs, foreign keys, indexes) |
| Storage | `StorageProvider` abstraction — local disk now, S3-compatible cloud storage ready for Phase 2 |

## 3. Run it on your computer

You need, once:

* **Python 3.11 or newer** — https://www.python.org/downloads/ (on Windows tick *"Add Python to PATH"*).
* **Node.js 20 or newer** — https://nodejs.org (choose the "LTS" version).
* **A free Neon account** — see section 4.
* *(Optional)* **Tesseract OCR** to read text inside images and scanned PDFs — Windows: https://github.com/UB-Mannheim/tesseract/wiki · Mac: `brew install tesseract` · Linux: `sudo apt install tesseract-ocr`.

Then open a terminal (Mac: *Terminal*; Windows: *PowerShell*) in the project folder and run:

```bash
# 1. Backend (Python) — first time only
cd backend
python -m venv .venv
# Mac/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 2. Configuration — first time only
cp .env.example .env        # Windows: copy .env.example .env
#    then open .env in a text editor and paste your Neon connection string into DATABASE_URL (section 5)

# 3. Frontend — first time only
cd frontend
npm install
cd ..
```

**Every time you want to use the app**, open two terminals:

```bash
# Terminal 1 – backend API  (http://localhost:8000)
cd backend
source .venv/bin/activate          # Windows: .venv\Scripts\activate
uvicorn app.main:app --reload --port 8000

# Terminal 2 – website  (http://localhost:5173)
cd frontend
npm run dev
```

Open **http://localhost:5173** in your browser, click *Create one* to register, and start a project.

> Alternative (one terminal): `cd frontend && npm run build`, then start only the backend and open
> http://localhost:8000 — the backend serves the built website itself. This is how it will run on Render.

## 4. Create the Neon database

1. Go to **https://neon.tech** and click **Sign up** (free plan; no card needed).
2. Click **Create project**. Name it e.g. `data-intelligence`; choose the region closest to you; click **Create**.
3. On the project dashboard click **Connect** (top right).
4. In the dialog, keep *Branch: main*, *Database: neondb*, and make sure **Connection pooling** is switched **on**.
5. Click **Copy snippet** next to the connection string. It looks like
   `postgresql://neondb_owner:abc123...@ep-cool-name-123456-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require`
6. Paste it into your `.env` file after `DATABASE_URL=` (no spaces, no quotes).

This string contains your database password — **keep it secret**: never e-mail it, never put it in GitHub.
*(Tip: Neon lets you create a separate **branch** — e.g. `dev` — so local experiments never touch production data.)*

## 5. Configure your local environment

`.env` is a simple text file of `NAME=value` lines read when the backend starts. For local use you only **must** set
`DATABASE_URL`. Everything else has a sensible default. All settings:

| Setting | What it is | Local value |
|---|---|---|
| `DATABASE_URL` | Address + password of your Neon database | your Neon string (**secret**) |
| `SECRET_KEY` | Random text used to protect sign-in tokens | optional locally; required in production. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` (**secret**) |
| `APP_ENV` | `development` or `production` | `development` |
| `APP_BASE_URL` | The address you open in the browser (used in reset e-mails) | `http://localhost:5173` |
| `COOKIE_SECURE` | Only send the sign-in cookie over https | `false` locally |
| `SESSION_DAYS` | How long you stay signed in | `7` |
| `AUTO_MIGRATE` | Create/update tables automatically on start-up | `true` |
| `STORAGE_PROVIDER` | Where uploaded files go | `local` |
| `LOCAL_STORAGE_DIR` | Folder for local files (blank = `.data/storage`) | blank |
| `MAX_UPLOAD_MB`, `MAX_FILES_PER_UPLOAD` | Upload limits | `50`, `30` |
| `OCR_ENABLED` | Read text in images (needs Tesseract) | `true` |
| `URL_FETCH_ENABLED` | Allow "fetch and analyse" for added URLs | `true` |
| `EMAIL_PROVIDER` | `console` prints reset e-mails in Terminal 1; `smtp` sends them | `console` |
| `AI_PROVIDER` | Optional AI re-wording of answers (`none` or `openai_compatible`) | `none` |

**Password reset locally:** the reset link is printed in the backend terminal (Terminal 1) instead of being e-mailed.

## 6. Database migrations

Tables are defined in `backend/migrations/*.sql`. With `AUTO_MIGRATE=true` they are created automatically when the
backend starts. To run them by hand:

```bash
cd backend && python -m app.migrate
```

It is safe to run repeatedly — already-applied migrations are skipped (tracked in the `schema_migrations` table).

## 7. Run the tests

```bash
cd backend
python -m pytest -q
```

82 automated tests cover authentication, project isolation, uploads, deletion, every parser, cleaning, data quality,
trend/comparison/gap/opportunity/ROAS calculations, strategy comparison, traceability, Ask Your Data, reports, all
three exports, authorisation and security protections. They use a temporary in-memory database so they need no
internet. To run the same tests against Neon, create a **separate Neon branch** (the tests wipe the database they
use!) and run `TEST_DATABASE_URL=<that branch's string> python -m pytest -q`.

A browser acceptance test (the 33-step checklist below) is in `tests/e2e/acceptance.mjs`
(needs Playwright: `npm i -D playwright && npx playwright install chromium`, the app running on port 8000, then
`BASE_URL=http://localhost:8000 node tests/e2e/acceptance.mjs`).

## 8. Try the demo project

On the Projects page click **Create demo project**. It loads the fictional **Consumer Health Market Analysis**
(focus company "Verdant Health"): a market report PDF (text, table, chart image, footnote), sales, competitor,
marketing and product datasets, previous and proposed strategy documents, a PowerPoint review and a survey
screenshot — plus six learning objectives. Wait for the files to finish processing, open **Analyse**, choose
**Deep analysis** and run. Everything works without any AI service.

The demo files live in `sample-data/consumer-health/` and can be regenerated with
`python scripts/generate_sample_data.py`. All names and numbers are invented.

## 9. Repository structure

```text
/
├── backend/
│   ├── app/
│   │   ├── api/          HTTP endpoints (auth, projects, files, analysis, ask, reports)
│   │   ├── auth/         authentication & e-mail provider abstractions
│   │   ├── parsers/      PDF, XLSX, CSV, DOCX, PPTX, TXT/MD, image/OCR/chart readers
│   │   ├── analytics/    the analysis engine (see docs/ANALYSIS_ENGINE.md)
│   │   ├── reports/      report builder + PDF/DOCX/XLSX exporters
│   │   ├── storage/      StorageProvider: local + S3-compatible
│   │   ├── ai/           optional AIProvider abstraction (off by default)
│   │   ├── services/     ingestion pipeline, analysis runs, background jobs
│   │   ├── db.py, migrate.py, config.py, security.py, main.py
│   ├── migrations/       SQL migrations (PostgreSQL)
│   ├── tests/            automated tests (pytest)
│   └── requirements.txt
├── frontend/             React + TypeScript + Vite + Tailwind website
├── sample-data/          fictional demo files
├── scripts/              demo-data generator, offline frontend build
├── tests/e2e/            browser acceptance test
├── docs/                 architecture, data model, analysis engine, security, Phase 2 checklist
├── .env.example
└── README.md
```

*Why `parsers/`, `analytics/` and `storage/` sit inside `backend/app/` instead of the top level:* they are Python
packages used by one deployable service. Keeping them together means one install, one deploy and simple imports,
while the folders keep the same clear separation.

## 10. Required vs optional dependencies

**Required (all free):** Python packages in `backend/requirements.txt`, Node.js packages in `frontend/package.json`,
a PostgreSQL database (Neon free tier).

**Optional:**

| Optional item | What it adds | Without it |
|---|---|---|
| Tesseract OCR (free) | Text from images, screenshots and scanned PDFs; chart labels | Images are stored; chart bar shapes are still detected (relative values); a clear message explains what was skipped |
| `boto3` + S3-compatible bucket | Durable cloud file storage (Phase 2) | Files are stored on local disk |
| SMTP e-mail account | Real password-reset e-mails | Reset link is printed in the server log |
| AI provider (e.g. free local Ollama) | AI-worded summary next to computed answers | Answers are fully computed and worded by the deterministic engine |

## 11. Current limitations

Honest list of what is partial in Phase 1 (details in `docs/ANALYSIS_ENGINE.md`):

* **Chart images:** only **vertical bar charts** are read automatically (values from data labels or the axis scale,
  otherwise relative heights). Line/pie charts in images contribute only their OCR'd text. Always labelled approximate.
* **OCR tables:** text from scanned pages is extracted as paragraphs; tables inside scans are not rebuilt into rows/columns.
* **Word files** have no fixed pages, so Word sources are referenced by *section and paragraph*, not page.
* **Legacy formats** (.xls, .doc, .ppt) and password-protected files are rejected with an explanation — re-save as modern formats.
* **Ask Your Data** understands common question types (compare, why/drivers, growing/declining, top/bottom, totals,
  gaps/opportunities/risks/next steps, ROAS) and falls back to evidence search. It is not a free-form language model.
* **Causation:** the engine never claims it; ROAS analysis uses *attributed* revenue; validation steps are suggested.
* **External research** is optional: you can add URLs and have public pages fetched; there is no paid search API.
* **Background jobs** run inside the web process; if the server restarts mid-analysis the run is marked *interrupted*
  and can simply be re-run. A dedicated job queue can be added later.
* **Sharing projects:** the data model supports members and roles (owner/editor/viewer) and the server enforces them,
  but there is no invite screen yet.
* Very large files are processed but analysis is designed for typical business files (up to ~hundreds of thousands of rows).
* The interface is light-mode only.

## 12. Phase 2 (deployment)

The app is ready to run online for free on **GitHub + Render + Neon** (database **and** private file storage).

| Document | Use it for |
|---|---|
| **docs/DEPLOYMENT_GUIDE.md** | Step-by-step setup wizard: Neon → GitHub → storage → Render → variables → deploy → test → troubleshooting |
| **docs/ENVIRONMENT_VARIABLES_GUIDE.md** | Every setting explained: what it is, where to get it, where to paste it, secret or not, how to check it |
| **docs/FREE_TIER_LIMITATIONS.md** | Verified free-plan limits, what happens when they are reached, backups, upgrade path |
| **docs/PHASE_2_AUDIT.md** | What Phase 1 had, what was fixed for production |

Render settings at a glance (details in the guide):

* **Build command:** `pip install -r backend/requirements.txt && cd frontend && npm install --include=dev && npm run build`
* **Start command:** `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --no-access-log`
* **Health check path:** `/health`
* Python version comes from `.python-version` (3.13).

Production extras: owner **Admin** page (health, usage vs free limits, recent errors, live self-test, password-reset
links, database backup), storage quota, rate limits, friendly free-tier messages, secret-free logs.
