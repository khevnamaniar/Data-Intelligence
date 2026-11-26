# Environment variables — master guide

**What is an environment variable?** A named setting that you type into Render, not into the code. The app reads
it when it starts. Secrets (passwords, keys) live **only** there: never in GitHub, never in an e-mail, never in
a chat.

**Where they go on Render:** Render dashboard → click your web service → **Environment** (left menu) → **Add
Environment Variable** → type the **Key** (the name, exactly as written below) and the **Value** → **Save Changes**.
Saving starts a new deploy automatically.

> **Example values in this guide are fake.** Never copy an example into Render. Always copy *your actual value* from
> the website named in "Where it comes from".

Legend: 🔒 **KEEP SECRET** · ✅ **SAFE TO SHARE**

---

## Summary table

| Variable | What it does | Where to get it | Secret? | Where to enter |
|---|---|---|---|---|
| `DATABASE_URL` | Address + login for the database | Neon → Connect | 🔒 | Render → Environment |
| `SECRET_KEY` | Protects sign-in sessions | Render "Generate" button | 🔒 | Render → Environment |
| `APP_ENV` | Switches on production safety | Type `production` | ✅ | Render → Environment |
| `APP_BASE_URL` | Your site's address (for e-mails/links) | Top of your Render service page | ✅ | Render → Environment |
| `COOKIE_SECURE` | Sign-in cookie only over https | Type `true` | ✅ | Render → Environment |
| `ADMIN_EMAILS` | Who sees the Admin page | Your own e-mail address | ✅ (keep private-ish) | Render → Environment |
| `STORAGE_PROVIDER` | Use cloud file storage | Type `s3` | ✅ | Render → Environment |
| `S3_BUCKET` | Name of your file "bucket" | The name you chose in Neon | ✅ | Render → Environment |
| `AWS_ENDPOINT_URL_S3` | Address of the file storage | Neon → Connect → Storage | ✅ | Render → Environment |
| `AWS_REGION` | Region of the file storage | Neon → Connect → Storage | ✅ | Render → Environment |
| `AWS_ACCESS_KEY_ID` | Storage "username" | Neon → Connect → Storage | 🔒 | Render → Environment |
| `AWS_SECRET_ACCESS_KEY` | Storage "password" | Neon → Connect → Storage | 🔒 | Render → Environment |
| `STORAGE_QUOTA_MB` | Stops uploads before the free 5 GB is full | Type `4500` | ✅ | Render → Environment |
| `MAX_UPLOAD_MB` | Largest file allowed | Type `20` | ✅ | Render → Environment |
| `OCR_ENABLED` | Read text in images (not available on Render free) | Type `false` | ✅ | Render → Environment |
| `EMAIL_PROVIDER` *(optional)* | Send reset e-mails | Type `brevo` | ✅ | Render → Environment |
| `BREVO_API_KEY` *(optional)* | Permission to send via Brevo | Brevo → SMTP & API | 🔒 | Render → Environment |
| `EMAIL_FROM` *(optional)* | Sender address for e-mails | Your verified Brevo sender | ✅ | Render → Environment |
| `REGISTRATION_OPEN` *(optional)* | Let strangers sign up? | `true` or `false` | ✅ | Render → Environment |

Python's version is set by the `.python-version` file in the repository, so you do **not** need `PYTHON_VERSION`.
Variables not listed here keep sensible defaults (see `.env.example`).

---

## DATABASE_URL 🔒
**Plain English:** the private address *and* password the app uses to talk to your Neon database. Think of it as the
database's front-door key.
**Where it comes from:** Neon (neon.tech). Neon creates it for you.
**Exact steps:**
1. Open **https://console.neon.tech** and sign in.
2. Click your project (e.g. `data-intelligence`).
3. Click **Connect** (button at the top of the project dashboard).
4. In the window: **Branch** = `production` (or `main`), **Database** = `neondb`, **Role** = the one shown (e.g. `neondb_owner`).
5. Make sure **Connection pooling** is switched **ON**. The address then contains `-pooler`. Use the pooled one:
   it handles many short connections, which suits this app.
6. Click **Copy snippet** next to the connection string (it starts with `postgresql://` and ends with
   `sslmode=require`; SSL means the connection is encrypted, which is required).
**Render location:** Environment → Key `DATABASE_URL` → Value: paste → Save Changes.
**Example format (fake):** `postgresql://neondb_owner:AbC123xyz@ep-example-123456-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
**How to check:** after deploy, open `https://<your-app>.onrender.com/health` → it must say `"database":"connected"`.

