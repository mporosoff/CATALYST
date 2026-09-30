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

# --------------------------------------------------------------------------
# Test protocol template (shared reactor testing conditions; each run records what differed)
# --------------------------------------------------------------------------
TEST_FIELDS = (
    ('reaction', 'Reaction', 'text', None, 'e.g. CO2 hydrogenation to methanol'),
    ('reactor', 'Reactor', 'text', None, 'e.g. fixed bed, stainless steel, 1/2 in'),
    ('reactor_id_cm', 'Reactor inner diameter (cm)', 'number', None, ''),
    ('catalyst_mass_g', 'Catalyst mass (g)', 'number', None, ''),
    ('sieve_um', 'Catalyst sieve fraction (µm)', 'number', None, ''),
    ('diluent', 'Diluent', 'text', None, 'e.g. quartz sand, SiC'),
    ('diluent_sieve_um', 'Diluent sieve fraction (µm)', 'number', None, ''),
    ('diluent_mass_g', 'Diluent mass (g)', 'number', None, ''),
    ('bed_length_cm', 'Catalyst bed length (cm)', 'number', None, 'without diluent'),
    ('bed_volume_cm3', 'Catalyst bed volume (cm³)', 'number', None, 'without diluent'),
    ('GHSV_h', 'GHSV (h⁻¹)', 'number', None, ''),
    ('total_flow_mL_min', 'Total flow (mL/min)', 'number', None, ''),
    ('pretreatment_gas', 'Pretreatment gas', 'text', None, 'e.g. H2'),
    ('pretreatment_flow_mL_min', 'Pretreatment flow (mL/min)', 'number', None, ''),
    ('pretreatment_ramp_C_min', 'Pretreatment ramp (°C/min)', 'number', None, ''),
    ('pretreatment_T_C', 'Pretreatment temperature (°C)', 'number', None, ''),
    ('pretreatment_time_h', 'Pretreatment time (h)', 'number', None, ''),
    ('reaction_T_C', 'Reaction temperature (°C)', 'number', None, ''),
    ('pressure_bar', 'Reaction pressure (bar)', 'number', None, ''),
    ('feed', 'Feed composition and flows', 'text', None, 'e.g. CO2 10 + H2 30 mL/min (H2:CO2 = 3:1)'),
    ('stabilization_h', 'Stabilization time (h)', 'number', None, ''),
    ('gc_method', 'GC method', 'text', None, 'oven program'),
    ('gc_split_ratio', 'GC split ratio', 'text', None, 'e.g. 10:1'),
    ('gc_analysis_min', 'GC analysis time per point (min)', 'number', None, ''),
    ('pressurization', 'Pressurization', 'long', None, 'e.g. He 40 mL/min, 1 bar/min at 250 °C'),
    ('steps', 'Procedure and notes', 'long', None, 'Anything else another lab needs to run the test the same way.'),
)
TEST_LABELS = {f[0]: f[1] for f in TEST_FIELDS}
CATEGORIES = {'synthesis': 'Synthesis procedure', 'testing': 'Test protocol'}


def fields_for(record_or_category):
    """Template fields of a procedure record (or a category name)."""
    category = record_or_category if isinstance(record_or_category, str) else (record_or_category or {}).get('category')
    return TEST_FIELDS if category == 'testing' else RECIPE_FIELDS


def same_value(a, b):
    """'250' == '250 °C' == '250.0'; '11000 h-1' == '11,000 h-1'; feeds compare gas by gas. Otherwise exact text."""
    a, b = str(a or '').strip(), str(b or '').strip()
    if a == b:
        return True
    from .filereaders import numbers
    gases = lambda t: sorted(re.findall(r'\b([A-Z][A-Za-z0-9]*)\s+(\d[\d.,]*)', t))
    if gases(a) or gases(b):
        return gases(a) == gases(b) and numbers(a) == numbers(b)
    na, nb = numbers(a), numbers(b)
    return bool(na) and na == nb


