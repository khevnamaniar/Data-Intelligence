# Security & privacy

| Area | Implementation |
|---|---|
| Passwords | scrypt (memory-hard, Python standard library), random salt per user; minimum strength rules |
| Sessions | Random 256-bit token in an **HttpOnly, SameSite=Lax** cookie (`Secure` when `COOKIE_SECURE=true`); only a keyed hash (HMAC with `SECRET_KEY`) is stored; expiry; logout and password change/reset revoke sessions |
| Password reset | Single-use, 1-hour, hashed tokens; identical response whether or not the e-mail exists (no account enumeration) |
| Brute force | Per-IP+e-mail login rate limit (10 / 5 min) and reset-request rate limit |
| Authorisation | Every project/file/run/report/ask endpoint calls `require_project()` on the **server**; non-members get **404** (IDs cannot be probed); roles owner/editor/viewer enforced; covered by tests |
| CSRF | State-changing API calls require the `X-Requested-With: dip` header (cannot be sent cross-site without CORS, which is not enabled) + SameSite cookies |
| SQL injection | All SQL uses bound parameters; no string-built queries with user input |
| XSS | React escapes output; strict **Content-Security-Policy** (`script-src 'self'`); no `dangerouslySetInnerHTML` |
| Security headers | CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff, Referrer-Policy, Permissions-Policy, COOP; HSTS when secure cookies are on; `Cache-Control: no-store` on API responses |
| Uploads | Extension allow-list **and** content signature checks; zip-structure checks for Office files; decompressed-size limit; per-file and per-request size limits; sanitised file names; stored under random UUID keys |
| File access | No public file URLs or static file routes; downloads only through authenticated, authorised endpoints with `Content-Disposition: attachment` |
| Storage paths | Keys validated; path traversal (`..`, absolute paths) rejected; tested |
| Safe deletion | Deleting a file/project/account removes database rows (cascades) **and** the stored objects |
| URL fetching | Optional; http(s) only; blocks private/loopback/link-local addresses (SSRF protection); size and time limits |
| Exports | XLSX cells beginning with `=`, `+`, `-`, `@` are neutralised (formula-injection protection) |
| AI | Off by default. If enabled, only computed facts are sent — never uploaded files |
| Secrets | Only in environment variables; `.env` is git-ignored; `.env.example` contains no secrets |
| Errors | Users see plain-language messages; stack traces only in server logs |
| Audit | `audit_logs` records sign-ins, failed sign-ins, uploads, downloads, deletions, analyses, exports |

## Production additions (Phase 2)
| Area | Implementation |
|---|---|
| Transport | Render serves only HTTPS (HTTP redirects); `COOKIE_SECURE=true`; HSTS header |
| Database | TLS enforced (`sslmode=require` added automatically); credentials only in Render's environment; only the backend talks to Neon; parameterised SQL; pooled connections; Neon has no public admin port to open |
| Least privilege | The app uses the Neon owner role because it applies its own migrations at start-up. Optional hardening: create a separate role for day-to-day use and run migrations with the owner role only |
| Migrations | Advisory lock (no concurrent runs); destructive statements refused; applied only when new |
| Storage | Private bucket; no public URLs; path-validated keys; deletes remove objects; storage quota |
| Uploads | Extension allow-list, **content-based type detection** (the browser's declared type is not trusted), executables/scripts/macros/zip bombs refused, size limits |
| Abuse limits | Per-user limits on analyses (30/h), Ask Your Data (120/h), uploads (60/h); registrations per IP; optional `REGISTRATION_OPEN=false` |
| Logs | No request bodies, cookies or query strings; automatic masking of database passwords, API keys and tokens; reset links are never logged in production |
| Errors | Users see plain-language messages; technical details stay in Render logs |
| Admin | Only `ADMIN_EMAILS`; others get 404; shows counts and sizes, never project contents |
| CORS | Not enabled — the website and API share one address, so other websites cannot call the API from a browser |

## Before going live (Phase 2)
Set `APP_ENV=production`, a strong `SECRET_KEY`, `COOKIE_SECURE=true`, cloud storage, and real SMTP. The app
refuses to start in production without a valid `SECRET_KEY`.
