# Phase 2 deployment checklist

This Phase 1 planning document has been replaced by the Phase 2 documents:

* **docs/DEPLOYMENT_GUIDE.md** — the step-by-step deployment and production test checklist
* **docs/ENVIRONMENT_VARIABLES_GUIDE.md** — every environment variable explained
* **docs/FREE_TIER_LIMITATIONS.md** — verified free-plan limits, backups and upgrade path
* **docs/PHASE_2_AUDIT.md** — audit of Phase 1 and the production changes

Decisions taken in Phase 2: Render **native Python** runtime (no Docker, so OCR is off online), **Neon Object
Storage** for files (Cloudflare R2 as fallback), **Brevo** HTTPS API for optional e-mail (Render free blocks SMTP),
start-up migrations.
