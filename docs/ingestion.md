# Offline ingestion component

## Implemented scope

`catalyst_ingest` is a dependency-free Python 3.13 processing-service component. It performs no network requests, SciSure writes, spreadsheet recalculation, or user authentication. The deployment topology is still open; this Python component requires a Python service/job runtime and is not directly executable in a JavaScript Cloudflare Worker.

Readers support XLSX, UTF-8 CSV, and JSON arrays of flat records. CSV values remain text, including IDs with leading zeros and strings beginning with `=`. JSON records must share field names. CSV/JSON are preserved for review but have no scientific mapping profiles yet. Unknown entities, modalities, or source layouts yield `MAPPING_REQUIRED` rather than a guessed mapping.

Two draft XLSX profiles cover the Rochester GC report and processed-analysis layouts. Draft status means the profiles are development candidates, not approved production mappings. Names and conversions are traceable to a profile version and content checksum. Some literal mass/time/reference-condition fields can be normalized. Scientific formula outputs remain unavailable.

The raw XLSX reader ignores formatting and extracts only the required source XML parts. It preserves source cell types, lexical numeric values, formulas, formula attributes, and cached results separately. No formula or macro is executed. Archive size, member count, cell count, coordinates, and text length are bounded. Entity declarations, unsafe archive paths, macros, and external workbook links are rejected. Parsing does not certify a file as safe to open in another application.

The pair-review path records explicit user confirmation. Superseded labels remain intact. It checks direct raw-row references for accepted-point assignments to source-labeled blanks/bypass/standby. It is not a complete Excel dependency evaluator and cannot prove the scientific results correct.

## Usage

From the repository root, using Python 3.13:

```sh
python -m catalyst_ingest /private/raw.xlsx /private/reprocessed.xlsx \
  --entity university-of-rochester --modality reactor --reactor-type packed_bed \
  --same-run --row-labels-superseded \
  --store-root /private/catalyst-store --output /private/preview.json
```

Use the relationship flags only when confirmed by the contributor. The two paths must be in raw, processed order. The output includes source content; put it in approved private storage, never in tracked source or public static assets.

`--store-root` keeps byte-for-byte originals under content-derived keys and verifies checksums. This local development helper is not a substitute for an authenticated durable artifact service, retention policy, or backups.

The command returns success when it creates a review preview, including a preview with blocking issues. Publication remains `not_published` and `eligible: false` for every current path. Errors and warnings do not authorize approval or scientific correction.

## Verification

```sh
python -m unittest discover -s tests -v
```

Synthetic tests cover source preservation, formula/cache handling, malformed-style GC exports, archive/XML rejection, missing-versus-zero values, ambiguous units, profile selection, deterministic literal conversions, row-category discrepancies, and an end-to-end offline import. GitHub Actions runs the same suite without secrets or research data.

## Next integration work

Implement the accessible upload/review interface and an authenticated backend with per-entity authorization, durable revisions, immutable approvals, and an operation ledger. Define worker resource limits and isolation for untrusted file parsing. Add tenant-specific typed-field/object discovery and sandbox publication after a backend secret is configured.

Before processing these source formats into approved scientific results, confirm the GC Amount/calibration basis, Ar/O2 reference-channel meaning, row roles/time-axis handling, bypass artifact/selection, gas-flow reference conditions, and physical specimen/run identity. Incomplete partner results may be archived as submitted artifacts without being represented as validated measurements.
