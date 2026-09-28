# CATALYST

## Download

**[Download the latest CATALYST](https://github.com/mporosoff/CATALYST/releases/latest)**. Choose `CATALYST-Windows.exe`, `CATALYST-Mac-AppleSilicon.dmg` or `CATALYST-Mac-Intel.dmg`. Installation takes about a minute; see [install instructions](docs/install.md). You also need your lab's SciSure token from the coordinator.

## Version 1.0 — redesigned around sample IDs

CATALYST 1.0 is a new interface on top of the tested SciSure transfer engine. Researchers work with four things:

- **samples**
- **procedures**
- **data**
- **shipments**

**Readable IDs are generated automatically:**

- sample: `UR-MDP-260925-01` (lab – initials – synthesis date – number)
- data: `UR-MDP-260925-01-XRD-01`
- procedure: `PRC-UR-001`

**Screens:**

- **Samples** (home screen): search and filter every sample in the consortium.
- **Sample page:** the recipe, every data record from every lab, and the shipping log.
- **New sample:** pick the shared procedure; only the differences are recorded.
- **Upload data:** sample, technique, date and files, with a calendar date picker, then preview and save.
- **Procedures:** master recipes and their versions.
- **Export & AI access:** tidy CSV/JSON export and a read-only MCP connector.

**What the app remembers and how it saves:**

- Your profile (name, initials, lab) is set once and saved locally, together with drafts of unfinished forms. No research data is stored on the computer.
- Original files are uploaded unchanged and checksum-verified.

**Guides:**

- [Researcher guide](docs/user-guide.md)
- [Coordinator setup](docs/coordinator-setup.md) (workspace, sharing, tokens)
- [AI and analysis access](docs/ai-access.md) (export, `catalyst_query`, MCP)

**Run and build:**

- Run from source: `python -m catalyst_desktop`. Add `--classic` to open the 0.11 review interface.
- Build the Windows app: double-click `Build-CATALYST-Windows.bat`, or push to `main` so GitHub Actions builds Windows and Mac.

**Tests:**

- `python -m unittest discover -s tests`
- `python scripts/test-redesign-gui.py`

---

*Earlier versions (0.x) are described below for reference.*

Local desktop upload, download, review, and standardization workspace for catalysis data, with a direct SciSure/eLabNext connector. The desktop application is the current implementation target; the earlier hosted web prototype is retained as historical source.

## Desktop application — Windows and Mac

Version **0.11.0** makes **Samples & data** the starting point. Open a sample to browse its records across experiments, read results and download originals; native experiment links remain distinct from exact sample attribution. Create projects, studies and runs in the app, add missing samples/methods while keeping the draft, and review file → sample → method → run associations in the batch. Each new connected review binds its run. Validation issues have correction buttons that return to the relevant field. Supporting documents can be attached directly to native samples without an experiment. Historical measurements can explicitly mark a method as not recorded.

Version **0.10.0** aligns connection help, adaptive forms and the native LIMS sample configuration. Material v2 stores sample identity and description without repeated synthesis requirements; existing v1 records are preserved. Researchers can search existing LIMS inventory, review sample creation/reuse and Used/Generated links, and include those actions with an approved single or batch upload. Token creation and university SSO instructions are built into Help. Native inventory actions are explicitly selected and reviewed; existing saved reviews retain their original behavior.

Version **0.9.0** adapts the upload form to samples, reusable procedures, synthesis executions, measurements, and computational results. Register samples and method versions once, then select them from a searchable LIMS library or add them while preserving the current draft. Original-file measurements need only a sample, method, date, and operator; table conversion is optional. Batch review stages related records and files, requires approval of each record, and sends definitions before the data that references them. Hover guidance and **Help · upload & access** explain fields, upload steps, and finding or downloading saved records.

Download the portable Windows EXE or the appropriate Mac DMG from [GitHub Releases](https://github.com/mporosoff/CATALYST/releases). No separate Python installation is needed. To update, close the app and replace its application file. Developers can run `python -m catalyst_desktop` with Python 3.12+ and Tk after installing `requirements-desktop.txt`. Builds contain no token or research data.

The app supports offline CSV/XLSX/JSON table selection, original images and native supporting files, explicit versioned mappings, scientific-context validation, preview, immutable revision approval, direct checksum-verified SciSure uploads, saved-review history, and experiment attachment downloads. Image-only submissions have a dedicated imaging modality and preserve-only review. It uses no hosted processing backend, AI service, Cloudflare service, or automatic local research-data cache. Original files and approved records go directly to SciSure; users can explicitly save individual attachments or a verified review ZIP. Optional remembered tokens live in the operating system credential store, separately for each server.

- [Desktop quick start](docs/desktop-quickstart.md)
- [Six-lab sample lineage and measurement workflow](docs/consortium-workflow.md)
- [Components, boundaries, tests, and packaging](docs/desktop-development.md)
- [SciSure integration audit and datatype readiness](docs/scisure-integration-review.md)
- [Version 0.8.0 review, repairs, and checks](docs/desktop-audit-0.8.md)
- [Selected SciSure configuration and guarded schema setup](docs/scisure-configuration.md)

Version **0.8.0** adds a user-entered SciSure server URL, per-server token storage, individual attachment saving, and **Saved records → Saved reviews → More actions → Download review package…**. The ZIP contains verified original bytes, the approved review with mapping/context/provenance, the transfer receipt, and standardized JSON plus a convenience CSV when rows exist. The sandbox remains the default. Other servers require an explicitly entered HTTPS DNS hostname on standard port 443 and a token valid for that server; SciSure permissions determine which records each token can read or write.

The 0.8.0 review repaired source-precision loss, cached-formula interpretation, incomplete toolkit-result checks, stored-review consistency, and stale/archived transfer reads. Native inventory publication and Used/Generated links were added later in 0.10.0 through reviewed adaptive submissions. Live tenant acceptance remains outstanding; schema setup and read-only inspections do not establish write permission.

Run `python -m unittest discover -s tests -v` and `python scripts/test-desktop-gui.py` for synthetic component and native-widget checks. No authenticated live SciSure verification was performed during the 0.8.0 review. Synthesis/spectroscopy use explicit table mappings; Rochester toolkit results are imported with provenance, not independently recalculated. Crash recovery for unfinished transfers remains manual, and reviewer names are self-reported rather than cryptographically authenticated.

## Historical web prototype

The web application now supports CSV/XLSX/flat-record JSON upload, immutable original files, source checksums, versioned partner/modality mappings, deterministic unit and exact-name normalization, scientific-context validation, revision history, explicit approval, and recoverable publication to verified SciSure experiment file sections. The original Python offline importer remains available.

The first specialized format is the University of Rochester catalysis toolkit RWGS bundle. It preserves partner processing results and creates a separately recorded time-axis revision when a confirmed interval is supplied. It does not independently reproduce GC conversion or selectivity. The historical web prototype uses explicit table mappings and has no native inventory writer. Current native sample creation and linking belong to the desktop application described above; technique-specific processing recipes remain separate development work.

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
