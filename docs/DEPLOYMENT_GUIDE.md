# Deployment guide (for non-developers)

You will set up four free accounts and connect them. Do **one step at a time**: each step says what to click and
how to know it worked. Allow about 60–90 minutes the first time.

```text
Your browser ──https──► Render (runs the app) ──► Neon database (accounts, projects, results)
                                              └──► Neon Object Storage (uploaded files, private)
GitHub (the code) ──automatic deploy──► Render
```

**Golden rules:** never paste a password, key or connection string into a chat, an e-mail or a GitHub file.
Paste secrets **only** into Render's Environment page. If a screen looks different from these instructions
(websites change), look for the closest matching button. The wording here was checked on 24 November 2026.

---

## Step 1 — Create the Neon database (≈10 min)

1. Open **https://neon.tech** → **Sign up** (GitHub or Google sign-in is simplest). No credit card is needed.
2. Click **New Project**.
3. **Project name:** `data-intelligence`.
4. **Region:** choose **AWS Asia Pacific 1 (Singapore)**. This region supports file storage and is close to India.
   (Other regions that support file storage: US East Ohio, US East N. Virginia, Europe Frankfurt.)
5. If you see a **Services** section with **Object storage**, switch it **on**, set **Bucket name** to `dip-files`
   and **Visibility / Access** to **Private**. (If you don't see it, you will create the bucket in Step 3.)
6. Click **Create project**.
7. ✔ *It worked if* you see the project dashboard with a branch called **production** (or **main**) and a database **neondb**.

**About the tables:** you do not create tables yourself. The app creates them automatically the first time it
starts (Step 6). These are called "migrations": scripts that add tables. Running them again is safe; they never
delete data.

## Step 2 — Prepare GitHub (≈10 min)

The code must be in a **private** GitHub repository that Render can read.

1. Sign in at **https://github.com**.
2. Open your project repository. If you don't have one yet: click **+** (top right) → **New repository** → name
   `data-intelligence-platform` → choose **Private** → **Create repository**.
3. Put the latest code in it (the Phase 2 ZIP). Pick one:
   - **Easiest:** in claude.ai go to **Settings → Connectors**, connect **GitHub**, and tell Claude
     "push the Phase 2 code". Claude uploads it for you.
   - **Or GitHub Desktop** (free app, desktop.github.com): clone the repository, copy the unzipped files into the
     folder, click **Commit to main** then **Push origin**.
   - **Or the website:** in the repository click **Add file → Upload files**, drag the unzipped folders in (GitHub
     accepts up to 100 files per upload, so do `backend`, then `frontend`, then the rest), then **Commit changes**.
4. Safety check, on the repository's main page:
   - ✔ there is a file called `.gitignore`;
   - ✔ there is **no** file called `.env` (only `.env.example` is allowed);
   - ✔ there is a file called `.python-version`.
   If you see a `.env` file, delete it (open it → ⋯ → **Delete file**) and treat everything in it as exposed:
   reset those passwords and keys at their websites.

## Step 3 — Create the file storage (≈5 min)

If you already created the `dip-files` bucket in Step 1, skip to point 5.

1. Neon console → your project → make sure the **production** branch is selected.
2. Open the **Object storage** tab (left menu or project tabs).
3. Click **New bucket** → name `dip-files` → access level **Private** → **Create**.
4. ✔ *It worked if* `dip-files` is listed.
5. Leave this tab open; you will copy the storage keys in Step 5.

*If Object Storage is not offered for your project* (it is in beta and only in some regions), use Cloudflare R2
instead: see the note at the end of `docs/ENVIRONMENT_VARIABLES_GUIDE.md` → "AWS_…" section.

## Step 4 — Create the Render web service (≈10 min)

Follow these in order and stop at "Deploy".

1. Open **https://render.com** → **Get Started** → sign up **with GitHub** (this connects the two). No card needed for the free instance.
2. In the Render dashboard click **New +** → **Web Service**.
3. **Connect a repository:** choose **GitHub**. If your repository isn't listed, click **Configure account**
   (or *Connect GitHub*) and allow Render access to **only** the `data-intelligence-platform` repository.
4. Select the repository → **Connect**.
5. **Name:** `data-intelligence` (this becomes part of your web address).
6. **Language / Runtime:** **Python 3**.
7. **Branch:** `main`.
8. **Region:** **Singapore** (same place as the Neon database, which keeps it fast).
9. **Root Directory:** leave **empty**.
10. **Build Command.** *This command tells Render how to prepare the application before it starts.* It installs the
    Python parts, then builds the website. Copy exactly:
    ```
    pip install -r backend/requirements.txt && cd frontend && npm install --include=dev && npm run build
    ```
11. **Start Command.** *This command tells Render how to start the application.* Copy exactly:
    ```
    cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips="*" --no-access-log
    ```
    (Render provides `$PORT` itself; `--proxy-headers` lets rate limits see each visitor's real address.)
12. **Instance Type:** **Free**.
13. Open **Advanced** → **Health Check Path:** `/health`. Render then checks this address to confirm the app is alive.
14. **Do not click Deploy yet.** Scroll to **Environment Variables**. Continue with Step 5.

## Step 5 — Add the environment variables (≈15 min)

For each row: click **Add Environment Variable**, type the **Key** exactly, paste or type the **Value**. Full
explanations for every one are in **docs/ENVIRONMENT_VARIABLES_GUIDE.md**.

| # | Key | Value — where it comes from | Secret? |
|---|---|---|---|
| 1 | `DATABASE_URL` | Neon → project → **Connect** → Connection pooling **ON** → **Copy snippet** | 🔒 KEEP SECRET |
| 2 | `SECRET_KEY` | Click **Generate** in Render | 🔒 KEEP SECRET |
| 3 | `APP_ENV` | `production` | ✅ safe |
| 4 | `COOKIE_SECURE` | `true` | ✅ safe |
| 5 | `ADMIN_EMAILS` | your own e-mail address | ✅ safe |
| 6 | `STORAGE_PROVIDER` | `s3` | ✅ safe |
| 7 | `S3_BUCKET` | `dip-files` (your bucket name) | ✅ safe |
| 8 | `AWS_ENDPOINT_URL_S3` | Neon → **Connect** → **Storage** tab → `.env` view | ✅ safe |
| 9 | `AWS_REGION` | same place (e.g. `ap-southeast-1`) | ✅ safe |
| 10 | `AWS_ACCESS_KEY_ID` | same place | 🔒 KEEP SECRET |
| 11 | `AWS_SECRET_ACCESS_KEY` | same place → **Reveal credential** | 🔒 KEEP SECRET |
| 12 | `STORAGE_QUOTA_MB` | `4500` | ✅ safe |
| 13 | `MAX_UPLOAD_MB` | `20` | ✅ safe |
| 14 | `OCR_ENABLED` | `false` | ✅ safe |

`APP_BASE_URL` is added **after** the first deploy (Step 6), because the address only exists once the service is created.
Optional e-mail (`EMAIL_PROVIDER`, `BREVO_API_KEY`, `EMAIL_FROM`) can be added any time later.

✔ *Check:* 14 rows; no spaces before or after values; Keys spelled exactly.

## Step 6 — Deploy (≈10 min)

1. Click **Deploy Web Service** (or **Create Web Service**).
2. Render opens the **Logs**. You will see, in order:
   - `pip install …` lines (Python parts), then `npm install` and `vite build` (the website): 3–8 minutes;
   - `Build successful`;
   - then the app starting:
     ```
     Starting Data Intelligence Platform (environment: production)
     Database: PostgreSQL at ep-…-pooler…neon.tech / database 'neondb'
     Database connection: OK
     Database migrations: applied 0001_initial
     File storage: s3
     Startup complete
     ```
   - and finally **"Your service is live"**.
3. Copy your address from the top of the page (e.g. `https://data-intelligence-xxxx.onrender.com`).
4. Add one more variable: Environment → **Add Environment Variable** → Key `APP_BASE_URL` → Value = that address
   (no slash at the end) → **Save Changes**. Render redeploys automatically (≈3 minutes).
5. **Check the tables exist:** Neon console → your project → **Tables** (left menu). You should see `users`,
   `projects`, `files`, `findings`, `schema_migrations` and others.
6. **Health check:** open `https://<your address>/health`. ✔ It must show `{"status":"healthy","database":"connected"}`.

Future deploys: every time new code reaches the `main` branch on GitHub, Render rebuilds and restarts automatically.
New migrations (if any) are applied at start-up; existing data is never removed.

## Step 7 — Test (≈20 min)

Open your address. The first visit after a quiet period can take about a minute (free plan wake-up).

**Owner checks**
- [ ] Register using the e-mail you put in `ADMIN_EMAILS`. An **Admin** link appears in the top menu.
- [ ] Admin → **Run self-test**. All four checks ✔ (database connection, migrations, read/write, file storage).

**Account**
- [ ] Sign out, sign in again.
- [ ] Password reset: Admin → Users → **Create reset link** → open it in a private window → set a new password → sign in.
      (If Brevo is set up: use *Forgot your password?* and check the e-mail.)

**Projects & files**
- [ ] Projects → **Create demo project**. Wait until all 9 files show *processed* (images may show *partial*
      because OCR is off; that is expected).
- [ ] Create a project of your own, edit its details in Settings, delete it.
- [ ] Upload a file, **Download** it, **Delete** it.

**Analysis**
- [ ] Demo project → Analyse → **Quick**, then **Deep**, then **Research** analysis. Each completes (1–3 minutes;
      keep the tab open).
- [ ] Results: open a finding → **View source** shows the original rows or passage.
- [ ] Ask your data: "Is ROAS being driven by Meta?" returns an answer with evidence.

**Reports**
- [ ] Reports → Generate → export **PDF**, **DOCX**, **XLSX**; each opens.

**Persistence**
- [ ] Render → your service → **Manual Deploy → Restart service** (or redeploy). After it is back, open the demo
      project → Sources → **Download** a file. ✔ It still downloads (proves files are in cloud storage, not on Render's disk).

**Security**
- [ ] In a private window, register a second account. It sees **no** projects of the first account.
- [ ] While signed in as the second account, paste the first account's project address (e.g. `…/#/p/<id>/sources`).
      ✔ "Project not found".
- [ ] Change the end of a project address to random letters. ✔ "Project not found" (no technical error).
- [ ] Signed out, open `https://<your address>/api/projects`. ✔ "You need to sign in."
- [ ] Open `https://<your address>/api/admin/overview` as the second account. ✔ "does not exist".

When every box is ticked, Phase 2 is complete. Optional final step: set `REGISTRATION_OPEN=false` once your users have registered.

## Step 8 — Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| Build log: `npm: command not found` or `vite: not found` | The build command was typed differently | Copy the build command from Step 4 again exactly |
| Build log: `No matching distribution` for a Python package | Python version | Check that `.python-version` (containing `3.13`) is in the repository root |
| Log: `CONFIGURATION: …` then the deploy fails | A required variable is missing or wrong | Read the sentence after `CONFIGURATION:`; it names the variable. Fix it in Environment |
| Log: `Database connection: FAILED` | `DATABASE_URL` wrong, or copied with spaces | Copy it again from Neon (Connect → pooled → Copy snippet) |
| `/health` shows `"database":"unavailable"` | Neon unreachable or monthly compute used up | Neon console → check the project and Usage |
| Self-test: storage "refused the credentials" | Keys copied wrongly | Neon → Connect → Storage → **Rotate credentials** if needed, copy both keys again |
| Self-test: "bucket does not exist" | `S3_BUCKET` doesn't match | Make the value exactly the bucket name |
| Site takes ~1 minute to open | Free plan sleep | Normal. Paid instance removes it |
| "The server is waking up or busy" message | Same | Wait a minute, try again |
| Analysis shows "interrupted" | The free server restarted (Render may restart it any time) | Run the analysis again |
| Upload refused "free storage limit" | Storage nearly full | Delete old projects/files, or raise the plan |
| "The database has reached its free-plan limit" | Neon 1 GB storage full | Delete old projects/analyses, or upgrade Neon |
| Password reset e-mail never arrives | No e-mail service configured, or Brevo sender not verified | Use Admin → Create reset link, or finish Brevo setup |
| Images show "partial" | OCR is off on the free setup | Expected. Chart shapes are still read approximately |
| Admin link missing | `ADMIN_EMAILS` doesn't exactly match your sign-in e-mail | Correct it in Environment |

**Never** fix problems by pasting secrets into chats or GitHub. When asking for help, say *"the variable has been
added"*; never send its value.

## Optional — your own domain (later)
Render → your service → **Settings → Custom Domains → Add Custom Domain**, then add the DNS record Render shows at
your domain registrar. Afterwards update `APP_BASE_URL` to the new address. Not needed to launch.
