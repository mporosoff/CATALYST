"""Versioned draft mappings and evidence-based review; no scientific processing."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re

from .readers import InputError, col_name, coordinate_parts

PROFILE_ROOT = Path(__file__).resolve().parents[1] / 'mappings'


def _value(sheet, address):
    return sheet['cells'].get(address, {}).get('value')


def _location(artifact, sheet, address):
    return {'artifact_sha256': artifact['sha256'], 'sheet': sheet, 'cell': address}


def _issue(code, message, severity='warning', locations=None, **extra):
    return {'code': code, 'severity': severity, 'message': message, 'locations': locations or [], **extra}


def _decimal(value):
    if isinstance(value, bool) or value is None:
        return None
    if len(str(value)) > 128 or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', str(value)):
        return None
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and abs(number.adjusted()) <= 300 else None
    except InvalidOperation:
        return None


def _role(label):
    text = str(label or '')
    matches = [role for pattern, role in [(r'\bblank\b', 'blank'), (r'\bbypass\b', 'bypass'),
               (r'\bRxn\b', 'reaction'), (r'\bSTANDBY\b', 'standby')] if re.search(pattern, text, re.I)]
    return matches[0] if len(matches) == 1 else 'unclassified'


def _profile_matches(artifact, entity, modality):
    matches = []
    for path in sorted(PROFILE_ROOT.rglob('*.json')):
        content = path.read_bytes()
        profile = json.loads(content)
        if profile['entity'] != entity or profile['modality'] != modality or profile['source_format'] != artifact['format']:
            continue
        for name, sheet in artifact['sheets'].items():
            if profile.get('sheet_name', name) != name:
                continue
            if any(n not in artifact['sheets'] for n in profile.get('required_sheets', [])):
                continue
            if all(_value(sheet, address) == value for address, value in profile['signature'].items()):
                profile['content_sha256'] = hashlib.sha256(content).hexdigest()
                matches.append((profile, name))
    return matches


def _raw_preview(artifact, profile, name):
    sheet = artifact['sheets'][name]
    cells = sheet['cells']
    channels = []
    max_col = max((coordinate_parts(a)[1] for a in cells), default=0)
    for column in range(profile['first_channel_column'], max_col + 1, 2):
        amount = col_name(column); area = col_name(column + 1)
        label = _value(sheet, f"{amount}{profile['channel_row']}")
        if label is None:
            continue
        metrics = [_value(sheet, f"{c}{profile['metric_row']}") for c in (amount, area)]
        if metrics != profile['metric_names']:
            raise InputError('GC report column layout differs from the selected draft profile.')
        channels.append({'source_label': label, 'amount_column': amount, 'peak_area_column': area,
                         **profile['channel_aliases'].get(label, {'species': None, 'detector': None})})
    rows = []
    unknown_values = []
    data_rows = sorted({coordinate_parts(a)[0] for a in cells if a.startswith(profile['label_column'])
                       and re.fullmatch(profile['label_column'] + r'\d+', a)})
    for number in data_rows:
        if number < profile['first_data_row']:
            continue
        address = f"{profile['label_column']}{number}"
        label = _value(sheet, address)
        if label is None:
            continue
        row = {'source_row': number, 'source_label': label, 'role_candidate': _role(label),
               'role_evidence': 'source_label_only', 'source': _location(artifact, name, address), 'measurements': []}
        for channel in channels:
            for metric, col in [('amount', channel['amount_column']), ('peak_area', channel['peak_area_column'])]:
                addr = f'{col}{number}'
                source = cells.get(addr, {})
                value = source.get('value')
                if value is None and 'formula' not in source:
                    continue  # A missing channel measurement never becomes zero.
                # A formula's lexical value is its unverified saved result, not a source observation.
                normalized = None if 'formula' in source else _decimal(
                    source.get('lexical_value') if source.get('source_type') == 'n' else value)
                row['measurements'].append({'channel': channel['source_label'], 'species_candidate': channel['species'],
                    'detector': channel['detector'], 'quantity': metric, 'source_value': value,
                    'numeric_value_decimal': normalized, 'unit': None,
                    'source': _location(artifact, name, addr), 'rule': 'strict_numeric_parse/1'})
                if normalized is None:
                    unknown_values.append(_location(artifact, name, addr))
        rows.append(row)
    issues = [
        _issue('AMOUNT_BASIS_UNCONFIRMED', 'GC Amount columns have no declared unit or calibration basis. Do not treat them as mole fractions or flows.', 'error'),
        _issue('PEAK_AREA_UNIT_UNDECLARED', 'Peak areas retain the source values with an unspecified signal unit.'),
        _issue('ROW_ROLE_REVIEW', 'Blank, bypass, reaction, and standby categories are candidates from source labels; preserve them separately for review.'),
        _issue('ACQUISITION_CONTEXT_REQUIRED', 'Confirm the canonical specimen/run identity, reaction temperature and pressure basis, gas flow reference conditions, composition basis, and calibration version.', 'error'),
        _issue('SEQUENCE_METADATA_UNCONFIRMED', 'Dates, temperatures, pressures, and flow values embedded in filenames or the sequence label are source hints, not verified run metadata.', locations=[_location(artifact, name, profile['sequence_cell'])]),
    ]
    if any(c['source_label'] in profile['ambiguous_channels'] for c in channels):
        issues.append(_issue('REFERENCE_CHANNEL_AMBIGUOUS', 'The Ar/O2 channel is not automatically mapped to pure argon. Confirm its meaning before internal-standard normalization.', 'error'))
    if unknown_values:
        issues.append(_issue('NONNUMERIC_MEASUREMENTS', 'Some measurements need explicit value interpretation.', 'error', unknown_values[:20], count=len(unknown_values)))
    return {'kind': 'raw_gc_observations', 'sequence_label': _value(sheet, profile['sequence_cell']),
            'channels': channels, 'rows': rows, 'row_counts': dict(Counter(r['role_candidate'] for r in rows)),
            'measurement_count': sum(len(r['measurements']) for r in rows)}, issues


def _processed_preview(artifact, profile, name):
    settings = artifact['sheets']['Settings']
    processed = artifact['sheets'][name]
    fields = []
    for address, cell in settings['cells'].items():
        if not re.fullmatch(r'A\d+', address):
            continue
        key = cell['value']; row = coordinate_parts(address)[0]; value_address = f'B{row}'
        source = settings['cells'].get(value_address, {'value': None})
        if row < 2 or not isinstance(key, str) or key not in profile['literal_settings'] and key not in profile['source_field_renames']:
            continue
        field = {'source_field': key, 'source_value': source.get('value'), 'source': _location(artifact, 'Settings', value_address)}
        if 'formula' in source:
            field.update(canonical_field=profile['source_field_renames'].get(key, key), formula=source['formula'],
                         value_decimal=None, status='unavailable_formula_not_evaluated')
        elif key in profile['literal_settings']:
            rule = profile['literal_settings'][key]
            number = _decimal(source.get('lexical_value') if source.get('source_type') == 'n' else source.get('value'))
            with localcontext() as precision:
                precision.prec = 800
                converted = str(Decimal(number) * Decimal(rule['factor'])) if number is not None else None
            field.update(canonical_field=rule['target'], source_unit=rule['source_unit'], unit=rule['unit'],
                         context=rule['context'], rule=f"multiply/{rule['factor']}",
                         value_decimal=converted,
                         status='normalized_literal' if number is not None else 'invalid_numeric_source')
        else:
            # A values-only export does not establish how a derived result was calculated.
            field.update(canonical_field=profile['source_field_renames'][key], value_decimal=None,
                         status='unavailable_partner_calculation_not_reproduced')
        fields.append(field)
    rows = []
    for address in processed['cells']:
        if not re.fullmatch(r'F\d+', address) or coordinate_parts(address)[0] < 2:
            continue
        number = coordinate_parts(address)[0]
        raw_label = processed['cells'].get(f'U{number}', {})
        rows.append({'source_row': number, 'catalyst_label': _value(processed, address),
            'source_status': _value(processed, f'B{number}'), 'use_in_steady_state_summary': _value(processed, f'A{number}'),
            'accepted_point': _value(processed, f'C{number}'), 'injection_number': _value(processed, f'E{number}'),
            'raw_label_formula': raw_label.get('formula'), 'source': _location(artifact, name, address)})
    formulas = [(sheet_name, address, cell) for sheet_name, sheet in artifact['sheets'].items()
                for address, cell in sheet['cells'].items() if 'formula' in cell]
    uncached = [(sheet, addr) for sheet, addr, cell in formulas if cell.get('cached_value') is None]
    issues = [_issue('PARTNER_PROCESSING_NOT_REPRODUCED', 'Partner processing formulas are preserved as evidence and have not been executed or independently reproduced.', 'error')]
    if uncached:
        issues.append(_issue('FORMULA_RESULTS_UNAVAILABLE', 'Formula cells have no saved results; dependent scientific results remain unavailable.', 'error',
                             [_location(artifact, sheet, addr) for sheet, addr in uncached[:12]], count=len(uncached)))
    for dependency in profile['raw_dependency_sheets']:
        nonempty = [c for c in artifact['sheets'][dependency]['cells'].values() if c.get('value') is not None or 'formula' in c]
        if len(nonempty) <= 1:
            issues.append(_issue('RAW_DEPENDENCY_MISSING', f'{dependency} has no source data table. Formulas referencing it cannot supply verified results.', 'error',
                                 [_location(artifact, dependency, 'A1')]))
    setting_index = {c.get('value'): coordinate_parts(a)[0] for a, c in settings['cells'].items() if re.fullmatch(r'A\d+', a) and isinstance(c.get('value'), str)}
    if 'calculated_GHSV_mL_g_hr' in setting_index:
        row = setting_index['calculated_GHSV_mL_g_hr']
        issues.append(_issue('SPACE_VELOCITY_DIMENSION', 'The workbook calculation labeled GHSV divides gas volume per hour by catalyst mass. Preserve it as mL/(g·h); a volume-based h^-1 GHSV needs catalyst-bed volume.', locations=[_location(artifact, 'Settings', f'B{row}')]))
    if _value(settings, f"B{setting_index.get('bypass_source', 0)}") == 'separate_file':
        issues.append(_issue('BYPASS_ARTIFACT_REQUIRED', 'The processing settings name a separate bypass file. Record that artifact or an explicit reviewed replacement before reproducing inlet normalization.', 'error',
                             [_location(artifact, 'Settings', f"B{setting_index.get('bypass_file', 0)}")]))
    if any(f['status'] == 'invalid_numeric_source' for f in fields):
        issues.append(_issue('INVALID_SETTING', 'A mapped processing setting is not numeric.', 'error'))
    return {'kind': 'partner_processed_workbook', 'normalized_literal_fields': fields, 'rows': rows,
            'formula_count': len(formulas), 'formula_without_cache_count': len(uncached),
            'formula_count_by_sheet': dict(Counter(s for s, _, _ in formulas)),
            'scientific_results': None, 'processing_reproduced': False}, issues


def preview_artifact(artifact, entity, modality):
    matches = _profile_matches(artifact, entity, modality)
    base = {'artifact_sha256': artifact['sha256'], 'canonical_schema_version': '0.1.0',
            'entity': entity, 'modality': modality, 'stage': 'draft_review',
            'approval': {'status': 'not_approved'}, 'publication': {'status': 'not_published', 'eligible': False},
            'processing': {'executed': False}}
    if len(matches) != 1:
        return {**base, 'profile': None, 'data': None, 'issues': [_issue('MAPPING_REQUIRED',
            'No unique matching profile. Preserve the source and define a versioned mapping before standardization.', 'error')]}
    profile, name = matches[0]
    data, issues = (_raw_preview if profile['kind'] == 'gc_raw' else _processed_preview)(artifact, profile, name)
    issues.insert(0, _issue('DRAFT_MAPPING', 'This mapping profile is a draft and requires review before production use.', 'error'))
    return {**base, 'profile': {k: profile[k] for k in ['id', 'version', 'status', 'content_sha256']},
            'normalization': {'profile_id': profile['id'], 'profile_version': profile['version']},
            'validation': {'rule_set': 'rochester-gc-review/0.1.0'}, 'data': data, 'issues': issues}


def review_pair(raw, processed, *, same_run_confirmed=False, labels_superseded=False):
    """Compare source roles via literal row pointers; never repair or execute formulas."""
    result = {'status': 'user_confirmed_same_run' if same_run_confirmed else 'unconfirmed',
              'raw_artifact_sha256': raw['artifact_sha256'], 'processed_artifact_sha256': processed['artifact_sha256'],
              'source_row_labels_superseded_by_user': labels_superseded, 'issues': [], 'role_discrepancies': []}
    if not same_run_confirmed:
        result['issues'].append(_issue('PAIR_REVIEW_REQUIRED', 'The raw/processed relationship must be confirmed before joining scientific records.', 'error'))
        return result
    if not raw.get('data') or not processed.get('data') or raw['data']['kind'] != 'raw_gc_observations' or processed['data']['kind'] != 'partner_processed_workbook':
        raise InputError('Pair comparison requires recognized raw and processed GC profiles.')
    raw_rows = {r['source_row']: r for r in raw['data']['rows']}
    for row in processed['data']['rows']:
        match = re.fullmatch(r"='Raw Original'!\$?A\$?(\d+)", row.get('raw_label_formula') or '')
        if not match:
            continue
        reference = raw_rows.get(int(match[1]))
        if reference and reference['role_candidate'] in ('blank', 'bypass', 'standby') and row['accepted_point'] is not None:
            result['role_discrepancies'].append({'raw_source': reference['source'], 'processed_source': row['source'],
                'raw_role_candidate': reference['role_candidate'], 'processed_accepted_point': row['accepted_point']})
    if result['role_discrepancies']:
        result['issues'].append(_issue('REACTION_ROLE_DISCREPANCY', 'Source-labeled blanks/bypass/standby have accepted reaction-point indices in the processed workbook. Review classification and the time axis before reproducing results.', 'error', count=len(result['role_discrepancies'])))
    if labels_superseded:
        result['identity_correction'] = {'basis': 'explicit_user_statement',
            'statement': 'The workbooks are the same run; source rows were mislabeled and the file was reprocessed.',
            'original_labels_retained': True,
            'canonical_specimen_id': None,
            'note': 'This correction does not establish acquisition date, row-role validity, or missing bypass data.'}
    return result
