# CATALYST

Local desktop upload, review, and standardization workspace for catalysis data, with a direct SciSure/eLabNext sandbox connector. The desktop application is the current implementation target; the earlier hosted web prototype is retained as historical source.

## Desktop application — Windows and Mac

Run `python -m catalyst_desktop` with Python 3.12+ and Tk. Install `requirements-desktop.txt` for optional operating-system credential storage. Packaged native applications are built by the **Desktop application** GitHub workflow, with no token or research data included in build inputs.

The app supports offline CSV/XLSX/JSON table selection, original images and native supporting files, explicit versioned mappings, scientific-context validation, preview, immutable revision approval, direct verified SciSure uploads, saved-review history, and browsing experiment file attachments. Image-only submissions have a dedicated imaging modality and preserve-only review. It uses no hosted processing backend, AI service, Cloudflare service, or local research-data cache. Original files and approved records go directly to SciSure. Optional remembered tokens live in the operating system credential store.

- [Desktop quick start](docs/desktop-quickstart.md)
- [Six-lab sample lineage and measurement workflow](docs/consortium-workflow.md)
- [Components, boundaries, tests, and packaging](docs/desktop-development.md)
- [SciSure integration audit and datatype readiness](docs/scisure-integration-review.md)

Version 0.4 retains the six-lab identity catalog and adds images/native attachments, preserve-only submissions, a read-only native SciSure setup inspector, and API-contract/recovery fixes. XRD, XAFS/XANES, TPR/TPD/TPO, CO uptake, and computational table imports have explicit contextual validation. Native inventory writes and Used/Generated sample links still require tenant configuration and live validation; inspecting a protocol version does not bind it to a submitted review.

Run `python -m unittest discover -s tests -v` and `python scripts/test-desktop-gui.py` for synthetic component and native-widget checks. The desktop build is sandbox-only. Synthesis/spectroscopy use explicit table mappings; Rochester toolkit results are imported with provenance, not independently recalculated.

## Historical web prototype

The web application now supports CSV/XLSX/flat-record JSON upload, immutable original files, source checksums, versioned partner/modality mappings, deterministic unit and exact-name normalization, scientific-context validation, revision history, explicit approval, and recoverable publication to verified SciSure experiment file sections. The original Python offline importer remains available.

The first specialized format is the University of Rochester catalysis toolkit RWGS bundle. It preserves partner processing results and creates a separately recorded time-axis revision when a confirmed interval is supplied. It does not independently reproduce GC conversion or selectivity. General synthesis and spectroscopy tables use explicit mappings; technique-specific processing recipes and native sample creation/linking remain future extensions.

The earlier browser/backend prototype used Sites with Cloudflare Workers, D1, and R2. That deployment does not meet the user's current hosting requirements and must not be used as the team solution. The owner still needs to retire the old hosted prototype and its credential. The desktop runtime imports none of that hosting code.

## Use and development

- [Web workflow, security, recovery, testing, and deployment](docs/web-application.md)
- [Setup and remaining partner inputs](docs/setup.md)
- [Offline importer](docs/ingestion.md)
- [Rochester GC integration](docs/toolkit-gc.md)
- [Scientific object mapping and future extensions](docs/scisure-destinations.md)

With Node 24: `npm ci --ignore-scripts`, `npm run typecheck`, `npm run test:web`, and `npm run build`. Apply the generated D1 migrations locally before running `npm run dev`. The local HTTP fixture is `node tests/web-workflow.mjs`. The offline suite is `python -m unittest discover -s tests -v`.

## Secrets and research data

Do not paste tokens into chat or commit credentials or real research datasets. Desktop users enter tokens directly into the app and can choose operating-system credential storage. Shared-token API actions use the token owner's SciSure permissions and identity; reviewer names in the desktop app are self-reported.

Additional entities and representative synthesis/spectroscopy examples are still needed to validate their specific conventions. New partners can be registered by the owner without inventing names or inferring scientific mappings.
