// Fallback frontend build that needs no `npm install` (used where Vite is unavailable).
// Normal builds use:  cd frontend && npm install && npm run build
// Usage: NODE_PATH=<dir containing esbuild, react, react-dom, tailwindcss> node scripts/build_frontend_offline.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "frontend");
const modDir = process.env.NODE_PATH || path.join(root, "node_modules");
const require = createRequire(path.join(modDir, "noop.js"));
const esbuild = require("esbuild");
const tw = await import(pathToFileURL(path.join(modDir, "tailwindcss", "dist", "lib.mjs")).href);
const out = path.join(root, "dist");
fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(path.join(out, "assets"), { recursive: true });

await esbuild.build({
  entryPoints: [path.join(root, "src", "main.tsx")], bundle: true, minify: process.env.NO_MINIFY ? false : true, format: "esm", target: "es2020",
  outfile: path.join(out, "assets", "app.js"), jsx: "automatic", nodePaths: [modDir], loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"production"' }, logLevel: "warning",
});

const css = fs.readFileSync(path.join(root, "src", "index.css"), "utf8");
const compiler = await tw.compile(css, {
  base: root,
  loadStylesheet: async (id, base) => {
    const file = id === "tailwindcss" ? path.join(modDir, "tailwindcss", "index.css") : path.resolve(base, id);
    return { path: file, base: path.dirname(file), content: fs.readFileSync(file, "utf8") };
  },
});
const candidates = new Set();
const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
  const p = path.join(d, e.name);
  if (e.isDirectory()) walk(p);
  else if (/\.(tsx?|html)$/.test(e.name)) for (const m of fs.readFileSync(p, "utf8").matchAll(/[A-Za-z0-9_\-:\/\.\[\]%#()!@,=]+/g)) candidates.add(m[0]);
});
walk(path.join(root, "src"));
fs.writeFileSync(path.join(out, "assets", "app.css"), compiler.build([...candidates]));

const html = fs.readFileSync(path.join(root, "index.html"), "utf8")
  .replace('<script type="module" src="/src/main.tsx"></script>', '<link rel="stylesheet" href="/assets/app.css" />\n    <script type="module" src="/assets/app.js"></script>');
fs.writeFileSync(path.join(out, "index.html"), html);
console.log("Built frontend into", out);
