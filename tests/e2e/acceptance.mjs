// Browser acceptance test for the Phase 1 checklist (README → "Final acceptance test").
// Requires a running app (default http://127.0.0.1:8000) and Playwright.
// Usage: BASE_URL=http://127.0.0.1:8000 node tests/e2e/acceptance.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(path.join(process.env.NODE_PATH || "", "noop.js"));
const { chromium } = require("playwright");
const BASE = process.env.BASE_URL || "http://127.0.0.1:8000";
const here = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE = path.resolve(here, "../../sample-data/consumer-health");
const SHOTS = process.env.SHOTS || "/tmp/e2e-shots";
fs.mkdirSync(SHOTS, { recursive: true });
const results = [];
const ok = (n, label, cond, extra = "") => { results.push({ n, label, pass: !!cond, extra }); console.log(`${cond ? "PASS" : "FAIL"}  ${n}. ${label} ${extra}`); };

const browser = await chromium.launch();
const ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1400, height: 950 } });
const page = await ctx.newPage();
page.on("dialog", (d) => d.accept());
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const shot = (name) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
const stamp = Date.now();

// 1-2 start & account
await page.goto(BASE + "/#/register");
await page.getByRole("heading", { name: "Create your account" }).waitFor();
ok(1, "Application starts", true);
await page.getByLabel("Your name").fill("Alice");
await page.getByLabel("E-mail").fill(`alice${stamp}@example.com`);
await page.getByLabel("Password").fill("CorrectHorse42battery");
await page.getByRole("button", { name: "Create account" }).click();
await page.getByRole("heading", { name: "Projects" }).waitFor();
ok(2, "Create an account", true);

// 3 project
await page.getByRole("button", { name: "New project" }).click();
await page.getByLabel("Project name").fill("Consumer Health Market Analysis");
await page.getByLabel("Industry").fill("Consumer health");
await page.getByLabel("Business objective").fill("Find growth opportunities and test the 2026 strategy against the data.");
await page.getByLabel("Your company / brand (optional)").fill("Verdant Health");
await page.getByRole("button", { name: "Create project" }).click();
await page.getByText("Upload sources").waitFor();
const projectUrl = page.url();
ok(3, "Create a project", projectUrl.includes("/p/"));

// 4-10 multi-file upload
const files = fs.readdirSync(SAMPLE).filter((f) => f !== "README.md").map((f) => path.join(SAMPLE, f));
await page.getByTestId("file-input").setInputFiles(files);
await page.waitForFunction((n) => document.querySelectorAll('[data-testid="file-row"]').length >= n, files.length, { timeout: 30000 });
await page.waitForFunction(() => ![...document.querySelectorAll('[data-testid="file-row"]')].some((r) => /uploaded|processing/.test(r.textContent || "")), null, { timeout: 120000 });
await shot("01-sources");
const rowText = async (name) => (await page.locator('[data-testid="file-row"]', { hasText: name }).textContent()) || "";
ok(4, "Upload multiple files", (await page.getByTestId("file-row").count()) === files.length, `(${files.length} files)`);
for (const [n, label, f] of [[5, "Upload CSV", "marketing_dataset.csv"], [6, "Upload XLSX", "sales_dataset.xlsx"], [7, "Upload PDF", "market_report_2025.pdf"],
  [8, "Upload DOCX", "strategy_2026_proposed.docx"], [9, "Upload PPTX", "business_review_2025.pptx"], [10, "Upload an image", "survey_purchase_drivers.png"]]) {
  const t = await rowText(f);
  ok(n, label, /processed|partial/.test(t), `→ ${t.match(/\d+ table\(s\), \d+ text block\(s\)/)?.[0] || t.slice(0, 60)}`);
}
await page.getByText("Data Quality Report").waitFor();

// 11-13 questions, deep mode, run
await page.getByRole("link", { name: "Analyse", exact: true }).click();
await page.getByText("What do you want to learn from this data?").waitFor();
const qs = ["What are the major trends?", "Which products have potential?", "What gaps exist?", "Is ROAS driven by a particular channel?",
  "How does future strategy compare with historical performance?", "What should we investigate next?"];
await page.getByLabel("Question 1").fill(qs[0]);
for (let i = 1; i < qs.length; i++) {
  await page.getByRole("button", { name: "+ Add another question" }).click();
  await page.getByLabel(`Question ${i + 1}`).fill(qs[i]);
}
ok(11, "Add multiple learning questions", (await page.locator('input[aria-label^="Question"]').count()) === qs.length);
await page.getByText("What can I do with this data?").waitFor();
await page.getByText("ROAS driver analysis").waitFor({ timeout: 10000 });
await page.getByRole("button", { name: /Deep analysis/ }).click();
ok(12, "Select Deep Analysis", await page.getByRole("button", { name: "Run deep analysis" }).isVisible());
await shot("02-analyse");
await page.getByRole("button", { name: "Run deep analysis" }).click();
await page.getByText("Executive Summary").first().waitFor({ timeout: 180000 });
ok(13, "Run analysis", true);
await shot("03-results");

