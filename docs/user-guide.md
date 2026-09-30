# CATALYST — researcher guide

CATALYST is the front door to the consortium's SciSure database. You never need to open SciSure itself.

## First start (once)

1. Open CATALYST. The **Settings** page opens.
2. Enter your **full name**. Your **initials** are suggested; keep the same initials for the whole project.
3. Choose your **lab**.
4. Paste your lab's **SciSure token** (your coordinator sends it) and click **Connect**. Leave **Remember on this computer** ticked.

That's the whole setup. CATALYST remembers you. The token is kept in the computer's secure credential store.

## The one idea: every sample has one ID

`UR-MDP-260925-01` = lab (UR) · your initials (MDP) · synthesis date (25 Sep 2026) · number that day (01).

CATALYST makes the ID when you save. Write it on the vial. Every measurement, from any lab, is attached to it. Data IDs extend it: `UR-MDP-260925-01-XRD-01`.

## Register a sample

**New sample** →

1. Choose **We made it**.
2. Pick the shared **procedure**. Type a few letters to search.
3. The recipe fills in. Change only what you did differently. CATALYST records the differences for you.
4. Pick the **synthesis date** from the calendar.
5. Check the suggested composition.
6. Add files if you like (photos, notebook scans).
7. **Preview & save**.

Press **Tab** to move from one field to the next, in order. **Alt+↓** in a date field opens the calendar.

**Metals and loadings.** Enter one row per metal, promoter or phase, each with its own loading and unit (wt%, mol%, at%, molar ratio or mmol/g). Use **+ Add another metal / phase** for more rows. Leave the loading blank for a bulk phase such as Mo2C. The composition is written for you as, for example, `10 wt% Mo + 1 wt% K on γ-Al2O3`: components joined by "+", then "on" and the support. There are no slashes, so it can't be misread.

Making it again next week? That's a new sample with a new ID, linked to the same procedure.

## Register a commercial or reference catalyst

This covers anything your lab didn't make: a purchased catalyst, an industry standard, or a material from a company partner.

**New sample** →

1. Choose **Commercial or reference material**.
2. Enter the supplier and product or grade. The catalog number, lot number and form are optional.
3. Pick the **date received**. It becomes part of the ID, just as the synthesis date does.
4. Enter the stated composition from the data sheet.
5. Attach the certificate of analysis if you have it.

No procedure is needed. The sample list shows it as "Commercial · supplier product", and **Procedure → Commercial** filters for these.

## Upload data

