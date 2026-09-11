# CATALYST

An upload, review, and standardization application for catalysis data contributed and processed by six entities, with secure publication to SciSure/eLabNext.

## Project status

Repository setup is complete. Application implementation and deployment have not started. No SciSure credentials or research datasets are stored here, and no SciSure records have been created.

Confirmed MVP modalities:

- Reactor data
- Catalyst synthesis
- Spectroscopy data (specific techniques and export formats to be identified from examples)

The remaining inputs are representative files from 2–3 entities and confirmation of an accessible SciSure environment/test group. SciSure destination objects will be proposed after inspecting the data and tenant.

## Intended workflow

1. Select the contributing entity, modality, and source format/version.
2. Upload CSV, XLSX, or JSON and preserve the original bytes and provenance.
3. Parse source content without changing its scientific meaning.
4. Apply a versioned mapping profile keyed by entity + modality + source format/version.
5. Normalize approved names and units deterministically.
6. Validate required scientific context and display errors and ambiguous meanings.
7. Preview the standardized revision alongside its original values and transformations.
8. Record explicit approval of an immutable revision.
9. Publish approved records/files through a secure backend to the selected SciSure objects.
10. Record returned SciSure IDs, operation outcomes, and publication status.

Normalization, scientific processing, validation, review, and publication will have separate provenance. Imported partner results must not be labeled as independently reproduced.

## Architecture

GitHub holds source code, schemas, versioned mappings, tests, and deployment configuration. A static frontend calls an authenticated backend; the backend handles scientific validation, durable artifact storage, revision records, and the SciSure adapter. SciSure credentials remain in the backend deployment's secret store.

Research data belongs in private application storage. App authorization must enforce entity and dataset access on every operation; possession of a backend SciSure credential does not give every app user access to all records visible to that credential.

- [Setup and remaining inputs](docs/setup.md)
- [Implementation requirements and acceptance checks](docs/implementation.md)

## Secrets

Store `SCISURE_API_TOKEN` using the backend deployment platform's secret mechanism. Do not paste it into chat, commit it, place it in frontend settings, or expose it through browser requests or logs. `.env.example` documents proposed configuration names with empty values; it is not a working configuration.
