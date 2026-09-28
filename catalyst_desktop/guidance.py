"""Plain-language field guidance and required-field state for the desktop form.

This module has no UI, network, or persistence side effects. Required-field state
describes presence requirements only; the review validators still check values,
dates, identifiers, scientific ranges, and consistency with saved records.
"""
from __future__ import annotations

from .model import MODALITY_CONTEXT
from .traceability import (COMPUTATIONAL_CONTEXT, MODEL_RELATIONS,
    PHYSICAL_CONTEXT, TRACE_COMMON, lab_id)
from catalyst_ingest.readers import InputError


_CONTEXT_HELP = {
    'specimenId': 'The permanent CATALYST sample ID for the material in this submission. Choose an existing sample from Samples & data, or add a new sample; the app generates its internal ID. This connects measurements across laboratories without confusing reused local labels.',
    'runId': 'The run or calculation label used in your local records. It connects this dataset to the instrument export, notebook entry, or calculation job.',
    'acquiredBy': 'The person who collected the measurements or ran the calculation. This records who can explain how the data were produced.',
    'acquiredAt': 'The date the measurements were collected or the calculation was performed, in YYYY-MM-DD format. It places this dataset in the sample history and lets the review check synthesis and transfer dates.',
    'processingVersion': 'The processing method and version that produced these results, with a record or reference where possible. This is required for the Rochester GC toolkit import so another researcher can identify the processing behind the imported values.',
    'identityNote': 'Optional explanation of corrected or inconsistent sample names in the source files. This preserves the reason for identity corrections; it does not automatically change labels or merge samples.',
    'datasetId': 'The permanent CATALYST ID for this dataset, generated for the measurement or calculation lab. It distinguishes this dataset from other runs on the same sample; reuse it when revising this dataset.',
    'submittingLab': 'The consortium lab submitting these records. It records submission responsibility and may differ from the lab that acquired or processed the data.',
    'acquisitionLab': 'The consortium lab that measured the sample or ran the calculation. It assigns the dataset to its producing lab and determines whether sample handoff details are needed.',
    'processingLab': 'The consortium lab that processed the data. This preserves responsibility for analysis when processing and acquisition happened at different labs.',
    'localSampleId': 'The sample or model label exactly as it appears in these source files, including capitalization and leading zeros. It links local names to the permanent CATALYST ID and lets the review detect data from a different sample.',
    'methodId': 'The identifier of the measurement or calculation method used for this dataset. It connects the results to a defined method so others can interpret or repeat the work.',
    'methodVersion': 'The version of that measurement or calculation method. It distinguishes results produced before and after a method change.',
    'batchId': 'The permanent CATALYST synthesis batch ID. Reuse the existing batch ID for samples from the same batch so their shared synthesis history stays connected.',
    'originLab': 'The consortium lab that synthesized the batch. This identifies its origin even after samples move to another lab; existing batch and synthesis execution IDs must belong to this lab.',
    'localBatchId': 'The batch label used by the synthesis lab in its own records. It makes the permanent batch ID traceable back to the original notebook or container labels.',
    'procedureId': 'The identifier of the shared synthesis procedure. It links batches made at different labs to the same intended recipe or protocol.',
    'procedureVersion': 'The version of the shared synthesis procedure used for this batch. It distinguishes recipe changes and must agree with the procedure document reference.',
    'procedureReference': 'A document link, SciSure protocol reference, or other retrievable reference for this exact procedure version. It lets another lab find the intended synthesis instructions.',
    'synthesisExecutionId': 'The permanent CATALYST ID for the actual synthesis execution that made this batch. It distinguishes one execution from other batches made using the same procedure.',
    'synthesizedAt': 'The date the batch was synthesized, in YYYY-MM-DD format. It establishes the start of the material history and cannot be later than acquisition or receipt.',
    'synthesisRecord': 'A reference to the actual synthesis record or electronic lab notebook entry for this batch. It preserves what was done, beyond the intended shared procedure.',
    'synthesisDeviations': 'Describe differences between the actual synthesis and the shared procedure, or enter none if there were none. An explicit answer distinguishes a confirmed procedure match from missing information.',
    'sampleCreatedLab': 'The consortium lab that created this particular sample, aliquot, or treated material. Its permanent sample ID belongs to this lab; a different acquisition lab requires handoff evidence.',
    'materialKind': 'Choose batch material for the original sample, aliquot for a portion of a parent sample, or treated material for a changed parent sample. This determines whether a parent sample ID is required and preserves the sample lineage.',
    'parentSampleId': 'The permanent CATALYST ID of the sample used to make this aliquot or treated material. Required for derivatives; leave blank for batch material. The parent must already be registered in the group and belong to the same batch.',
    'materialState': 'The state of this sample and any treatment record, such as as synthesized, reduced, or dried with a reference. It tells another researcher which material condition the measurements describe.',
    'custodyFromLab': 'The consortium lab that sent the sample or parent sample to the acquisition lab. Required when the sample was created elsewhere or any handoff detail is entered, so its movement can be traced.',
    'custodySampleId': 'The permanent ID of the sample that was transferred. Optional: a blank value means this submission’s sample. Supply its parent ID when recording a parent transfer before creating a derivative at the receiving lab.',
    'custodyRecord': 'A shipment, receipt, or handoff record reference for the transfer. Required with a cross-lab sample or any handoff detail so the movement has supporting evidence.',
    'receivedAt': 'The date the acquisition lab received the transferred sample or parent, in YYYY-MM-DD format. Required with a transfer; it must be on or after synthesis and on or before acquisition.',
    'modelCreatedLab': 'The consortium lab that created the computational model. Its permanent model ID belongs to this lab, preserving the model’s origin when calculations are shared.',
    'modelDescription': 'Describe the model’s composition, structure, and representation. This identifies what was modeled so computed results can be interpreted correctly.',
    'modelRelation': 'Choose how the model relates to physical samples, or no physical link for a standalone model. A physical relationship requires related sample IDs; this avoids treating an assumed model as a measured sample.',
    'relatedSampleIds': 'Comma-separated permanent CATALYST sample IDs represented by, used to derive, or compared with the model. Required for a physical relationship; use at most 20 distinct samples already registered in the group.',
    'modelLinkEvidence': 'Explain or reference the evidence connecting the model to the listed physical samples. Required when related sample IDs are supplied so others can judge the stated relationship.',
    'technique': 'The spectroscopy technique used, such as infrared or UV-visible spectroscopy. It identifies the measurement approach needed to interpret the recorded axis and signal.',
    'imageContext': 'Describe what the image shows, the acquisition instrument, and relevant settings or conditions. This gives the image enough context to be interpreted beyond its filename.',
    'scaleReference': 'Describe the image scale or calibration reference, or explicitly state not quantitative. This tells viewers whether distances or sizes can be measured from the image.',
    'reactorType': 'Describe the reactor configuration used for the experiment. Reactor design affects transport and measured performance, so it is needed to compare results.',
    'temperatureC': 'The reactor temperature in degrees Celsius, entered as a number without unit text. It records a key reaction condition and allows the review to check the physical range.',
    'pressureKpaAbs': 'The reactor pressure in absolute kilopascals, entered as a positive number without unit text. Absolute pressure avoids ambiguity with gauge pressure and supports meaningful comparisons.',
    'catalystMassMg': 'The amount of catalyst or sample used, in milligrams as a positive number without unit text. This records the amount tested and the basis for comparing measurements.',
    'intervalMin': 'The nominal interval between GC injections in minutes, entered as a positive number. For toolkit imports it defines a review time axis from the included reaction rows while preserving the original axis.',
    'flowBasis': 'State the gas flow and its reference temperature, pressure, and composition basis as applicable. Flow values depend on those conditions, so they must be explicit for comparisons.',
    'calibration': 'A calibration record identifier, reference, or version for this measurement. It makes the relationship between instrument signals and reported results traceable.',
    'scale': 'State the experimental scale, such as laboratory or pilot, with relevant details. It gives the operating context needed to compare syntheses or reactor results.',
    'synthesisMethod': 'The synthesis method or protocol used, with a short description or reference. It gives scientific context for how this material was prepared.',
    'axisUnit': 'The source unit of the independent axis, such as eV or degree (2theta). It must match the unit chosen in the column mapping so the review does not apply an inconsistent conversion.',
    'signalUnit': 'The measured signal unit or normalization basis, such as counts or normalized absorption. The generic signal mapping preserves values as recorded, so this field explains what those numbers mean.',
    'radiation': 'The X-ray radiation source and wavelength or their documented reference. These determine how diffraction positions relate to the material structure.',
    'geometry': 'The diffraction measurement geometry. It records how the sample and detector were arranged, which is needed to interpret intensities and compare measurements.',
    'absorberEdge': 'The absorbing element and absorption edge measured, such as an element’s K edge. This identifies which element and transition the X-ray absorption data describe.',
    'detectionMode': 'The X-ray absorption detection mode, such as transmission or fluorescence. It gives the signal’s measurement basis and helps others assess or compare the spectrum.',
    'energyReference': 'The reference material or method used to align the energy axis. This explains how edge positions can be compared between measurements.',
    'pretreatment': 'Describe or reference the sample preparation before this measurement. It records the starting material state because treatment can change reduction, desorption, oxidation, or uptake behavior.',
    'gasComposition': 'The measurement gas mixture and carrier, including concentrations or a recipe reference. Gas composition affects the observed response and is needed to repeat the experiment.',
    'rampProgram': 'The temperature and time program, including ramps, holds, and their units or a method reference. It explains the conditions underlying the signal along the measured axis.',
    'adsorptionTemperature': 'The adsorption temperature with its unit. It records the condition at which CO uptake was measured so values can be interpreted and compared.',
    'uptakeBasis': 'State the sample mass basis and gas reference conditions used for CO uptake. They define what a mass-normalized uptake value represents.',
    'stoichiometry': 'State the assumed CO-to-site ratio, or explicitly enter not calculated when no site quantity is derived. This prevents an unstated adsorption assumption from being mistaken for a measured site count.',
    'softwareVersion': 'The calculation program name and version. It identifies the software implementation needed to reproduce the computational results.',
    'calculationMethod': 'The calculation method or theory level. It defines the scientific approximation behind the reported result.',
    'inputStructure': 'A reference to the exact input structure or geometry. It identifies the atomic or molecular arrangement used for the calculation.',
    'parameters': 'The calculation parameters or a reference to the input configuration. It preserves settings that can change the result even when the program and method are the same.',
    'environment': 'A record or reference for the software environment and dependencies. It helps another researcher recreate the conditions in which the calculation ran.',
    'convergence': 'Evidence or a record that the calculation completed and met the stated convergence criteria. It distinguishes an accepted result from an unfinished or unconverged calculation.',
    'quantityBasis': 'State the computed quantity, its unit, and its reference or normalization basis. This makes numerical results interpretable, particularly energies whose zero or per-configuration basis can differ.',
}

