# CATALYST / SciSure integration review

Reviewed 12 September 2026 against the desktop implementation, its tests, and official SciSure/eLabNext product guides and REST API schemas. Changes are in CATALYST 0.4.0. The previous hosted prototype is historical source, not the desktop runtime.

**Assessment:** the desktop has a testable direct-to-SciSure file/review connector and a useful consortium identity model. It is not yet a complete native inventory integration or a validated scientific processor for every datatype. No live authenticated SciSure requests or writes were performed in this audit. Successful synthetic tests do not establish permissions, tenant behavior, or scientific validity of an unseen partner format.

## Bugs and coverage problems corrected

| Priority | Finding and consequence | Correction / verification |
| --- | --- | --- |
| P1 | Every paginated API list relied on `hasNextPage`, which can be false or absent on non-sample endpoints. This could omit experiments/files and miss existing identities or writes. | Use documented totals/page size; reject changed counts, incomplete pages, duplicates, and over-limit catalogs. Tests include 205 records across 21 pages with false `hasNextPage`. |
| P1 | A rate-limited or permission-rejected write was treated as permanently ambiguous, preventing legitimate retry. Conversely, reconnecting discarded uncertainty guards. | Separate rejected requests from unknown outcomes; bounded read retries honor rate limits. Writes never retry automatically. Preserve guards across reconnects within the app session. A successful POST followed by failed verification remains unknown. |
| P2 | Only FILES sections were recognized, although current guides also use FILE. | New sections use FILE; reads reconcile both. Legacy and new section round trips are covered. |
| P2 | A saved manifest could be accepted under another CATALYST revision heading. Incoming publication did not recheck lineage against its context. | Require exact heading/revision identity and matching lineage/context before publication. Tests reject both inconsistencies. |
| P2 | Native instrument files, images and non-tabular scientific files could not be submitted. | Preserve arbitrary original bytes beside tables or in an explicitly files-and-context-only review. Added imaging context; PNG/JPEG/TIFF/SVG and computational-file round trips are tested without decoding/execution. |
| P2 | Spreadsheet error cells could enter standardized text fields. Very small valid numbers could lose precision with offsets or fail revalidation after normalization. | Block spreadsheet error cells; retain supported decimal precision through conversion and validate canonical values without applying the input string-length limit. |
| P2 | Same-name Office file revisions looked indistinguishable; some unsupported file reads produced generic failures. | Show file IDs, parent IDs and dates; identify eLABHybrid storage explicitly; rename the browser to reflect its attachment-only coverage. |
| P2 | Missing-original messages implied that the GUI could resume a transfer after restart. | Explain the actual same-session recovery route and the remaining manual restart-reconciliation requirement. |

