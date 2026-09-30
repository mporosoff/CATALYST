"""Read partner file formats so the forms fill themselves in, and compute standard results.

Every reader is conservative: it only reports what it can find, the original file is always stored unchanged,
and anything it could not interpret is listed as a warning rather than guessed.

Readers
  ssrl-qexafs     SSRL (SLAC) QEXAFS raw scan (.txt): header and energy range. μ(E) waits for a confirmed channel map.
  nu-gc-workbook  Northwestern GC analysis workbook (.xlsx): reactor setup, injections, CATALYST-standard
                  carbon-based CO2 conversion and product selectivity (alongside the lab's own values).
  test-protocol   A standard testing-conditions document (.docx tables) -> test protocol fields.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

MAX_BYTES = 20 * 1024 * 1024
CALCULATION = ('Carbon basis, from the product amounts in the workbook: selectivity S_i = c_i·n_i / Σ c_j·n_j; '
    'CO2 conversion X = Σ c_j·n_j / (n_CO2 + Σ c_j·n_j), where c = carbon atoms per molecule and n = amount (µmol). '
    'Assumes the carbon balance closes and CO2 is the only carbon feed.')


class Found(dict):
    """What a reader found: technique, conditions to prefill, metadata, results, derived files, warnings."""


def read_file(name, content):
    """Try the readers that fit the file type; return a Found dict, or None if the format isn't recognised."""
    if not content or len(content) > MAX_BYTES:
        return None
    readers = (read_nu_gc_workbook,) if name.lower().endswith('.xlsx') else (read_ssrl_qexafs,)
    for reader in readers:
        try:
            found = reader(name, content)
        except Exception:  # a reader must never break the upload form; the file is still stored unchanged
            found = None
        if found:
            return found
    return None


# ---------------------------------------------------------------------------------------------- SSRL QEXAFS
EDGES = {'K': 'K-edge', 'L-1': 'L1-edge', 'L-2': 'L2-edge', 'L-3': 'L3-edge', 'L1': 'L1-edge', 'L2': 'L2-edge',
    'L3': 'L3-edge', 'M-5': 'M5-edge', 'M5': 'M5-edge'}
CRYSTALS = ((3.1356, 'Si(111)'), (1.9201, 'Si(220)'), (1.6375, 'Si(311)'))


def read_ssrl_qexafs(name, content):
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        text = content.decode('latin-1')
    lines = text.splitlines()
    if not lines or not lines[0].startswith('# Scan Name:'):
        return None
    header, columns, rows = {}, None, []
    for line in lines:
        if line.startswith('#'):
            body = line[1:].strip()
            if body.startswith('Encoder') and 'Energy' in body:
                columns = body.split('\t')
            elif ':' in body:
                key, _, value = body.partition(':')
                header[key.strip()] = value.strip()
            continue
        if columns and line.strip():
            parts = line.split('\t')
            if len(parts) == len(columns):
                rows.append(parts)
    if not columns or 'Trajectory Name' not in header or not rows:
        return None
    energy_index = columns.index('Energy')
    energies = [float(r[energy_index]) for r in rows]
    trajectory = header.get('Trajectory Name', '')
    element, edge, scan_type = parse_trajectory(trajectory)
    lattice = number(header.get('Lattice Spacing'))
    crystal = next((c for d, c in CRYSTALS if lattice and abs(lattice - d) < 0.002), None)
    created = parse_us_date(header.get('This Scan Create Date') or header.get('First Scan Create Date'))
    detectors = [c for c in columns if c.startswith('ADC_')]
    conditions = {}
    if element and edge:
        conditions['edge'] = f'{element} {edge}'
    conditions['instrument'] = 'SSRL QEXAFS' + (f', {crystal} monochromator' if crystal else '')
    metadata = {'Scan name': header.get('Scan Name', ''), 'Run number': header.get('Run Number', ''),
        'Session': header.get('Session Id', ''), 'Trajectory': trajectory, 'Scan type': scan_type or '',
        'Scan started': header.get('First Scan Create Date', ''), 'This scan saved': header.get('This Scan Create Date', ''),
        'Monochromator': crystal or (f'lattice spacing {lattice} Å' if lattice else ''),
        'Points': len(rows), 'Energy range (eV)': f'{min(energies):.1f}–{max(energies):.1f}',
        'Detector channels': ', '.join(detectors)}
    return Found(reader='ssrl-qexafs/1', label='SSRL QEXAFS raw scan', technique='XAS', date=created,
        title=' '.join(x for x in (header.get('Scan Name'), f'{element} {edge}' if element else '', scan_type) if x),
        conditions=conditions, metadata={k: v for k, v in metadata.items() if v not in ('', None)}, results={},
        derived=[], warnings=['μ(E) was not calculated: which detector channel is I0, It, Iref or fluorescence has '
            'not been confirmed by SLAC yet. The raw file is stored unchanged.'])


