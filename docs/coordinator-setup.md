# CATALYST 1.0 — coordinator setup (one time)

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

Rules the app follows:

- Original files are uploaded first and `catalyst-record.json` last. A section without a record is an unfinished upload, and the app shows it as one.
- Every uploaded file is downloaded again and its SHA-256 checksum compared.
- Saves are never repeated blindly. The app looks for an existing section or file before writing.

## What changed from 0.11

Earlier CATALYST reviews, stored in "CATALYST desktop | …" sections, are untouched. They can still be read with `CATALYST --classic`.

Version 1.0 doesn't use the native "CATALYST Material" inventory types. Samples are experiments, so all of a sample's data sits in one place. Mirroring samples into SciSure inventory can be added later if it's wanted.
