"""Adaptive record definitions: register once, then reference completed records."""
from __future__ import annotations

from .traceability import date_value, identity_lab, lab_id, MODEL_RELATIONS
from catalyst_ingest.readers import InputError

RECORD_TYPES = ('measurement', 'procedure', 'sample', 'synthesis', 'computation')
METADATA_TYPES = ('procedure', 'sample', 'synthesis')
PROCEDURE_TYPES = ('synthesis', 'measurement', 'computation')
WORKFLOW_VERSION = '2'

INTERNAL_FIELDS = {
    'datasetId': 'Record ID', 'submittingLab': 'Submitting lab',
    'acquisitionLab': 'Acquisition / execution lab', 'processingLab': 'Processing lab',
}
WORKFLOW_INTERNAL = INTERNAL_FIELDS
LINK_FIELDS = {'specimenId': 'Registered sample', 'methodId': 'Saved procedure',
    'methodVersion': 'Procedure version'}
EXECUTION_FIELDS = {'acquiredBy': 'Researcher / operator', 'acquiredAt': 'Date performed (YYYY-MM-DD)',
    'runId': 'Run label (optional)', 'notes': 'Run notes / deviations (optional)',
    'localSampleId': 'Sample label in these files (optional)'}
PROCEDURE_FIELDS = {'procedureId': 'Procedure ID', 'procedureName': 'Procedure name',
    'procedureVersion': 'Version', 'procedureType': 'Procedure type',
    'procedureModality': 'Technique / data type', 'procedureText': 'Instructions',
    'procedureReference': 'Existing document / ELN reference (optional)'}
SAMPLE_FIELDS = {'specimenId': 'Sample ID', 'localSampleId': 'Sample label',
    'sampleDescription': 'Sample description / composition', 'materialState': 'State / treatment (optional)',
    'sampleCreatedLab': 'Sample owner lab', 'parentSampleId': 'Parent sample (optional)',
    'notes': 'Sample notes (optional)'}
COMPUTATION_FIELDS = {'specimenId': 'Model ID', 'modelDescription': 'Model description',
    'inputStructure': 'Input structure / geometry reference', 'modelCreatedLab': 'Model owner lab',
    'relatedSampleIds': 'Related registered sample IDs (optional)',
    'modelRelation': 'Relationship to physical samples (optional)',
    'modelLinkEvidence': 'Basis for sample relationship (if linked)'}


def is_workflow(context):
    return isinstance(context, dict) and context.get('workflowVersion') == WORKFLOW_VERSION


def workflow_fields(record_type, modality):
    """Labels for one record type; internal generated identifiers remain identifiable."""
    if record_type == 'procedure':
        return INTERNAL_FIELDS | PROCEDURE_FIELDS
    if record_type == 'sample':
        return INTERNAL_FIELDS | SAMPLE_FIELDS
    if record_type == 'synthesis':
        return INTERNAL_FIELDS | EXECUTION_FIELDS | {
            'specimenId': 'Registered product sample', 'procedureId': 'Saved synthesis procedure',
            'procedureVersion': 'Procedure version', 'synthesisExecutionId': 'Synthesis execution ID',
            'synthesisDeviations': 'Changes from the saved procedure (optional)'}
    if record_type == 'computation':
        return INTERNAL_FIELDS | EXECUTION_FIELDS | COMPUTATION_FIELDS | {
            'methodId': 'Saved computational procedure', 'methodVersion': 'Procedure version',
            'quantityBasis': 'Computed quantity / reference basis', 'processingVersion': 'Processing version evidence'}
    if record_type == 'measurement':
        result = INTERNAL_FIELDS | LINK_FIELDS | EXECUTION_FIELDS
        if modality in ('spectroscopy', 'XRD', 'XAFS/XANES', 'TPR', 'TPD', 'TPO'):
            result |= {'signalUnit': 'Measured signal unit / normalization basis', 'axisUnit': 'Axis source unit (optional)'}
        if modality == 'reactor':
            result |= {'processingVersion': 'Processing version evidence', 'intervalMin': 'Nominal injection interval (min, optional)',
                'identityNote': 'Identity corrections / source-label notes (optional)'}
        return result
    return {}