## SECRET_KEY 🔒
**Plain English:** a long random password that only the app knows. It is used to protect sign-in sessions and reset
links, so that a stolen copy of the database cannot be used to log in.
**Where it comes from:** you generate it. Nobody else needs to know it.
**Exact steps:** in Render's *Add Environment Variable* row, type the Key `SECRET_KEY`, then click **Generate**
(next to the value box) and Render fills in a random value. If you don't see a Generate button, type a random
sentence of at least 40 characters that you will never reuse (e.g. from a password manager's generator).
**Render location:** Environment → `SECRET_KEY`.
**Example format (fake):** `kZ3v9…(40+ random characters)…Qp1`
**How to check:** the deploy log shows `Startup complete`. If it is missing, the log says
`CONFIGURATION: SECRET_KEY must be set…`. Changing it later simply signs everyone out.

## APP_ENV ✅
**Plain English:** tells the app "this is the real, public site". It switches on extra safety checks.
**Value:** `production`. **Render location:** Environment → `APP_ENV`.
**How to check:** the log shows `Starting Data Intelligence Platform (environment: production)`.

## APP_BASE_URL ✅
**Plain English:** your site's public address, used inside password-reset links.
**Where:** Render → your service page → the address under the service name (looks like `https://data-intelligence-xxxx.onrender.com`).
**What to copy:** the whole address, **without** a slash at the end.
**Render location:** Environment → `APP_BASE_URL`.
**How to check:** Admin → Users → *Create reset link*. The link must start with your address.

## COOKIE_SECURE ✅
**Plain English:** makes the browser send the sign-in cookie only over the encrypted (https) connection.
**Value:** `true`. **How to check:** you can sign in on the https address.

## ADMIN_EMAILS ✅
**Plain English:** the e-mail address(es) that may open the **Admin** page (health, usage, self-test, backups).
**Value:** your own e-mail, exactly as you will register it. Separate several with commas.
**How to check:** after signing in with that e-mail, an **Admin** link appears in the top menu.

## STORAGE_PROVIDER ✅
**Plain English:** tells the app to keep uploaded files in cloud storage instead of on Render's temporary disk.
**Value:** `s3` ("S3-compatible", the common language of cloud storage. Neon's storage speaks it.)
**How to check:** Admin → System health shows *File storage (s3): connected*.

## S3_BUCKET ✅
**Plain English:** the name of your storage "bucket", the private folder holding all uploaded files.
**Where it comes from:** you choose it when creating the bucket in Neon (Deployment guide, Step 3).
**Value:** exactly that name, e.g. `dip-files`.
**How to check:** Admin → **Run self-test** → "File storage upload/download/delete" ✔.

## AWS_ENDPOINT_URL_S3 ✅ · AWS_REGION ✅ · AWS_ACCESS_KEY_ID 🔒 · AWS_SECRET_ACCESS_KEY 🔒
**Plain English:** the address of your file storage (endpoint), its region, and the username/password pair that
lets the app put files in and take them out. (The "AWS_" names are just the standard names S3 tools use. You are
not using Amazon.)
**Where they come from:** Neon creates them.
**Exact steps:**
1. Neon console → your project → make sure the **production** branch is selected.
2. Click **Connect** → open the **Storage** tab.
3. Choose the **.env** view (or "Parameters only").
4. Click **Reveal credential** to show the secret.
5. You will see four lines: `AWS_ENDPOINT_URL_S3=…`, `AWS_REGION=…`, `AWS_ACCESS_KEY_ID=…`, `AWS_SECRET_ACCESS_KEY=…`.
**Render location:** add each one as a separate variable with exactly that Key and the text after `=` as the Value.
*Shortcut:* if Render shows **Add from .env**, you can paste all four lines at once.
**Example formats (fake):**
`AWS_ENDPOINT_URL_S3` → `https://br-example-123456.storage.c-1.ap-southeast-1.aws.neon.tech`
`AWS_REGION` → `ap-southeast-1` · `AWS_ACCESS_KEY_ID` → `nak_live_…` · `AWS_SECRET_ACCESS_KEY` → `nsk_live_…`
**How to check:** Admin → Run self-test → storage check ✔. If it says "refused the credentials", re-copy the two keys.
*(Using Cloudflare R2 instead: endpoint = R2 "S3 API" address, region = `auto`, keys from "Manage R2 API Tokens" with Object Read & Write.)*

## STORAGE_QUOTA_MB ✅
**Plain English:** a safety limit so uploads stop with a clear message *before* the free 5 GB is used up.
**Value:** `4500`. **How to check:** Admin → Free-tier usage shows "… of 4500 MB".

## MAX_UPLOAD_MB ✅
**Plain English:** the largest single file accepted. **Value:** `20` (keeps memory use safe on the free server).

## MAX_CELLS (optional — leave it out on the free plan)
**Plain English:** the most spreadsheet cells read from one file (rows × columns). The default, `1500000`
(about 150,000 rows of 10 columns), keeps the free 512 MB server from running out of memory. Bigger files are
read up to this point and the file shows a clear note. **Only raise it** if you move to a larger Render plan
(for example `4000000` on a 2 GB plan).

## OCR_ENABLED ✅
**Plain English:** reading text inside images and scanned PDFs needs a program (Tesseract) that the free Render
setup cannot install. **Value:** `false`. Images are still accepted, and bar-chart shapes are still read approximately.

## EMAIL_PROVIDER, BREVO_API_KEY 🔒, EMAIL_FROM ✅ (optional)
**Plain English:** lets the app e-mail password-reset links. Without it, use Admin → *Create reset link*.
**Exact steps (Brevo, free 300 e-mails/day):**
1. Sign up at **https://www.brevo.com** (free, no card).
2. Add and verify a **sender**: Brevo → Senders, Domains & Dedicated IPs → **Senders** → **Add a sender** → your e-mail → confirm the e-mail Brevo sends you.
3. Create an API key: Brevo → your name (top right) → **SMTP & API** → **API Keys** tab → **Generate a new API key** → name it `data-intelligence` → copy it (shown once).
**Render:** `EMAIL_PROVIDER` = `brevo`, `BREVO_API_KEY` = the key, `EMAIL_FROM` = the verified sender address.
**How to check:** sign out → *Forgot your password?* → the e-mail arrives (check spam the first time).

## REGISTRATION_OPEN ✅ (optional)
`true` (default) lets anyone with the link create an account. `false` allows only existing users and the
`ADMIN_EMAILS` addresses. Recommended `false` once your users are registered.
