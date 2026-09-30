# AI and analysis access (read-only)

There are three ways in. All of them only read.

## 1. One-click export (no programming)

In the app, go to **Export & AI access → Choose folder & export**. You get:

| File | Contents |
| --- | --- |
| `samples.csv` | One row per sample: identity, composition, procedure and version, the recipe actually used (`recipe_*`), deviations from the master procedure |
| `data_records.csv` | One row per measurement with its conditions (`cond_*`) and the sample's recipe columns (`sample_*`). Ready for modeling. |
| `procedures.csv` | Every version of every master procedure |
| `catalyst_dataset.json` | Everything, nested |
| `originals/…` | The original files (optional): `originals/<sample ID>/<data ID>/<file>` |

## 2. Python (notebooks, ML pipelines)

```python
import pandas as pd
from catalyst_query import CatalystReader   # this repository must be on PYTHONPATH

db = CatalystReader(token='LAB-TOKEN', server='https://sandbox.elabjournal.com')
samples = pd.DataFrame(db.samples(search='Mo K'))
reactor = pd.DataFrame(db.data(technique='RXN'))     # conditions + recipe columns per test
raw = db.file_bytes('UR-MDP-260925-01-RXN-01', 'gc-summary.csv')
```

From the command line:

```
python -m catalyst_query samples Mo2C
python -m catalyst_query export ./out --technique RXN --originals
```

Both read `CATALYST_TOKEN` and `CATALYST_SERVER` from the environment.

## 3. Live connection for an AI assistant (MCP)

`catalyst_query.mcp_server` is a small, dependency-free MCP server. Its tools are:

- `catalyst_summary`
- `list_samples`
- `get_sample`
- `list_procedures`
- `get_procedure`
- `find_data`
- `read_data_file`

Example configuration for Claude Desktop (`claude_desktop_config.json`). The app's **Copy AI-assistant setup snippet** button copies this for you:

```json
{
  "mcpServers": {
    "catalyst": {
      "command": "python",
      "args": ["-m", "catalyst_query.mcp_server"],
      "env": {
        "CATALYST_SERVER": "https://sandbox.elabjournal.com",
        "CATALYST_TOKEN": "PASTE-LAB-TOKEN",
        "PYTHONPATH": "C:\\path\\to\\CATALYST"
      }
    }
  }
}
```

It needs Python 3.10 or later. The reader refuses any request other than a read, so an assistant can't change data even by mistake.

## Record format (`catalyst-record/2`)

Every record is JSON with `format`, `kind` (`sample`, `data`, `shipment` or `procedure`), `id`, `created_by` (`name`, `initials`, `lab`) and `created_at`, plus:

- **sample:**
  - `source` (`synthesized` or `commercial`), `synthesis_date` or `received_date`, `composition`
  - `procedure` (`id`, `version`, `name`), or `commercial` (`supplier`, `product`, `catalog_number`, `lot`, `form`)
  - `recipe` (the fields in `records.RECIPE_FIELDS`; numbers in °C, h, °C/min, g; `components` = `[{component, loading, unit}]`)
  - `deviations` (automatic differences from the master recipe), `deviation_notes`
  - `amount_g` (older records: `amount_made_g`), `parent_id`, `notes`, `files`
- **data:**
  - `sample_id`, `technique` (code) and `technique_label`, `date`, `title`
  - `conditions` (technique-specific, all optional)
  - `pooled_with` (other samples in the same test), `notes`
  - `files` (`name`, `sha256`, `size_bytes`)
  - `protocol` (`id`, `version`, `name`) and `protocol_deviations` (`field`, `label`, `protocol`, `run`) when a test protocol was followed
  - `derived_from` (the raw data IDs an analysis was made from)
  - `extracted` (what CATALYST read from each file: `file`, `reader`, `label`, `metadata`, `warnings`)
  - `results` (computed values, e.g. `co2_conversion_pct`, `selectivity_CH3OH_pct`, with a `calculation` text); per-injection values are in the `… - CATALYST results.csv` file
- **shipment:** `sample_id`, `from_lab`, `to_lab`, `date`, `amount`, `tracking`, `notes`
- **procedure:** `id` (`PRC-…` synthesis or `TST-…` test protocol), `version`, `name`, `category` (`testing` for test protocols), `recipe` (the synthesis template, or the test conditions in `records.TEST_FIELDS`)

## Corrections

A corrected record keeps its ID and adds:

- `revision` (1 for an uncorrected record), `revised_at`, `revised_by`, `revision_note`
- `history`: one entry per revision (`revision`, `at`, `by`, `note`, `changes`, `status`)
- `status`: `active`, `withdrawn` (data that should not be used) or `registered_in_error` (a sample ID that was wrong); `status_note` gives the reason, and a retired sample may have `replaced_by`
- `superseded_files`: files that were replaced. They stay in SciSure but are not listed in `files`.

The reader and exports return only the latest revision. They leave out withdrawn data and samples registered in error unless you ask for a sample by its ID, in which case `get_sample` includes a `warning`. The `status`, `revision` and `last_corrected` columns in the tables show what was corrected.

Technique codes: RXN, HTE, PILOT, XRD, BET, CHEM, TPR, TPD, TPO, TEM, SEM, XPS, XAS, INSITU, RAMAN, IR, ICP, TGA, CALC, OTHER.