def workflow_required(context, modality, toolkit=False):
    """Require only the facts this record declares; reused scientific details live in the method."""
    kind = context.get('recordType')
    required = {'datasetId', 'recordType', 'uploadMode'}
    if kind == 'procedure':
        required |= {'procedureId', 'procedureName', 'procedureVersion', 'procedureType', 'procedureModality'}
    elif kind == 'sample':
        required |= {'specimenId', 'localSampleId', 'sampleDescription'}
    elif kind in ('measurement', 'computation', 'synthesis'):
        required |= {'specimenId', 'acquiredBy', 'acquiredAt'}
        if kind == 'synthesis':
            required |= {'procedureId', 'procedureVersion', 'synthesisExecutionId'}
        elif kind != 'measurement' or context.get('methodStatus') != 'not-recorded':
            required |= {'methodId', 'methodVersion'}
        if kind == 'computation':
            required |= {'modelDescription', 'inputStructure'}
            if context.get('relatedSampleIds', '').strip():
                required |= {'modelRelation', 'modelLinkEvidence'}
        if context.get('uploadMode') == 'mapped':
            if modality in ('spectroscopy', 'XRD', 'XAFS/XANES', 'TPR', 'TPD', 'TPO'):
                required.add('signalUnit')
            if kind == 'computation':
                required.add('quantityBasis')
        if toolkit:
            required.add('processingVersion')
    return required


def workflow_context_issues(context, modality, toolkit=False):
    issues = []
    def error(code, message):
        issues.append(dict(code=code, message=message, severity='error'))
    kind = context.get('recordType')
    labels = workflow_fields(kind, modality)
    for key in sorted(workflow_required(context, modality, toolkit)):
        if not context.get(key, '').strip():
            message = labels.get(key, key) + ' is required.'
            if key == 'specimenId' and kind in ('measurement', 'synthesis'):
                message = ('Choose the sample produced, or select + Add new sample.' if kind == 'synthesis'
                    else 'Choose the measured sample, or select + Add new sample.')
            if key == 'methodId' and kind == 'measurement':
                message = 'Choose the procedure used, add a procedure, or mark the method as not recorded for historical data.'
            if key == 'procedureId' and kind == 'synthesis':
                message = 'Choose the synthesis procedure used, or select + Add new procedure.'
            error('CONTEXT_' + key, message)
    if kind not in RECORD_TYPES:
        error('RECORD_TYPE', 'Choose a supported record type.')
    method_status = context.get('methodStatus', '')
    if method_status not in ('', 'recorded', 'not-recorded') or (method_status == 'not-recorded' and kind != 'measurement'):
        error('METHOD_STATUS', 'Method not recorded is available only for historical measurement data.')
    if method_status == 'not-recorded' and any(context.get(key, '').strip() for key in
            ('methodId', 'methodVersion', 'procedureSourceRevisionId', 'procedureSourceRevisionSha256')):
        error('METHOD_STATUS', 'Clear the selected method before marking it as not recorded.')
    inventory_mode = context.get('inventoryMode', '')
    if inventory_mode not in ('', 'records', 'native') or (inventory_mode == 'native' and kind not in ('sample', 'measurement', 'synthesis')):
        error('INVENTORY_MODE', 'Inventory registration applies to physical samples, measurements and synthesis executions.')
    native_keys = ('nativeSampleId', 'nativeSampleTypeId', 'nativeSampleSnapshotSha256', 'nativeSampleTenant')
    if any(context.get(key) for key in native_keys):
        if kind != 'sample' or inventory_mode != 'native' or not all(context.get(key) for key in native_keys):
            error('NATIVE_SAMPLE_REFERENCE', 'Choose an existing inventory sample through the library so its complete reference is recorded.')
        else:
            import re
            from .scisure import remote_id, tenant_origin, SciSureError
            try:
                remote_id(context['nativeSampleId']); remote_id(context['nativeSampleTypeId'])
                if tenant_origin(context['nativeSampleTenant']) != context['nativeSampleTenant'] or not re.fullmatch(r'[0-9a-f]{64}', context['nativeSampleSnapshotSha256']):
                    raise ValueError
            except (SciSureError, ValueError):
                error('NATIVE_SAMPLE_REFERENCE', 'The inventory sample reference is invalid. Refresh the library and select the sample again.')
    mode = context.get('uploadMode')
    if mode not in ('originals', 'mapped', 'metadata') or (mode == 'metadata' and kind not in METADATA_TYPES):
        error('UPLOAD_MODE', 'Choose original files or mapped data; metadata-only records are limited to samples, procedures, and synthesis executions.')
    if kind in METADATA_TYPES and mode == 'mapped':
        error('UPLOAD_MODE', 'Register this record using its details and optional supporting files.')
    if kind == 'computation' and modality != 'computational':
        error('RECORD_MODALITY', 'Computational results require the computational data type.')
    if kind == 'synthesis' and modality != 'synthesis':
        error('RECORD_MODALITY', 'Synthesis executions require the synthesis data type.')
    if kind == 'measurement' and modality in ('computational', 'synthesis'):
        error('RECORD_MODALITY', 'Choose a measurement technique for a measurement record.')
    if kind in ('measurement', 'computation', 'synthesis') and modality in ('sample', 'procedure'):
        error('RECORD_MODALITY', 'Choose a scientific technique for acquired data or an execution.')
    if kind in ('sample', 'procedure') and modality != kind:
        error('RECORD_MODALITY', 'Use the matching registration data type for samples and procedures.')
    if kind == 'procedure':
        procedure_type = context.get('procedureType')
        procedure_modality = context.get('procedureModality')
        if procedure_type not in PROCEDURE_TYPES:
            error('PROCEDURE_TYPE', 'Choose synthesis, measurement, or computation for the procedure.')
        from .model import MODALITIES
        if procedure_modality not in MODALITIES or procedure_modality in METADATA_TYPES[0:2]:
            error('PROCEDURE_MODALITY', 'Choose a supported technique for the procedure.')
        elif ((procedure_type == 'synthesis') != (procedure_modality == 'synthesis')
                or (procedure_type == 'computation') != (procedure_modality == 'computational')):
            error('PROCEDURE_MODALITY', 'The procedure type must match its technique.')
    if context.get('acquiredAt'):
        try:
            date_value(context['acquiredAt'])
        except ValueError:
            error('DATE_INVALID', 'Use a date in YYYY-MM-DD format.')
    # Optional entered numbers retain the legacy physical bounds.
    from .model import number
    from decimal import Decimal
    for key in ('temperatureC', 'pressureKpaAbs', 'catalystMassMg', 'intervalMin'):
        if context.get(key):
            try:
                value = number(context[key])
                if value < Decimal('-273.15') if key == 'temperatureC' else value <= 0:
                    raise InputError('Outside the physical range.')
            except InputError:
                error('CONTEXT_INVALID_' + key, key + ' must contain a number in the physical range.')
    return issues


