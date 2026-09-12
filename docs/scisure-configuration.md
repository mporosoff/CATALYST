# Selected SciSure configuration

CATALYST 0.5 uses the SciSure sandbox at `https://sandbox.elabjournal.com` and the active group named **CATALYST**. The application discovers the group's actual IDs. The configuration is versioned as `catalyst-scisure-configuration/1`; its complete public definition is in [scisure-configuration.json](scisure-configuration.json). This document selects the configuration; it does not claim it has been applied or verified with a live tenant credential.

## Apply the material schema

Open **SciSure connection**, enter the API token there, and connect. Expand **Administrator tools · SciSure configuration**, then choose **Prepare CATALYST material configuration**. This reads the existing schema and shows the exact proposed additions. Review the plan, then use **Apply listed schema additions**. Never put the token in chat or GitHub. Optional storage uses Windows Credential Manager or macOS Keychain.

The installer creates only one dedicated sample type, **CATALYST Material v1**, and its 16 metadata fields. It reads each addition back and discovers its native ID. The token account's primary group and active group must both be CATALYST because the [create sample type endpoint](https://developer.elabnext.com/reference/sampletype_createsampletype) uses the caller's primary group. Existing unrelated types are untouched. A conflicting type, archived type, changed plan, or unexpected required field stops installation. Existing fields are never overwritten; [metadata creation](https://developer.elabnext.com/reference/sampletype_createsampletypemeta) is additive. Uncertain writes require reconciliation before retrying.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| catalyst_sample_id | TEXT | Yes | Permanent physical sample ID; also the native sample's external `altID` |
| catalyst_batch_id | TEXT | Yes | Synthesis batch ID shared by its descendants |
| origin_lab | COMBO | Yes | Lab that synthesized the original batch |
| sample_created_lab | COMBO | Yes | Lab that created this material or aliquot |
| origin_batch_label | TEXT | Yes | Originating lab's own batch label |
| material_kind | COMBO | Yes | Batch material, aliquot, or treated material |
| material_state | TEXTAREA | Yes | Physical state and treatment record |
| parent_catalyst_sample_id | TEXT | No | Parent material ID; required by CATALYST for derivatives |
| shared_procedure_id | TEXT | Yes | Common synthesis procedure identity |
| shared_procedure_version | TEXT | Yes | Exact common procedure version |
| shared_procedure_reference | TEXTAREA | Yes | Source procedure document or protocol reference |
| synthesis_execution_id | TEXT | Yes | Individual synthesis execution ID |
| synthesis_date_iso | TEXT | Yes | Calendar date, YYYY-MM-DD |
| synthesis_record | TEXTAREA | Yes | Actual synthesis record / ELN reference |
| synthesis_deviations | TEXTAREA | Yes | Recorded deviations, or explicitly none |
| catalyst_schema_version | TEXT | Yes | Value `1` |

The lab choices are A*STAR, SLAC, VA Tech (computational), Oxeon (pilot scale), Northwestern, and Rochester. Local naming conventions remain aliases; identical nicknames and shared procedures never merge independently prepared samples. Dates remain explicit calendar text because the [API date/time convention](https://developer.elabnext.com/docs/datetime-usage) uses UTC timestamps; CATALYST does not invent a time or timezone for date-only records.

Stock quantity is optional. When a future inventory write supplies mass it must use explicit quantity settings; reactor loading mass must never become an assumed stock quantity. [Sample creation](https://developer.elabnext.com/reference/sample_createsample) requires explicit required metadata values and their native `sampleTypeMetaID` bindings; declaring a required field alone does not populate it.

## Notebook and datatype routing

Use a **CATALYST** project with one study per executing or acquiring lab. Create an experiment for each synthesis execution, measurement acquisition, pilot run, or computational calculation. Preserve existing institutional approval rules and explicitly enable collaboration on newly created experiments where appropriate; [experiment creation](https://developer.elabnext.com/reference/experiment_createexperiment) otherwise defaults `autoCollaborate` to false. Group, subgroup, and collaborator permissions need verification using the intended team accounts.

Physical synthesis inputs belong in **Used samples** and physical products in **Generated samples**. Measurement experiments refer to the measured material in Used samples; a dataset is not a new physical sample. A declared treated material or aliquot gets its own ID and explicit parent. Native [sample-section linking](https://developer.elabnext.com/reference/experimentsection_addsectionsamples) uses native numeric IDs, distinct from CATALYST external IDs. Computations retain model and calculation identities; a comparison to a physical sample does not imply consuming or generating that material.

Reactor, synthesis, spectroscopy, XRD, XAFS/XANES, TPR/TPD/TPO, CO uptake, computational, and imaging submissions all retain originals in **FILE** sections with separate mapping, normalization, scientific-processing, validation, approval, and publication provenance in the review package. Original images remain full-byte file attachments; embedded display images must not replace the scientific original. Technique-specific parsing and fitting are separate capabilities: an accepted native file is not a claim that CATALYST interprets its contents.

Shared procedures should resolve to the exact published SciSure **protocol version ID**, with actual reviewed method content. API documentation does not establish the laboratories' scientific SOPs; CATALYST must not invent or silently select those contents.

## Current implementation boundary

The installer applies **only the material type and metadata fields**. It does not create research samples, projects, studies, experiments, protocols, Used/Generated links, or change permissions. The native sample payload builder is tested but is not connected to the publication workflow. Current publication stores approved review packages and original files in an existing verified experiment, with upload/read-back checksums and a completion receipt. Full native inventory and protocol binding still need publisher implementation and live validation. Successful schema reads do not establish write permission.

Runtime research transfers go directly to SciSure. GitHub distributes program files only; no research files or API token are included in the application, update link, or release assets.
