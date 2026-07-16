// Builds every *_chart.html entry in this directory as a separate
// vite-plugin-singlefile bundle (it only supports one entry per build).
// Discovers entries automatically so adding a new chart file is enough —
// no need to remember to also update a build script.
import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";

const entries = readdirSync(import.meta.dirname).filter((f) => f.endsWith("_chart.html"));

for (const entry of entries) {
  console.log(`\n--- building ${entry} ---`);
  const result = spawnSync("npx vite build", {
    stdio: "inherit",
    shell: true,
    env: { ...process.env, INPUT: entry },
  });
  if (result.status !== 0) {
    console.error(`Build failed for ${entry}`);
    process.exit(result.status ?? 1);
  }
}