def workflow_mapping_issues(context, modality, rules):
    """Recheck adaptive mapped-data requirements when loading an approved stored review."""
    issues = []
    def error(code, message):
        issues.append(dict(code=code, message=message, severity='error'))
    targets = {rule['target'] for rule in rules}
    axes = {'spectroscopy': ('wavenumber_cm_inverse', 'wavelength_nm', 'energy_eV'),
        'XRD': ('two_theta_deg', 'scattering_q_A_inverse'),
        'XAFS/XANES': ('energy_eV', 'photoelectron_k_A_inverse', 'radial_distance_A'),
        **{technique: ('temperature_K', 'time_s') for technique in ('TPR', 'TPD', 'TPO')}}
    if modality in axes:
        if not targets.intersection(axes[modality]) or 'signal' not in targets:
            error('SPECTRAL_MAPPING', 'Map the independent axis and measured signal for this data type.')
        if context.get('axisUnit') and any(rule['unit'] != context['axisUnit'] for rule in rules if rule['target'] in axes[modality]):
            error('AXIS_UNIT_CONFLICT', 'The axis source unit must match the selected mapping.')
    if modality == 'CO uptake' and 'uptake_mol_g' not in targets:
        error('UPTAKE_MAPPING', 'Map uptake with its mass-normalized source unit.')
    if modality == 'computational' and not ('computed_energy_eV' in targets or
            {'computed_quantity', 'computed_value', 'computed_unit'}.issubset(targets)):
        error('COMPUTATION_MAPPING', 'Map computed energy or an explicit quantity, value, and unit.')
    return issues


