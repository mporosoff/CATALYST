# CATALYST / SciSure integration review

Updated 23 September 2026. Scope: the adaptive desktop upload forms, reusable definitions, batch review, connection guidance, minimal native sample configuration, explicit inventory publication, saved-record access and export. The legacy Sites application is historical source, not the desktop runtime.

The current implementation keeps required fields consistent across the form, review contract and native sample schema. Characterization links an existing sample and method, then records this run's date, operator and files. Sample/procedure registration and synthesis execution are separate. Native inventory changes are an explicit additional choice with a reviewed plan. These are locally implemented capabilities exercised with synthetic API fixtures; no authenticated live tenant verification is claimed.

## Connection guidance from the supplied API guide

The eLabNext REST API Quick Start Guide is useful for authentication and API usage, not for scientific file parsing or a universal list of sample fields. Its relevant guidance is integrated into the Help pane, tooltips and [desktop quickstart](desktop-quickstart.md):

- Create a server-specific token in **Apps & Connections → Manage Authentication** after signing in. University SSO/SAML and two-factor users should use that in-product route.
- Paste the raw token into CATALYST. The client supplies the `Authorization` header; no username/password exchange or `Bearer` prefix is required in the form.
- Match the token to the server that issued it. Sandbox credentials are not credentials for another installation.
- Account/group permissions govern API access. A successful connection or read-only inspection does not prove permission for schema, sample, section or file writes.
- The API maximum is 1,000 results per page, not a database-wide limit. CATALYST reads pages automatically and separately bounds one loaded list to 1,000 total records and the package catalog to 250 reviews.

