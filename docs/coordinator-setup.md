# CATALYST — coordinator setup (one time)

## 1. Create the shared workspace

1. Connect CATALYST with your own token.
2. Go to **Settings → Coordinator tools → Set up CATALYST workspace**. This creates the following in the active SciSure group, only if they are missing:
   - project **CATALYST**
   - study **CATALYST Samples** (one experiment per sample)
   - study **CATALYST Procedures** (one experiment per master procedure)
3. Don't rename these three items. The app finds them by name.

## 2. Give every lab access

SciSure only shows a user their own experiments and experiments where they are a collaborator. So in SciSure:

- Put every lab account in the same group.
- Add every lab account to the **CATALYST** project as a collaborator with edit rights.
- Turn on the project's auto-collaborate setting. CATALYST creates every experiment with `autoCollaborate`, so new sample experiments inherit these collaborators.

After this, a SLAC account can add XRD data to a Rochester sample. If a lab sees *"cannot add to this record"*, its account is missing from the project collaborators.

## 3. Tokens

Create one API token per lab (**Apps & Connections → Manage Authentication**) and send it privately.

Tokens made from separate lab accounts are best, because SciSure then shows which lab wrote what. CATALYST records the actual person (name, initials, lab) inside every record either way.

## 4. Test, then switch servers

1. Run a full round trip in the sandbox: procedure → sample → upload → another lab uploads → export.
2. For production, researchers change **SciSure server** in Settings and paste a production token. Nothing else changes.

## Where things live in SciSure

```
Project  CATALYST
  Study  CATALYST Samples
    Experiment  "UR-MDP-260925-01 | 10 wt% Mo, 1 wt% K / γ-Al2O3 | PRC-UR-001 v2"
      FILE section  "CATALYST sample | UR-MDP-260925-01"                              catalyst-record.json + synthesis files
      FILE section  "CATALYST data | UR-MDP-260925-01-XRD-01 | XRD | 2026-09-30 | SLAC | JL"   catalyst-record.json + originals
      FILE section  "CATALYST shipment | UR-MDP-260925-01-SHP-01 | UR>SLAC | 2026-09-28 | MDP"
  Study  CATALYST Procedures
    Experiment  "PRC-UR-001 | K-promoted Mo2C/γ-Al2O3 (IWI + carburization)"
      FILE section  "CATALYST procedure | PRC-UR-001 v1 | …"  one section per version
```

Every record also gets a **readable text section** just above its file section. It shows everything in the record as a labelled table (recipe, deviations from the procedure, conditions, files), so people browsing SciSure can read it without opening any file. It's a copy: edits made there are not read back into CATALYST. To add readable copies to records saved before version 1.2, use **Settings → Coordinator tools → Repair readable copies and the sample list**.

Rules the app follows:

- Original files are uploaded first and `catalyst-record.json` last. A section without a record is an unfinished upload, and the app shows it as one.
- Every uploaded file is downloaded again and its SHA-256 checksum compared.
- Saves are never repeated blindly. The app looks for an existing section or file before writing.

## Corrections

- **Revisions:** a corrected record is saved as a new revision in the same section: `catalyst-record.json` is revision 1, then `catalyst-record-r2.json`, `-r3.json` and so on. The latest one counts, and each revision carries the full history. Replaced files stay in the section, listed as superseded. Nothing is ever deleted.
- **Sample list:** the list is built from experiment names, which don't change. So each sample correction also adds a line to the experiment **CATALYST corrections log** in the Samples study. Don't rename or delete it. If a correction's log line couldn't be written, the app says so; **Repair readable copies and the sample list** adds it later.
- **Who may correct:** the lab that saved a record, or the coordinator. Tick **Settings → Coordinator tools → I am the CATALYST coordinator** on your own computer only. This setting prevents honest mistakes but is not a security control: every lab account has edit rights in SciSure. Each revision records who made it, and **History of corrections** shows it.
- **Undo:** only the coordinator can restore a withdrawn data record or a sample registered in error (in the Fix menus). The restore is recorded in the history too.
- **Wrong IDs:** a sample ID is never edited or reused. The wrong one is marked *registered in error* and linked to the correct sample.

## What changed from 0.11

Earlier CATALYST reviews, stored in "CATALYST desktop | …" sections, are untouched. They can still be read with `CATALYST --classic`.

Version 1.0 doesn't use the native "CATALYST Material" inventory types. Samples are experiments, so all of a sample's data sits in one place. Mirroring samples into SciSure inventory can be added later if it's wanted.