_CONTROL_HELP = {
    'title': 'A short, descriptive title for this submission, up to 200 characters. It helps you and collaborators recognize the saved review.',
    'entity': 'The consortium laboratory whose source files and local labels are being submitted. It identifies the source of the records and scopes reusable column mappings.',
    'modality': 'The kind of data being uploaded. This selects the scientific context and mapping checks needed to interpret the data correctly.',
    'files': 'Select one to six original files, each up to 20 MiB and up to 40 MiB combined. Original bytes are preserved with the review so results can be traced back to the submitted evidence.',
    'supporting_files': 'Add native instrument files, images, or other supporting originals for this sample and dataset. They are preserved with the review; only the selected table is standardized in ordinary table mode.',
    'toolkit': 'Use the dedicated Rochester GC toolkit bundle mapping for a supported toolkit export. It preserves imported processing evidence and requires a processing method or version; no custom column mapping is needed.',
    'raw_only': 'Preserve the original files with their sample and scientific context. Choose this for images or native files when no table conversion is needed; the sample and context requirements still apply.',
    'source_choice': 'The file whose table will be standardized. Choose a CSV, XLSX, or tabular JSON file; additional originals remain supporting evidence.',
    'sheet_choice': 'The worksheet or table containing the data to standardize. It identifies which part of the selected file the mapping will read.',
    'header_row': 'The one-based row number containing the column labels. Each label must be unique and nonempty so the app can map the correct columns.',
    'source_version': 'The source format or instrument export version, such as the version named by your export software. It distinguishes input layouts and helps prevent reuse of a mapping on an incompatible export.',
    'profile_name': 'A descriptive name for this column mapping. It makes the mapping recognizable when you reuse or revise a saved review.',
    'profile_version': 'A positive whole-number version for this mapping. Increment it when changing how columns are interpreted so the saved review records which mapping was used.',
    'mapping_target': 'The standard CATALYST field represented by this source column, or leave unmapped to omit it from the standardized table. This states the meaning of the values; each source column and target can be used only once.',
    'mapping_unit': 'The unit actually used in this source column. The app uses this choice to convert numbers to standard units, so select the source unit rather than the desired output unit.',
    'mapping_aliases': 'Optional exact source-label to standard-label replacements for a text column. Enter a JSON object, for example {"CO2": "carbon dioxide"}. This records deliberate name changes without guessing or merging similar labels.',
    'tenant': 'Your laboratory’s SciSure / eLabNext server origin, including https:// and without a sign-in path. Use the same server on which you created the API token; a sandbox token will not connect to another installation.',
    'token': 'Sign in to the chosen SciSure / eLabNext server, then open Apps & Connections → Manage Authentication to create an API token. For university SSO, SAML or two-factor sign-in, create the token in the product after signing in. Paste only the token here; CATALYST adds the Authorization header. Access follows the token account’s permissions.',
    'remember': 'Optionally save this server’s API token in your operating system credential store. This allows reconnecting without entering the token again.',
    'experiment': 'The SciSure experiment to read from or upload into. Verify the selected destination before sending an approved review so the files reach the intended record. Successful reads do not prove permission to create sections, upload files, or change inventory.',
    'inspect_sample': 'An optional existing native SciSure sample ID for an administrator’s read-only setup check. It is separate from a CATALYST sample ID. Reading it does not establish permission to create or edit samples.',
    'inspect_protocol': 'An optional SciSure protocol version ID for an administrator’s read-only setup check. Reading that exact version does not establish permission to edit or publish protocols.',
    'reviewer': 'The name of the person reviewing this exact revision. It records a self-reported reviewer; SciSure actions still use the connected token account’s identity.',
    'review_note': 'A note describing your review and how you assessed any warnings. It records the reasoning behind approval and is required before sending a review.',
    'acknowledge': 'Confirm that you reviewed this exact revision and its warnings. It is required for approval; changes to files, context, or mapping require a new preview and approval.',
    'catalog_query': 'Search saved sample and model records by permanent ID, local label, laboratory, or related details. This helps you reuse the correct identity and find its datasets across experiments.',
}

