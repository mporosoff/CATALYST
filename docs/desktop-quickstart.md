# CATALYST desktop

CATALYST runs on your computer and talks directly to SciSure. There is no hosted processing service, AI account, Cloudflare service, or research-data storage in GitHub. This development release connects to `https://sandbox.elabjournal.com` only.

## Open the application

Windows: extract the complete ZIP and open `CATALYST/CATALYST.exe`. Keep the accompanying `_internal` folder beside the executable. Mac: extract the ZIP and open `CATALYST.app`. These initial builds are unsigned development builds; institutional software approval or signing may be needed before team distribution.

## Prepare a submission offline

For consortium work, first read the [six-lab workflow](consortium-workflow.md). For a new synthesis, choose the lab and use **New batch + sample IDs**, then enter the shared procedure/version/reference and actual synthesis record. For an existing material, connect and use **Samples & models → Refresh from SciSure** to find it by canonical ID, lab, or local alias. Choose **Use for a new measurement / calculation**, or **Create a derived sample** for an aliquot or treated material. A shared procedure does not merge independent batches. Computations use separate model IDs and explicit optional links to physical samples.

1. In **Files & mapping**, select CSV, XLSX, flat-record JSON, images, or native files. Choose the data-format/source lab and modality. The submitting, acquisition, and processing labs are separate choices in the context.
2. Supply the scientific context. CATALYST does not infer scientific meaning from filenames. For spectroscopy, the context axis unit must match the mapping's source unit.
3. For ordinary tables, choose the source file, worksheet, and header row; click **Read columns**. Select canonical fields and source units. Optional exact name aliases use JSON such as `{"CO2":"carbon dioxide"}`. Ignored columns remain in the original file.
4. Supply the source export/version, mapping name, and mapping version. Changing established rules requires a new version. Profiles are embedded in the review package and can be reused from SciSure history.
5. For the Rochester toolkit RWGS bundle, select the original GC report XLSX, analysis XLSX, summary CSV, and flows CSV, and check the toolkit option. Its dedicated profile checks cross-file relationships. The entered interval creates a separately documented nominal time axis. No default of 22.4 minutes is imposed on other runs.
6. Click **Build review preview**. Inspect standardized values, warnings, and **Lab & sample lineage**. Resolve blocking errors, enter your reviewer name and note, acknowledge the review, and click **Approve this revision**. Editing the files, context, or mapping invalidates approval. Reprocessing retains the acquisition's dataset ID; a new measurement gets a new dataset ID.

**Images and other original files:** Use **Add images / supporting files** to attach PNG, JPEG, TIFF, other image formats, PDFs, native instrument exports, structures, or logs alongside a table. Files keep their original bytes, metadata within the file, filename, checksum, and submission's sample/dataset provenance. No image conversion, OCR, executable content, or AI analysis runs. This button also preserves nested JSON or arbitrary CSV/XLSX supporting documents without trying to interpret them as tables.

For a submission with only images, choose **imaging**, enter the image type, what is shown/acquisition conditions, and scale reference (or explicitly `not quantitative`). **Preserve original files with context only** is selected automatically for native-only selections. It permits approval without a dummy table and clearly reports that no standardization or scientific interpretation occurred. It also works for native-only data under the other modalities. This release verifies image bytes and provenance; it does not display an image thumbnail or perform scientific image analysis. Limits remain six files, 20 MiB each and 40 MiB combined.

## Connect and send

1. In **SciSure connection**, paste your API token. Optionally select **Remember** to keep it in Windows Credential Manager or macOS Keychain. Otherwise it remains in application memory for the session. **Use saved token** retrieves the saved credential; **Forget token & disconnect** removes it.
2. Click **Connect / refresh experiments**. Select an unsigned experiment in the intended group and click **Verify selected destination**. The existing test experiment can be selected; the app does not create another project/study/experiment.
3. Return to **Review & approve** and click **Send / check transfer to SciSure**. Confirm the displayed destination. Source files and the approved review package are uploaded into a revision-specific experiment section. Every file is downloaded and checksum-checked before the app records a completion receipt.
4. If the connection fails, keep the app open and use **Send / check transfer** to reconcile it. The app checks for completed writes before sending another. A write whose outcome remains unknown is paused rather than duplicated. If a partial transfer has no review manifest, resolve the incomplete section in SciSure before starting another submission.

**Inspect setup:** After connecting, optionally select an experiment and enter an existing SciSure sample ID or an exact protocol **version** ID, then click **Inspect native SciSure setup (read only)**. The overview shows accessible sample types, required fields/quantities, account scope, collaborators, and the optional sample/protocol references. Field details include the actual metadata IDs needed for future native bindings. No inventory records or permissions are changed. Successful reads do not establish write permission.

Rate-limited reads have a bounded retry; a rejected write can be retried manually after the displayed delay. An unknown write retains its duplicate-write guard even after reconnecting within the same app session. Recovery after closing/crashing the app is still manual: inspect and reconcile incomplete records in SciSure. **Reuse mapping / revise** creates a new revision; it does not resume the old transfer.

## Read existing data

Connect and select an experiment, then open **Read from SciSure**. **List saved CATALYST reviews** shows desktop review packages, including incomplete transfers. **Open selected review** reads the standardized data and history into memory. **Load review + originals** additionally retrieves and verifies original files. **Reuse mapping / revise** prepares a new revision linked to the saved one; the previous approval does not carry forward.

**Browse file attachments** lists FILE/FILES/CUSTOM section attachments, including Office Online files not created by CATALYST. File ID, previous file ID, and stored date distinguish same-name revisions; newest IDs appear first. Embedded notebook images, legacy Excel sections, canvas drawings, and protocol attachments use other endpoints and are not included. **Read selected file** shows CSV/XLSX/JSON content in memory; other file types show receipt/size information. Signed experiments can be read. eLABHybrid files remain accessible through SciSure; this release does not contact the separate institutional file host.

## Where information lives

- Existing source files stay in their original location. The app creates no additional research-data cache, database, or automatic log file on your computer.
- Working files, previews, and unsent approvals live in memory and are lost on exit. The operating system manages process memory; this is not a forensic guarantee against swap or crash dumps.
- SciSure stores the transferred original files, review/mapping/context/provenance package, approval statement, IDs, and completion receipt.
- Only the optional token is stored in the operating system credential store. It is never put in GitHub, packaged code, or a research manifest.
- Shared tokens use the same SciSure account and permissions for everyone. CATALYST reviewer names are self-reported, not independent authenticated signatures. Native SciSure signing and permissions remain authoritative.

This release imports toolkit scientific results; it does not execute or reproduce the GC processor. The additional technique and computational paths support explicit table mappings and contextual validation, not scientific fitting or calculation engines. Partner-specific profiles require representative data and review. The shared catalog covers identity-bearing CATALYST packages visible to the token in its active SciSure group, up to 250 reviews. Native inventory links and cross-group catalogs are not implemented. See the accompanying integration audit for datatype coverage and remaining setup work.
