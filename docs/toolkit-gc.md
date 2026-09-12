# Catalysis toolkit GC integration

CATALYST will use the Porosoff Group catalysis toolkit as the Rochester GC processing engine. The initial integration imports an existing result bundle. It does not execute the toolkit, duplicate its scientific calculations, or execute workbook formulas.

The reviewed source is [catalysis-toolkit at 28b95977](https://github.com/The-Porosoff-Group/catalysis-toolkit/tree/28b95977c43ee96bb04b5259a9cd766d84fa1975), particularly `modules/gc_processor.py`, `modules/reaction_configs/rwgs.yaml`, `scripts/gc_batch.py`, and `tests/test_gc_regressions.py`. The processor and command-line tool share the GUI calculation path. The original toolkit repository is unchanged by this integration.

## Scientific conventions found in the toolkit

- Parse Shimadzu report XLSX directly from worksheet XML, locate Amount/Peak Area headers, and retain source references. These are component reports, not chromatogram time traces.
- The RWGS reaction configuration assigns the shared `Ar/O2` header to argon. Current aliases include `Argon` and `Argon/O2`. This is a method-specific interpretation and is not a universal equivalence for other entities or oxygen-containing experiments.
- TCD outlet flow is entered argon MFC flow multiplied by the species Amount/argon Amount ratio. Amount is used as a calibrated concentration-like signal. The export does not independently establish whether its numerical scale is percent or ppm. A common scale cancels in the ratio; instrument calibration and detector compatibility still need provenance.
- FID processing can use the CH4 TCD/FID ratio as a bridge when paired channels exist. If no bridge is detected anywhere in the run, FID uses the direct Amount/argon ratio. When bridge mode is enabled but an injection lacks usable paired methane channels, affected FID flows are omitted. Do not silently zero-fill these at import.
- Inlet flows can come from same-file or separate-file bypass averages, anchored by entered argon flow. Bypass omission applies before selecting the requested number of retained points.
- Conversion uses reactant inlet and outlet flows. Selectivity uses each product's carbon flow divided by total counted product carbon flow. The configuration controls included products; duplicate FID/TCD products are excluded from double counting.
- Carbon balance uses counted outlet carbon divided by inlet carbon. The toolkit's processing formula treats missing product entries as zero in this calculation. CATALYST preserves empty imported measurements and records the toolkit method; it does not silently generalize that zero-substitution rule to other processing methods.
- Blank and leak-check rows are excluded from reaction calculations and timing. Initial/final exclusions and the steady-state injection interval remain separate choices. Current `n_reaction` counts nonblank, nonbypass rows before initial/final exclusions, including an unrecognized standby label. CATALYST reports source roles separately and flags standby rows if included in analysis.
- Time on stream comes from the entered injection interval or duration. It is a nominal processing axis, not an acquired timestamp.
- `calculated_ghsv_ml_g_hr` is volume flow per catalyst mass, in mL/(g h). Preserve its dimension; do not relabel it as a bed-volume GHSV in h^-1. The toolkit uses 298 K and 1 atm for its molar-volume convention. Do not assume every submitting instrument's standard-flow calibration uses those conditions.
- C5/C6 area response fallbacks and coeluted-peak splits exist for other reaction configurations. **RWGS disables both.** Their presence in a settings export does not mean those steps were executed.

These statements document code behavior, not independent validation of an instrument calibration or experimental conditions.

## Import one existing revision

Supply four matching artifacts from one processing execution:

1. Original GC report XLSX.
2. `PREFIX_gc_analysis.xlsx`.
3. `PREFIX_gc_summary.csv`.
4. `PREFIX_gc_flows.csv`.

The prefix and source filename must agree with the summary. The embedded raw table must match the supplied original, CSV labels must match raw row order, and row flags/counts and shared literal settings are checked. Numerical CSV values are normalized with source locations. Original bytes are retained unchanged.

```text
python -m catalyst_ingest source.xlsx PREFIX_gc_analysis.xlsx PREFIX_gc_summary.csv PREFIX_gc_flows.csv --entity university-of-rochester --modality reactor --reactor-type packed_bed --toolkit-gc-bundle --store-root /private/catalyst-data --output /private/catalyst-data/revision-preview.json
```

This draft profile supports the inspected RWGS format with same-file bypass. A separate-file bypass recipe or another reaction needs an explicit additional profile. The generated plot can be retained later as a publication attachment; this importer currently accepts the four data artifacts above. Unknown entities and modalities remain unsupported for scientific mapping until representative data and profiles exist.

The companion CSVs provide numerical results even when the analysis workbook has no saved formula values. A missing Excel cache therefore remains an artifact warning rather than erasing the available CSV results. An older workbook with missing Raw Original data is still defective; the importer does not repair it.

## Processing provenance and review

Legacy toolkit exports do not contain a producer Git commit, configuration digest, or input checksums. The profile's inspected commit identifies the code reviewed to define the mapping. It must never be filled in as the original producer commit. The importer sets `processing.executed=false`, retains partner results, and leaves approval/publication disabled.

For backend execution, pin a reviewed toolkit version and store a separate processing-execution manifest containing input and configuration hashes, toolkit version, runtime/dependency versions, all entered parameters, actual selected bypass/reaction/steady-state source rows, detector-bridge decisions, calibration references, numerical outputs, warnings, and timestamps. Reprocessing creates a new immutable revision.

Use the toolkit as an isolated worker behind the authenticated CATALYST backend. Its existing desktop Flask GUI is not the six-entity authorization boundary. CATALYST owns immutable storage, entity access, mapping versions, validation, review approval, and SciSure publication status.

Keep normalized names/units, executed scientific calculations, validation results, reviewer decisions, and publication receipts distinct. A legacy import can be reviewed with a recorded provenance exception or reprocessed under a pinned engine; it must not acquire fabricated historical provenance.

## Verification

Synthetic tests cover missing/mixed artifacts, unknown entities/methods, raw-table and row-order mismatches, invalid flags/nonfinite values, counts, inclusion of nonreaction rows, dimensional conversions, shared-setting disagreements, and Excel serialization precision. A relative tolerance of 1e-14 is used only to classify shared-setting serialization differences. Both source values remain recorded; normalization never uses that tolerance to alter them.

Private local verification of the supplied Rochester report found an identical input digest in the toolkit uploads folder. Both inspected August 27 result sets reproduced with the reviewed current calculation path and stored settings. All numerical flow columns agreed within 1e-10 relative/absolute tolerance, and the reported conversion, selectivity and carbon-balance summaries agreed at their stored precision. This is a present-day reproduction check, not evidence of the historical producer version. Research values and verification artifacts remain outside Git.
