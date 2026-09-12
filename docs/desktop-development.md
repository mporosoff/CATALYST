# Desktop components and verification

Run with Python 3.12+ and Tk installed: `python -m pip install -r requirements-desktop.txt`, then `python -m catalyst_desktop`. Python.org Windows and macOS installers include Tk. Linux additionally requires a graphical desktop and the platform Tk package.

`catalyst_desktop/model.py` owns in-memory source snapshots, deterministic decimal unit/name mappings, required context, toolkit bundle integration, immutable revision encoding/digests, and explicit approval. It reuses the bounded `catalyst_ingest` readers. No spreadsheet formula, macro, or AI processing executes. Source-version, entity, modality, and format are part of mapping identity. Mapping documents and their digests are embedded in saved revisions.

`catalyst_desktop/scisure.py` owns direct authenticated HTTPS, strict sandbox origin/path checks, redirect rejection, TLS verification, bounded responses, paginated reads, and unsigned destination validation. It does not use an environment-provided HTTP proxy. Token text and remote error bodies are never included in displayed errors or repr strings.

`catalyst_desktop/credentials.py` selects the native Windows, macOS, or Secret Service keyring implementation explicitly. It does not accept third-party plaintext/cloud fallback keyrings. Credential storage is optional and failure leaves the user able to work with a session-only token.

`catalyst_desktop/publication.py` verifies revision approval and source hashes, creates a revision-specific FILES section, stores the review manifest, uploads originals, verifies every byte by SHA-256, and writes a completion receipt last. It checks destination status before writes. Ambiguous outcomes never trigger blind automatic retries. Duplicate remote names and conflicting profile versions are rejected. Profile-version checking now scans CATALYST packages across the active group, including legacy profiles. SciSure does not provide an atomic registry through this implementation.

`traceability.py` owns the six-lab registry and review schema v2's separate batch, sample, synthesis execution, dataset, model, alias, procedure, and handoff declarations. UUID-based IDs include the creating lab and record type. Shared procedure references do not imply sample equivalence. Every mapped row is bound to one declared subject; conflicting mapped labels block approval. `catalog.py` reconstructs an in-memory catalog from completed review packages, preserves distinct matches for reused local labels, rejects changed identity definitions, validates completed parent/model links, and reserves IDs from incomplete packages. It fails closed on incomplete reads or more than 250 reviews. It does not cache manifests or research data on disk. Older reviews remain readable, but publication requires the new identity context.

New technique paths validate declared context and supported table axes/units. They do not run scientific analyses or verify procedure references against native SciSure protocols. The sample graph is persisted in CATALYST attachments; native inventory Samples and Used/Generated links are still not created. See `docs/consortium-workflow.md` for scope and data-entry semantics.

The review manifest explicitly says `prepared`; the completion receipt is a separate record. `history` and `read_review` support read-only access to signed experiments and return no new disk files. Downloaded original bytes are verified before reuse. Checksums establish integrity consistency, not a cryptographic identity signature. With shared tokens, client-entered names cannot supply independently enforced reviewer identity or partner isolation.

The native Tk GUI uses a single worker thread for parsing/network operations and locks editable controls while an operation runs. The UI never passes Tk calls to the worker thread. It stores no research state in a local database. Closing an incomplete in-memory review discards it. Cross-process concurrent writes to the same revision cannot be made atomic by the client; duplicate/conflicting remote records stop the workflow for owner review.

## Tests and builds

- `python -m unittest discover -s tests -v`: synthetic scientific boundaries, source precision/preservation, native transport security, approval, upload/read-back, corruption, and lost-response recovery. No live credentials or research data.
- `python scripts/test-desktop-gui.py`: native widget integration through selection, mapping, preview, approval, and invalidation. No network or credential store calls.
- `python -m pip install -r requirements-desktop-build.txt` then `python scripts/build-desktop.py`: package on the target operating system. Outputs go to ignored `desktop-dist/`; only code, Python dependencies, and public mapping definitions are included.
- `.github/workflows/desktop.yml` builds Windows and macOS packages using synthetic data only, with no repository secrets. Download packages from the workflow artifacts. These development builds are not publisher-signed or Apple-notarized.

The legacy Sites web application is retained as historical source and is not the desktop runtime. Do not deploy it as the solution to the current requirements. The earlier hosted prototype and its saved credential still require retirement through the owner’s platform controls. No decommissioning or token revocation is claimed by this desktop change.