**Upload data** (or **Upload data for this sample** on a sample's page) →

1. Choose the sample. Your recent samples are listed first.
2. Choose the technique and the date.
3. Fill in any conditions you know. They're optional, but they make cross-lab comparison and AI modeling far more useful.
4. Add the original files. They are stored unchanged.
5. **Preview & save**.

Every file is checked after upload.

## Test protocols (reactor testing)

A test protocol is the shared set of standard testing conditions, such as Northwestern's CO₂-to-methanol test. It is to reactor data what a synthesis procedure is to a sample. Protocol IDs look like `TST-NU-001`.

- **Create one:** go to **Procedures → + New test protocol**. If the conditions are already written in a Word document, click **Fill in from a document (.docx)…**. The fields fill in from the document's tables and the document is attached. Check the fields, then save.
- **Use one:** when uploading reactor data, pick the protocol under **Test protocol**. Its conditions fill in. Change anything your run did differently, and CATALYST records those changes as differences from the protocol.
- **Improve one:** use **Save a new version**. Each run keeps the version it followed.

## Files CATALYST reads for you

When you add one of these files to an upload, CATALYST reads it and fills in the form. Check what it filled in. It only fills empty fields, and when a file disagrees with a value you already have, it tells you. The original file is always stored unchanged.

- **Northwestern GC analysis workbook (.xlsx):** reads the catalyst mass, gas flows, pressure, GC method and each injection. It follows the template's own layout: the reference injections before reaction (rows 5–7, averaged in row 8) give the feed, and the unlabeled column V is unreacted CO₂. The product column labelled "CO2" is read as CO. For each injection CATALYST calculates:
  - CO₂ conversion = (feed CO₂ − CO₂ out) / feed CO₂, as the template does
  - H₂ conversion from the H₂ peak area against the reference injections
  - carbon balance
  - carbon-basis selectivity (DME counts two carbons)

  The record shows the average of the last three injections. A results table (`… - CATALYST results.csv`) with every injection, and the workbook's own values beside CATALYST's, is added to the record.
- **SLAC (SSRL) raw EXAFS scan (.txt):** reads the scan name, date, edge, monochromator and energy range, and calculates μ(E) into `… - CATALYST mu(E).csv`:
  - sample, transmission: ln(ADC_01/ADC_02) = ln(I0/I1)
  - reference foil: ln(ADC_02/ADC_03) = ln(I1/I2)
  - fluorescence: ADC_04/ADC_01 = If/I0

  It also reports the edge position (steepest rise) for each, as a quick check. The reference-foil edge should match the element's tabulated edge.

## Analysis results (fits, wavelet transforms, figures)

Upload processed results as their own data record, then choose the raw record under **This is an analysis of**. On the sample page the record is marked "analysis" and links back to the raw scan. If you have the numbers behind a figure (for example χ(k) or the wavelet map as a CSV), add them too, since AI tools can't read values off a picture.

## Find and download data

**Samples** is the home screen.

- Search by ID, composition or procedure, or filter by lab, person, procedure or date.
- Double-click a sample to see its recipe, every data record from every lab, and its shipping log.
- Select a record, then **Download files**.

## Export (for analysis or AI)

**Export & AI** →

1. Choose which samples and data type to include.
2. Click **Preview**. You see exactly what the export will contain: samples, data records, procedures and original files, with counts and total size.
3. Use **Find in table** to search the preview. Double-click a row to see all its values.
4. **Choose folder & export**.

## Shipping a sample to another lab

Open the sample → **Log shipment** (destination, date, amount, tracking). The receiving lab uploads its data to the same ID.

## A sample that changes physically

For example, reduced, spent, pelletized or scaled up: open it and click **New sample made from this**. The new sample gets its own ID and is linked to the original.

## Looking at records in SciSure

You don't need SciSure itself, but if you do open it, each record appears as a readable table (recipe, conditions, who, when) above its files. Make changes in CATALYST, not in SciSure: edits to that table are not read back.

## Editing a record

Records can be corrected after they are saved. Every correction needs a short reason, and earlier versions are kept, so nothing is ever lost. You can correct records your lab saved. The coordinator can correct any record.

**A sample's details** (composition, recipe, amount, notes, supplier details, files): open the sample → **Edit → Edit this sample's details…** → change what was wrong → write what was wrong → **Preview & save**.

**The ID itself is wrong** (wrong date, wrong initials, or the sample was entered twice): the ID can't be edited, because it may already be written on vials. Open the sample → **Edit → Registered in error…**. Pick the correct sample if it already exists, or register it again afterwards. The wrong ID is hidden from the sample list and exports, it points to the correct sample, and its number is never reused. To see these samples, tick **Show samples registered in error** under the sample list.

**A data record:** select it on the sample page → **Edit ▾**:

- **Edit this record…** fixes the date, conditions or notes. To replace a file, tick **Wrong or replaced** next to the old file and add the right one. The old file stays in SciSure, marked as superseded, but is no longer offered as the data.
- **Withdraw this record…** is for data that shouldn't be used, such as a failed run or data on the wrong sample. It's hidden from the sample page and exports but kept in SciSure with your reason. **Show withdrawn** under the list shows it again.

**A procedure:** **Procedures → Edit this version…** fixes the description, written steps or documents. To change the recipe itself, use **Save a new version**.

**History of corrections** (in either Edit menu, or on the Procedures page) shows who changed what, when and why. Only the coordinator can restore a withdrawn record or a retired sample.

## Drafts

Half-finished forms are saved automatically and come back when you reopen CATALYST. Only what you typed and the locations of the files you picked are saved, not the files themselves.

## If something goes wrong

- **"Cannot see the shared CATALYST project"** — ask the coordinator to share the project with your lab account.
- **"Upload did not finish"** on a sample page — upload those files again. Unfinished copies are ignored.
- **Files larger than 20 MB** — split or compress them for now.