The [current API overview](https://developerdocs.elabnext.com/docs/overview) and the detailed OpenAPI definitions supply the underlying connection and endpoint contracts. Tenant-specific sample types, field IDs and permissions are inspected from the selected server rather than inferred from the PDF.

## Current record model

| Record or feature | Implemented behavior | Boundary |
| --- | --- | --- |
| Sample information | Label and description/composition; generated canonical ID and lab attribution; optional state/parent/notes. Later data links this definition. | A readable label is not globally unique. Native selection verifies identity instead of merging names. |
| Reusable method | Name, version, type, technique and instructions/document/reference; searchable saved and queued definitions. | Scientific completeness remains a reviewer responsibility. No method is inferred from a filename. |
| Native procedure selection | Read a published, nondeleted protocol version visible in the group and stage an exact CATALYST reference for review. | Does not create, edit or publish a native protocol. A protocol reference alone does not prove execution. |
| Synthesis execution | Select the product sample and saved synthesis method; record date/operator and optional deviations/files. | Multiple precursor inventories, mixtures and full custody tracking are outside this path. |
| Measurements | Select sample, matching method (or explicit historical method-not-recorded), date/operator and files; preserve originals by default. Mapping adds the required axis/quantity/unit checks. | No repeated synthesis history, no automatic vendor interpretation or scientific fitting. |
| Computational results | Saved computational method, model description/input structure, date/operator, results and optional declared physical links. | Models remain distinct from inventory samples; the app does not run calculations. |
| Add-new and batch review | Preserve the unfinished draft while staging missing definitions; stage grouped runs or one record per batch file; approve every exact record. Definitions precede dependents. | Memory-only queue: 50 records and 200 MiB including files, parsed data and reviews. Closing discards unsent state. |
| Native inventory | Connected new samples and selected native samples default to reviewed creation/reuse of a physical sample and add applicable Used/Generated links. A connected, verified destination is required to prepare its exact reviewed plan. | Unchecked/absent inventory choice preserves experiment-package behavior. External changes or permissions can block writes. |
| Saved access and download | Read CATALYST reviews and ordinary attachments; explicitly export completed reviews as ZIPs with verified originals and standardized JSON/CSV when applicable. | No automatic local research cache; embedded notebook images and separate eLABHybrid-host access remain outside browsing. |

## Sample workspace and standalone documents

Samples & data merges native inventory and completed CATALYST sample definitions by exact identity. The detail view aggregates only explicit sample subjects/relationships across experiments. Native Used/Generated links are verified from the sample's experiment sections; generic experiment attachments are labeled at experiment scope and never inferred to belong to each linked sample. Saved records remains available for experiment-level retrieval. Coverage is limited to the connected account's active group and bounded API/catalog results.

Supporting documents use sample-local native FILE metadata without an experiment. The review fixes the sample, exact original bytes and a unique document-group key. Every uploaded file is downloaded and checksum-verified before a POST creates that group with only the new file IDs. Existing attachment lists are never replaced and the shared sample-type schema is not changed. This uses the documented ability to create an undefined sample field; these ad-hoc fields have limited native field-specific search support. CATALYST reads all FILE fields to list documents. No automatic write retry or rollback is claimed. Dedicated supporting-document metadata does not redefine scientific sample identity. File reads verify current membership and metadata; external hybrid storage is not followed.

New connected review destinations are immutable with their file/sample/method context. A batch can dispatch records to distinct reviewed runs; changing the active upload form does not retarget staged records. Experiment creation inherits configured collaborators and is verified before selection. The UI creates blank pending runs; it does not copy experiment templates.

## Configuration and native publication

**CATALYST Material v2** has six typed metadata fields: canonical ID, creating lab, description/composition, schema version, optional state and optional parent. The first four are required native values; the app supplies all except the description. The sample's readable name is entered in the sample form. Synthesis dates, operators, method details and acquisition settings remain in linked work records and are not required native sample metadata.

The installer is additive: it discovers actual IDs, shows the proposed changes, rejects incompatible existing definitions and adds missing schema records only after approval. Material v1 types, fields and existing samples remain untouched. Existing native samples can be selected and verified for reuse without rewriting them or converting their type. New native samples require v2 setup. See [configuration and migration](scisure-configuration.md).

Selecting **Also register / link this sample in LIMS inventory** includes native operations in the immutable review. **Search LIMS** and **Choose existing LIMS inventory sample…** offer native sample search and reviewed reuse. The review identifies creation or reuse, metadata, destination and relevant sample relationships. Publication verifies the approved plan and reads back its results. It does not silently add native writes to older saved reviews or publish an unreviewed sample as part of a library refresh.

Native procedure references remain exact published-version references in the reviewed method record. Attaching a PDF procedure does not create a native protocol. The sample/procedure/work graph and source evidence remain accessible through CATALYST packages even when inventory is also used.

## Transport and integrity contracts

| Area | CATALYST behavior |
| --- | --- |
| Server/token | Explicit HTTPS DNS origin on port 443; sandbox default; token bound to that origin; TLS verification; no redirects or environment proxy. Optional native OS credential storage is separate by server. Tokens and remote error bodies are omitted from displayed errors and review packets. |
| Paging and scope | Endpoint-specific IDs, bounded complete page reads and group rechecks. Incomplete/inconsistent results fail rather than silently appearing complete. Read permission remains distinct from write permission. |
| Experiment writes | Select an existing run or explicitly review/create project, study and experiment in the app; require unsigned status. New connected reviews bind their own run. Recheck before writes; signed experiments remain readable. |
| Source/review integrity | Preserve originals and exact numeric evidence, explicit mappings, immutable revision digests and approved source pins. Changed definitions require relinking and renewed review. |
| Transfer completion | Dedicated revision section, review manifest, unchanged originals and completion receipt. Verify downloaded bytes and planned native results before reporting completion. |
| Interruption | GET rate limits have bounded retries; writes are not blindly retried. Unknown outcomes retain in-session guards and require reconciliation. Batch sending stops at the first failure. |
| Exports | Only save to a chosen destination. Review ZIPs require a complete verified packet. CSV is a convenience view with formula-like text escaped; JSON/originals retain evidence. |

Detailed endpoint references: [pagination](https://developer.elabnext.com/docs/pagination), [experiment metadata](https://developer.elabnext.com/reference/experiment_getexperimentbyid), [section creation](https://developer.elabnext.com/reference/experimentsection_createsection), [binary uploads](https://developer.elabnext.com/docs/api-file-upload), [sample creation](https://developer.elabnext.com/reference/sample_createsample), [sample metadata fields](https://developer.elabnext.com/reference/sampletype_getsampletypemetas), [Used/Generated links](https://developer.elabnext.com/reference/experimentsection_addsectionsamples), and [protocol versions](https://developer.elabnext.com/reference/protocols_getprotocolbyprotocolversionid). These establish API contracts, not the objects or permissions available in a particular tenant.

## Scientific and operational boundaries

CATALYST imports reviewed values; it does not execute the Rochester GC processor, perform XRD phase identification, fit XAFS data, integrate peaks, infer image scale or run computations. CSV, XLSX and flat-record JSON are explicit table formats. Other originals remain uninterpreted bytes. Partner formats and new techniques need representative data and validated mappings before claiming standardization.

Each record is bounded to six originals, 20 MiB per original/review and 40 MiB of combined original files, with 200,000 combined source cells. Native protocol reads are bounded to 250 published versions. The catalog covers visible CATALYST records in the active group; cross-group and inaccessible definitions cannot be validated. A limit is an application constraint, not an assertion that SciSure cannot hold more records.

Section, inventory and relationship writes are separate API calls, not an atomic transaction. After closing/crashing the app, owners may need to reconcile incomplete records manually. An external edit, concurrent submission or permission change can invalidate a previously read plan. Creating another revision does not resolve an unfinished transfer.

Shared tokens share account permissions and API identity. CATALYST reviewer names and laboratory declarations are self-reported; hashes provide integrity consistency, not independently authenticated signatures. Native SciSure signing and audit controls remain authoritative. The desktop creates no automatic research-data database/cache/log, although normal operating-system memory and temporary-library behavior applies.

Before use with research data, an owner should test a synthetic sample registration/reuse, method reference, linked run, upload and download in the intended tenant, including permission rejection and an interrupted transfer. No live schema installation, inventory creation, account-permission verification or synthetic tenant upload is claimed by this source update. Windows/Mac development packages remain unsigned/not notarized unless a particular release states otherwise.

## Historical verification

The **0.8.0 audit, 19 September 2026**, passed 225 Python unit/integration tests on Python 3.13.15, the Windows native GUI check and packaged standalone self-tests. It did not include the later adaptive forms or current native inventory path, and it performed no authenticated live SciSure verification. Its detailed source, transport, export and packaging repairs are retained in [desktop-audit-0.8.md](desktop-audit-0.8.md).

The **0.9.0 adaptive update** reported 281 automated tests plus native interface and standalone launch checks. Transfers used synthetic endpoints. Those historical totals are not a claim about a newly built release; run the current suites listed in [desktop-development.md](desktop-development.md) for the modified source. Current synthetic coverage includes the minimal schema, exact reference pins, native review/write boundaries, stop-on-failure batch behavior, approval invalidation and legacy compatibility. Live tenant acceptance remains outstanding.
