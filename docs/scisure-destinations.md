# Proposed SciSure destinations for reactor data

**Current desktop implementation:** the connector publishes original files and an approved JSON/provenance package to dedicated sections of a verified existing experiment. Version 0.3 records lab-specific batches, physical sample lineage, shared procedure references, dataset identities, and computational model relationships in those packages; see the [consortium workflow](consortium-workflow.md). Its shared catalog reads packages across the active group. The desktop app selects existing experiments. Physical sample creation, protocol-reference verification, and native Used/Generated links remain extensions requiring tenant schema validation.

The table below is a proposed scientific mapping based on the first Rochester packed-bed reactor examples. It is not a discovered tenant schema or authorization to create records. Sample types, projects, studies, section templates, permissions, and IDs remain to be inspected after secure authentication.

| Scientific concept | Proposed SciSure object | CATALYST responsibility |
| --- | --- | --- |
| Physical catalyst specimen / aliquot | Existing sample, or a sample created only after identity and required metadata are confirmed | Stable canonical identity, original aliases, batch/specimen relationship, verified sample ID |
| Packed-bed reactor run | Experiment within the designated study/project | Run ID, experimental context, acquisition attribution, exact source artifacts |
| Catalyst loaded into the reactor | Experiment's Used Samples relationship where supported | Link the existing physical specimen; do not create a new sample per GC injection |
| Submitted GC report | File attachment in the experiment's raw-data section | Immutable original bytes, checksum, source entity, source layout/version |
| Partner-reprocessed workbook | Separately labeled file attachment | Parent artifact link, contributor-reported reprocessing, unresolved issues, processing status |
| Approved canonical dataset revision | Standardized JSON/CSV attachment plus a concise experiment summary | Mapping/schema versions, original-to-canonical provenance, validation and approval record |
| Reproducible recalculation | New result revision / linked result section | Method version, parameters, calibration inputs, row inclusion, output checksum and QC |
| Publication | Persisted CATALYST operation ledger referencing SciSure objects | Record experiment, section, sample, and file IDs separately; reconcile partial completion |

Do not flatten every injection into sample metadata or make each reprocessing execution a new physical sample. Use structured sample fields only for suitable stable specimen metadata after inspecting the tenant's type/field registry. Keep acquisition, data-processing, and material lineage separate.

For the first end-to-end test, one verified catalyst sample, one test-study experiment, and distinct raw/partner-result/approved-output file sections should be sufficient if the tenant supports that structure. Keep submitted-but-unverified partner results clearly labeled. Do not overwrite signed/finalized records or imply that a submitted workbook is scientifically approved.

The public API supports experiment discovery by study/project and binary file upload to experiment sections. These pages establish capabilities, not available objects in the user's group: [experiments](https://developer.elabnext.com/reference/experiment_getexperiments), [file upload](https://developer.elabnext.com/docs/api-file-upload).