def protocol_conditions(recipe):
    """Run conditions (reactor data form) that follow from a test protocol."""
    r = recipe or {}
    def num(key):
        value = r.get(key)
        return None if value in (None, '') else f'{value:g}' if isinstance(value, (int, float)) else str(value)
    out = {}
    for protocol_key, condition_key in (('reaction_T_C', 'temperature_C'), ('pressure_bar', 'pressure_bar'),
            ('total_flow_mL_min', 'flow_mL_min'), ('feed', 'feed')):
        if num(protocol_key):
            out[condition_key] = num(protocol_key)
    if num('GHSV_h'):
        out['GHSV'] = f"{num('GHSV_h')} h-1"
    if isinstance(r.get('catalyst_mass_g'), (int, float)):
        out['catalyst_mass_mg'] = f"{r['catalyst_mass_g'] * 1000:g}"
    pre = ', '.join(x for x in (
        ' '.join(x for x in (r.get('pretreatment_gas'), f"{num('pretreatment_flow_mL_min')} mL/min" if num('pretreatment_flow_mL_min') else '') if x),
        f"{num('pretreatment_ramp_C_min')} °C/min to" if num('pretreatment_ramp_C_min') else '',
        f"{num('pretreatment_T_C')} °C" if num('pretreatment_T_C') else '',
        f"{num('pretreatment_time_h')} h" if num('pretreatment_time_h') else '') if x).replace('to, ', 'to ')
    if pre:
        out['pretreatment'] = pre
    return out

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


def clean_recipe(values, fields=RECIPE_FIELDS):
    recipe, problems = {}, []
    values = dict(values or {})
    if fields is RECIPE_FIELDS and 'components' not in values and values.get('metals'):
        values['components'] = parse_metals(values['metals'])  # records saved before components existed
    for key, label, kind, _, _ in fields:
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
    record = dict(previous)  # fields this app version doesn't know about are carried forward, never dropped
    record.update(updated)
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


def procedure_record(*, procedure_id, version, name, profile, recipe, description='', files=(), app_version='',
        category='synthesis'):
    """A synthesis procedure (PRC-…) or a test protocol (TST-…): a shared, versioned template."""
    problems = []
    category = category if category in CATEGORIES else 'synthesis'
    testing = category == 'testing'
    name = one_line(name, 120)
    if not name:
        problems.append('Give the test protocol a short name (e.g. "CO2 to methanol, 250 °C, 30 bar").' if testing
            else 'Give the procedure a short name (e.g. "Mo2C carburization").')
    recipe, recipe_problems = clean_recipe(recipe, fields_for(category))
    problems += recipe_problems
    if testing and not (any(v not in (None, '') for v in recipe.values()) or files):
        problems.append('Fill in the test conditions or attach the protocol document.')
    if not testing and not (recipe.get('steps') or files):
        problems.append('Add the step-by-step recipe or attach the procedure document.')
    record = dict(format=RECORD_FORMAT, kind='procedure', id=procedure_id, version=int(version), name=name,
        created_at=now(), created_by=person(profile), description=clean_text(description), recipe=recipe,
        files=list(files), app_version=app_version)
    if testing:
        record['category'] = 'testing'
    return record, problems


def data_record(*, data_id, sample_id, technique, profile, measured_date, conditions=None, notes='', title='',
        pooled_with=(), files=(), app_version='', protocol=None, derived_from=(), extracted=(), results=None):
    """One measurement. ``protocol`` is the test protocol version followed (reactor data), ``derived_from`` the raw
    data this analysis was made from, ``extracted`` what CATALYST read from each file, ``results`` computed values."""
    problems = []
    code = ids.technique_code(technique)
    if not files:
        problems.append('Add at least one data file.')
    pooled = [p.strip() for p in pooled_with if p.strip()]
    if any(not ids.parse_sample_id(p) for p in pooled):
        problems.append('Other samples in the same test must be sample IDs.')
    sources = [d.strip() for d in derived_from if d and d.strip()]
    if any(not ids.DATA_RE.fullmatch(d) for d in sources):
        problems.append('"Analysis of" must be data IDs, e.g. UR-MDP-260925-01-XAS-01.')
    if any(d == data_id for d in sources):
        problems.append('A record cannot be an analysis of itself.')
    conditions = {k: clean_text(v, 500) for k, v in (conditions or {}).items() if str(v or '').strip()}
    record = dict(format=RECORD_FORMAT, kind='data', id=data_id, sample_id=sample_id, technique=code,
        technique_label=ids.TECHNIQUES[code], created_at=now(), created_by=person(profile),
        date=ids.as_date(measured_date).isoformat(), title=one_line(title, 120), conditions=conditions,
        notes=clean_text(notes), pooled_with=pooled, files=list(files), app_version=app_version)
    if protocol:
        expected = protocol_conditions(protocol.get('recipe'))
        record['protocol'] = dict(id=protocol['id'], version=protocol.get('version'), name=protocol.get('name', ''))
        labels = {f[0]: f[1] for f in condition_fields(code)}
        record['protocol_deviations'] = [dict(field=k, label=labels.get(k, k), protocol=v, run=conditions.get(k, ''))
            for k, v in expected.items() if not same_value(conditions.get(k, ''), v)]
    if sources:
        record['derived_from'] = sources
    if extracted:
        record['extracted'] = list(extracted)
    if results:
        record['results'] = dict(results)
    return record, problems


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