const sectionCount = async (title) => {
  const h = page.locator("section h2", { hasText: new RegExp(`^${title} \\(`) });
  if (!(await h.count())) return 0;
  return Number(((await h.first().textContent()) || "").match(/\((\d+)\)/)?.[1] || 0);
};
ok(14, "See trends", (await sectionCount("Major Trends")) > 0);
ok(15, "See comparisons", (await sectionCount("Comparisons")) > 0);
ok(16, "See gaps", (await sectionCount("Gaps")) > 0);
ok(17, "See opportunities", (await sectionCount("Opportunities")) > 0);
ok(18, "See marketing insights", (await sectionCount("Marketing Insights")) > 0);
const body = await page.locator("main").textContent();
ok(19, "See ROAS analysis", /ROAS by Channel/.test(body));
ok(20, "See strategy/change analysis", /Strategy change:/.test(body) && /mismatch/.test(body));
ok(21, "See potential implications", (await sectionCount("Strategic Implications")) > 0 && /If the trend continues/.test(body));
ok(22, "See confidence levels", (await page.getByText(/(high|medium|low) confidence/i).count()) > 5);

// 23 traceability
const first = page.getByTestId("finding").first();
await first.getByRole("button", { name: "View source" }).first().click();
await page.getByText("Source evidence").waitFor();
await page.waitForTimeout(800);
const drawer = await page.locator('[role="dialog"]').textContent();
await shot("04-source");
ok(23, "Trace findings back to source data", /Source:/.test(drawer) && (/Row/.test(drawer) || /Passage in context/.test(drawer)), `→ ${drawer.match(/Source: [^\n]{0,90}/)?.[0] || ""}`);
await page.getByRole("button", { name: "Close ✕" }).click();
ok(24, "See automatically generated questions", (await page.getByText("generated by the system").count()) > 0);

// 25 ask
await page.getByRole("link", { name: "Ask your data" }).click();
await page.getByLabel("Your question").fill("Is ROAS being driven by Meta?");
await page.getByRole("button", { name: "Ask", exact: true }).click();
await page.getByTestId("ask-message").first().waitFor({ timeout: 30000 });
await page.waitForTimeout(500);
const ans = await page.getByTestId("ask-message").first().textContent();
await shot("05-ask");
ok(25, "Ask a follow-up question", /Meta/.test(ans) && /EVIDENCE/.test(ans));

// 26-29 report & exports
await page.getByRole("link", { name: "Reports" }).click();
await page.getByRole("button", { name: "Generate report" }).click();
await page.getByTestId("report-row").first().waitFor({ timeout: 60000 });
ok(26, "Generate a report", true);
for (const [n, fmt, magic] of [[27, "PDF", "%PDF"], [28, "DOCX", "PK"], [29, "XLSX", "PK"]]) {
  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 90000 }), page.getByRole("link", { name: `Export ${fmt}` }).first().click()]);
  const p = await dl.path();
  const buf = fs.readFileSync(p);
  fs.copyFileSync(p, `${SHOTS}/report.${fmt.toLowerCase()}`);
  ok(n, `Export ${fmt}`, buf.slice(0, magic.length).toString() === magic && buf.length > 5000, `(${Math.round(buf.length / 1024)} KB, ${dl.suggestedFilename()})`);
}
await shot("06-reports");

// 30 delete a file
await page.getByRole("link", { name: "Sources" }).click();
await page.getByTestId("file-row").first().waitFor();
const before = await page.getByTestId("file-row").count();
await page.locator('[data-testid="file-row"]', { hasText: "survey_purchase_drivers.png" }).getByRole("button", { name: "Delete" }).click();
await page.waitForFunction((n) => document.querySelectorAll('[data-testid="file-row"]').length === n - 1, before);
ok(30, "Delete a file", true);

// 32 isolation (before deleting the project)
const ctxB = await browser.newContext();
const b = await ctxB.newPage();
await b.goto(BASE + "/#/register");
await b.getByLabel("E-mail").fill(`bob${stamp}@example.com`);
await b.getByLabel("Password").fill("CorrectHorse42battery");
await b.getByRole("button", { name: "Create account" }).click();
await b.getByRole("heading", { name: "Projects" }).waitFor();
const bobSees = await b.getByRole("link", { name: "Consumer Health Market Analysis", exact: true }).count();
await b.goto(projectUrl);
await b.waitForTimeout(1500);
const bobView = (await b.locator("body").textContent()) || "";
await b.screenshot({ path: `${SHOTS}/07-isolation.png` });
console.log("bob view:", bobView.slice(0, 300));
const pid = projectUrl.split("/p/")[1].split("/")[0];
const apiStatus = await b.evaluate(async (id) => (await fetch(`/api/projects/${id}/files`)).status, pid);
ok(32, "Project isolation between users", bobSees === 0 && /Project not found/.test(bobView) && apiStatus === 404, `(other user's API call → ${apiStatus})`);
await ctxB.close();

// 31 delete project
await page.getByRole("link", { name: "Settings", exact: true }).last().click();
await page.getByRole("button", { name: "Delete project" }).click();
await page.getByRole("heading", { name: "Projects" }).waitFor();
await page.waitForTimeout(1000);
await shot("08-after-delete");
const after = (await page.locator("main").textContent()) || "";
console.log("after delete:", after.slice(0, 300));
ok(31, "Delete a project", (await page.getByRole("link", { name: "Consumer Health Market Analysis", exact: true }).count()) === 0);

ok(0, "No browser JavaScript errors", errors.length === 0, errors.slice(0, 3).join(" | "));
await browser.close();
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
fs.writeFileSync(`${SHOTS}/results.json`, JSON.stringify(results, null, 2));
process.exit(failed.length ? 1 : 0);
