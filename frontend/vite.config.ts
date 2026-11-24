import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the API runs on http://localhost:8000 and Vite proxies /api to it,
// so the browser sees a single origin (needed for the secure session cookie).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist", sourcemap: false },
});