def recipe_lines(recipe, fields=RECIPE_FIELDS):
    recipe = dict(recipe or {})
    if fields is RECIPE_FIELDS and 'components' not in recipe and recipe.get('metals'):
        recipe['components'] = parse_metals(recipe['metals'])
    lines = []
    for key, label, *_ in fields:
        value = (recipe or {}).get(key)
        if value not in (None, '', []):
            lines.append((label, format_value(key, value)))
    return lines


RESULT_LABELS = {'co2_conversion_pct': 'CO2 conversion (%)', 'time_on_stream_h': 'Time on stream (h)',
    'injections': 'GC injections', 'averaged_over_last': 'Averaged over last injections',
    'last_injection_time_min': 'Last injection (min after the first)', 'status': 'Calculation status',
    'source_file': 'Calculated from', 'h2_conversion_pct': 'H2 conversion (%)', 'carbon_balance_pct': 'Carbon balance (%)',
    'points': 'Spectrum points', 'channel_map': 'Detector channels', 'edge_transmission_eV': 'Edge, sample (eV)',
    'edge_reference_eV': 'Edge, reference foil (eV)', 'edge_fluorescence_eV': 'Edge, fluorescence (eV)'}


def result_label(key):
    if key in RESULT_LABELS:
        return RESULT_LABELS[key]
    match = re.fullmatch(r'selectivity_(.+)_pct', key)
    return f'Selectivity to {match.group(1)} (%)' if match else key


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
        noun = 'Test protocol' if record.get('category') == 'testing' else 'Procedure'
        return f"{noun} {record['id']} version {record['version']} — {record['name']}"
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
        if record.get('protocol'):
            p = record['protocol']
            lines.append(('Test protocol', f"{p['id']} v{p['version']} · {p.get('name', '')}"))
            for d in record.get('protocol_deviations', []):
                lines.append(('Differs from protocol', f"{d['label']}: {d['protocol']} → {d['run'] or '—'}"))
        if record.get('derived_from'): lines.append(('Analysis of', ', '.join(record['derived_from'])))
        if record.get('pooled_with'): lines.append(('Tested together with', ', '.join(record['pooled_with'])))
        for key, value in (record.get('results') or {}).items():
            if key != 'calculation':
                lines.append((result_label(key), format_value(key, value)))
        if (record.get('results') or {}).get('calculation'):
            lines.append(('How results were calculated', record['results']['calculation']))
        for item in record.get('extracted', []):
            details = '; '.join(f'{k}: {v}' for k, v in (item.get('metadata') or {}).items())
            lines.append((f"Read from {item.get('file')}", f"{item.get('label', '')}" + (f' — {details}' if details else '')))
            for warning in item.get('warnings', []):
                lines.append(('Note', warning))
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
        noun = 'Test protocol' if record.get('category') == 'testing' else 'Procedure'
        lines = [(noun, f"{record['id']} v{record['version']}"), ('Name', record['name']), ('Written by', who)]
        if record.get('description'): lines.append(('Purpose', record['description']))
        lines += recipe_lines(record['recipe'], fields_for(record))
        return lines
    return []
