# CATALYST desktop

CATALYST runs on your computer and talks directly to SciSure. There is no hosted processing service, AI account, Cloudflare service, or research-data storage in GitHub. This development release connects to `https://sandbox.elabjournal.com` only.

## Open the application

Windows: extract the complete ZIP and open `CATALYST/CATALYST.exe`. Keep the accompanying `_internal` folder beside the executable. Mac: extract the ZIP and open `CATALYST.app`. These initial builds are unsigned development builds; institutional software approval or signing may be needed before team distribution.

## Prepare a submission offline

1. In **Files & mapping**, select CSV, XLSX, or JSON files. Enter the partner identifier and choose reactor, synthesis, or spectroscopy.
2. Supply the scientific context. CATALYST does not infer scientific meaning from filenames. For spectroscopy, the context axis unit must match the mapping's source unit.
3. For ordinary tables, choose the source file, worksheet, and header row; click **Read columns**. Select canonical fields and source units. Optional exact name aliases use JSON such as `{"CO2":"carbon dioxide"}`. Ignored columns remain in the original file.
4. Supply the source export/version, mapping name, and mapping version. Changing established rules requires a new version. Profiles are embedded in the review package and can be reused from SciSure history.
5. For the Rochester toolkit RWGS bundle, select the original GC report XLSX, analysis XLSX, summary CSV, and flows CSV, and check the toolkit option. Its dedicated profile checks cross-file relationships. The entered interval creates a separately documented nominal time axis. No default of 22.4 minutes is imposed on other runs.
6. Click **Build review preview**. Inspect standardized values, warnings, and provenance. Resolve blocking errors, enter your reviewer name and note, acknowledge the review, and click **Approve this revision**. Editing the files, context, or mapping invalidates approval.

## Connect and send

1. In **SciSure connection**, paste your API token. Optionally select **Remember** to keep it in Windows Credential Manager or macOS Keychain. Otherwise it remains in application memory for the session. **Use saved token** retrieves the saved credential; **Forget token & disconnect** removes it.
2. Click **Connect / refresh experiments**. Select an unsigned experiment in the intended group and click **Verify selected destination**. The existing test experiment can be selected; the app does not create another project/study/experiment.
3. Return to **Review & approve** and click **Send / check transfer to SciSure**. Confirm the displayed destination. Source files and the approved review package are uploaded into a revision-specific experiment section. Every file is downloaded and checksum-checked before the app records a completion receipt.
4. If the connection fails, keep the app open and use **Send / check transfer** to reconcile it. The app checks for completed writes before sending another. A write whose outcome remains unknown is paused rather than duplicated. If a partial transfer has no review manifest, resolve the incomplete section in SciSure before starting another submission.

## Read existing data

Connect and select an experiment, then open **Read from SciSure**. **List saved CATALYST reviews** shows desktop review packages, including incomplete transfers. **Open selected review** reads the standardized data and history into memory. **Load review + originals** additionally retrieves and verifies original files. **Reuse mapping / revise** prepares a new revision linked to the saved one; the previous approval does not carry forward.

**Browse all experiment files** lists other file attachments, including those not created by CATALYST. **Read selected file** shows CSV/XLSX/JSON content in memory. Signed experiments can be read. eLABHybrid files remain accessible through SciSure; this release does not contact the separate institutional file host.

## Where information lives

- Existing source files stay in their original location. The app creates no additional research-data cache, database, or automatic log file on your computer.
- Working files, previews, and unsent approvals live in memory and are lost on exit. The operating system manages process memory; this is not a forensic guarantee against swap or crash dumps.
- SciSure stores the transferred original files, review/mapping/context/provenance package, approval statement, IDs, and completion receipt.
- Only the optional token is stored in the operating system credential store. It is never put in GitHub, packaged code, or a research manifest.
- Shared tokens use the same SciSure account and permissions for everyone. CATALYST reviewer names are self-reported, not independent authenticated signatures. Native SciSure signing and permissions remain authoritative.

This release imports toolkit scientific results; it does not execute or reproduce the GC processor. Synthesis and spectroscopy support explicit table mappings and contextual validation, not technique-specific scientific fitting or processing. Partner-specific profiles require representative data and review.
