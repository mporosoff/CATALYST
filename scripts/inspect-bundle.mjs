import { build } from "esbuild";
import { readFileSync, readdirSync, mkdirSync } from "node:fs";
import { resolve, basename, join } from "node:path";
import { pathToFileURL } from "node:url";
const [rawPath, resultDirectory] = process.argv.slice(2);
if (!rawPath || !resultDirectory)
  throw new Error(
    "Supply an original workbook path and one toolkit result directory.",
  );
mkdirSync(".test-build", { recursive: true });
await build({
  entryPoints: ["lib/ingest.ts"],
  bundle: true,
  platform: "node",
  format: "esm",
  outfile: ".test-build/ingest.mjs",
});
const { parseFile, buildPreview } = await import(
  pathToFileURL(resolve(".test-build/ingest.mjs"))
);
const files = [
  rawPath,
  ...readdirSync(resultDirectory)
    .filter((n) => /_gc_(summary\.csv|flows\.csv|analysis\.xlsx)$/.test(n))
    .map((n) => join(resultDirectory, n)),
];
const parsed = await Promise.all(
  files.map((path, i) =>
    parseFile(new Uint8Array(readFileSync(path)), basename(path), String(i)),
  ),
);
const p = buildPreview(parsed, "university-of-rochester", "reactor", {
  intervalMin: "22.4",
});
console.log(
  JSON.stringify(
    {
      kind: p.kind,
      files: p.artifacts.length,
      rows: p.rows.length,
      processing: p.processing,
      issues: p.validation.issues,
    },
    null,
    2,
  ),
);