_MAPPING_HELP = {
    'specimen_id': 'A sample label in the source table. It must match the declared permanent sample ID or exact source-lab label, so rows cannot silently refer to another sample.',
    'species': 'The chemical species associated with a row. This identifies what was measured; use explicit text aliases for any intended name changes.',
    'time_s': 'Elapsed time for a data point. The source unit is converted to seconds so records can share a consistent time axis.',
    'temperature_K': 'Measurement temperature. The source unit is converted to kelvin so temperatures can be compared on an absolute scale.',
    'pressure_Pa_abs': 'Absolute measurement pressure. The source unit is converted to pascals; gauge pressure must first be resolved to avoid an ambiguous pressure basis.',
    'mass_g': 'The measured material mass. The source unit is converted to grams so mass values can be compared consistently.',
    'flow_mL_min': 'Volumetric flow rate. The source unit is converted to milliliters per minute; the context must explain the relevant gas reference conditions.',
    'conversion_fraction': 'The fraction of reactant converted. Percent values are converted to a fraction from zero to one for consistent comparisons.',
    'selectivity_fraction': 'The reported selectivity. Percent values are converted to a fraction from zero to one; the method must explain its scientific basis.',
    'loading_fraction': 'The reported material loading. Percent values are converted to a fraction from zero to one; the method must explain the loading basis.',
    'wavenumber_cm_inverse': 'The spectral wavenumber axis in inverse centimeters. It places each signal value at its measured spectral position.',
    'wavelength_nm': 'The spectral wavelength axis. The source unit is converted to nanometers to pair signals with a consistent wavelength scale.',
    'energy_eV': 'The photon-energy axis. The source unit is converted to electronvolts to preserve the energy position of each signal value.',
    'signal': 'The measured detector or spectral signal, preserved as recorded. Specify its unit or normalization in the scientific context so the values have a clear meaning.',
    'precursor_name': 'The precursor material named in a synthesis record. It identifies a starting material and preserves explicit naming decisions.',
    'support_name': 'The support material named in a synthesis record. It identifies the material carrying the active catalyst component.',
    'two_theta_deg': 'The diffraction 2-theta angle in degrees. It identifies the XRD axis and must lie between zero and 180 degrees.',
    'scattering_q_A_inverse': 'The nonnegative scattering-vector magnitude q in inverse angstroms. It records the diffraction axis on a reciprocal-space scale.',
    'photoelectron_k_A_inverse': 'The nonnegative photoelectron wave number k in inverse angstroms. It identifies the X-ray absorption data axis after conversion to k space.',
    'radial_distance_A': 'The nonnegative radial-distance axis in angstroms. It identifies the distance scale of the reported X-ray absorption transform.',
    'uptake_mol_g': 'Mass-normalized CO uptake. The source unit is converted to moles per gram; context records the gas conditions, mass basis, and any site-count assumption.',
    'model_id': 'A model label in the source table. It must match the declared permanent model ID or exact source-lab label so computed rows remain tied to the correct model.',
    'configuration_id': 'The identifier of the computed configuration within this model or dataset. It distinguishes structures or configurations that have different computed results.',
    'computed_energy_eV': 'Computed energy in electronvolts per configuration. Signed values are allowed; the context must state the energy reference so the numbers can be interpreted.',
    'computed_quantity': 'The name of the computed property. Map it together with computed value and unit when the result is not an energy per configuration.',
    'computed_value': 'The numerical value of the computed property, preserved as recorded. Map the quantity and unit as well so this number has a defined meaning.',
    'computed_unit': 'The unit of the computed property. Map it together with quantity and value to preserve the calculation’s reported measurement basis.',
}


