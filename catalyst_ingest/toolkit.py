"""Import numerical toolkit outputs with their source evidence; never recalculate GC data."""

from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import json
import re

from .preview import PROFILE_ROOT, _decimal, _issue, _location, _role
from .readers import InputError, col_name, coordinate_parts, MAX_ROWS


def _table(artifact):
    if artifact['format'] != 'csv' or list(artifact['sheets']) != ['Table']:
        raise InputError('Toolkit numerical exports must be CSV tables.')
    cells = artifact['sheets']['Table']['cells']
    rows = {}
    for address, cell in cells.items():
        row, col = coordinate_parts(address)
        rows.setdefault(row, {})[col] = cell.get('value')
    header = rows.pop(1, {})
    names = list(header.values())
    if not names or any(not n for n in names) or len(set(names)) != len(names):
        raise InputError('Toolkit CSV headers must be nonempty and unique.')
    if any(set(row) != set(header) for row in rows.values()):
        raise InputError('Toolkit CSV rows must have the same width as their header.')
    return {name: col for col, name in header.items()}, [
        (number, {name: values[col] for col, name in header.items()})
        for number, values in sorted(rows.items())]


def _bool(value):
    if value not in ('True', 'False'):
        raise InputError('Toolkit row flags must be True or False.')
    return value == 'True'


def _numeric(value, *, optional=False):
    if value == '' and optional:
        return None
    number = _decimal(value)
    if number is None:
        raise InputError('Toolkit numeric fields must contain finite, unambiguous numbers.')
    return Decimal(number)


def _integer(value):
    number = _numeric(value)
    if number != number.to_integral_value() or not 0 <= number <= MAX_ROWS:
        raise InputError('Toolkit counts and injection limits must be integers within the supported row limit.')
    return int(number)


def _cell_evidence(cell):
    value = cell.get('value')
    if cell.get('source_type') == 'n' and cell.get('lexical_value') is not None:
        return 'number', Decimal(cell['lexical_value'])
    return type(value).__name__, value


def preview_toolkit_bundle(artifacts, entity, modality):
    """Require an explicit bundle import; filenames alone never establish a match."""
    with localcontext() as context:
        context.prec = 800
        return _preview_toolkit_bundle(artifacts, entity, modality)


