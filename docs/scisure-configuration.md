# Selected SciSure configuration

CATALYST connects to the selected SciSure/eLabNext tenant and the active group named **CATALYST**. A sandbox and a production tenant have separate records, IDs and API tokens. The application discovers the group's actual IDs. The configuration is versioned as `catalyst-scisure-configuration/2`; its complete public definition is in [scisure-configuration.json](scisure-configuration.json). This document describes the implementation and intended configuration. Applying it to a live tenant is a separate, reviewed action.

## Apply the material schema

Open **SciSure connection**, enter the API token there, and connect. Expand **Administrator tools · SciSure configuration**, then choose **Prepare CATALYST material configuration**. This reads the existing schema and shows the exact proposed additions. Review the plan, then use **Apply listed schema additions**. Never put the token in chat or GitHub. Optional storage uses Windows Credential Manager or macOS Keychain.

The installer creates the dedicated sample type **CATALYST Material v2** and six metadata fields. Four fields are required by the native schema, and the app supplies the canonical ID, creating lab and schema version. The researcher supplies a sample label and description/composition once in the registration form. State/treatment and parent are optional. A measurement uses the registered sample and saved procedure, without asking for synthesis history again.

The installer reads each addition back and discovers its native ID. The token account's primary group and active group must both be CATALYST because sample type creation uses the caller's primary group. Existing unrelated types are untouched. A conflicting type, archived type, changed plan, or unexpected required field stops installation. Existing fields are never overwritten; metadata creation is additive. Uncertain writes require reconciliation before retrying.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| catalyst_sample_id | TEXT | Yes | Permanent physical sample ID; also the native sample's external `altID` |
| sample_created_lab | COMBO | Yes | Lab that created this material or aliquot |
| sample_description | TEXTAREA | Yes | Researcher-supplied description or composition |
| material_state | TEXTAREA | No | Physical state or treatment, when relevant |
| parent_catalyst_sample_id | TEXT | No | Declared canonical parent; resolves to a verified native parent sample |
| catalyst_schema_version | TEXT | Yes | App-supplied value `2` |

The lab choices are A*STAR, SLAC, VA Tech (computational), Oxeon (pilot scale), Northwestern, and Rochester. Local naming conventions remain aliases; the sample's matching creator-lab label is its native display name, and the canonical ID remains its external `altID`. Identical nicknames and shared procedures never merge independently prepared samples. Synthesis and acquisition dates are recorded on the corresponding execution records, with their original calendar precision.

Stock quantity is optional. Registration does not assign an assumed stock mass from reactor loading. Native sample creation supplies each required metadata value with its discovered `sampleTypeMetaID` binding; declaring a required field alone does not populate it.

## Existing version 1 records

The preparation plan reports existing **CATALYST Material v1** types and whether their identities can be verified for reuse. Version 2 is installed alongside version 1. The installer does not relax old requirements, rewrite old samples, move records, remove fields, or create replacement copies. Existing experiment packages and their immutable source links remain readable.

Use verified existing inventory records when a sample is already registered. A new child sample may reference an active, verified version 1 parent by its original canonical ID. Archived, foreign-group or unmarked same-name types require manual review and cannot silently supply a native parent. Preparing the schema again reports changes before anything can be applied.

A legacy CATALYST record may contain synthesis history without a sample description. That history is retained in its original package. A new native registration requires an explicit description/composition; the app must not invent it from an SOP, state label or synthesis date. Any transfer of old records into a different native type requires a separately reviewed migration plan and is not part of configuration setup.

## Notebook and datatype routing

Use a **CATALYST** project with one study per executing or acquiring lab. Approved sample registrations, procedure definitions, synthesis executions and measurement/calculation records retain their own versioned review packages. The app publishes to the explicitly selected existing experiment. This installer does not create projects, studies or experiments. Preserve existing institutional approval and collaboration settings. Group, subgroup and collaborator permissions need verification using the intended team accounts.

Physical synthesis inputs belong in **Used samples** and physical products in **Generated samples**. Measurement experiments refer to the measured material in Used samples; a dataset is not a new physical sample. A declared treated material or aliquot gets its own ID and explicit parent. Native [sample-section linking](https://developer.elabnext.com/reference/experimentsection_addsectionsamples) uses native numeric IDs, distinct from CATALYST external IDs. Computations retain model and calculation identities; a comparison to a physical sample does not imply consuming or generating that material.

Reactor, synthesis, spectroscopy, XRD, XAFS/XANES, TPR/TPD/TPO, CO uptake, computational, and imaging submissions all retain originals in **FILE** sections with separate mapping, normalization, scientific-processing, validation, approval, and publication provenance in the review package. Original images remain full-byte file attachments; embedded display images must not replace the scientific original. Technique-specific parsing and fitting are separate capabilities: an accepted native file is not a claim that CATALYST interprets its contents.

Shared procedures can be selected from saved CATALYST definitions or from published SciSure protocols. A selected native protocol is registered as a reviewed reference to its exact **protocol version ID**. Later measurements and executions reuse that definition. A new procedure can instead contain instructions, an attached document or an explicit document reference. API documentation does not establish the laboratories' scientific SOPs; CATALYST must not invent or silently select those contents.

## Current implementation boundary

The installer applies **only the material type and metadata fields**. Research record submission is a separate reviewed workflow. Approved review packages and original files are stored in an existing verified experiment, with upload/read-back checksums and a completion receipt. Native inventory selection, registration and experiment links are reviewed independently from schema setup; the interface displays the proposed action before sending it. Successful schema reads do not establish write permission, and synthetic tests do not replace live validation with the intended team accounts.

Runtime research transfers go directly to SciSure. GitHub distributes program files only; no research files or API token are included in the application, update link, or release assets.
