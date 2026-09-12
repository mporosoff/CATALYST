"""Pure, deterministic review components. No network or persistent file writes."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
import uuid

from catalyst_ingest.readers import InputError, read_artifact, coordinate_parts
from catalyst_ingest.toolkit import preview_toolkit_bundle
from . import __version__
from .traceability import lab_id, build_traceability, attach_subject

MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 40 * 1024 * 1024
MAX_CELLS = 200_000
MODALITIES = ('reactor', 'synthesis', 'spectroscopy', 'XRD', 'XAFS/XANES', 'TPR', 'TPD', 'TPO', 'CO uptake', 'computational', 'imaging')
FIELDS = {
    'specimen_id': ('text', ('text',)), 'species': ('text', ('text',)),
    'time_s': ('number', ('s', 'min', 'h')),
    'temperature_K': ('number', ('K', 'degC')),
    'pressure_Pa_abs': ('number', ('Pa absolute', 'kPa absolute', 'bar absolute', 'atm absolute')),
    'mass_g': ('number', ('g', 'mg', 'kg')),
    'flow_mL_min': ('number', ('mL/min', 'L/min')),
    'conversion_fraction': ('number', ('fraction', '%')),
    'selectivity_fraction': ('number', ('fraction', '%')),
    'loading_fraction': ('number', ('fraction', '%')),
    'wavenumber_cm_inverse': ('number', ('1/cm',)),
    'wavelength_nm': ('number', ('nm', 'um')), 'energy_eV': ('number', ('eV', 'keV')),
    'signal': ('number', ('as recorded',)),
    'precursor_name': ('text', ('text',)), 'support_name': ('text', ('text',)),
    'two_theta_deg': ('number', ('degree (2theta)',)),
    'scattering_q_A_inverse': ('number', ('1/angstrom (q)',)),
    'photoelectron_k_A_inverse': ('number', ('1/angstrom (k)',)),
    'radial_distance_A': ('number', ('angstrom',)),
    'uptake_mol_g': ('number', ('mol/g', 'mmol/g', 'umol/g')),
    'model_id': ('text', ('text',)), 'configuration_id': ('text', ('text',)),
    'computed_energy_eV': ('number', ('eV per configuration',)),
    'computed_quantity': ('text', ('text',)), 'computed_value': ('number', ('as recorded',)),
    'computed_unit': ('text', ('text',)),
}
CONVERSIONS = {u: ('1', '0') for _, units in FIELDS.values() for u in units}
CONVERSIONS.update({'min': ('60', '0'), 'h': ('3600', '0'), 'degC': ('1', '273.15'),
    'kPa absolute': ('1000', '0'), 'bar absolute': ('100000', '0'),
    'atm absolute': ('101325', '0'), 'mg': ('0.001', '0'), 'kg': ('1000', '0'),
    'L/min': ('1000', '0'), '%': ('0.01', '0'), 'um': ('1000', '0'), 'keV': ('1000', '0'),
    'mmol/g': ('0.001', '0'), 'umol/g': ('0.000001', '0')})
COMMON_CONTEXT = {
    'specimenId': 'Canonical sample / model ID', 'runId': 'Local run / calculation label',
    'acquiredBy': 'Person who acquired / calculated the data', 'acquiredAt': 'Acquisition date (YYYY-MM-DD)',
    'processingVersion': 'Processing method / version evidence',
    'identityNote': 'Identity corrections / source-label notes',
}
MODALITY_CONTEXT = {
    'imaging': {'technique': 'Image type / technique (photo, SEM, TEM, other)',
        'imageContext': 'What is shown / acquisition instrument and conditions',
        'scaleReference': 'Scale / calibration reference (or explicitly not quantitative)'},
    'reactor': {'reactorType': 'Reactor configuration', 'temperatureC': 'Temperature (°C)',
        'pressureKpaAbs': 'Absolute pressure (kPa)', 'catalystMassMg': 'Catalyst mass (mg)',
        'intervalMin': 'Injection interval (min)', 'flowBasis': 'Flow reference conditions / composition basis',
        'calibration': 'Calibration record / version', 'scale': 'Scale (laboratory / pilot / other)'},
    'synthesis': {'synthesisMethod': 'Synthesis method / protocol', 'scale': 'Scale (laboratory / pilot / other)'},
    'spectroscopy': {'technique': 'Spectroscopy technique', 'axisUnit': 'Independent-axis unit',
        'signalUnit': 'Measured signal unit', 'calibration': 'Calibration record / version'},
    'XRD': {'axisUnit': 'Independent-axis source unit', 'signalUnit': 'Intensity unit / normalization basis',
        'radiation': 'Radiation source / wavelength', 'geometry': 'Measurement geometry', 'calibration': 'Calibration record / version'},
    'XAFS/XANES': {'axisUnit': 'Independent-axis source unit', 'signalUnit': 'Signal unit / normalization basis',
        'absorberEdge': 'Absorbing element and edge', 'detectionMode': 'Detection mode',
        'energyReference': 'Energy reference / alignment', 'calibration': 'Calibration record / version'},
    'CO uptake': {'pretreatment': 'Pretreatment record', 'adsorptionTemperature': 'Adsorption temperature and unit',
        'uptakeBasis': 'Uptake basis (mass, gas reference conditions)', 'stoichiometry': 'CO:site assumption (or not calculated)',
        'calibration': 'Calibration record / version'},
    'computational': {'softwareVersion': 'Calculation program and version', 'calculationMethod': 'Calculation method / theory level',
        'inputStructure': 'Input structure / geometry reference', 'parameters': 'Parameters / configuration reference',
        'environment': 'Environment / dependency record', 'convergence': 'Convergence / completion evidence',
        'quantityBasis': 'Computed quantity, unit, and reference basis'},
}
for _technique in ('TPR', 'TPD', 'TPO'):
    MODALITY_CONTEXT[_technique] = {'pretreatment': 'Pretreatment record', 'gasComposition': 'Gas composition / carrier',
        'rampProgram': 'Temperature / time program', 'flowBasis': 'Flow and reference conditions',
        'catalystMassMg': 'Sample mass (mg)', 'signalUnit': 'Detector signal unit / basis', 'calibration': 'Calibration record / version'}

def now():
    return datetime.now(timezone.utc).isoformat()

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value: bytes):
    return hashlib.sha256(value).hexdigest()

def issue(code, message, severity='error', **extra):
    return dict(code=code, message=message, severity=severity, **extra)

def number(value):
    if isinstance(value, bool) or value is None:
        raise InputError('Enter a finite number with an explicit decimal point, without unit text.')
    text = str(value).strip()
    if len(text) > 128 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text):
        raise InputError('Enter a finite number; commas and unit text are ambiguous.')
    try:
        result = Decimal(text)
        if not result.is_finite() or abs(result.adjusted()) > 300:
            raise InvalidOperation
        return result
    except InvalidOperation:
        raise InputError('Number exceeds the supported scientific range.') from None

def decimal_text(value):
    if value == 0:
        return '0'
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')

def convert(value, unit):
    with localcontext() as ctx:
        ctx.prec = 800  # Covers supported exponent span plus all lexical source digits and offsets.
        factor, offset = CONVERSIONS[unit]
        return decimal_text(number(value) * Decimal(factor) + Decimal(offset))

@dataclass(frozen=True)
class Source:
    name: str
    content: bytes = field(repr=False)
    artifact: dict = field(repr=False)

    @classmethod
    def from_bytes(cls, name, content, *, parse=True):
        if parse:
            artifact = read_artifact(content, name)
        else:
            name = str(name).replace('\\', '/').rsplit('/', 1)[-1]
            if (not name or len(name) > 240 or any(ord(c) < 32 for c in name)
                    or not content or len(content) > MAX_FILE):
                raise InputError('Supporting files need a valid filename and 1 byte–20 MiB of content.')
            artifact = dict(filename=name, sha256=digest(content), size_bytes=len(content), format='binary', sheets={},
                parser=dict(name='catalyst-opaque', version='1', formulas_executed=False, parsed=False))
        # Preserve lexical JSON decimals in the desktop model, not Python float rounding.
        if artifact['format'] == 'json':
            records = json.loads(content.decode('utf-8-sig'), parse_float=str, parse_int=str)
            from catalyst_ingest.readers import col_name
            headers = list(records[0])
            cells = artifact['sheets']['Table']['cells']
            for row, record in enumerate(records, 2):
                for col, key in enumerate(headers, 1):
                    cells[f'{col_name(col)}{row}']['value'] = record[key]
        return cls(artifact['filename'], bytes(content), artifact)

    @classmethod
    def from_path(cls, path, *, parse=True):
        path = Path(path)
        with path.open('rb') as stream:
            content = stream.read(MAX_FILE + 1)
        return cls.from_bytes(path.name, content, parse=parse)

    def metadata(self):
        return {k: self.artifact[k] for k in ('filename', 'sha256', 'size_bytes', 'format', 'parser')}

def check_sources(sources):
    if not 1 <= len(sources) <= 6 or sum(len(s.content) for s in sources) > MAX_TOTAL:
        raise InputError('Select 1–6 files, at most 20 MiB each and 40 MiB combined.')
    if any(not s.content or len(s.content) > MAX_FILE for s in sources):
        raise InputError('Each source must contain 1 byte–20 MiB of content.')
    if len({s.name.casefold() for s in sources}) != len(sources):
        raise InputError('Source filenames must be unique, ignoring letter case.')
    if sum(len(sheet['cells']) for s in sources for sheet in s.artifact['sheets'].values()) > MAX_CELLS:
        raise InputError('The selected files exceed 200,000 combined source cells.')
    if any(digest(s.content) != s.artifact['sha256'] for s in sources):
        raise InputError('A source integrity check failed.')

def table(source, sheet_name, header_row=1):
    if type(header_row) is not int or not 1 <= header_row <= 50000:
        raise InputError('Header row must be a positive integer.')
    cells = source.artifact['sheets'].get(sheet_name, {}).get('cells')
    if cells is None:
        raise InputError('Choose a source worksheet.')
    rows = {}
    for address, cell in cells.items():
        row, col = coordinate_parts(address)
        if row >= header_row:
            rows.setdefault(row, {})[col] = (address, cell)
    header = rows.pop(header_row, {})
    headers = {}
    for col, (_, cell) in sorted(header.items()):
        name = cell.get('value')
        if 'formula' in cell or not isinstance(name, str) or not name.strip() or name in headers.values():
            raise InputError('The selected header row must contain unique, nonempty text labels.')
        headers[col] = name
    if not headers:
        raise InputError('The selected header row is empty.')
    result = []
    for row, values in sorted(rows.items()):
        if not any(c.get('value') not in (None, '') or 'formula' in c for _, c in values.values()):
            continue
        if set(values) - set(headers):
            raise InputError('A data row contains columns without a header.')
        result.append((row, {name: values.get(col, (None, {'value': None})) for col, name in headers.items()}))
    return list(headers.values()), result

def make_profile(entity, modality, source_format, source_version, name, version, sheet, header_row, rules):
    entity = lab_id(entity)
    if modality not in MODALITIES or not all(isinstance(v, str) and v.strip() and len(v) <= 200
            for v in (entity, source_version, name, sheet)):
        raise InputError('Partner, modality, source version, profile name, and worksheet are required.')
    if source_format not in ('csv', 'xlsx', 'json') or type(version) is not int or version < 1:
        raise InputError('Choose a source format and a positive mapping version.')
    if type(header_row) is not int or not 1 <= header_row <= 50000:
        raise InputError('Choose a valid header row.')
    if not rules or len(rules) > len(FIELDS):
        raise InputError('Map at least one source column to a canonical field.')
    if len({r['target'] for r in rules}) != len(rules) or len({r['source'] for r in rules}) != len(rules):
        raise InputError('Map each source column and canonical field only once.')
    normalized = []
    for rule in rules:
        target, unit = rule['target'], rule['unit']
        if target not in FIELDS or unit not in FIELDS[target][1]:
            raise InputError('Choose a supported canonical field and source unit.')
        aliases = rule.get('aliases', {})
        if not isinstance(aliases, dict) or len(aliases) > 100 or any(
                not isinstance(k, str) or not k or len(k) > 200 or not isinstance(v, str) or not v or len(v) > 200
                for k, v in aliases.items()) or (FIELDS[target][0] == 'number' and aliases):
            raise InputError('Use exact source-name to canonical-name aliases only for text fields.')
        normalized.append(dict(source=rule['source'], target=target, unit=unit, aliases=dict(sorted(aliases.items()))))
    return dict(format='catalyst-mapping/1', entity=entity.strip(), modality=modality,
        source_format=source_format, source_version=source_version.strip(), name=name.strip(), version=version,
        sheet=sheet, header_row=header_row, rules=normalized)

def context_issues(context, modality, toolkit=False):
    required = ['specimenId', 'runId', 'acquiredBy'] + list(MODALITY_CONTEXT[modality])
    if toolkit:
        required.append('processingVersion')
    issues = [issue('CONTEXT_' + k, (COMMON_CONTEXT | MODALITY_CONTEXT[modality])[k] + ' is required.')
        for k in required if not context.get(k, '').strip()]
    if not context.get('acquiredAt'):
        issues.append(issue('DATE_UNCONFIRMED', 'Acquisition date is not confirmed.', 'warning'))
    else:
        try:
            datetime.strptime(context['acquiredAt'], '%Y-%m-%d')
        except ValueError:
            issues.append(issue('DATE_INVALID', 'Use an acquisition date in YYYY-MM-DD format.'))
    for key in ('temperatureC', 'pressureKpaAbs', 'catalystMassMg', 'intervalMin'):
        if not context.get(key):
            continue
        try:
            n = number(context[key])
            if n < Decimal('-273.15') if key == 'temperatureC' else n <= 0:
                raise InputError('Outside the physical range.')
        except InputError:
            issues.append(issue('CONTEXT_INVALID_' + key, f'{key} must contain a number in the physical range.'))
    return issues

def build_preview(sources, entity, modality, context, profile=None, source_index=0, toolkit=False, raw_only=False):
    check_sources(sources)
    entity = lab_id(entity)
    if not entity.strip() or modality not in MODALITIES:
        raise InputError('Choose a partner and supported modality.')
    if not isinstance(context, dict) or any(not isinstance(v, str) or len(v) > 4000 for v in context.values()):
        raise InputError('Context values must be text, at most 4,000 characters each.')
    context = {k: v.strip() for k, v in context.items()}
    issues = context_issues(context, modality, toolkit)
    traceability, trace_issues = build_traceability(entity, modality, context)
    issues.extend(trace_issues)
    preview = dict(schema_version='catalyst-desktop-review/2', software_version=__version__, traceability=traceability,
        entity=entity.strip(), modality=modality, context=context, artifacts=[s.metadata() for s in sources],
        normalization={}, scientific_processing={'executed': False}, validation={'version': 'desktop/1', 'issues': issues},
        standardized={'columns': [], 'rows': []})
    if raw_only:
        if toolkit:
            raise InputError('Choose either toolkit import or preserve-only mode.')
        preview['normalization'] = dict(executed=False, method='preserve-files-with-context/1')
        preview['data_status'] = 'original_files_only'
        issues.append(issue('FILES_ONLY', 'Original files and context are preserved. No tabular standardization or scientific interpretation was performed.', 'warning'))
        return preview
    if toolkit:
        imported = preview_toolkit_bundle([s.artifact for s in sources if s.artifact['format'] != 'binary'], entity, modality)
        if any(s.artifact['format'] == 'binary' for s in sources):
            issues.append(issue('OPAQUE_SUPPORTING_FILES', 'Additional native/supporting files are preserved byte-for-byte and are not parsed or executed.', 'warning'))
        # Keep the legacy review untouched. Resolve only specific contextual requirements.
        preview['toolkit_source_review'] = deepcopy(imported)
        for i in imported['issues']:
            if i['code'] == 'ACQUISITION_CONTEXT_REVIEW':
                continue  # The typed required-context checks above replace this general placeholder.
            copy = deepcopy(i)
            if i['code'] == 'DRAFT_MAPPING':
                copy['severity'] = 'warning'
                copy['message'] += ' Approval explicitly accepts this mapping for this revision.'
            if i['code'] == 'PRODUCER_VERSION_UNRECORDED' and context.get('processingVersion'):
                copy['severity'] = 'warning'
                copy['message'] += ' Contributor method evidence is recorded separately; historical version remains unknown.'
            issues.append(copy)
        preview['normalization'] = dict(imported['normalization'], profile=imported['profile'],
            profile_sha256=imported['profile_content_sha256'])
        data = deepcopy(imported['data'])
        interval = None
        if context.get('intervalMin'):
            try: interval = number(context['intervalMin'])
            except InputError: pass
        accepted = 0
        for row in data['rows']:
            row['review_time_s'] = None
            if row['analysis_include'] and interval is not None and interval > 0:
                with localcontext() as ctx:
                    ctx.prec = 180
                    row['review_time_s'] = decimal_text(interval * 60 * accepted)
                accepted += 1
        preview['standardized'] = data
        preview['scientific_processing'] = dict(imported['processing'],
            time_axis={'executed': interval is not None and interval > 0, 'method': 'nominal-gc-time-axis/1',
                'interval_min': str(interval) if interval is not None else None,
                'index_basis': 'zero-based included reaction row order', 'original_axis_preserved': True})
        if context.get('identityNote'):
            issues.append(issue('LABEL_CORRECTION_RECORDED', 'Original source labels are preserved alongside the contributor correction.', 'warning'))
        if interval is not None and interval != number(data['declared_processing_settings']['injection_interval_min']):
            issues.append(issue('TIME_AXIS_REVISION', 'Review time uses the entered interval; original toolkit time values are retained.', 'warning'))
        for key, source_key in [('catalystMassMg', 'catalyst_mass_mg'), ('temperatureC', 'temperature_C')]:
            declared = data['declared_processing_settings'].get(source_key)
            if context.get(key) and declared:
                try: different = number(context[key]) != number(declared)
                except InputError: different = True
                if different:
                    issues.append(issue('CONTEXT_SOURCE_CONFLICT', f'{key} differs from the partner processing setting. Resolve or reprocess the source.'))
        return attach_subject(preview)
    if not profile:
        issues.append(issue('MAPPING_REQUIRED', 'Create or load an explicit versioned mapping.'))
        return preview
    source = sources[source_index]
    if source.artifact['format'] == 'binary':
        raise InputError('Select a CSV/XLSX/flat JSON table for mapping. Native/supporting files are preserved without interpretation.')
    validated = make_profile(**{k: profile[k] for k in ('entity', 'modality', 'source_format', 'source_version', 'name', 'version', 'sheet', 'header_row', 'rules')})
    if (lab_id(profile['entity']), profile['modality'], profile['source_format']) != (entity, modality, source.artifact['format']):
        raise InputError('This mapping belongs to a different partner, modality, or format.')
    headers, records = table(source, profile['sheet'], profile['header_row'])
    if any(r['source'] not in headers for r in profile['rules']):
        raise InputError('Mapping source columns do not match this table.')
    unmapped = [h for h in headers if h not in {r['source'] for r in profile['rules']}]
    if unmapped:
        issues.append(issue('UNMAPPED_COLUMNS', 'Unmapped columns remain in the original file: ' + ', '.join(unmapped), 'warning'))
    out = []
    for source_row, cells in records:
        row = {'source_row': source_row, 'source_artifact_sha256': source.artifact['sha256']}
        for rule in profile['rules']:
            address, cell = cells[rule['source']]
            value = cell.get('lexical_value') if cell.get('source_type') == 'n' else cell.get('value')
            target = rule['target']
            try:
                if 'formula' in cell:
                    raise InputError('Formula cells are not executed; provide an explicit values export.')
                if cell.get('source_type') == 'e':
                    raise InputError('Spreadsheet error cells cannot become standardized values or sample labels.')
                if value is None or value == '':
                    raise InputError('Mapped value is missing; it has not been replaced with zero.')
                if FIELDS[target][0] == 'text':
                    text = str(value)
                    row[target] = rule['aliases'].get(text, text)
                else:
                    row[target] = convert(value, rule['unit'])
                    n = Decimal(row[target])  # Already validated by convert; normalized decimals can exceed the input-text length.
                    if target.endswith('_fraction') and not 0 <= n <= 1:
                        raise InputError('Fraction is outside 0–1; confirm the measurement and basis.')
                    if target in ('mass_g', 'time_s', 'flow_mL_min') and n < 0:
                        raise InputError('Negative physical value is not supported.')
                    if target in ('temperature_K', 'pressure_Pa_abs', 'wavelength_nm', 'energy_eV') and n <= 0:
                        raise InputError('A positive absolute quantity is required.')
            except InputError as e:
                row[target] = None
                if len(issues) < 250:
                    issues.append(issue('VALUE_INVALID', f'{rule["source"]}, row {source_row}: {e}', location=address))
        out.append(row)
    targets = [r['target'] for r in profile['rules']]
    axes = {'spectroscopy': ('wavenumber_cm_inverse', 'wavelength_nm', 'energy_eV'),
        'XRD': ('two_theta_deg', 'scattering_q_A_inverse'),
        'XAFS/XANES': ('energy_eV', 'photoelectron_k_A_inverse', 'radial_distance_A'),
        **{t: ('temperature_K', 'time_s') for t in ('TPR', 'TPD', 'TPO')}}
    if modality in axes:
        if not any(t in targets for t in axes[modality]) or 'signal' not in targets:
            issues.append(issue('SPECTRAL_MAPPING', modality + ' requires a supported independent axis and signal mapping.'))
        axis_rules = [r for r in profile['rules'] if r['target'] in axes[modality]]
        if axis_rules and context.get('axisUnit') and any(r['unit'] != context['axisUnit'] for r in axis_rules):
            issues.append(issue('AXIS_UNIT_CONFLICT', 'The context axis unit must match the source unit selected in the mapping.'))
    if modality == 'CO uptake' and 'uptake_mol_g' not in targets:
        issues.append(issue('UPTAKE_MAPPING', 'Map mass-normalized CO uptake with an explicit mol/g, mmol/g, or umol/g source unit.'))
    if modality == 'computational' and not ('computed_energy_eV' in targets or
            {'computed_quantity', 'computed_value', 'computed_unit'}.issubset(targets)):
        issues.append(issue('COMPUTATION_MAPPING', 'Map energy per configuration, or a computed quantity, value, and unit. The reference basis must be explicit.'))
    if not out:
        issues.append(issue('NO_RESULTS', 'No standardized data rows were found.'))
    if len(sources) > 1:
        issues.append(issue('SUPPORTING_ARTIFACTS', 'Only the selected table is standardized. Other files are retained as supporting originals.', 'warning'))
    preview['normalization'] = dict(profile=validated, profile_sha256=digest(encode(validated)),
        source_artifact_sha256=source.artifact['sha256'], method='explicit-units-and-exact-aliases/1')
    preview['standardized'] = dict(columns=targets, rows=out)
    return attach_subject(preview)

@dataclass(frozen=True)
class Revision:
    content: bytes = field(repr=False)
    sha256: str

    @classmethod
    def create(cls, preview, title, parent=None):
        if not title.strip() or len(title) > 200:
            raise InputError('Enter a title, at most 200 characters.')
        payload = dict(id=str(uuid.uuid4()), created_at=now(), title=title.strip(), parent=parent, preview=deepcopy(preview))
        data = encode(payload)
        return cls(data, digest(data))

    def value(self):
        if digest(self.content) != self.sha256:
            raise InputError('Revision integrity check failed.')
        return json.loads(self.content)

    def approve(self, reviewer, note, acknowledged=False):
        payload = self.value()
        if any(i['severity'] == 'error' for i in payload['preview']['validation']['issues']):
            raise InputError('Resolve validation errors before approval.')
        if not acknowledged or not reviewer.strip() or not note.strip():
            raise InputError('Enter the reviewer name, review note, and acknowledge the exact revision and warnings.')
        if len(reviewer) > 200 or len(note) > 4000:
            raise InputError('Reviewer or review note exceeds the supported length.')
        return dict(revision_id=payload['id'], revision_sha256=self.sha256,
            reviewer=reviewer.strip(), identity_basis='self-reported; API actions use the token account',
            note=note.strip(), acknowledged=True, approved_at=now())