def field_help(key, modality='reactor'):
    """Return meaning and purpose for a context key, UI control, or mapped field.

    Unknown keys return an empty string so callers do not present invented help.
    """
    if key == 'specimenId' and modality == 'computational':
        return ('The permanent CATALYST model ID for this submission. Reuse an existing model from Samples & data, '
            'or generate an ID for a new model. It connects calculations to the same model without confusing reused local labels.')
    if key == 'technique' and modality == 'imaging':
        return ('The image type or technique, such as photograph, SEM, or TEM. It identifies how the image was produced '
            'so another researcher can interpret what it shows.')
    return _CONTEXT_HELP.get(key, _CONTROL_HELP.get(key, _MAPPING_HELP.get(key, '')))


def _lab_or_none(value):
    try:
        return lab_id(value)
    except InputError:
        return None


def required_context_keys(modality, context, toolkit=False):
    """Return fields that must be nonempty for the current review context.

    Inputs use the same string-valued context contract as ``build_preview``.
    Adaptive records delegate to their workflow validator; historical reviews
    retain their original field requirements below.
    Lab aliases are resolved before deciding whether handoff evidence is needed.
    The transfer sample ID is optional because the validator defaults it to the
    current sample. A model link may additionally fail catalog validation even
    when every field is populated.
    """
    from .workflow import is_workflow, workflow_required
    context = {key: value.strip() for key, value in context.items()}
    if is_workflow(context):
        return frozenset(workflow_required(context, modality, toolkit))
    required = {'specimenId', 'runId', 'acquiredBy', 'acquiredAt'} | set(TRACE_COMMON) | set(MODALITY_CONTEXT[modality])
    if toolkit:
        required.add('processingVersion')
    if modality == 'computational':
        required.update(set(COMPUTATIONAL_CONTEXT) - {'relatedSampleIds', 'modelLinkEvidence'})
        if context.get('modelRelation') in MODEL_RELATIONS[1:]:
            required.add('relatedSampleIds')
        if any(item.strip() for item in context.get('relatedSampleIds', '').split(',')):
            required.add('modelLinkEvidence')
    else:
        custody = {'custodyFromLab', 'custodySampleId', 'custodyRecord', 'receivedAt'}
        required.update(set(PHYSICAL_CONTEXT) - custody - {'parentSampleId'})
        # First ask for a relationship; a blank/invalid choice does not yet
        # establish whether a parent is needed, even if validation flags it.
        if context.get('materialKind') in ('aliquot', 'treated material'):
            required.add('parentSampleId')
        transferred = any(context.get(key) for key in custody)
        if _lab_or_none(context.get('sampleCreatedLab')) != _lab_or_none(context.get('acquisitionLab')) or transferred:
            required.update(custody - {'custodySampleId'})
    return frozenset(required)