def _preview_toolkit_bundle(artifacts, entity, modality):
    content = (PROFILE_ROOT / 'university-of-rochester/reactor/toolkit-gc-bundle-v1.json').read_bytes()
    profile = json.loads(content)
    if entity != profile['entity'] or modality != profile['modality']:
        raise InputError('This toolkit bundle profile is scoped to Rochester reactor data.')
    if len({a['filename'] for a in artifacts}) != len(artifacts):
        raise InputError('Bundle filenames must be unique.')
    by_name = {a['filename']: a for a in artifacts}
    summaries = [a for a in artifacts if a['filename'].endswith('_gc_summary.csv')]
    if len(summaries) != 1:
        raise InputError('Select exactly one toolkit summary CSV per processing revision.')
    summary_artifact = summaries[0]
    summary_columns, records = _table(summary_artifact)
    if len(records) != 1 or not set(profile['summary_required']).issubset(summary_columns):
        raise InputError('Toolkit summary must contain one run and the supported versioned fields.')
    summary = records[0][1]
    for key in ('catalyst_mass_mg', 'injection_interval_min', 'space_velocity_reference_temperature_K',
            'space_velocity_reference_pressure_atm'):
        if _numeric(summary[key]) <= 0:
            raise InputError('Toolkit mass, injection interval and gas reference conditions must be positive, explicit numbers.')
    if any(_numeric(summary[k]) < 0 for k in ('inlet_Ar_sccm', 'inlet_CO2_sccm', 'inlet_H2_sccm')):
        raise InputError('Declared inlet gas flows cannot be negative.')
    if summary['reaction_type'] not in profile['supported_reactions']:
        raise InputError('This draft toolkit profile supports RWGS; other methods need their own reviewed profile.')
    prefix = summary['output_prefix']
    expected = [prefix + '_gc_summary.csv', prefix + '_gc_flows.csv', prefix + '_gc_analysis.xlsx', summary['source_file']]
    if len(set(expected)) != 4 or set(by_name) != set(expected):
        raise InputError('A toolkit revision requires its matching summary CSV, flows CSV, analysis XLSX, and original report XLSX.')
    raw, workbook, flows_artifact = by_name[expected[3]], by_name[expected[2]], by_name[expected[1]]
    if raw['format'] != 'xlsx' or workbook['format'] != 'xlsx':
        raise InputError('Toolkit original report and analysis workbook must be XLSX.')
    if summary['bypass_source'] != 'same_file' or summary['bypass_file'] != 'same input file':
        raise InputError('This bundle version requires bypass measurements within the original report; separate bypass files need an extended profile.')
    columns, source_rows = _table(flows_artifact)
    if not source_rows or not set(profile['flows_required']).issubset(columns):
        raise InputError('Toolkit flows CSV is empty or lacks supported fields.')
    issues = [_issue('DRAFT_MAPPING', 'The toolkit mapping requires scientific review before production use.', 'error'),
              _issue('PRODUCER_VERSION_UNRECORDED', 'These legacy exports do not record their producer commit or configuration digest. Importing them does not establish a reproduced processing execution.', 'error')]
    # Compare the embedded input against the exact separately preserved artifact.
    candidates = [s for s in raw['sheets'].values() if s['cells'].get('B1', {}).get('value') == 'Sequence Name']
    embedded = workbook['sheets'].get('Raw Original', {}).get('cells', {})
    if len(candidates) != 1 or not embedded:
        raise InputError('The source report and its embedded Raw Original table are required.')
    source_cells = candidates[0]['cells']
    original_values = {a: c['value'] for a, c in source_cells.items() if c.get('value') is not None}
    embedded_values = {a: c['value'] for a, c in embedded.items() if c.get('value') is not None}
    raw_matches = ({a: _cell_evidence(c) for a, c in source_cells.items() if c.get('value') is not None}
        == {a: _cell_evidence(c) for a, c in embedded.items() if c.get('value') is not None}
        and not any('formula' in c for c in source_cells.values()))
    if not raw_matches:
        issues.append(_issue('EMBEDDED_RAW_MISMATCH', 'Embedded raw values differ from the supplied original report.', 'error'))
    raw_labels = [value for address, value in sorted(original_values.items(), key=lambda x: coordinate_parts(x[0]))
                  if re.fullmatch(r'A\d+', address) and coordinate_parts(address)[0] >= 6]
    if raw_labels != [row['label'] for _, row in source_rows]:
        issues.append(_issue('ROW_LINK_MISMATCH', 'Flows CSV rows do not match original report labels in source order.', 'error'))
    settings = workbook['sheets'].get('Settings', {}).get('cells', {})
    for address, cell in settings.items():
        if not re.fullmatch(r'A\d+', address) or not isinstance(cell.get('value'), str):
            continue
        key = cell['value']
        if key not in summary:
            continue
        value = settings.get('B' + address[1:], {})
        if 'formula' in value or value.get('value') is None:
            continue
        left, right = value['value'], summary[key]
        equal = str(left) == right or (_decimal(left) is not None and _decimal(right) is not None and Decimal(str(left)) == Decimal(right))
        if not equal:
            precision_only = False
            if _decimal(left) is not None and _decimal(right) is not None:
                a, b = Decimal(str(left)), Decimal(right)
                precision_only = abs(a-b) <= max(abs(a), abs(b)) * Decimal('1e-14')
            issues.append(_issue('WORKBOOK_SETTING_PRECISION' if precision_only else 'WORKBOOK_SETTING_MISMATCH',
                                 f'Workbook and CSV serialize {key} at slightly different precision.' if precision_only else f'Workbook and CSV disagree on {key}.',
                                 'info' if precision_only else 'error', [_location(workbook, 'Settings', 'B' + address[1:])],
                                 workbook_value=left, csv_value=right, comparison_relative_tolerance='1e-14'))
    quantities = []
    def quantity(artifact, col_map, row_num, source_name, source_value, field, unit, factor):
        number = _numeric(source_value, optional=True)
        return {'field': field, 'unit': unit, 'value_decimal': str(number * Decimal(factor)) if number is not None else None,
                'source_value': source_value, 'source_field': source_name, 'rule': 'multiply/' + factor,
                'source': _location(artifact, 'Table', f'{col_name(col_map[source_name])}{row_num}')}
    for name, rule in profile['summary_quantities'].items():
        if name in summary:
            quantities.append(quantity(summary_artifact, summary_columns, 2, name, summary[name], **rule))
    for name in summary:
        if name.startswith('sel_') and name.endswith('_%'):
            species = name[4:-2]
            if species not in profile['flow_species']:
                issues.append(_issue('UNMAPPED_SELECTIVITY', f'Unmapped selectivity retained: {name}.', 'error'))
                continue
            quantities.append(quantity(summary_artifact, summary_columns, 2, name, summary[name], 'steady_state_carbon_selectivity/' + species, '1', '0.01'))
    counts = {name: _integer(summary[name]) for name in ['n_bypass', 'n_reaction', 'n_blank_excluded', 'plot_reaction_points', 'bypass_omit_initial', 'bypass_points_used', 'bypass_selected_points', 'ss_inj_start', 'ss_inj_end']}
    if counts['ss_inj_start'] > counts['ss_inj_end'] or counts['bypass_omit_initial'] > counts['bypass_points_used']:
        issues.append(_issue('INVALID_SELECTION_SETTINGS', 'Steady-state or bypass selection settings are inconsistent.', 'error'))
    rows = []; measured = Counter()
    for number, row in source_rows:
        bypass, blank, included = (_bool(row[key]) for key in ['is_bypass', 'is_blank', 'analysis_include'])
        if row['catalyst_id'] != summary['catalyst_id']:
            issues.append(_issue('CATALYST_ID_MISMATCH', 'Flows CSV and summary have different catalyst identifiers.', 'error'))
        measured.update(n_bypass=int(bypass), n_blank_excluded=int(blank), n_reaction=int(not bypass and not blank), plot_reaction_points=int(included))
        source_role = _role(row['label'])
        if included and (bypass or blank or source_role in ('blank', 'bypass', 'standby')):
            issues.append(_issue('EXCLUDED_ROLE_INCLUDED', 'A blank, bypass, or standby row is included in analysis.', 'error', [_location(flows_artifact, 'Table', f'{col_name(columns["label"])}{number}')]))
        values = []
        for species in profile['flow_species']:
            if species in row:
                values.append(quantity(flows_artifact, columns, number, species, row[species], 'outlet_standard_volumetric_flow/' + species, 'mL/min', '1'))
        values.extend([quantity(flows_artifact, columns, number, 'time_on_stream_h', row['time_on_stream_h'], 'nominal_time_on_stream', 's', '3600'),
                       quantity(flows_artifact, columns, number, 'conversion', row['conversion'], 'reactant_conversion', '1', '1')])
        injection = _numeric(row['inj_num'], optional=True)
        if injection is not None and (injection < 0 or injection != injection.to_integral_value()):
            raise InputError('Injection numbers must be nonnegative integers when present.')
        rows.append({'source_row': number, 'original_label': row['label'], 'source_role_candidate': source_role,
                     'partner_row_status': row['row_status'], 'analysis_include': included,
                     'steady_state_include': included and not bypass and not blank and injection is not None and counts['ss_inj_start'] <= injection <= counts['ss_inj_end'],
                     'injection_number': int(injection) if injection is not None else None, 'quantities': values})
    for key, count in measured.items():
        if counts[key] != count:
            issues.append(_issue('ROW_COUNT_MISMATCH', f'Summary {key} disagrees with flows CSV.', 'error'))
    formulas = [c for sheet in workbook['sheets'].values() for c in sheet['cells'].values() if 'formula' in c]
    if any(c.get('cached_value') is None for c in formulas):
        issues.append(_issue('UNCACHED_WORKBOOK_FORMULAS', 'Workbook formulas have no cached results. Numerical results are imported from the companion CSVs; no workbook formulas were executed.'))
    issues.append(_issue('ACQUISITION_CONTEXT_REVIEW', 'Review catalyst identity, mass, acquisition conditions, flow reference conditions, and calibration records against this processing revision.', 'error'))
    return {'profile': {key: profile[key] for key in ['id', 'version', 'status', 'source_format_version']},
            'profile_content_sha256': hashlib.sha256(content).hexdigest(), 'entity': entity, 'modality': modality,
            'stage': 'draft_review', 'artifact_sha256s': [a['sha256'] for a in artifacts],
            'normalization': {'profile_id': profile['id'], 'profile_version': profile['version']},
            'processing': {'executed': False, 'imported_partner_results': True, 'producer_commit': None, 'configuration_sha256': None, 'method_review': profile['method_review']},
            'validation': {'rule_set': 'toolkit-gc-bundle/0.1.0', 'embedded_raw_values_match': raw_matches},
            'data': {'kind': 'toolkit_gc_processing_revision', 'declared_processing_settings': summary, 'summary_quantities': quantities,
                     'flow_reference_conditions': {'temperature_K': str(_numeric(summary['space_velocity_reference_temperature_K'])),
                                                   'pressure_atm': str(_numeric(summary['space_velocity_reference_pressure_atm'])),
                                                   'basis': 'toolkit_processing_convention; verify against instrument/MFC calibration'},
                     'rows': rows, 'source_role_counts': dict(Counter(r['source_role_candidate'] for r in rows)),
                     'steady_state_row_count': sum(r['steady_state_include'] for r in rows)},
            'issues': issues, 'approval': {'status': 'not_approved'}, 'publication': {'status': 'not_published', 'eligible': False}}
