# Implementation requirements

This is a design contract, not a description of implemented functionality. Tenant capabilities, data formats, mappings, processing recipes, and object destinations remain subject to inspection.

## Components and authority

- Static accessible frontend: selection, upload, mapping review, validation issues, standardized preview, revision approval, and publication status.
- Authenticated backend: authorization, parsing, deterministic normalization, validation, and controlled publication. Do not expose an unrestricted SciSure passthrough.
- Private artifact store: immutable original bytes, content checksums, standardized exports, and processing outputs.
- Metadata database: entity-scoped identities, artifacts, dataset revisions, provenance events, validation reports, approvals, and publication operations.
- Job runner: bounded processing/publication work with durable status. Recheck authorization before delayed publication.
- SciSure adapter: tenant-specific object/field registry and typed request/response handling.

SciSure remains authoritative for physical inventory and native notebook records. CATALYST owns canonical cross-entity identifiers, dataset revisions, transformation history, and standardization review. Integration-owned fields require explicit ownership rules.

## Revision and provenance model

Preserve material definition, synthesis batch, physical specimen, acquisition/run, dataset revision, processing execution, review decision, and publication attempt as distinct concepts where applicable.

For each source artifact, retain the original filename, byte count, checksum, submitter, submitting entity, acquisition/processing attribution when known, and receipt timestamp. Mapping provenance must identify source locations such as sheet, row, column, or JSON path, original values/units, applied rules, and output values/units.

Maintain separate records for:

1. Parsing: extraction and format/parser version.
2. Normalization: deterministic unit/name rules and mapping version.
3. Scientific processing: inputs, method/software version, parameters, calibrations, outputs, and QC; explicitly absent when no processing occurred.
4. Validation: rule-set version, required context, errors, warnings, and resolutions.
5. Review: authenticated reviewer, exact revision/content digest, decision, and timestamp.
6. Publication: approved revision, tenant/destination, operation ledger, returned IDs, outcome, and reconciliation history.

Acquiring, processing, submitting, and reviewing entities must not be inferred from one another. Unknown attribution stays unknown and is validated according to the modality's requirements.

## Mapping and scientific rules

Mapping profiles are immutable versions keyed by entity + modality + source format/version. Profiles specify canonical schema versions, expected source fields, approved aliases, deterministic conversions, required context, and transformation rule versions. Unknown formats and fields require explicit review; the application must not silently select a near-match profile.

Source formats may be CSV, XLSX, or JSON. Define file-size, expanded workbook size, worksheet, row, column, nesting, and processing-time limits. Detect malformed files and ambiguous numeric/date conventions. Never execute workbook macros or formulas; establish an explicit policy for formula cells and cached values.

Context to evaluate against real examples includes:

- Reactor data: sample/run identity; measured versus set-point temperature; absolute versus gauge pressure; flow reference conditions; composition basis; species-specific conversion/selectivity definitions; catalyst versus metal mass basis; calibration and uncertainty meaning.
- Catalyst synthesis: material/batch identity; precursor identities and amounts; compositions and their basis; chronological treatment conditions; generated specimen links.
- Spectroscopy: technique; specimen/acquisition identity; independent-axis quantity/unit; signal quantity/unit; acquisition settings; calibration references; processing method and result provenance where supplied.

Do not guess that “activity” means a particular rate or that two catalyst names refer to the same physical material. Explicitly unresolved scientific ambiguity must remain visible. A warning that changes scientific interpretation must be resolved before an affected record can be published; low-impact warnings may use a recorded acknowledgment policy.

Approvals bind immutable content and rule versions. Any source, context, mapping, transformation, or output change produces a new revision requiring review.

## Publication behavior

Inspect the tenant before selecting object destinations. Review sample types and stable metadata IDs, project/study/experiment structure, sections, permissions, and signed-record restrictions. Do not recreate field definitions during upload processing.

Keep approval and publication status separate. A publication attempt tracks each operation and distinguishes pending, executing, succeeded, failed, and outcome-unknown states. Only report complete publication after every required operation has been verified.

Protect against concurrent duplicate publication using a persistent unique operation key incorporating tenant, destination, approved revision, and operation identity. A timeout after a creation request is an uncertain outcome: reconcile before retrying. Do not assume SciSure supports a multi-call transaction or idempotency header.

Respect rate-limit responses and server retry instructions. Handle endpoint-specific pagination. Keep experiment IDs, section IDs, and file IDs distinct. Preserve binary file bytes during upload/download and verify checksums when possible.

Current public guides: [rate limiting and authentication](https://developer.elabnext.com/docs/overview), [pagination](https://developer.elabnext.com/docs/pagination), [binary file upload](https://developer.elabnext.com/docs/api-file-upload), [file download](https://developer.elabnext.com/docs/api-file-download), and [deployment-dependent OAuth](https://developer.elabnext.com/docs/oauth2-developer-guide). Treat public documentation as a starting point for tenant verification.

## Acceptance checks for implementation

- Representative contributions from 2–3 entities traverse upload → review → approved revision → sandbox publication, with an extensible path for all six entities.
- Original artifact bytes and checksum survive upload, storage, and retrieval unchanged.
- CSV/XLSX/JSON handling rejects malformed or oversized inputs and retains precise source locations.
- Approved name/unit conversions have exact, independently checked expected results; unsupported or ambiguous meanings generate explicit issues.
- Required context is enforced by CATALYST even if SciSure accepts an incomplete record.
- Reprocessing creates a new execution and revision without overwriting previous results or approvals.
- Users cannot read, approve, or publish another entity's data without authorization; client claims cannot expand access.
- Backend secrets are absent from frontend bundles, responses, logs, repository history, and artifacts.
- Unapproved or stale revisions cannot publish; concurrent requests and partial failure cannot silently duplicate records.
- Typed metadata, destination permissions, binary attachment integrity, and returned SciSure IDs are verified against the selected test environment.
- Keyboard navigation, visible focus, labeled fields, accessible status/error announcements, and usable desktop/mobile layouts are checked.
- Deployment configuration, automated checks, and rollback/operating instructions match the implemented stack.
