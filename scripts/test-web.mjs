import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { join, resolve } from "node:path";
mkdirSync(".test-build", { recursive: true });
const directory = mkdtempSync(resolve(".test-build", "run-"));
try {
  const output = join(directory, "core.mjs");
  await build({
    entryPoints: ["tests/web-core.test.ts"],
    bundle: true,
    platform: "node",
    format: "esm",
    outfile: output,
  });
  const r = spawnSync(process.execPath, ["--test", output], {
    stdio: "inherit",
  });
  process.exitCode = r.status ?? 1;
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
