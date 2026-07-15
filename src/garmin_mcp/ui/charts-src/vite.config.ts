import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Each chart is a standalone HTML entry point, bundled into one
// self-contained file (JS/CSS inlined) so the Python server can serve it
// as-is with no CDN dependency at runtime. vite-plugin-singlefile only
// supports a single entry per build, so each chart is built as a separate
// `vite build` invocation selecting its entry via the INPUT env var
// (see package.json's "build" script).
const input = process.env.INPUT || "sleep_chart.html";

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    outDir: "../charts",
    emptyOutDir: false,
    target: "esnext",
    rollupOptions: {
      input,
    },
  },
});
