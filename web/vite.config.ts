import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The build is served by src/webapp.py under a strict CSP (script-src/style-src/font-src 'self'):
// no inlined assets, so fonts and images stay files.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
  },
});
