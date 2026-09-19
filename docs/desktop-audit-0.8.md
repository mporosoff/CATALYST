# CATALYST desktop audit — 0.8.0

Reviewed 19 September 2026. This review covers the desktop submission, scientific normalization, SciSure transport, publication/read-back, credentials, and explicit download workflows. Repairs retain original files and provenance; they do not reproduce partner scientific calculations.

## Current workflow

Users can prepare CSV/XLSX/flat JSON data or preserve native files with context, review the mapping and validation, approve an exact revision, and send it directly to a permitted SciSure experiment. The sandbox remains the default. **SciSure connection** also accepts an explicitly entered HTTPS DNS server origin on port 443. Tokens are entered for that server and optionally stored separately by server in the operating system credential store. SciSure's account and group permissions determine access; sharing a token shares its account permissions and identity.

**Saved records → Original files → Download…** saves a selected attachment. **Saved records → Saved reviews → More actions → Download review package…** saves a ZIP after verifying the completed review and all original bytes. It contains the approved review with context/mapping/provenance, completion receipt, originals, and standardized JSON plus a convenience CSV when rows exist. These are explicit user-selected saves. The application still creates no automatic research-data cache.

## Findings and repairs

| Area | Problem found | Repair | Regression checks |
| --- | --- | --- | --- |
| Server and token access | The desktop connection was fixed to the sandbox; users could not target their own SciSure server. | Accept a validated, explicitly chosen HTTPS DNS origin on port 443; bind tokens, destinations, and configuration plans to that server. Clear token/connection state when it changes. Reject ambiguous origins and redirects. | `test_transport_audit.py`, `test_publication_audit.py`: custom-server upload/download, cross-server rejection, redirects, credential separation, permission diagnostics. |
| Usable downloads | Saved reviews and attachments could be read into memory, without an explicit local download workflow. | Add individual attachment saving and verified complete review ZIPs; validate receipts and original bindings before export. Stage a complete save before replacing the requested destination. Flatten toolkit quantities into CSV columns labeled with field and unit, retain exact decimal text, and reject column collisions. | `test_exports.py`, `test_export_audit.py`, and native GUI checks: exact original bytes, complete-package requirements, altered receipts, stale attachments, cancellation/failed saves, formula-safe CSV views, toolkit flattening and collision rejection. |
| Numeric evidence | Offline JSON reviews lost decimal precision; legacy processed-workbook normalization used rounded values and ambient Decimal precision. | Retain JSON numeric tokens and use exact XLSX numeric evidence with isolated precision for supported unit conversions. | `test_data_audit.py`: long decimals, changed ambient precision, small nonzero converted values. |
| Formulas and workbook usability | Cached formula results could become raw GC observations. Values-only derived settings could crash preview. Formatting-only cells could create phantom table columns. | Keep formula results unverified, preserve derived literals without claiming reproduced calculations, and ignore genuinely empty cells as column definitions. Unlabeled nonempty data still fails validation. | `test_data_audit.py`: cached formulas, literal derived settings, empty formatting cells, nonempty unlabeled columns. |
| Toolkit result consistency | Included rows could lack injection/time/conversion; contradictory blank/bypass flags, impossible values, and extra embedded formulas could pass earlier checks. | Flag these conditions, require a nonempty steady-state selection, bound injection indices, and compare workbook settings at source precision. Preserve original reported values for review. | `test_data_audit.py`: missing selected results, range checks, row-role conflicts, embedded formulas, precision comparisons. |
| Stored review consistency | Recomputed checksums alone could accompany invalid normalized numbers, conflicting subject labels, or toolkit values/time axes that differed from preserved evidence. | Revalidate numeric bounds and declared subject aliases; compare toolkit results and nominal time axes with the preserved source review and source hashes. | `test_data_audit.py`: malformed stored numbers, changed subject labels, changed quantities/axes, source hashes and unresolved source-review errors. |
| Transfer and catalog changes | Changed or archived files/sections, late corruption, or concurrent profile conflicts could leave a stale apparent success. API IDs could differ only by string/integer representation. | Recheck destination, section and attachment state; read back the completed packet and originals; recheck catalog conflicts; normalize IDs; reserve orphan revision IDs. | `test_publication_audit.py`: stale snapshots, late corruption, archived sections, profile conflicts, orphan reservations and string IDs. |
| Retry identity | Renamed experiment display labels or a changed approval could interfere with retrying an existing revision. | Keep canonical ownership checks and reuse the existing saved destination/approval envelope; reject approval replacement. | `test_publication_audit.py`: renamed destinations, partial-transfer continuation and approval replacement. |
| API and setup diagnostics | Malformed account/group/configuration responses or wrong sample/protocol bindings could cause exceptions or misleading readiness results. | Return actionable validation errors, check actual IDs/group bindings, and handle documented rate-limit dates without replaying writes. | `test_transport_audit.py`, `test_publication_audit.py`: malformed response shapes, wrong bindings, controlled options, HTTP-date rate limits. |

## Verification

**225 Python unit/integration tests passed on Python 3.13.15.** Focused synthetic suites cover these repairs alongside the existing source, lineage, modality, approval, configuration, transport, and recovery tests. They use synthetic records and simulated SciSure responses.

The final Windows native GUI check passed, including the custom-server credential workflow, individual attachment and review ZIP saves, cancellation, failed-save preservation, and connection-state resets. The portable Windows executable built successfully. Its packaged self-test passed both in the build output and after copying the executable alone into an isolated temporary directory. No fresh Mac package was built or tested in this review.

The dependency advisory check passed for 13 installed Windows Python distributions on Python 3.13.15. This is a check of the reported package versions against the queried advisory records, not a security certification of the application or operating system.

A read-only live connection probe found no saved sandbox token. No authenticated live SciSure verification was run, and no server's live upload/download behavior or permissions have been established by this audit.

## Remaining limits

- Native inventory publication, required inventory quantities, verified protocol bindings, and native Used/Generated links remain unfinished. The schema installer does not enable those workflows.
- Scientific cleaning is limited to explicit mappings, supported unit conversions, exact aliases, and validation. Toolkit reaction results are imported with evidence; chromatographic integration, fitting, or reaction calculations are not rerun.
- Closing or crashing during an unfinished transfer still requires manual reconciliation in SciSure. In-session unknown-write guards and read-back checks do not make separate API calls an atomic transaction.
- Reviewer names are self-reported. Checksums bind consistent content; they do not cryptographically authenticate a reviewer or protect against an authorized account replacing both content and hashes. SciSure permissions, signing, and audit controls remain authoritative.
- Existing size and visibility limits remain. The catalog covers accessible CATALYST review packages in the active group; native-only records, other groups, embedded notebook images, and separate eLABHybrid file hosts are outside the implemented retrieval scope.
- Development executables remain unsigned; Mac packages remain unnotarized. This Windows review does not establish fresh Mac build or live-server acceptance results.

See the [quick start](desktop-quickstart.md) for controls and the [integration review](scisure-integration-review.md) for datatype coverage, limits, and historical findings.
