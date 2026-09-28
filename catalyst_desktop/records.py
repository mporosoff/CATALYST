"""The four things researchers work with: samples, procedures, data and shipments.

Each saved record is a small JSON document (``catalyst-record.json``) stored in
SciSure next to the researcher's original files. Names and section headings
carry the fields needed for fast searching, so the sample list loads with a
single SciSure request.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from . import ids

RECORD_FILE = 'catalyst-record.json'          # revision 1; later revisions: catalyst-record-r2.json, -r3.json …
RECORD_FILE_RE = re.compile(r'catalyst-record(?:-r(?P<rev>[1-9]\d{0,3}))?\.json')
STATUSES = {'active': 'Active', 'withdrawn': 'Withdrawn', 'registered_in_error': 'Registered in error'}
RECORD_FORMAT = 'catalyst-record/2'
SEP = ' | '

# --------------------------------------------------------------------------
# Synthesis recipe template (shared by a master procedure and each sample).
# kind: text, number, long (multi-line), choice
# --------------------------------------------------------------------------
RECIPE_FIELDS = (
    ('method', 'Preparation method', 'choice', ('Incipient wetness impregnation', 'Wet impregnation',
        'Co-precipitation', 'Deposition–precipitation', 'Sol–gel', 'Hydrothermal', 'Solid-state / physical mixing',
        'Temperature-programmed carburization', 'Commercial / as received', 'Other'), ''),
    ('components', 'Active metals / phases and loadings', 'components', None,
        'One row per metal, promoter or phase, e.g. Mo 10 wt% and K 1 wt%. Leave the loading blank for a bulk phase.'),
    ('support', 'Support', 'text', None, 'e.g. γ-Al2O3 (Sasol, 200 m²/g) or none'),
    ('precursors', 'Precursors (name, supplier, amount)', 'long', None, 'one per line'),
    ('solvent', 'Solvent / solution', 'text', None, 'e.g. deionized water, 1.2 mL/g support'),
    ('drying_T_C', 'Drying temperature (°C)', 'number', None, ''),
    ('drying_time_h', 'Drying time (h)', 'number', None, ''),
    ('calcination_T_C', 'Calcination temperature (°C)', 'number', None, ''),
    ('calcination_ramp_C_min', 'Calcination ramp (°C/min)', 'number', None, ''),
    ('calcination_time_h', 'Calcination time (h)', 'number', None, ''),
    ('calcination_atmosphere', 'Calcination atmosphere', 'text', None, 'e.g. static air, 50 mL/min air'),
    ('activation_type', 'Activation', 'choice', ('None', 'Reduction', 'Carburization', 'Nitridation',
        'Sulfidation', 'Passivation', 'Other'), ''),
    ('activation_T_C', 'Activation temperature (°C)', 'number', None, ''),
    ('activation_ramp_C_min', 'Activation ramp (°C/min)', 'number', None, ''),
    ('activation_time_h', 'Activation time (h)', 'number', None, ''),
    ('activation_gas', 'Activation gas', 'text', None, 'e.g. 20% CH4/H2, 100 mL/min'),
    ('batch_size_g', 'Batch size (g)', 'number', None, ''),
    ('steps', 'Step-by-step recipe', 'long', None, 'Write the recipe the way you would explain it to another lab.'),
)
RECIPE_KEYS = tuple(f[0] for f in RECIPE_FIELDS)
RECIPE_LABELS = {f[0]: f[1] for f in RECIPE_FIELDS}

SAMPLE_STATES = ('As synthesized', 'Calcined', 'Reduced / activated', 'Passivated', 'Spent (after reaction)',
    'Regenerated', 'Pelletized / sieved', 'Technical form (extrudate, pellet)', 'Other')

# Optional, technique-specific run conditions. Everything here is optional.
COMMON_CONDITIONS = (('instrument', 'Instrument', 'text', None, ''),
    ('sample_state', 'Sample state', 'choice', SAMPLE_STATES, ''))
REACTOR = (('temperature_C', 'Temperature (°C)', 'text', None, 'single value or range, e.g. 250–350'),
    ('pressure_bar', 'Pressure (bar)', 'text', None, ''),
    ('feed', 'Feed composition', 'text', None, 'e.g. H2:CO2 = 3:1, 10% N2'),
    ('flow_mL_min', 'Total flow (mL/min)', 'text', None, ''),
    ('GHSV', 'GHSV / WHSV', 'text', None, 'include units'),
    ('catalyst_mass_mg', 'Catalyst mass (mg)', 'text', None, ''),
    ('time_on_stream_h', 'Time on stream (h)', 'text', None, ''),
    ('pretreatment', 'In-reactor pretreatment', 'text', None, ''))
TPX = (('gas', 'Gas', 'text', None, 'e.g. 10% H2/Ar'), ('ramp_C_min', 'Ramp (°C/min)', 'text', None, ''),
    ('max_T_C', 'Final temperature (°C)', 'text', None, ''), ('sample_mass_mg', 'Sample mass (mg)', 'text', None, ''))
TECHNIQUE_CONDITIONS = {
    'RXN': REACTOR, 'HTE': REACTOR + (('channel', 'Reactor channel / pool', 'text', None, ''),), 'PILOT': REACTOR,
    'XRD': (('radiation', 'Radiation', 'text', None, 'e.g. Cu Kα'), ('scan_range', '2θ range', 'text', None, 'e.g. 10–80°')),
    'BET': (('adsorbate', 'Adsorbate', 'text', None, 'e.g. N2 at 77 K'), ('degas', 'Degas conditions', 'text', None, 'e.g. 250 °C, 4 h')),
    'CHEM': (('probe', 'Probe gas', 'text', None, 'e.g. CO pulse'), ('temperature_C', 'Temperature (°C)', 'text', None, ''),
        ('pretreatment', 'Pretreatment', 'text', None, '')),
    'TPR': TPX, 'TPD': TPX, 'TPO': TPX,
    'TEM': (('voltage_kV', 'Voltage (kV)', 'text', None, ''),),
    'SEM': (('voltage_kV', 'Voltage (kV)', 'text', None, ''),),
    'XPS': (('source', 'X-ray source', 'text', None, 'e.g. Al Kα'),),
    'XAS': (('edge', 'Edge', 'text', None, 'e.g. Mo K-edge'), ('mode', 'Detection mode', 'choice',
        ('Transmission', 'Fluorescence', 'Electron yield'), ''), ('beamline', 'Beamline', 'text', None, 'e.g. SSRL 9-3')),
    'INSITU': (('method', 'Measurement', 'text', None, 'e.g. operando XAS'), ('temperature_C', 'Temperature (°C)', 'text', None, ''),
        ('gas', 'Gas environment', 'text', None, ''), ('beamline', 'Beamline / cell', 'text', None, '')),
    'CALC': (('software', 'Software / version', 'text', None, ''), ('method', 'Method', 'text', None, 'e.g. DFT PBE, COMSOL')),
}


def condition_fields(technique):
    return COMMON_CONDITIONS + TECHNIQUE_CONDITIONS.get(ids.technique_code(technique), ())


class RecordError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value, limit=4000):
    text = str(value or '').replace('\r\n', '\n').strip()
    if len(text) > limit:
        raise RecordError(f'Text is longer than {limit} characters.')
    return text


def one_line(value, limit=200):
    text = re.sub(r'\s+', ' ', str(value or '')).strip().replace('|', '/')
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


LOADING_UNITS = ('wt%', 'mol%', 'at%', 'molar ratio', 'mmol/g')
COMPOSITION_HELP = ('Composition is written as components joined by “+”, then “on” and the support, '
    'e.g. “10 wt% Mo + 1 wt% K on γ-Al2O3”. Bulk (unsupported) materials have no “on”, e.g. “Mo2C”.')


def parse_metals(text):
    """Legacy free text 'Mo 10, K 1' -> component rows (loading in wt%)."""
    result = []
    for part in re.split(r'[;,\n]+', str(text or '')):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r'(?P<name>.+?)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?:wt\s*%|%)?', part)
        if match:
            result.append(dict(component=match['name'].strip(), loading=float(match['value']), unit='wt%'))
        else:
            result.append(dict(component=part, loading=None, unit=''))
    return result


def clean_components(rows):
    """Validate component rows typed in the form. Returns (rows, problems)."""
    if isinstance(rows, str):
        return parse_metals(rows), []
    result, problems = [], []
    for row in rows or []:
        name = one_line((row or {}).get('component'), 60).replace('+', ' ')
        raw = str((row or {}).get('loading') if (row or {}).get('loading') is not None else '').strip().rstrip('%').strip()
        unit = str((row or {}).get('unit') or '').strip()
        if not name and not raw:
            continue
        if not name:
            problems.append('A loading was entered without its metal or phase.')
            continue
        loading = None
        if raw:
            try:
                loading = float(raw)
            except ValueError:
                problems.append(f'Loading for {name}: enter a number, e.g. 10 (the unit is chosen next to it).')
        if loading is not None and loading < 0:
            problems.append(f'Loading for {name} cannot be negative.')
        result.append(dict(component=name, loading=loading, unit=(unit or 'wt%') if loading is not None else ''))
    return result, problems


def component_text(rows):
    parts = []
    for row in rows or []:
        loading, unit = row.get('loading'), row.get('unit') or ''
        if loading is None:
            parts.append(row['component'])
        elif unit == 'molar ratio':
            parts.append(f"{row['component']} ({loading:g} molar ratio)")
        else:
            parts.append(f"{loading:g} {unit} {row['component']}".strip())
    return ' + '.join(parts)


def suggest_composition(recipe):
    rows = recipe.get('components')
    if rows is None and recipe.get('metals'):
        rows = parse_metals(recipe['metals'])
    active = component_text(clean_components(rows or [])[0])
    support = re.sub(r'\s*\(.*?\)', '', str(recipe.get('support') or '')).strip()
    if support.casefold() in ('', 'none', 'unsupported', 'n/a', 'bulk'):
        return active
    return f'{active} on {support}' if active else support


def number_or_none(value, label):
    text = str(value if value is not None else '').strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise RecordError(f'{label}: enter a number (no units).') from None


def clean_recipe(values):
    recipe, problems = {}, []
    values = dict(values or {})
    if 'components' not in values and values.get('metals'):
        values['components'] = parse_metals(values['metals'])  # records saved before components existed
    for key, label, kind, _, _ in RECIPE_FIELDS:
        raw = values.get(key, '')
        if kind == 'components':
            recipe[key], component_problems = clean_components(raw)
            problems += component_problems
        elif kind == 'number':
            try:
                recipe[key] = number_or_none(raw, label)
            except RecordError as error:
                problems.append(str(error))
        else:
            recipe[key] = clean_text(raw)
    return recipe, problems


def recipe_differences(master, actual):
    """Fields where the sample differs from its master procedure (automatic deviations)."""
    differences = []
    for key in RECIPE_KEYS:
        a, b = (master or {}).get(key), actual.get(key)
        if (a in (None, '') and b in (None, '')) or a == b:
            continue
        differences.append(dict(field=key, label=RECIPE_LABELS[key], procedure=a, sample=b))
    return differences


def person(profile):
    return dict(name=profile.get('name', ''), initials=profile.get('initials', ''),
        lab=ids.lab_code(profile['lab']) if profile.get('lab') else '')


# --------------------------------------------------------------------------
# Record builders
# --------------------------------------------------------------------------
SOURCES = {'synthesized': 'Made in our lab', 'commercial': 'Commercial / reference material'}
COMMERCIAL_FIELDS = (
    ('supplier', 'Supplier / manufacturer', 'e.g. Johnson Matthey, Clariant, Sigma-Aldrich, a partner company'),
    ('product', 'Product name / grade', 'e.g. HiFUEL R120, Katalco 51-8, EUROPT-1'),
    ('catalog_number', 'Catalog / product number (optional)', ''),
    ('lot', 'Lot / batch number (optional)', 'Printed on the container or certificate of analysis'),
    ('form', 'Form as received (optional)', 'e.g. powder, 3 mm extrudates, pellets, crushed 250–500 µm'),
)


def sample_record(*, sample_id, profile, synthesis_date, procedure, recipe, composition, label='',
        amount_g='', parent_id='', notes='', deviation_notes='', files=(), app_version='', source='synthesized',
        commercial=None):
    """``synthesis_date`` is the date made (synthesized) or the date received (commercial); the ID uses it."""
    problems = []
    source = source if source in SOURCES else 'synthesized'
    if not ids.parse_sample_id(sample_id):
        problems.append('The sample ID could not be generated.')
    commercial_info = None
    if source == 'synthesized':
        if not procedure:
            problems.append('Choose the synthesis procedure (or create one).')
    else:
        commercial_info = {key: one_line((commercial or {}).get(key), 120) for key, _, _ in COMMERCIAL_FIELDS}
        if not commercial_info['supplier']:
            problems.append('Enter the supplier or manufacturer.')
        if not commercial_info['product']:
            problems.append('Enter the product name or grade.')
    recipe, recipe_problems = clean_recipe(recipe)
    problems += recipe_problems
    composition = one_line(composition, 160) or suggest_composition(recipe)
    if not composition:
        problems.append('Enter the composition (add the metals/phases and support, or type it).')
    try:
        amount = number_or_none(amount_g, 'Amount made (g)' if source == 'synthesized' else 'Amount received (g)')
    except RecordError as error:
        problems.append(str(error)); amount = None
    if parent_id and not ids.parse_sample_id(parent_id):
        problems.append('The parent sample must be an existing sample ID.')
    master = clean_recipe((procedure or {}).get('recipe') or {})[0] if source == 'synthesized' and procedure else {}
    date_key = 'synthesis_date' if source == 'synthesized' else 'received_date'
    record = dict(format=RECORD_FORMAT, kind='sample', id=sample_id, created_at=now(), created_by=person(profile),
        source=source, commercial=commercial_info, composition=composition, label=one_line(label, 120),
        procedure=dict(id=(procedure or {}).get('id', ''), version=(procedure or {}).get('version'),
            name=(procedure or {}).get('name', '')) if procedure else dict(id='', version=None, name=''),
        recipe=recipe, deviations=recipe_differences(master, recipe) if procedure else [],
        deviation_notes=clean_text(deviation_notes), amount_g=amount, parent_id=parent_id or None,
        notes=clean_text(notes), files=list(files), app_version=app_version)
    record[date_key] = ids.as_date(synthesis_date).isoformat()
    return record, problems


def record_file_name(revision):
    return RECORD_FILE if int(revision) <= 1 else f'catalyst-record-r{int(revision)}.json'


def record_file_revision(name):
    """Revision number encoded in a record file name, or None for any other file."""
    match = RECORD_FILE_RE.fullmatch(str(name or ''))
    if not match:
        return None
    return int(match['rev'] or 1)


def revision_of(record):
    return int((record or {}).get('revision') or 1)


def status_of(record):
    return (record or {}).get('status') or 'active'


def can_change(record, profile, coordinator=False):
    """Corrections: the lab that created the record, or the CATALYST coordinator."""
    if coordinator:
        return True
    lab = ids.lab_code(profile['lab']) if (profile or {}).get('lab') else None
    return bool(lab) and (record or {}).get('created_by', {}).get('lab') == lab


def changed_fields(previous, current):
    """Labels of the fields whose shown value differs between two versions of a record."""
    before, after = dict(summary_lines(previous)), dict(summary_lines(current))
    if previous.get('kind') == 'sample':
        before.update(recipe_lines(previous.get('recipe')))
        after.update(recipe_lines(current.get('recipe')))
    skip = {'Differs from procedure', 'Status', 'Revision', 'Superseded files (kept)'}
    labels = [label for label in list(before) + [l for l in after if l not in before]
        if label not in skip and before.get(label) != after.get(label)]
    files = lambda r: [(f['name'], f.get('sha256')) for f in r.get('files', [])]
    if files(previous) != files(current):
        labels.append('Files')
    return labels


def revise(previous, updated, profile, note, status=None, status_note=''):
    """New revision of ``previous`` with the content of ``updated``. Identity and creator never change."""
    problems = []
    note = clean_text(note, 1000)
    if not note:
        problems.append('Say briefly why the record is being corrected (kept in its history).')
    record = dict(updated)
    for key in ('kind', 'id', 'created_at', 'created_by', 'format'):
        if key in previous:
            record[key] = previous[key]
    revision = revision_of(previous) + 1
    history = list(previous.get('history') or [dict(revision=1, at=previous.get('created_at'), by=previous.get('created_by'),
        note='Original record', changes=[])])
    record.update(revision=revision, revised_at=now(), revised_by=person(profile), revision_note=note,
        status=status or status_of(previous), status_note=clean_text(status_note, 1000) if status else previous.get('status_note', ''),
        superseded_files=list(previous.get('superseded_files') or []) + list(updated.get('superseded_files') or []))
    for key in ('replaced_by',):
        if key in previous and key not in updated:
            record[key] = previous[key]
    history.append(dict(revision=revision, at=record['revised_at'], by=record['revised_by'], note=note,
        status=record['status'], changes=changed_fields(previous, record)))
    record['history'] = history
    return record, problems


def sample_date(record):
    return record.get('synthesis_date') or record.get('received_date')


def procedure_record(*, procedure_id, version, name, profile, recipe, description='', files=(), app_version=''):
    problems = []
    name = one_line(name, 120)
    if not name:
        problems.append('Give the procedure a short name (e.g. "Mo2C carburization").')
    recipe, recipe_problems = clean_recipe(recipe)
    problems += recipe_problems
    if not (recipe.get('steps') or files):
        problems.append('Add the step-by-step recipe or attach the procedure document.')
    return dict(format=RECORD_FORMAT, kind='procedure', id=procedure_id, version=int(version), name=name,
        created_at=now(), created_by=person(profile), description=clean_text(description), recipe=recipe,
        files=list(files), app_version=app_version), problems


def data_record(*, data_id, sample_id, technique, profile, measured_date, conditions=None, notes='', title='',
        pooled_with=(), files=(), app_version=''):
    problems = []
    code = ids.technique_code(technique)
    if not files:
        problems.append('Add at least one data file.')
    pooled = [p.strip() for p in pooled_with if p.strip()]
    if any(not ids.parse_sample_id(p) for p in pooled):
        problems.append('Other samples in the same test must be sample IDs.')
    conditions = {k: clean_text(v, 500) for k, v in (conditions or {}).items() if str(v or '').strip()}
    return dict(format=RECORD_FORMAT, kind='data', id=data_id, sample_id=sample_id, technique=code,
        technique_label=ids.TECHNIQUES[code], created_at=now(), created_by=person(profile),
        date=ids.as_date(measured_date).isoformat(), title=one_line(title, 120), conditions=conditions,
        notes=clean_text(notes), pooled_with=pooled, files=list(files), app_version=app_version), problems


def shipment_record(*, shipment_id, sample_id, profile, to_lab, ship_date, amount='', tracking='', notes='',
        app_version=''):
    problems = []
    from_code = ids.lab_code(profile['lab'])
    try:
        to_code = ids.lab_code(to_lab)
    except ids.IdError:
        problems.append('Choose the lab the sample is going to.'); to_code = ''
    if to_code and to_code == from_code:
        problems.append('The destination is your own lab.')
    return dict(format=RECORD_FORMAT, kind='shipment', id=shipment_id, sample_id=sample_id, created_at=now(),
        created_by=person(profile), from_lab=from_code, to_lab=to_code, date=ids.as_date(ship_date).isoformat(),
        amount=one_line(amount, 80), tracking=one_line(tracking, 120), notes=clean_text(notes),
        app_version=app_version), problems


# --------------------------------------------------------------------------
# Names and headings stored in SciSure (searchable without downloads)
# --------------------------------------------------------------------------
def sample_experiment_name(record):
    procedure = record['procedure']
    if record.get('source') == 'commercial':
        c = record.get('commercial') or {}
        origin = one_line('Commercial · ' + ' '.join(x for x in (c.get('supplier'), c.get('product')) if x), 90)
    else:
        origin = f"{procedure['id']} v{procedure['version']}" if procedure.get('id') else ''
    return SEP.join([record['id'], one_line(record['composition'], 140) or '—', origin or '—'])


def parse_sample_experiment_name(name):
    parts = str(name or '').split(SEP)
    info = ids.parse_sample_id(parts[0].strip()) if parts else None
    if not info:
        return None
    info['composition'] = parts[1].strip() if len(parts) > 1 and parts[1].strip() != '—' else ''
    third = parts[2].strip() if len(parts) > 2 and parts[2].strip() != '—' else ''
    info['source'] = 'commercial' if third.startswith('Commercial') else 'synthesized'
    info['procedure'] = '' if info['source'] == 'commercial' else third
    info['origin'] = third  # what the list shows: "PRC-UR-001 v2" or "Commercial · Johnson Matthey HiFUEL"
    return info


def procedure_experiment_name(procedure_id, name):
    return f'{procedure_id}{SEP}{one_line(name, 150)}'


def parse_procedure_experiment_name(name):
    parts = str(name or '').split(SEP, 1)
    if not parts or not ids.PROCEDURE_RE.fullmatch(parts[0].strip()):
        return None
    return dict(id=parts[0].strip(), name=parts[1].strip() if len(parts) > 1 else '')


def section_header(record):
    kind, by = record['kind'], record['created_by']
    if kind == 'sample':
        return SEP.join(['CATALYST sample', record['id']])
    if kind == 'data':
        return SEP.join(['CATALYST data', record['id'], record['technique'], record['date'], by['lab'], by['initials']])
    if kind == 'shipment':
        return SEP.join(['CATALYST shipment', record['id'], f"{record['from_lab']}>{record['to_lab']}",
            record['date'], by['initials']])
    if kind == 'procedure':
        return SEP.join(['CATALYST procedure', f"{record['id']} v{record['version']}", record['created_at'][:10],
            by['lab'], by['initials']])
    raise RecordError('Unknown record type.')


def parse_section_header(header):
    parts = [p.strip() for p in str(header or '').split(SEP)]
    if not parts or not parts[0].startswith('CATALYST '):
        return None
    kind = parts[0][len('CATALYST '):]
    try:
        if kind == 'sample' and len(parts) >= 2:
            return dict(kind='sample', id=parts[1])
        if kind == 'data' and len(parts) >= 6:
            return dict(kind='data', id=parts[1], technique=parts[2], date=parts[3], lab=parts[4], initials=parts[5])
        if kind == 'shipment' and len(parts) >= 5:
            origin, _, destination = parts[2].partition('>')
            return dict(kind='shipment', id=parts[1], from_lab=origin, to_lab=destination, date=parts[3], initials=parts[4])
        if kind == 'procedure' and len(parts) >= 5:
            pid, _, version = parts[1].partition(' v')
            return dict(kind='procedure', id=pid, version=int(version), date=parts[2], lab=parts[3], initials=parts[4])
    except ValueError:
        return None
    return None


def format_value(key, value):
    if key == 'components' or isinstance(value, list):
        return component_text(value) or '—'
    if isinstance(value, float):
        return f'{value:g}'
    return '—' if value in (None, '') else str(value)


def recipe_lines(recipe):
    recipe = dict(recipe or {})
    if 'components' not in recipe and recipe.get('metals'):
        recipe['components'] = parse_metals(recipe['metals'])
    lines = []
    for key in RECIPE_KEYS:
        value = (recipe or {}).get(key)
        if value not in (None, '', []):
            lines.append((RECIPE_LABELS[key], format_value(key, value)))
    return lines


def readable_header(record):
    """Heading of the human-readable text section shown in SciSure next to the record."""
    kind = record['kind']
    if kind == 'sample':
        return f"Sample {record['id']} — details (readable copy)"
    if kind == 'data':
        return f"{record.get('technique_label', record['technique'])} — {record['id']} (readable copy)"
    if kind == 'shipment':
        return f"Shipment {record['from_lab']} → {record['to_lab']} — {record['id']} (readable copy)"
    if kind == 'procedure':
        return f"Procedure {record['id']} version {record['version']} — {record['name']}"
    raise RecordError('Unknown record type.')


def readable_html(record):
    """A plain table of everything in the record, for people browsing SciSure."""
    import html
    esc = lambda value: html.escape(str(value)).replace('\n', '<br>')
    kind = record['kind']
    rows = list(summary_lines(record))
    if kind == 'sample':
        rows += [(label, value) for label, value in recipe_lines(record.get('recipe'))]
    if kind in ('sample', 'data', 'procedure') and record.get('files'):
        rows.append(('Files in this record', '\n'.join(f"{f['name']} ({f['size_bytes']:,} bytes)" for f in record['files'])))
    for step in (record.get('history') or [])[1:]:
        by = step.get('by') or {}
        rows.append((f"Change r{step.get('revision')}", f"{str(step.get('at', ''))[:10]} by {by.get('name', '')} "
            f"({by.get('lab', '')}): {step.get('note', '')}" + (f" [changed: {', '.join(step.get('changes') or [])}]"
            if step.get('changes') else '')))
    body = ''.join(f'<tr><td style="padding:3px 12px 3px 0;vertical-align:top;color:#555"><b>{esc(label)}</b></td>'
        f'<td style="padding:3px 0;vertical-align:top">{esc(value)}</td></tr>' for label, value in rows if value not in (None, ''))
    return (f'<p><b>{esc(readable_header(record))}</b></p>'
        f'<table style="border-collapse:collapse">{body}</table>'
        f'<p style="color:#777;font-size:90%">Written by the CATALYST app on '
        f'{esc(str(record.get("revised_at") or record.get("created_at", ""))[:10])}. '
        'This is a readable copy: edits made here are not read back into CATALYST. The machine-readable record is '
        f'{record_file_name(revision_of(record))} in the file section below (earlier revisions are kept beside it).</p>')


def summary_lines(record):
    """Human-readable lines for previews and the sample page."""
    lines = _summary_lines(record)
    if status_of(record) != 'active':
        note = record.get('status_note') or ''
        if record.get('replaced_by'):
            note = (note + ' ' if note else '') + f"Replaced by {record['replaced_by']}."
        lines.insert(0, ('Status', STATUSES.get(status_of(record), status_of(record)) + (f' — {note}' if note else '')))
    if revision_of(record) > 1:
        by = record.get('revised_by') or {}
        lines.append(('Revision', f"{revision_of(record)} — corrected {str(record.get('revised_at', ''))[:10]} by "
            f"{by.get('name', '')} ({by.get('initials', '')}): {record.get('revision_note', '')}"))
    if record.get('superseded_files'):
        lines.append(('Superseded files (kept)', ', '.join(record['superseded_files'])))
    return lines


def _summary_lines(record):
    kind = record['kind']
    by = record['created_by']
    who = f"{by['name']} ({by['initials']}, {ids.lab_name(by['lab']) if by.get('lab') else ''})"
    if kind == 'sample':
        proc = record['procedure']
        amount = record.get('amount_g', record.get('amount_made_g'))
        lines = [('Sample ID', record['id']), ('Composition', record['composition'])]
        if record.get('source') == 'commercial':
            c = record.get('commercial') or {}
            lines.append(('Source', 'Commercial / reference material'))
            for key, label, _ in COMMERCIAL_FIELDS:
                if c.get(key): lines.append((label.replace(' (optional)', ''), c[key]))
            lines += [('Received', record.get('received_date')), ('Registered by', who)]
            if amount is not None: lines.append(('Amount received', f'{amount:g} g'))
        else:
            lines += [('Procedure', f"{proc['id']} v{proc['version']} · {proc['name']}" if proc.get('id') else '—'),
                ('Synthesized', record.get('synthesis_date')), ('Made by', who)]
            if amount is not None: lines.append(('Amount made', f'{amount:g} g'))
        if record.get('label'): lines.append(('Lab notebook label', record['label']))
        if record.get('parent_id'): lines.append(('Made from sample', record['parent_id']))
        for d in record.get('deviations', []):
            lines.append(('Differs from procedure', f"{d['label']}: {format_value(d['field'], d['procedure'])} → "
                f"{format_value(d['field'], d['sample'])}"))
        if record.get('deviation_notes'): lines.append(('Other deviations', record['deviation_notes']))
        if record.get('notes'): lines.append(('Notes', record['notes']))
        return lines
    if kind == 'data':
        lines = [('Data ID', record['id']), ('Sample', record['sample_id']), ('Technique', record['technique_label']),
            ('Measured', record['date']), ('Measured by', who)]
        if record.get('title'): lines.append(('Title', record['title']))
        for key, value in record.get('conditions', {}).items():
            label = next((f[1] for f in condition_fields(record['technique']) if f[0] == key), key)
            lines.append((label, value))
        if record.get('pooled_with'): lines.append(('Tested together with', ', '.join(record['pooled_with'])))
        if record.get('notes'): lines.append(('Notes', record['notes']))
        return lines
    if kind == 'shipment':
        lines = [('Shipment', record['id']), ('Sample', record['sample_id']),
            ('Route', f"{ids.lab_name(record['from_lab'])} → {ids.lab_name(record['to_lab'])}"),
            ('Shipped', record['date']), ('Logged by', who)]
        for key, label in (('amount', 'Amount sent'), ('tracking', 'Tracking / carrier'), ('notes', 'Notes')):
            if record.get(key): lines.append((label, record[key]))
        return lines
    if kind == 'procedure':
        lines = [('Procedure', f"{record['id']} v{record['version']}"), ('Name', record['name']), ('Written by', who)]
        if record.get('description'): lines.append(('Purpose', record['description']))
        lines += recipe_lines(record['recipe'])
        return lines
    return []