The pagination behavior is specified in the official [pagination guide](https://developer.elabnext.com/docs/pagination). File section and Office revision distinctions are described in [API file download](https://developer.elabnext.com/docs/api-file-download). Rate-limit handling follows the [REST overview](https://developer.elabnext.com/docs/overview) and [error/status guide](https://developer.elabnext.com/docs/error-handling).

## Datatype readiness

All files in a submission are associated with its declared sample/model, dataset, labs, method and immutable review revision. Originals retain filename, bytes, embedded metadata and SHA-256. Supporting-file preservation is not scientific interpretation. One submission currently has one subject and acquisition context.

| Datatype | Supported now | Still needed for scientific processing |
| --- | --- | --- |
| Rochester reactor / GC toolkit | Four-file bundle checks, imported producer results, separately recorded nominal time-axis revision, explicit reactor context; originals preserved | Validated integration with the actual toolkit processor and its calibration/version evidence; additional labs' reactor export profiles |
| Other reactor / pilot data | CSV/XLSX/flat JSON mapping, explicit absolute pressure, temperature, mass, flow basis and scale | Pilot historian/time-series readers, actual timing conventions, mass/carbon balance and reviewed QC rules |
| Catalyst synthesis | Procedure/execution/batch/sample distinctions, mapped tables, deviations and actual-record context; native documents/images may stand alone | Native inventory creation, quantities/units, reagent roles, multi-parent material mixtures, native Used/Generated links |
| XRD | Explicit 2-theta or q table axis, intensity basis, radiation/geometry/calibration context; native files preserved | Vendor decoders, uncertainty, peak/phase analysis and reviewed fitting recipes |
| XAFS/XANES | Explicit energy, k or R table axis, absorber/edge, detection and reference context; native files preserved | Scan alignment/merging, detector-channel models, normalization, Fourier transforms/fits and parameter provenance |
| TPR / TPD / TPO | Time/temperature and signal mappings, gas/pretreatment/ramp/mass/calibration context | Baselines, peak integration, detector response/stoichiometric assumptions and uncertainty |
| CO uptake | Explicit mass-normalized uptake, reference conditions, pretreatment and site-ratio assumptions | Validated blank correction, uptake integration, dispersion/site calculations and propagated uncertainty |
| Other spectroscopy | Explicit wavelength/wavenumber/energy and signal table mappings, method/calibration context | Technique/vendor profiles, multidimensional scans and technique-specific analyses |
| Computational | Separate model/calculation identities and optional physical-sample relationships; scalar results tables; structures, nested JSON and logs preserved | Native code/output readers, trajectories, parameter/convergence validation and reproducible result extraction |
| Images / microscopy / photographs | PNG, JPEG, TIFF and other image originals, including image-only submissions; technique, image context and scale reference | Thumbnail viewing, dimensions/channels/calibration extraction, image stacks and optional deterministic analysis; no OCR/AI is used |
| Supporting evidence | PDFs, calibration records, protocols, logs and other native bytes linked to the review | Per-file roles/captions and explicit relationships between raw and processed artifacts beyond existing toolkit conventions |

Limits are currently **six files, 20 MiB per file, 40 MiB per submission**, 200,000 combined parsed cells, and bounded XLSX expansion. These are application limits, not SciSure capacity claims. Large beamline files, TIFF stacks and pilot histories will need streaming transfer and memory-bounded readers before routine use. The official [upload guide](https://developer.elabnext.com/docs/api-file-upload) requires raw binary transfer and gives a 150 MB cap, while the [section upload reference](https://developer.elabnext.com/reference/experimentsection_uploadsectionfile) describes 250 MB. Confirm the actual endpoint/tenant limit before increasing CATALYST's cap.

## Native SciSure integration contract

The new **Inspect native SciSure setup (read only)** button checks the real token account, active group, selected experiment/collaborators, sample types, field definitions and quantity requirements. Optional inputs inspect an existing sample and an exact protocol version. Results stay in memory. Unreadable resources remain explicitly unverified; the inspector never enables native writes or runs remote field-validation scripts.

| Requirement | Documented API / behavior | CATALYST status |
| --- | --- | --- |
| Account and workspace | GET `/users/getCurrentUserInfo`, `/groups/active`, `/experiments`, `/experiments/{id}/collaborators` | Read-only inspection; experiment selection and group/signature guards |
| Tenant-specific inventory schema | GET `/sampleTypes`, `/sampleTypes/{id}`, `/sampleTypes/{id}/meta` | Read-only inspection of actual IDs, typed fields/options, required fields, quantity/unit settings |
| Native sample identity | GET `/samples/{id}?$expand=meta,parents`; create through POST `/samples` | Optional read inspection; creation/binding not implemented |
| Shared procedure version | GET `/protocols/version/{protVersionID}` | Optional exact-version read; submission linkage still pending |
| Used/Generated samples | SAMPLESIN/SAMPLESOUT sections; PUT `/experiments/sections/{sectionID}/samples` with an array of sample IDs; 204 response | Not implemented; transport currently permits only the GET/POST operations used by this release |
| Research attachments | GET/POST `/experiments/sections/{expJournalID}/files`; binary GET of each file ID | Implemented with digest read-back and completion receipt |

Paths in the table are under `/api/v1`. Relevant primary references: [current account](https://developer.elabnext.com/reference/user_getcurrentuserid), [sample types](https://developer.elabnext.com/reference/sampletype_getsampletypes), [sample field definitions](https://developer.elabnext.com/reference/sampletype_getsampletypemetas), [sample details](https://developer.elabnext.com/reference/sample_getsamplebyid), [exact protocol version](https://developer.elabnext.com/reference/protocols_getprotocolbyprotocolversionid), and [Used/Generated section membership](https://developer.elabnext.com/reference/experimentsection_addsectionsamples).

**Native field binding must be explicit.** Sample creation does not automatically populate all required metadata. `autoCreateMetaDefaults` only helps fields with defaults/auto-numbering. Submitted metadata must use the real `sampleTypeMetaID` and matching `sampleDataType`; unbound values become free metadata instead of structured field values. Quantity-tracking types may require `quantitySettings`. This is why sending arbitrary canonical JSON as a sample is insufficient. See [Create a sample](https://developer.elabnext.com/reference/sample_createsample).

Proposed binding design, pending tenant inspection:

- Physical material/container → native Sample with its immutable CATALYST sample ID in `altID` plus explicitly bound batch, origin lab, creation lab, state and synthesis-execution fields. Preserve native sample ID separately from the consortium ID; do not repurpose a generated native barcode.
- Shared procedure → exact published native protocol version. A synthesis execution → its own experiment/record with actual conditions and deviations. Bind procedure variables explicitly. The latest protocol endpoint is not a reproducible substitute for a version ID. See [Procedure sections](https://support.elabnext.com/hc/en-us/articles/36677123228820-Procedure-Section).
- Reagents/parents → native Used samples; products/aliquots → Generated samples and appropriate native parent relationships. Add multi-parent model support before representing mixed batches; never flatten a mixture into a single false parent.
- Measurements → experiments with their physical sample references and versioned review/file attachments. Computational models/calculations remain distinguishable from physical inventory and link to samples only with a stated relationship.

These are proposed mappings, not names or IDs already found in the tenant.

## Permissions, data location and remaining release work

**P1 — establish native bindings and collaboration before consortium rollout.** Experiment listing is scoped to records visible to the account, not guaranteed to include every record in a group. Experiment creation defaults to `autoCollaborate=false`; a shared template/project alone is not proof of access. Check intended accounts across all six labs. CATALYST's catalog is limited to visible identity-bearing review packages and 250 reviews; it cannot prove global uniqueness or validate inaccessible/native-only records. See [list experiments](https://developer.elabnext.com/reference/experiment_getexperiments) and [create experiment](https://developer.elabnext.com/reference/experiment_createexperiment).

**P1 — validate the desktop connector in the real sandbox.** The existing test experiment can be used without creating an extra project. Check account/group, required sample fields, exact protocol version, signed/read-only behavior, cross-lab visibility, synthetic image/table/native-file upload and hash read-back, rejected permissions, and interrupted-transfer reconciliation. Successful reads do not prove writes. Native experiment signing remains authoritative; CATALYST's approval statement is not an independent electronic signature. See [Signing experiments](https://support.elabnext.com/hc/en-us/articles/36677169103508-Signing-Experiments) and [Group policies](https://support.elabnext.com/hc/en-us/articles/36677251533972-Group-Policies).

**P2 — durable recovery and larger datasets.** Same-session reconciliation is tested. Restart recovery remains manual because the app deliberately has no local research ledger and this implementation has no atomic server-side write registry. Add an explicit remote reconciliation workflow, streaming transfers, selective/indexed catalog access, per-artifact roles, and schema migration before scaling up. Do not weaken unknown-write guards to simulate resumability.

**P2 — complete retrieval coverage.** CATALYST reads FILE/FILES/CUSTOM attachments, including Office revisions. Native embedded images, legacy Excel, canvas, chemical drawings and protocol files require their distinct endpoints. eLABHybrid ONSITE files require another institutional host; this release explains the limitation and does not forward a token to that host. This does not prevent CATALYST from uploading original images as ordinary file attachments. See [file download and section types](https://developer.elabnext.com/docs/api-file-download).

**P2 — simplify team onboarding.** SciSure documents a desktop consent flow using a registered application URI and a short-lived, one-use exchange token. A future **Sign in with SciSure** button could obtain the user's API credential without manual copying or an AI account. It needs Windows/macOS URI registration, strict callback/tenant validation, replay protection and tenant testing. It is not implemented in this release. Manual tokens and optional OS-vault storage still work. See [desktop authentication flow](https://developer.elabnext.com/docs/api-key-auth-flow).

**Distribution and privacy:** package/sign/notarize the installers for low-friction institutional use; current ZIPs remain development builds. Only the verified sandbox host is enabled; production tenant configuration needs validation before release. Runtime research data travels directly between this app and SciSure. No extra research cache/database/log is created, and no GitHub, Cloudflare or AI service handles runtime submissions. Shared tokens retain shared-account permissions and self-reported person/lab attribution. The former hosted prototype's saved credential/deployment must be retired through its owner controls; this review does not claim it was revoked or decommissioned.

## Verification recorded for 0.4.0

- 102 synthetic unit/integration tests cover source preservation, numeric/context boundaries, six-lab lineage, API contracts, approval, publication/read-back, image/native round trips, corruption, permission/rate limits, and uncertain-write recovery.
- Native widget checks cover table mapping, image-only review/approval, lineage reuse, setup inspection, and retained uncertainty guards after reconnecting. These are synthetic widget checks, not a visual accessibility audit.
- Packaging executes a native app self-test on each supported runner. GitHub Actions is the authoritative Windows, Apple Silicon Mac and Intel Mac build result for the release commit.
- No live token was read, stored, transmitted or included in test fixtures. Representative non-Rochester files and vendor formats still need partner validation before their scientific pipelines can be considered supported.
