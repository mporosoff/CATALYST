# CATALYST

Private upload, review, and standardization workspace for catalysis data, with a secure SciSure/eLabNext sandbox connector.

The web application now supports CSV/XLSX/flat-record JSON upload, immutable original files, source checksums, versioned partner/modality mappings, deterministic unit and exact-name normalization, scientific-context validation, revision history, explicit approval, and recoverable publication to verified SciSure experiment file sections. The original Python offline importer remains available.

The first specialized format is the University of Rochester catalysis toolkit RWGS bundle. It preserves partner processing results and creates a separately recorded time-axis revision when a confirmed interval is supplied. It does not independently reproduce GC conversion or selectivity. General synthesis and spectroscopy tables use explicit mappings; technique-specific processing recipes and native sample creation/linking remain future extensions.

The browser and backend are deployed together on Sites. The backend uses Cloudflare Workers, private D1 metadata, and private R2 files. The SciSure token is a backend deployment secret. The current tenant allowlist is `https://sandbox.elabjournal.com`. Live connection verification depends on securely installing the user's token and confirming the sandbox destination.

## Use and development

- [Web workflow, security, recovery, testing, and deployment](docs/web-application.md)
- [Setup and remaining partner inputs](docs/setup.md)
- [Offline importer](docs/ingestion.md)
- [Rochester GC integration](docs/toolkit-gc.md)
- [Scientific object mapping and future extensions](docs/scisure-destinations.md)

With Node 24: `npm ci --ignore-scripts`, `npm run typecheck`, `npm run test:web`, and `npm run build`. Apply the generated D1 migrations locally before running `npm run dev`. The local HTTP fixture is `node tests/web-workflow.mjs`. The offline suite is `python -m unittest discover -s tests -v`.

## Secrets and research data

Do not paste tokens into chat, commit credentials or real research datasets, expose the token in a frontend environment variable, or store it in browser storage. Production secrets are managed through Sites runtime settings. The connection screen reports only whether the token is configured.

Additional entities and representative synthesis/spectroscopy examples are still needed to validate their specific conventions. New partners can be registered by the owner without inventing names or inferring scientific mappings.