def parse_trajectory(trajectory):
    """'Re_L-3_EXAFS' -> ('Re', 'L3-edge', 'EXAFS')."""
    parts = [p for p in re.split(r'[_\s]+', trajectory or '') if p]
    element = parts[0] if parts and re.fullmatch(r'[A-Z][a-z]?', parts[0]) else None
    edge = EDGES.get(parts[1].upper().replace('–', '-')) if len(parts) > 1 else None
    scan = next((p.upper() for p in parts[2:] if p.upper() in ('EXAFS', 'XANES', 'XAFS')), None)
    return element, edge, scan


def parse_us_date(value):
    for fmt in ('%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %H:%M:%S', '%m/%d/%Y'):
        try:
            return datetime.strptime(str(value or '').strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


NUMBER = re.compile(r'[-+]?\d[\d,]*(?:\.\d+)?(?:[eE][-+]?\d+)?')


def _to_float(token):
    if ',' in token and '.' not in token and not re.fullmatch(r'[-+]?[1-9]\d{0,2}(,\d{3})+', token):
        token = token.replace(',', '.', 1) if token.count(',') == 1 else token.replace(',', '')  # 0,252 -> 0.252
    return float(token.replace(',', ''))


def number(value):
    """First number in a text: '11,000' -> 11000, '0,252 g' -> 0.252, '250 ℃' -> 250."""
    match = NUMBER.search(str(value or ''))
    return _to_float(match.group()) if match else None


def numbers(value):
    return [_to_float(m) for m in NUMBER.findall(str(value or ''))]


# ---------------------------------------------------------------------------------------------- NU GC workbook
INERT = {'H2', 'HE', 'N2', 'AR', 'O2', 'H2O'}


def carbon_atoms(formula):
    """Carbon atoms in a formula written like CH3OCH3 or C2H5OH (C followed by anything but a lowercase letter)."""
    return sum(int(n or 1) for n in re.findall(r'C(?![a-z])(\d*)', formula or ''))


def _grid(sheet):
    from catalyst_ingest.readers import coordinate_parts
    grid, formulas = {}, {}
    for address, cell in sheet['cells'].items():
        row, col = coordinate_parts(address)
        value = cell.get('value') if cell.get('formula') is None else cell.get('cached_value')
        if cell.get('formula'):
            formulas[(row, col)] = cell['formula']
        if value not in (None, ''):
            grid[(row, col)] = value
    return grid, formulas


def _text(value):
    return str(value).strip() if value is not None else ''


def _find(grid, pattern):
    regex = re.compile(pattern, re.I)
    for (row, col), value in sorted(grid.items()):
        if isinstance(value, str) and regex.search(value):
            return row, col
    return None


def read_nu_gc_workbook(name, content):
    from catalyst_ingest.readers import read_artifact, InputError
    try:
        artifact = read_artifact(content, re.sub(r'[<>:"/\\|?*]', '_', name) or 'workbook.xlsx')
    except InputError:
        return None
    for sheet_name, sheet in artifact['sheets'].items():
        grid, formulas = _grid(sheet)
        catalyst_at, injection_at = _find(grid, r'^catalyst name'), _find(grid, r'^injection no')
        if catalyst_at and injection_at:
            return _nu_gc(sheet_name, grid, formulas, catalyst_at, injection_at)
    return None


def _right(grid, row, col, span=3):
    for c in range(col + 1, col + 1 + span):
        if (row, c) in grid:
            return grid[(row, c)]
    return None


def _nu_gc(sheet_name, grid, formulas, catalyst_at, injection_at):
    warnings, conditions, metadata = [], {}, {'Sheet': sheet_name}
    catalyst = _right(grid, *catalyst_at, span=1)
    if isinstance(catalyst, str) and not re.match(r'^mass of', catalyst, re.I):
        metadata['Catalyst name in workbook'] = catalyst
    mass_at = _find(grid, r'^mass of catalyst')
    if mass_at:
        mass = _right(grid, *mass_at, span=1)
        unit = _text(_right(grid, mass_at[0], mass_at[1] + 1, span=1)) or 'mg'
        if isinstance(mass, (int, float)):
            conditions['catalyst_mass_mg'] = f'{mass * 1000:g}' if unit.lower() == 'g' else f'{mass:g}'
    feed, pressure = {}, None
    for (row, col), value in sorted(grid.items()):
        if col != 1 or not isinstance(value, str):
            continue
        amount, unit = grid.get((row, 2)), _text(grid.get((row, 3)))
        gas = re.sub(r'^(research|uhp|uhp grade|ultra high purity)\s+', '', value.strip(), flags=re.I)
        if re.match(r'^pressure', value, re.I) and isinstance(amount, (int, float)):
            pressure = f'{amount:g}' + ('' if unit.lower() == 'bar' else f' {unit}')
        elif isinstance(amount, (int, float)) and re.search(r'ml/min|sccm', unit, re.I) and re.fullmatch(r'[A-Za-z0-9]{1,8}', gas):
            feed[gas] = amount
    if pressure:
        conditions['pressure_bar'] = pressure.strip()
    flowing = {g: f for g, f in feed.items() if f}
    if flowing:
        conditions['flow_mL_min'] = f'{sum(flowing.values()):g}'
        conditions['feed'] = feed_text(flowing)
    oven_at = _find(grid, r'^oven temp')
    if oven_at:
        method = [_text(grid[(r, oven_at[1])]) for r in range(oven_at[0] + 1, oven_at[0] + 5) if isinstance(grid.get((r, oven_at[1])), str)]
        if method:
            metadata['GC method'] = '; '.join(method)
    # ---- injection table
    head = injection_at[0]
    groups = sorted((col, _text(v)) for (row, col), v in grid.items() if row == head and isinstance(v, str))
    def span(pattern):
        for index, (col, label) in enumerate(groups):
            if re.search(pattern, label, re.I):
                end = groups[index + 1][0] - 1 if index + 1 < len(groups) else col + 12
                return col, end
        return None
    conc, sel = span(r'^concentration'), span(r'selectivity')
    x_co2, x_h2, time_at = span(r'^co2 conversion'), span(r'^h2 conversion'), span(r'^injection time')
    species_row = None
    if conc:
        scores = {row: sum(1 for c in range(conc[0], conc[1] + 1) if is_formula(grid.get((row, c))))
            for row in range(head + 1, head + 6)}
        best = max(scores, key=lambda r: (scores[r], -r)) if scores else None
        species_row = best if best and scores[best] else None
    if not conc or species_row is None:
        warnings.append('The injection table could not be read (no species names under "Concentration").')
        return _gc_found(conditions, metadata, {}, [], warnings)
    species = {c: grid[(species_row, c)] for c in range(conc[0], conc[1] + 1) if is_formula(grid.get((species_row, c)))}
    sel_species = {c: grid[(species_row, c)] for c in range(sel[0], sel[1] + 1)
        if is_formula(grid.get((species_row, c)))} if sel else {}
    odd = [s for s in species.values() if s.upper() not in INERT and carbon_atoms(s) == 0]
    if odd:
        warnings.append(f"No carbon atoms found in {', '.join(odd)}; they are left out of conversion and selectivity.")
    inj_col = injection_at[1]
    injection_rows = {r for (r, c) in grid if c == inj_col and r > species_row} | {r for (r, c) in formulas if c == inj_col}
    broken = sorted({c for (r, c), f in formulas.items() if r in injection_rows and '#REF!' in f})
    if broken:
        from catalyst_ingest.readers import col_name
        warnings.append('The workbook has broken formulas (#REF!) in column(s) ' + ', '.join(col_name(c) for c in broken)
            + '. Its own values there may be wrong; CATALYST recalculates conversion and selectivity from the amounts.')
    rows = []
    for row in sorted({r for (r, _c) in grid if r > species_row}):
        amounts = {s: grid.get((row, c)) for c, s in species.items()}
        if not isinstance(grid.get((row, inj_col)), (int, float)) or not any(isinstance(v, (int, float)) for v in amounts.values()):
            continue  # calibration rows, averages and empty template rows have no injection number
        entry = dict(injection=grid.get((row, inj_col)), time_min=grid.get((row, time_at[0])) if time_at else None,
            amounts_umol={s: v for s, v in amounts.items() if isinstance(v, (int, float))})
        entry['lab_co2_conversion_pct'] = grid.get((row, x_co2[0])) if x_co2 else None
        entry['lab_h2_conversion_pct'] = grid.get((row, x_h2[0])) if x_h2 else None
        entry['lab_selectivity_pct'] = {s: grid.get((row, c)) for c, s in sel_species.items()
            if isinstance(grid.get((row, c)), (int, float))}
        entry.update(standard_metrics(entry['amounts_umol']))
        rows.append(entry)
    negative = [r['injection'] for r in rows if any(v < 0 for v in r['amounts_umol'].values())]
    if negative:
        warnings.append(f"Injection(s) {', '.join(str(n) for n in negative)} have negative amounts; they were left out "
            'of the calculated results.')
    differ = [r['injection'] for r in rows if isinstance(r['lab_co2_conversion_pct'], (int, float))
        and r['co2_conversion_pct'] is not None and abs(r['lab_co2_conversion_pct'] - r['co2_conversion_pct']) > 2]
    if differ:
        warnings.append(f"CATALYST's CO2 conversion differs from the workbook's own value by more than 2 points in "
            f"injection(s) {', '.join(str(n) for n in differ[:10])}. The two use different formulas; check which "
            'columns hold which gas with the lab before relying on either.')
    if rows:
        warnings.append('Calculated results are provisional until Northwestern confirms what each amount column '
            'contains. Both CATALYST\'s values and the workbook\'s own values are kept.')
    if not rows:
        warnings.append('No injections with amounts yet (this looks like an empty template). Setup details were read; '
            'conversion and selectivity will be calculated once the workbook has data.')
    return _gc_found(conditions, metadata, summarise(rows), rows, warnings)


def is_formula(value):
    """'CH3OCH3', 'CO2', 'H2' are formulas; 'Methanol' or 'Carbon Dioxide' are not."""
    return isinstance(value, str) and bool(re.fullmatch(r'(?:[A-Z][a-z]?\d*)+', value.strip())) and \
        all(el in ELEMENTS for el in re.findall(r'[A-Z][a-z]?', value))


ELEMENTS = {'H', 'He', 'C', 'N', 'O', 'Ar', 'S', 'Cl', 'F', 'Ne', 'Kr', 'Xe'}


def standard_metrics(amounts):
    """CATALYST standard carbon-basis CO2 conversion and product selectivity for one injection."""
    result = dict(co2_conversion_pct=None, selectivity_pct={})
    if any(v < 0 for v in amounts.values()):
        return result
    carbon = {s: carbon_atoms(s) * v for s, v in amounts.items() if s.upper() not in INERT and s != 'CO2' and carbon_atoms(s)}
    total = sum(carbon.values())
    co2 = amounts.get('CO2')
    if total > 0:
        result['selectivity_pct'] = {s: round(100 * c / total, 4) for s, c in carbon.items()}
        if isinstance(co2, (int, float)) and co2 + total > 0:
            result['co2_conversion_pct'] = round(100 * total / (co2 + total), 4)
    return result


def summarise(rows):
    if not rows:
        return {}
    last = rows[-3:]
    conversions = [r['co2_conversion_pct'] for r in last if r['co2_conversion_pct'] is not None]
    products = sorted({s for r in last for s in r['selectivity_pct']})
    summary = dict(injections=len(rows), averaged_over_last=len(last), calculation=CALCULATION)
    if conversions:
        summary['co2_conversion_pct'] = round(sum(conversions) / len(conversions), 3)
    for s in products:
        values = [r['selectivity_pct'].get(s, 0.0) for r in last if r['selectivity_pct']]
        if values:
            summary[f'selectivity_{s}_pct'] = round(sum(values) / len(values), 3)
    times = [r['time_min'] for r in rows if isinstance(r['time_min'], (int, float))]
    if times:
        summary['last_injection_time_min'] = max(times)
    summary['status'] = 'provisional (calculation not yet confirmed by the lab)'
    return summary


def _gc_found(conditions, metadata, results, rows, warnings):
    derived = []
    if rows:
        species = sorted({s for r in rows for s in r['amounts_umol']})
        products = sorted({s for r in rows for s in r['selectivity_pct']})
        lab = sorted({s for r in rows for s in r['lab_selectivity_pct']})
        header = (['injection', 'time_min'] + [f'amount_{s}_umol' for s in species] + ['catalyst_CO2_conversion_pct']
            + [f'catalyst_selectivity_{s}_pct' for s in products] + ['lab_CO2_conversion_pct', 'lab_H2_conversion_pct']
            + [f'lab_selectivity_{s}_pct' for s in lab])
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        for r in rows:
            writer.writerow([r['injection'], r['time_min']] + [r['amounts_umol'].get(s, '') for s in species]
                + [r['co2_conversion_pct']] + [r['selectivity_pct'].get(s, '') for s in products]
                + [r['lab_co2_conversion_pct'], r['lab_h2_conversion_pct']] + [r['lab_selectivity_pct'].get(s, '') for s in lab])
        derived.append(dict(suffix='CATALYST results.csv', content=out.getvalue().encode('utf-8'),
            description='Per-injection amounts with CATALYST-standard conversion and selectivity, and the lab\'s own values.'))
    conditions.setdefault('instrument', 'GC')
    return Found(reader='nu-gc-workbook/1', label='Northwestern GC analysis workbook', technique='RXN', date=None,
        title='', conditions=conditions, metadata=metadata, results=results, derived=derived, warnings=warnings)


# ---------------------------------------------------------------------------------------------- test protocol (.docx)
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def docx_tables(content):
    """Rows of cell texts from every table in a .docx (merged cells appear once)."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = archive.getinfo('word/document.xml')
        if info.file_size > 16 * 1024 * 1024:
            raise ValueError('document too large')
        xml = archive.read(info)
        if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
            raise ValueError('documents with DTDs are not read')
        root = ET.fromstring(xml)
    tables = []
    for table in root.iter(W + 'tbl'):
        rows = []
        for tr in table.findall(W + 'tr'):
            cells = [' '.join(''.join(t.text or '' for t in p.iter(W + 't')) for p in tc.iter(W + 'p')).strip()
                for tc in tr.findall(W + 'tc')]
            rows.append([re.sub(r'\s+', ' ', c) for c in cells])
        tables.append(rows)
    return tables


def feed_text(flows):
    """One way of writing a feed everywhere: 'CO2 10 + H2 30 mL/min (H2:CO2 = 3:1)'."""
    text = ' + '.join(f'{g} {f:g}' for g, f in flows.items()) + ' mL/min'
    if flows.get('H2') and flows.get('CO2'):
        text += f" (H2:CO2 = {flows['H2'] / flows['CO2']:.3g}:1)"
    return text


def hours(value):
    amount = number(value)
    if amount is None:
        return None
    return amount / 60 if re.search(r'\bmin', str(value).lower()) else amount


def grams(value):
    amount = number(value)
    if amount is None:
        return None
    return amount / 1000 if re.search(r'\bmg\b', str(value).lower()) else amount


PRESSURE = (('mpa', 10.0), ('kpa', 0.01), ('psi', 0.0689476), ('atm', 1.01325), ('bar', 1.0))


def bar(value):
    amount = number(value)
    if amount is None:
        return None
    unit = next((f for u, f in PRESSURE if re.search(rf'\b{u}', str(value).lower())), 1.0)
    return round(amount * unit, 6)


def _gas(label):
    return re.sub(r'^(research|uhp|uhp grade)\s+', '', label.strip(), flags=re.I)


def read_test_protocol(name, content):
    """Map a standard-testing-conditions document onto the test protocol template. Unmapped rows become notes."""
    if not content or len(content) > MAX_BYTES:
        return None
    try:
        tables = docx_tables(content)
    except Exception:
        return None
    if not tables:
        return None
    values, notes = {}, []
    stage, previous = '', ''
    feed, reduction_gas = {}, {}
    title = ''
    def put(key, value):
        if value in (None, ''):
            return False
        if key in values and values[key] != value:
            notes.append(f'Alternative in "{title}": {text}')  # e.g. a second way of choosing the catalyst mass
            return True
        values[key] = value
        return True
    for table in tables:
        title = table[0][0] if table and table[0] else ''
        stage, previous = '', ''
        for row in table:
            cells = [c for c in row if c]
            if not cells or (row[0] and len(row) > 1 and all(c == row[0] for c in row)):
                continue  # empty or a merged title row
            label = row[0].strip()
            value = next((c for c in row[1:] if c and c != label), '')
            unit = row[2].strip() if len(row) > 2 and row[2] != value else ''
            if re.match(r'^stage\s*\d', label, re.I):
                stage, previous = value.lower(), ''
                continue
            text = f'{label}: {value} {unit}'.strip()
            low, both = label.lower(), (value + ' ' + unit).lower()
            mapped = False
            if not label and previous == 'gc':
                values['gc_method'] = '; '.join(x for x in (values.get('gc_method'), value) if x)
                continue
            full = f'{value} {unit}'.strip()
            if low == 'ghsv':
                mapped = put('GHSV_h', number(value))
            elif re.search(r'catalyst mesh|catalyst sieve|particle size', low):
                mapped = put('sieve_um', number(value))
            elif re.search(r'reactor (inner )?diameter', low):
                mapped = put('reactor_id_cm', number(value))
            elif low.startswith('catalyst mass'):
                mapped = put('catalyst_mass_g', grams(full))
            elif low.startswith('diluent identity'):
                mapped = put('diluent', value)
            elif low.startswith('diluent mesh'):
                mapped = put('diluent_sieve_um', number(value))
            elif low.startswith('diluent mass'):
                mapped = put('diluent_mass_g', grams(full))
            elif re.search(r'bed.*length', low):
                mapped = put('bed_length_cm', number(value))
            elif low.startswith('bed volume'):
                mapped = put('bed_volume_cm3', number(value))
            elif low.startswith('total flow'):
                mapped = put('total_flow_mL_min', number(value))
            elif low.startswith('temperature ramp'):
                mapped = put('gc_method', value)
            elif low.startswith('split ratio'):
                mapped = put('gc_split_ratio', value)
            elif low.startswith('total analysis time'):
                amount = number(value)
                mapped = put('gc_analysis_min', None if amount is None else amount * 60 if re.search(r'\bh', full.lower()) else amount)
            elif any(w in stage for w in ('reduction', 'activation', 'pretreat')):
                if '/min' in both and not re.search(r'sccm|ml/min|cm3/min', both):
                    mapped = put('pretreatment_ramp_C_min', number(value))
                    inert = re.search(r'\bin\s+([A-Za-z0-9]+)', value)
                    if inert:
                        notes.append(f'Heating to the reduction temperature is done in {inert.group(1)}.')
                elif re.search(r'temperature', low):
                    mapped = put('pretreatment_T_C', number(value))
                elif re.search(r'time', low):
                    mapped = put('pretreatment_time_h', hours(full))
                elif re.search(r'sccm|ml/min|cm3/min', both):
                    reduction_gas[_gas(label)] = number(value)
                    mapped = True
            elif 'pressur' in stage:
                values['pressurization'] = '; '.join(x for x in (values.get('pressurization'), text) if x)
                mapped = True
            elif stage:
                if re.search(r'temperature', low):
                    mapped = put('reaction_T_C', number(value))
                elif re.search(r'stabili', low):
                    mapped = put('stabilization_h', hours(full))
                elif re.search(r'pressure', low) and not re.search(r'/\s*(min|h)', full.lower()):
                    mapped = put('pressure_bar', bar(full))
                elif re.search(r'sccm|ml/min|cm3/min', both):
                    feed[_gas(label)] = number(value)
                    mapped = True
            previous = 'gc' if low.startswith('temperature ramp') else ''
            if not mapped and label and value:
                notes.append(text)
    if reduction_gas:
        gas, flow = next(iter(reduction_gas.items()))
        values.setdefault('pretreatment_gas', gas)
        if flow is not None:
            values.setdefault('pretreatment_flow_mL_min', flow)
    if feed:
        values['feed'] = feed_text({g: f for g, f in feed.items() if f is not None})
        values.setdefault('total_flow_mL_min', sum(f for f in feed.values() if f))
    if not values:
        return None
    if notes:
        values['steps'] = 'From the document (not mapped to a field):\n' + '\n'.join(f'- {n}' for n in notes)
    return values
