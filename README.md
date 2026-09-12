# CATALYST

An upload, review, and standardization application for catalysis data contributed and processed by six entities, with secure publication to SciSure/eLabNext.

## Project status

The offline ingestion component preserves source bytes, reads CSV/XLSX/flat-record JSON, and produces source-located review previews. It supports two draft Rochester XLSX profiles and a complete Rochester RWGS result bundle from the Porosoff Group catalysis toolkit. Bundle import retains numerical CSV results, verifies embedded raw values and row links, and keeps processing revisions separate. No SciSure credentials or research datasets are stored here, and no SciSure records have been created.

The upload/review web application, authenticated backend, approval workflow, scientific processing recipes, and SciSure publication adapter are not implemented or deployed yet. This component is a processing-service building block, not a production web service.

Confirmed MVP modalities:

- Reactor data
- Catalyst synthesis
- Spectroscopy data (specific techniques and export formats to be identified from examples)

Initial data: University of Rochester packed-bed reactor GC export plus a reprocessed workbook, confirmed by the user to be the same run with superseded row labels. Additional entities and synthesis/spectroscopy examples remain outstanding. The initial SciSure environment is `https://sandbox.elabjournal.com`; an unauthenticated request returned HTTP 401. Group access and destination IDs are not yet verified.

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
- [Run the offline importer](docs/ingestion.md)
- [Catalysis toolkit GC integration](docs/toolkit-gc.md)
- [Proposed SciSure object mapping](docs/scisure-destinations.md)

## Secrets

Store `SCISURE_API_TOKEN` using the backend deployment platform's secret mechanism. Do not paste it into chat, commit it, place it in frontend settings, or expose it through browser requests or logs. `.env.example` documents the development sandbox with empty token/group fields; it is not a working connection.
