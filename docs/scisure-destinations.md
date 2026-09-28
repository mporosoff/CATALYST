# SciSure destinations and record relationships

CATALYST publishes each approved record and its original files to a dedicated section of a verified existing experiment. It does not create projects, studies or experiments. Reusable sample/procedure registrations and new work are separate record types; measurements select previously registered definitions instead of repeating them. See the [consortium workflow](consortium-workflow.md).

Native inventory is an explicit additional choice, **Also register / link this sample in LIMS inventory**, for eligible physical-sample records. The review displays the planned native sample creation/reuse and Used/Generated relationship before approval. The plan uses actual visible IDs and required-field bindings from the connected server. A readable destination or successful setup inspection does not establish write permission.

| Scientific concept | SciSure destination | CATALYST responsibility |
| --- | --- | --- |
| Reusable sample definition | CATALYST sample registration package; optionally an existing or new native Sample | Stable canonical identity, readable label, description/composition and verified native reference when requested. Reuse existing inventory rather than creating a sample per measurement. |
| Shared synthesis or measurement method | CATALYST procedure registration package | Name, type, technique, exact version and instructions/document/reference. Published native protocols can be selected by exact version; CATALYST does not create or publish native protocols. |
| Actual synthesis execution | CATALYST execution package in the selected experiment | Link the product sample and saved procedure version; record date, operator and run-specific changes. An opted-in native plan can add a Generated sample relationship. |
| Characterization or reactor run | CATALYST measurement package in the selected experiment | Link the existing sample and saved method; record date, operator and original files. An opted-in native plan can add a Used sample relationship. |
| Computational result | CATALYST model/result package in the selected experiment | Keep computational models separate from physical inventory, retain input/method references, and record optional physical-sample relationships explicitly. |
| Submitted scientific file | Original attachment in a revision-specific experiment section | Preserve exact bytes, filename, checksum, source lab and format evidence. A toolkit import preserves producer results; it does not rerun the scientific processor. |
| Standardized dataset revision | Reviewed JSON/provenance package and transfer receipt | Preserve mapping versions, units, identity links, validation, approval and exact originals; downloaded packages can also include a convenience CSV. |
| Reprocessing | New reviewed revision linked to the earlier revision | Retain the acquisition's dataset identity, original evidence and explicit mapping/version changes. A new acquisition needs a new dataset ID. |
| Completed transfer | CATALYST completion receipt with verified SciSure references | Read back the planned writes and originals, record the result, and stop for reconciliation when the outcome is uncertain. |

The minimal **CATALYST Material v2** type contains six stable metadata fields. Canonical ID, creating lab, description/composition and schema version are required; CATALYST supplies the ID, lab and version. State and parent are optional. Synthesis records, dates, operators, procedure settings and acquisition details belong in their own linked work records. The additive installer leaves Material v1 types, fields and samples intact. Existing native samples can be selected and verified without conversion to v2. See [configuration and migration](scisure-configuration.md).

Do not flatten each GC injection into sample metadata, create a new physical sample for reprocessing, or treat a common procedure as proof that samples are identical. Native protocol references do not prove that a procedure was actually executed; the synthesis/measurement record makes that declaration.

Before using a tenant for research data, an owner should perform an approved synthetic registration, linked run and download using the intended account and group. Check the exact native sample and experiment relationships, uploaded files and downloaded hashes. API definitions and synthetic fixtures establish expected behavior, not tenant configuration or live write permissions. No authenticated live tenant verification is claimed here.

Writes to experiment sections, samples and relationships are separate calls, not a server transaction. A failed read-back or uncertain response can require reconciliation in SciSure; do not duplicate an uncertain item. Do not overwrite signed/finalized records. Source processing status and a CATALYST review are not independently authenticated scientific approval.

The documented API supports [experiments](https://developer.elabnext.com/reference/experiment_getexperiments), [binary uploads](https://developer.elabnext.com/docs/api-file-upload), [sample creation](https://developer.elabnext.com/reference/sample_createsample) and [Used/Generated links](https://developer.elabnext.com/reference/experimentsection_addsectionsamples). Available objects and operations remain subject to the selected server and account.
