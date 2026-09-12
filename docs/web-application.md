# CATALYST web application

CATALYST runs on Sites using a Cloudflare Worker backend, browser assets, a private D1 database, and private R2 object storage. Source is maintained in the private `mporosoff/CATALYST` GitHub repository. The deployment source mirror contains the same committed source.

## User workflow

1. Sign in, select a partner and modality, and upload CSV, XLSX, or a JSON array of flat records. Upload at most six files, 4 MiB each, 8 MiB combined, and 50,000 combined cells.
2. For Rochester toolkit RWGS, include one original GC report, matching analysis XLSX, summary CSV, and flows CSV. Select one processing revision per submission. For other supported tables, select or create a mapping from the Field mapping tab.
3. Confirm scientific context. The reactor form asks for absolute pressure, catalyst mass, flow reference conditions, calibration, reactor configuration, and GC injection interval. Spectroscopy requires technique, spectral axis/signal units, calibration, and mappings for the axis and signal. Synthesis requires an explicit method and specimen/run identity.
4. Save a new revision. It preserves the source artifacts, mapping content hash, source format/version, processing choices, validation findings, creator, and parent revision.
5. Review the preview, source files, and warnings. A reviewer records a decision and explicitly acknowledges the warnings before approving the exact revision checksum. Any later edit creates an unapproved revision.
6. From SciSure connection, verify the active sandbox group. Choose an unsigned experiment or create `CATALYST Testing → Connector Tests → GC Upload Test` in the confirmed group. The server saves a destination separately for each partner.
7. Publish the approved revision. CATALYST adds a source-files section and an approved-data/provenance section named with the immutable revision ID. Original files and a canonical JSON package are uploaded. Each upload is downloaded and SHA-256 checked before its operation is marked complete.

The owner can register more partners and assign viewer, contributor, or reviewer roles to verified account IDs. Sites sharing and application membership are separate: both must permit access. The initial deployment remains owner-private. An unassigned user cannot read data. An administrator should obtain an account's stable ID from its verified sign-in context, never infer it from an email address.

## Scientific scope

Normalization uses explicit source-column mappings, a fixed unit-conversion registry, and exact name aliases. Mapping identity includes partner, modality, file format, source format/version, mapping name, and version number. Numbers retain source decimal text; supported numerical operations use 80-digit decimal arithmetic and limit source numeric fields to 60 digits. Missing numerical values are not zero.

XLSX formulas and cached values are retained separately. They are never executed. Generic mapping of a worksheet containing formulas is blocked; supply a values export. Macros, external workbook relationships, DTD/entity declarations, ambiguous headers, duplicate JSON keys, unsafe ZIP paths, and oversized workbooks are rejected.

The Rochester web adapter imports toolkit numerical results and records their provenance. It does not independently reproduce GC conversion or selectivity. The confirmed **22.4-minute** interval can be entered for the current run; this creates a nominal sequential included-point time axis in seconds while retaining the source time axis. It is not a global default for other runs. A confirmed catalyst mass can produce a separately recorded mass-normalized inlet-flow value. This is not volume-based GHSV. Mass, standard-flow basis, calibration, and source-identity discrepancies still require review.

Original and embedded raw report text must match. Numerical report cells permit a relative difference of at most 2e-15 for Excel serialization; every nonexact numerical comparison is flagged. Both byte-exact originals remain available. No filename-derived date, catalyst identity, mass, or flow is silently accepted.

The initial connector publishes experiment file sections. Creation of physical catalyst samples, custom sample-type schemas, native used/generated sample links, and experiment-per-run automation are later extensions requiring validated tenant schemas and representative partner examples. The canonical record already carries specimen and run identifiers. General synthesis and spectroscopy tables support explicit mappings; technique-specific scientific processing requires separate validated recipes.

## Security and recovery

- The SciSure token exists only in the backend deployment secret store. It is never returned by an API route, included in browser bundles, stored in D1/R2, or committed. The token is sent as the raw `Authorization` header value, per the official REST contract.
- This release allowlists `https://sandbox.elabjournal.com`. Redirects are not followed. Each write checks the active group and the experiment's identity, study, deletion/template flags, and signature status. It never switches the account's active group.
- Sites supplies verified sign-in headers. Do not expose this Worker through an independent public endpoint that permits callers to forge those headers. Initial owner bootstrap is allowed only while the Site is owner-private; turn `ALLOW_OWNER_BOOTSTRAP` off after the owner has enrolled, before sharing.
- All data routes enforce application membership. Mutating requests require the exact application Origin. File downloads are private, noncached attachments and verify their stored checksum.
- Revisions and approvals are immutable. Database compare-and-swap guards reject stale edits and approvals. Publication has unique revision/tenant/destination records and unique per-operation records. Concurrent callers cannot both claim the same write.
- Unknown write outcomes are never automatically reissued. `Reconcile and continue` searches for exact section/file identities and verifies file bytes. If found, it completes the existing operation and continues pending work. If absent or conflicting, it pauses for an owner investigation. The owner must resolve such a case through SciSure support and a documented ledger repair; the web app deliberately has no blind retry/reset action. A crashed in-progress request has a 60-second initial cooldown.
- Test-project creation supplies the nonempty `notes` field required by SciSure's service validation. Migration 0002 records and repairs only the inspected initial sandbox project's HTTP 400 rejection, matching its exact operation, timestamp, state, and evidence. It preserves that evidence in audit and retains remote read-back before a corrected write. Other uncertain operations remain paused.
- An incomplete publication blocks a new revision until reconciliation. Partial remote files are retained and their known IDs remain visible. The app does not delete remote laboratory records to roll back a failed upload.

## Development and validation

Use Node 24. Install with `npm ci --ignore-scripts`. Generate schema migrations with `npm run db:generate`; never edit applied migrations. Build with `npm run build`. Apply the SQL migrations to local D1, then `npm run dev` starts the application at `http://localhost:5173`. The starter provides a localhost-only synthetic sign-in; it strips incoming forged identity headers and is not included in production authentication.

- `npm run typecheck`
- `npm run test:web`: parser/unit/transport and SQLite-backed publication-ledger checks.
- `node tests/web-workflow.mjs`: 21 real local HTTP checks with synthetic data; requires the development server and applied migrations. Checks auth, CSRF, source preservation, mappings, stale edits, approval, and new-revision invalidation. Publishing must remain disabled locally for this fixture.
- `python -m unittest discover -s tests -v`: 37 existing offline ingestion tests.
- `node scripts/inspect-bundle.mjs ORIGINAL_XLSX RESULT_DIRECTORY`: inspect a local toolkit bundle without copying research files into the repository.

The optional WebMCP surface can read review state and save confirmed context as a revision through the same authenticated application action. It cannot approve or publish. Unsupported browsers retain the visible UI. A supported live WebMCP validation context was unavailable during initial implementation; those browser tools are not claimed as verified. Browser visual/accessibility automation was not performed; the interface uses labeled controls, semantic tables, keyboard-accessible component primitives, status messages, responsive layouts, and reduced-motion support.

## Official API references

The connector contract was checked against the current official OpenAPI reference on September 12, 2026, including `GET /groups/active`, paged experiments/projects/studies, experiment sections, and binary file upload/download.

- [REST overview and API token](https://developer.elabnext.com/docs/overview)
- [General developer documentation](https://developer.elabnext.com/docs/general)
- [REST API reference](https://developer.elabnext.com/reference)

Live tenant validation requires the user's secret and sandbox access. A successful local test with simulated SciSure responses does not establish actual SciSure permissions or production readiness.