def build_workflow_trace(entity, modality, context, artifacts=()):
    """Build declarations without inventing scientific facts or duplicating referenced records."""
    issues = []
    def error(code, message):
        issues.append(dict(code=code, message=message, severity='error'))
    def owner(key, default):
        try:
            return lab_id(context.get(key) or default)
        except InputError:
            error('LAB_' + key, 'Choose a registered consortium laboratory.')
            return None
    def identifier(key, kind, expected_lab=None):
        value = context.get(key, '')
        # Required-field validation already gives an actionable message for a
        # missing selection. Internal ID syntax is relevant only for a value.
        if not value.strip():
            return value
        try:
            actual_lab = identity_lab(value, kind)
            if expected_lab and actual_lab != expected_lab:
                error('ID_LAB_' + key, 'This ID belongs to a different creating lab: ' + key + '.')
        except InputError as exc:
            error('ID_' + key, str(exc))
        return value
    source = lab_id(entity)
    acquisition = owner('acquisitionLab', source)
    kind = context.get('recordType')
    dataset = dict(id=identifier('datasetId', 'DS', acquisition), kind=kind, modality=modality,
        acquisition_lab=acquisition, local_id=context.get('runId', ''),
        acquired_by=context.get('acquiredBy', ''), acquired_at=context.get('acquiredAt', ''),
        subject_id='', method=None, notes=context.get('notes', ''))
    trace = dict(schema_version='catalyst-traceability/2', lab_registry_version=1, record_type=kind,
        source_lab=source, submitting_lab=owner('submittingLab', source),
        processing_lab=owner('processingLab', source), dataset=dataset, aliases=[], custody_evidence=None)
    if kind == 'procedure':
        pid = identifier('procedureId', 'PRC')
        try: procedure_owner = identity_lab(pid, 'PRC')
        except InputError: procedure_owner = None
        trace['procedure'] = dict(id=pid, version=context.get('procedureVersion', ''),
            name=context.get('procedureName', ''), type=context.get('procedureType', ''),
            modality=context.get('procedureModality', ''), instructions=context.get('procedureText', ''),
            reference=context.get('procedureReference', ''), owner_lab=procedure_owner,
            artifact_sha256s=[a['sha256'] for a in artifacts])
        if not (context.get('procedureText') or context.get('procedureReference') or artifacts):
            error('PROCEDURE_CONTENT', 'Add procedure instructions, a document reference, or an attached procedure file.')
        dataset['subject_id'] = pid
    elif kind == 'sample':
        creator = owner('sampleCreatedLab', source)
        sid = identifier('specimenId', 'SMP', creator)
        parent = context.get('parentSampleId', '')
        if parent:
            identifier('parentSampleId', 'SMP')
            if parent == sid:
                error('SELF_PARENT', 'A sample cannot be its own parent.')
        trace['material'] = dict(id=sid, creator_lab=creator, kind='registered sample',
            description=context.get('sampleDescription', ''), state=context.get('materialState', ''),
            parent_sample_id=parent or None)
        dataset['subject_id'] = sid
    elif kind == 'computation':
        creator = owner('modelCreatedLab', source)
        mid = identifier('specimenId', 'MDL', creator)
        linked = [v.strip() for v in context.get('relatedSampleIds', '').split(',') if v.strip()]
        if len(linked) > 20 or len(set(linked)) != len(linked):
            error('MODEL_LINKS', 'Use at most 20 distinct related sample IDs.')
        for sid in linked:
            try: identity_lab(sid, 'SMP')
            except InputError as exc: error('MODEL_SAMPLE_ID', str(exc))
        relation = context.get('modelRelation') or 'no physical link'
        if relation not in MODEL_RELATIONS or (relation == 'no physical link') != (not linked):
            error('MODEL_RELATION', 'Choose a physical relationship when linking samples; otherwise use no physical link.')
        trace['model'] = dict(id=mid, creator_lab=creator, description=context.get('modelDescription', ''),
            input_structure=context.get('inputStructure', ''), physical_relation=relation,
            related_sample_ids=linked, link_evidence=context.get('modelLinkEvidence', ''))
        dataset['subject_id'] = mid
    elif kind in ('synthesis', 'measurement'):
        sid = identifier('specimenId', 'SMP')
        dataset['subject_id'] = sid
        trace['sample_ref'] = dict(id=sid)
        for source_key, target_key in (('sampleSourceRevisionId', 'source_revision_id'),
                ('sampleSourceRevisionSha256', 'source_revision_sha256')):
            if context.get(source_key):
                trace['sample_ref'][target_key] = context[source_key]
    if kind == 'measurement' and context.get('methodStatus') == 'not-recorded':
        dataset['method_status'] = 'not-recorded'
    elif kind in ('measurement', 'computation', 'synthesis'):
        procedure_key = 'procedureId' if kind == 'synthesis' else 'methodId'
        version_key = 'procedureVersion' if kind == 'synthesis' else 'methodVersion'
        # Legacy synthesis procedure IDs may be local strings. Completeness is checked against saved definitions.
        dataset['method'] = dict(id=context.get(procedure_key, ''), version=context.get(version_key, ''))
        for source_key, target_key in (('procedureSourceRevisionId', 'source_revision_id'),
                ('procedureSourceRevisionSha256', 'source_revision_sha256')):
            if context.get(source_key):
                dataset['method'][target_key] = context[source_key]
    if kind == 'synthesis':
        trace['synthesis_execution'] = dict(id=identifier('synthesisExecutionId', 'SYN', acquisition),
            sample_id=dataset['subject_id'], procedure=dict(dataset['method']),
            performed_at=dataset['acquired_at'], performed_by=dataset['acquired_by'], lab=acquisition,
            deviations=context.get('synthesisDeviations', ''), notes=context.get('notes', ''))
    if kind != 'procedure' and context.get('localSampleId'):
        trace['aliases'].append(dict(lab=source, local_label=context['localSampleId'], canonical_id=dataset['subject_id']))
    return trace, issues
