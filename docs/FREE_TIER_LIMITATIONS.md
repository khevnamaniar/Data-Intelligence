# Free-tier limitations (verified 24 November 2026)

All figures come from the providers' official pages on the date above. Providers change their plans, so check the
linked pages again before relying on any number.

**Total mandatory monthly cost: $0.** No credit card is required for the recommended setup (GitHub, Render, Neon,
and optionally Brevo).

## Render — application hosting (Free web service)
Sources: render.com/docs/free, render.com/pricing, render.com/docs/web-services

| Item | Limit |
|---|---|
| Memory | 512 MB RAM per free instance. The CPU share is not published, but expect a slow processor. |
| Instance hours | 750 free instance hours per workspace per month. One service running all month uses at most ~744. When hours run out, free services are suspended until next month. |
| Sleep | The service **spins down after 15 minutes without visitors**. The next visit wakes it in **about one minute**, and Render shows a loading page meanwhile. |
| Disk | **Temporary.** Local files are lost on redeploy, restart or spin-down. This is why files go to cloud storage. |
| Bandwidth | 5 GB per month on the Hobby workspace. Without a payment method, all free services are suspended for the rest of the month if it is exceeded. |
| Build minutes | 500 per month. If exceeded, new deploys stop for the month but the site keeps running. |
| E-mail ports | **Outgoing SMTP ports 25, 465 and 587 are blocked**, which is why e-mail uses Brevo's HTTPS API. |
| Restarts | Render may restart a free service at any time. Running analyses are then marked "interrupted" and can be re-run. |
| Other | One instance only, no shell access, no persistent disks. HTTPS is included automatically. |
| Expiry | None stated for free web services. (Render's *free Postgres* expires after 30 days, which is why the database is Neon, not Render.) |

**What this means for you:** the first visit after a quiet period takes about a minute. Analyses of the demo take
roughly 1–3 minutes on the free CPU. Keep the browser tab open while an analysis runs: the page's progress checks
count as visits and keep the server awake.

## Neon — PostgreSQL database (Free plan)
Source: neon.com/pricing

| Item | Limit |
|---|---|
| Price | $0, permanent, no credit card |
| Storage | **1 GB per project** (20 GB across all projects). If exceeded, **writes are blocked** (reading still works) |
| Compute | 100 CU-hours per project per month. If used up, the database is suspended until next month |
| Data transfer | 5 GB per project per month, **shared by the database and Object Storage** |
| Sleep | The database sleeps after 5 minutes idle and wakes on the next query (typically a few hundred milliseconds) |
| Inactivity | Neon does **not** pause or delete free projects for inactivity |
| Projects / branches | 100 projects, 10 branches per project |
| Recovery | **6-hour** restore window (1 GB of history); 1 manual snapshot. Instant restore is not available on Free |
| Data deletion | "No limit deletes your data". Upgrading lifts limits immediately |

## Neon Object Storage — uploaded files (recommended storage)
Sources: neon.com/pricing, neon.com/docs/storage/*

| Item | Limit / fact |
|---|---|
| Storage | **5 GB per project** on Free |
| Status | **Beta** — functional, but Neon may change details |
| Regions | AWS US East (Ohio), US East (N. Virginia), Europe (Frankfurt), **Asia Pacific (Singapore)** — the project must be in one of these |
| Max object size | 5 GiB per single upload (far above this app's upload limit) |
| Privacy | Buckets can be **private** (the setting used here). The app never creates public links |
| Data transfer | Counts toward the same 5 GB/month as the database |
| Compatibility | S3-compatible (boto3), path-style addressing — supported by this app |

The app keeps you inside the allowance with `STORAGE_QUOTA_MB=4500` (uploads are refused with a clear message at about 4.4 GB).

## Fallback storage: Cloudflare R2 (if Neon Object Storage is unavailable for you)
Source: developers.cloudflare.com/r2/pricing

Free each month: 10 GB storage, 1 million write operations, 10 million read operations, **free egress**.
Cloudflare's pricing page does not say whether a payment method is needed to turn R2 on. Community reports say
Cloudflare asks for one before enabling R2, even if usage stays free. The app works with R2 by changing the
storage settings only (see the environment variables guide).

## Brevo — password-reset e-mails (optional)
Source: brevo.com — free plan **300 e-mails per day**, no credit card required.
Without Brevo, the owner can create reset links on the Admin page.

## GitHub — source code
Source: github.com/pricing — Free plan includes **unlimited public/private repositories** and 2,000 Actions minutes
per month (not used by this project).

## What happens when a limit is reached

| Situation | What users see | What to do |
|---|---|---|
| Server asleep | Loading page for ~1 minute, then normal | Nothing |
| Storage allowance nearly full | Upload refused: "This upload exceeds the current free storage limit…" | Delete old projects/files (deleting removes the files from storage) |
| Neon storage (1 GB) full | "The database has reached its free-plan limit." | Delete old projects/analyses, or upgrade Neon |
| Neon compute hours used up | "The application could not connect to the database." until the next month | Check usage in the Neon console; upgrade if needed |
| Render hours or bandwidth used up | Site suspended until next month | Check Render Billing; add a payment method or upgrade |
| File too large | Rejected with the size limit explained | Split the file |
| Server out of memory | That file or analysis fails with an explanation | Upload smaller files / use Quick analysis |
| Analysis too long (>20 min) | Marked failed: "took too long" | Re-run with fewer files |

## Backup & recovery on the free plans
- **Neon:** you can restore the database to any moment in the **last 6 hours**, and keep **1 manual snapshot**
  (Neon console → your project → Backup & Restore, or Branches). Take a snapshot before any big change.
- **The app:** Admin → **Download database backup** produces a ZIP of all tables as CSV. Password hashes and login
  tokens are excluded, so after a restore users reset their passwords. Recommended: download it **weekly**, and keep it
  somewhere private.
- **Uploaded files:** stored in Neon Object Storage. The free plan has no separate file backup. Keep the original
  files you upload, since the platform never modifies them.
- Restoring from the CSV backup needs technical help (it is a safety net, not a one-click restore).

## Upgrade path (when the free tier is no longer enough)

| Need | Change |
|---|---|
| No 1-minute wake-up; faster analyses; more memory | Render paid instance (e.g. Starter). No code change |
| More database space/compute, longer restore window | Neon paid plan. No code change |
| More file space | Neon paid plan, or switch storage settings to Cloudflare R2 / AWS S3. No code change |
| OCR for scanned documents online | Deploy with Docker (installs Tesseract). A small configuration change |
| Very long analyses | Render Background Worker (paid) reading the same job table. Small code change |
| More e-mails | Brevo paid plan |
