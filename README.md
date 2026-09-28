# CATALYST

## Download

**[Download the latest CATALYST](https://github.com/mporosoff/CATALYST/releases/latest)**. Choose `CATALYST-Windows.exe`, `CATALYST-Mac-AppleSilicon.dmg` or `CATALYST-Mac-Intel.dmg`. Installation takes about a minute; see [install instructions](docs/install.md). You also need your lab's SciSure token from the coordinator.

## About

CATALYST is the desktop front door to the consortium's SciSure database. Researchers work with four things:

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

## Classic interface (0.x)

Reviews saved by versions 0.x remain readable with `CATALYST --classic` (or `python -m catalyst_desktop --classic`). Its documentation is kept in `docs/` (desktop-quickstart, consortium-workflow, scisure-configuration, scisure-integration-review, toolkit-gc, ingestion). The retired Cloudflare web prototype was removed from the code in version 1.1 and remains in the Git history.

## Secrets and research data

Do not paste tokens into chat or commit credentials or real research datasets. Desktop users enter tokens directly into the app and can choose operating-system credential storage. Shared-token API actions use the token owner's SciSure permissions and identity; reviewer names in the desktop app are self-reported.

Additional entities and representative synthesis/spectroscopy examples are still needed to validate their specific conventions. New partners can be registered by the owner without inventing names or inferring scientific mappings.
