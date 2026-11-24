# Phase 2 audit — what Phase 1 delivered and what changed for production

Audit date: 24 November 2026. Audited: the Phase 1 code base (this repository).

## What Phase 1 contains

| Area | Finding |
|---|---|
| Frontend | React 19 + TypeScript + Vite + Tailwind (single-page app, hash routing). Built files are served by the backend. |
| Backend | Python 3.13, Starlette + Uvicorn, Pydantic validation. |
| Architecture | **Single service**: one web process serves the website and the API from the same address. No separate frontend host is needed. |
| Database | PostgreSQL via psycopg 3, plain parameterised SQL, SQL migration files with a `schema_migrations` tracking table. The automated tests use SQLite. |
| Authentication | E-mail and password (scrypt hashes), server-side sessions in an HttpOnly cookie, password reset tokens, CSRF header, rate limits, owner/editor/viewer roles. |
| File storage | `StorageProvider` abstraction: local disk (development) and an S3 adapter that had not been tested. |
| Analytics, parsers, exports | Fully implemented and tested (see docs/ANALYSIS_ENGINE.md). |
| Background work | In-process thread pool. Status stored in the database, polled by the UI. |
| Environment variables | Documented in `.env.example`. |
| Tests | 62 automated tests plus a 33-step browser acceptance test. |
| Deployment configuration | None (deliberately left for Phase 2). |
| Secrets in the repository | **None found.** `.gitignore` excludes `.env`, `.env.*` (except `.env.example`), local data and build output. |

## Working (kept unchanged)
- All analysis features, parsers, reports and exports.
- Authentication, sessions, password hashing, project isolation, roles, CSRF protection, security headers.
- Single-service architecture. No CORS configuration is needed because the browser only ever talks to the same address.
- Job status model (queued, running, completed, partial, failed) with progress shown in the UI.
- Per-file error isolation ("partial analysis").

## Needs configuration (exists, set via Render)
- `DATABASE_URL` (Neon), `SECRET_KEY`, `APP_ENV=production`, `APP_BASE_URL`, `COOKIE_SECURE=true`.
- Cloud storage settings (`STORAGE_PROVIDER=s3`, bucket, endpoint, keys, region).
- Admin owner (`ADMIN_EMAILS`), limits (`MAX_UPLOAD_MB`, `STORAGE_QUOTA_MB`).

## Needed fixing before production (fixed in Phase 2)
| Problem | Why it mattered | Fix |
|---|---|---|
| A new database connection was opened for every query block, with no pooling | Slow on a remote database, and wasteful | Connection pool (`psycopg_pool`) with dead-connection checks. Neon sleeps after 5 minutes of inactivity, and checked connections recover automatically. |
| Automatic prepared statements were left on | They can break behind Neon's pooled connection endpoint (PgBouncer) | Disabled (`prepare_threshold=None`) |
| SSL was not enforced if missing from the URL | Database traffic could be unencrypted | `sslmode=require` is added automatically |
| Python strings sent as `text` might not cast to `uuid` / `jsonb` | PostgreSQL type errors | Strings are sent as "unknown" type so PostgreSQL casts them. numpy values are converted to plain Python. |
| `research_sources.title` could exceed its column length | Insert error on PostgreSQL (SQLite does not check length) | Value trimmed to 500 characters |
| The S3 adapter used virtual-host addressing and had no error handling | Neon Object Storage needs path-style addressing; errors were raw | Path-style, SigV4, checksum compatibility, friendly errors, quota detection, pagination |
| SMTP e-mail | **Render free blocks outgoing SMTP ports 465/587** | Brevo HTTPS e-mail API provider, plus an owner-only "create reset link" feature that needs no e-mail at all |
| The console e-mail fallback printed reset links in logs | Reset tokens would leak into production logs | Not printed in production |
| Health endpoint returned `{"status":"ok","database":true}` only at `/api/health` | The brief asks for `/health` with plain wording | `/health` and `/api/health` return `{"status":"healthy","database":"connected"}` |
| Migrations had no lock | Two starting instances could race | PostgreSQL advisory lock. Destructive statements (DROP/TRUNCATE) are refused. `python -m app.migrate --status` added. |
| Client IP behind Render's proxy | Rate limits would treat all users as one IP | Start command uses `--proxy-headers` |
| Production safety checks were limited | The app could start with local disk storage on Render (files lost) | Start-up refuses production without PostgreSQL, a SECRET_KEY, cloud storage and secure cookies, and explains why in the log |
| Upload checks | Executables and macros not explicitly blocked | Programs/scripts (by content), macro-enabled Office content and zip bombs refused |
| Files left "uploaded" after a restart were never processed | Stuck files | Processing resumes automatically at start-up |
| Long or stuck analyses ran forever | A server restart or sleep mid-run leaves them "running" | Watchdog marks runs older than `ANALYSIS_MAX_MINUTES` as failed with an explanation |
| Two jobs could run at once | Memory peaks on a 512 MB server | One job at a time by default (`JOB_WORKERS=1`). Measured peak ~330 MB. |
| Errors from the database or storage showed generic text | Unclear to users | Plain-language messages ("could not connect", "free-plan limit reached", "storage limit"), with details only in logs |
| Logs could contain secrets if a library printed a URL | Credential leakage | Log redaction filter, request log without query strings |

## Missing in Phase 1 (added in Phase 2)
- Owner/admin view: health, usage vs free-tier limits, recent errors, live self-test, user list with reset links, database backup download.
- App-level storage quota (`STORAGE_QUOTA_MB`) so uploads stop with a clear message before the free allowance runs out.
- Free-tier-aware messages (server waking up, quota reached, too large).
- Rate limits for analyses, Ask Your Data, uploads and registrations.
- Option to close registration (`REGISTRATION_OPEN=false`).
- `.python-version` (pins Python 3.13 on Render).

## Still not possible to verify inside the build environment
The build environment could not reach the internet to install PostgreSQL, boto3 or Vite. So:
1. PostgreSQL behaviour was reviewed by hand (types, lengths, NOT NULL, transactions) and is checked live by **Admin → Run self-test** right after deployment.
2. The S3 adapter was tested against a simulated storage service. The live check is again the self-test.
3. The production website build (`npm run build` with Vite) runs for the first time on Render. The same code was compiled and browser-tested with an equivalent build.

## Recommended before go-live (all done, or covered in the deployment guide)
Set every variable in docs/ENVIRONMENT_VARIABLES_GUIDE.md. Run the Admin self-test. Run the production test checklist in docs/DEPLOYMENT_GUIDE.md.
