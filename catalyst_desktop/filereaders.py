"""Read partner file formats so the forms fill themselves in, and compute standard results.

Every reader is conservative: it only reports what it can find, the original file is always stored unchanged,
and anything it could not interpret is listed as a warning rather than guessed.

Readers
  ssrl-qexafs     SSRL (SLAC) QEXAFS raw scan (.txt): header, energy range, and μ(E) in transmission, reference foil
                  and fluorescence (channel map confirmed by SLAC via Marc, 2026-09-30).
  nu-gc-workbook  Northwestern GC analysis workbook (.xlsx): reactor setup, injections, CO2 conversion against the
                  reference injections, carbon balance, carbon-basis selectivity (alongside the lab's own values).
                  The column labelled CO2 among the products is CO (correction from Marc, 2026-09-30).
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
CALCULATION = ('CO2 conversion X = (n_CO2,feed − n_CO2,out) / n_CO2,feed, with n_CO2,feed the average of the reference '
    'injections before reaction (as in the workbook). Selectivity on a carbon basis: S_i = c_i·n_i / Σ c_j·n_j over the '
    'carbon products (c = carbon atoms: CO 1, CH3OCH3 2, CH2O 1, CH3OH 1). Carbon balance = (n_CO2,out + Σ c_j·n_j) / '
    'n_CO2,feed. H2 conversion from the H2 peak area relative to the reference injections. n = amount in µmol.')


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
    channels = {name: columns.index(name) if name in columns else None for name in ('ADC_01', 'ADC_02', 'ADC_03', 'ADC_04')}
    warnings, results, derived = [], {}, []
    if all(i is not None for i in channels.values()):
        spectrum, skipped = mu_spectrum(rows, energy_index, channels)
        if skipped:
            warnings.append(f'{skipped} point(s) had a zero or negative detector reading and were left out of μ(E).')
        if spectrum:
            results = dict(points=len(spectrum), channel_map=CHANNEL_MAP, calculation=MU_CALCULATION)
            for key, label in (('mu_transmission', 'edge_transmission_eV'), ('mu_reference', 'edge_reference_eV'),
                    ('mu_fluorescence', 'edge_fluorescence_eV')):
                e0 = edge_position(spectrum, key)
                if e0 is not None:
                    results[label] = round(e0, 1)
            out = io.StringIO()
            writer = csv.writer(out, lineterminator='\n')
            writer.writerow(['energy_eV', 'mu_transmission', 'mu_reference', 'mu_fluorescence'])
            for point in spectrum:
                writer.writerow([f"{point['energy']:.4f}"] + ['' if point[k] is None else f'{point[k]:.6g}'
                    for k in ('mu_transmission', 'mu_reference', 'mu_fluorescence')])
            derived.append(dict(suffix='CATALYST mu(E).csv', content=out.getvalue().encode('utf-8'),
                description='μ(E) in transmission, reference foil and fluorescence'))
    else:
        warnings.append('μ(E) was not calculated: the file does not have the ADC_01–ADC_04 channels.')
    return Found(reader='ssrl-qexafs/2', label='SSRL QEXAFS raw scan', technique='XAS', date=created,
        title=' '.join(x for x in (header.get('Scan Name'), f'{element} {edge}' if element else '', scan_type) if x),
        conditions=conditions, metadata={k: v for k, v in metadata.items() if v not in ('', None)}, results=results,
        derived=derived, warnings=warnings)


CHANNEL_MAP = 'ADC_01 = I0 (incident), ADC_02 = I1 (after sample), ADC_03 = I2 (after reference foil), ADC_04 = fluorescence'
MU_CALCULATION = ('Transmission μ = ln(I0/I1) = ln(ADC_01/ADC_02); reference foil μ = ln(I1/I2) = ln(ADC_02/ADC_03); '
    'fluorescence μ = If/I0 = ADC_04/ADC_01. Readings at the same energy are averaged first. Detector offsets in the '
    'header are not applied. Edge positions are the maximum of dμ/dE (lightly smoothed): a quick check, not a calibration.')


def mu_spectrum(rows, energy_index, channels):
    """Average readings at the same energy (the scan dwells at its end), then take the ratios."""
    import math
    grouped, order = {}, []
    for r in rows:
        try:
            energy = round(float(r[energy_index]), 4)
            values = [float(r[channels[k]]) for k in ('ADC_01', 'ADC_02', 'ADC_03', 'ADC_04')]
        except (ValueError, IndexError):
            continue
        if energy not in grouped:
            grouped[energy] = []
            order.append(energy)
        grouped[energy].append(values)
    spectrum, skipped = [], 0
    for energy in sorted(order):
        group = grouped[energy]
        i0, i1, i2, fl = (sum(v[k] for v in group) / len(group) for k in range(4))
        point = dict(energy=energy, mu_transmission=None, mu_reference=None, mu_fluorescence=None)
        if i0 > 0 and i1 > 0:
            point['mu_transmission'] = math.log(i0 / i1)
        if i1 > 0 and i2 > 0:
            point['mu_reference'] = math.log(i1 / i2)
        if i0 > 0:
            point['mu_fluorescence'] = fl / i0
        if any(point[k] is None for k in ('mu_transmission', 'mu_reference', 'mu_fluorescence')):
            skipped += 1
        spectrum.append(point)
    return spectrum, skipped


def edge_position(spectrum, key, window=5):
    points = [(p['energy'], p[key]) for p in spectrum if p[key] is not None]
    if len(points) < 3 * window:
        return None
    smooth = []
    for i in range(len(points)):
        chunk = points[max(0, i - window):i + window + 1]
        smooth.append((points[i][0], sum(v for _, v in chunk) / len(chunk)))
    best, energy = None, None
    for (e1, v1), (e2, v2) in zip(smooth[window:-window - 1], smooth[window + 1:-window]):
        if e2 > e1:
            slope = (v2 - v1) / (e2 - e1)
            if best is None or slope > best:
                best, energy = slope, (e1 + e2) / 2
    return energy


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
    from catalyst_ingest.readers import col_name, coordinate_parts
    inj_col = injection_at[1]
    injection_rows = sorted(r for (r, c), v in grid.items() if c == inj_col and r > species_row and isinstance(v, (int, float)))
    formula_rows = sorted({r for (r, c) in formulas if c == inj_col and r > species_row} | set(injection_rows))
    # How the workbook is meant to be filled in is read from its own formulas:
    #   CO2 conversion  =($V$8-V10)/$V$8*100  ->  column V holds unreacted CO2, row 8 is the reference (feed) average.
    reactant_col, reference_row = None, None
    first = next((r for r in formula_rows if (r, x_co2[0]) in formulas), None) if x_co2 else None
    if first is not None:
        absolute = re.findall(r'\$([A-Z]{1,3})\$(\d+)', formulas[(first, x_co2[0])])
        if absolute:
            reference_row, reactant_col = coordinate_parts(absolute[0][0] + absolute[0][1])
    species = {c: grid[(species_row, c)] for c in range(conc[0], conc[1] + 1) if is_formula(grid.get((species_row, c)))}
    sel_species = {c: grid[(species_row, c)] for c in range(sel[0], sel[1] + 1)
        if is_formula(grid.get((species_row, c)))} if sel else {}
    if reactant_col is not None:
        relabelled = [c for c, name in species.items() if name == 'CO2' and c != reactant_col]
        for c in relabelled:
            species[c] = 'CO'
        for c, name in list(sel_species.items()):
            if name == 'CO2':
                sel_species[c] = 'CO'
        if relabelled:
            metadata['Column labels'] = (f"Column {', '.join(col_name(c) for c in relabelled)} is labelled CO2 but is a "
                'product; read as CO. Unreacted CO2 is column ' + col_name(reactant_col) + ' (from the CO2 conversion formula).')
        species[reactant_col] = 'CO2'
        metadata['Reference injections'] = (f'rows {species_row + 1}–{reference_row - 1}, averaged in row {reference_row} '
            '(feed composition before reaction)')
    odd = [s for s in species.values() if s.upper() not in INERT and carbon_atoms(s) == 0]
    if odd:
        warnings.append(f"No carbon atoms found in {', '.join(odd)}; they are left out of conversion and selectivity.")
    unnamed = [c for c in range(conc[0], conc[1] + 1) if c not in species and not isinstance(grid.get((species_row - 1, c)), str)
        and any(isinstance(grid.get((r, c)), (int, float)) for r in injection_rows)]
    if unnamed:
        warnings.append(f"Column(s) {', '.join(col_name(c) for c in unnamed)} have amounts but no product formula in row "
            f'{species_row}; they are left out. Add the formula (e.g. CH4) to include them.')
    broken = sorted({c for (r, c), f in formulas.items() if r in formula_rows and '#REF!' in f})
    if broken:
        warnings.append('The workbook has broken formulas (#REF!) in column(s) ' + ', '.join(col_name(c) for c in broken)
            + '. CATALYST calculates conversion and selectivity itself, so its values do not depend on them.')
    # Reference (feed) values: the reference row if filled in, otherwise the average of the reference injections.
    def reference(col):
        if reference_row is None or col is None:
            return None
        value = grid.get((reference_row, col))
        if isinstance(value, (int, float)):
            return value
        values = [grid.get((r, col)) for r in range(species_row + 1, reference_row)]
        values = [v for v in values if isinstance(v, (int, float))]
        return sum(values) / len(values) if values else None
    feed_co2 = reference(reactant_col)
    area = span(r'^peak area')
    area_cols = {grid[(species_row, c)]: c for c in range(area[0], area[1] + 1) if is_formula(grid.get((species_row, c)))} if area else {}
    ref_area = {gas: reference(c) for gas, c in area_cols.items()}
    rows = []
    for row in injection_rows:
        amounts = {s: grid.get((row, c)) for c, s in species.items()}
        if not any(isinstance(v, (int, float)) for v in amounts.values()):
            continue  # an injection number with no results yet
        entry = dict(injection=grid.get((row, inj_col)), time_min=grid.get((row, time_at[0])) if time_at else None,
            amounts_umol={s: v for s, v in amounts.items() if isinstance(v, (int, float))},
            areas={g: grid.get((row, c)) for g, c in area_cols.items() if isinstance(grid.get((row, c)), (int, float))})
        numeric = lambda value: value if isinstance(value, (int, float)) else None  # '#DIV/0!' and blanks -> empty
        entry['lab_co2_conversion_pct'] = numeric(grid.get((row, x_co2[0]))) if x_co2 else None
        entry['lab_h2_conversion_pct'] = numeric(grid.get((row, x_h2[0]))) if x_h2 else None
        entry['lab_selectivity_pct'] = {s: grid.get((row, c)) for c, s in sel_species.items()
            if isinstance(grid.get((row, c)), (int, float))}
        entry.update(standard_metrics(entry['amounts_umol'], feed_co2, entry['areas'], ref_area))
        rows.append(entry)
    negative = [r['injection'] for r in rows if any(v < 0 for v in r['amounts_umol'].values())]
    if negative:
        warnings.append(f"Injection(s) {', '.join(str(n) for n in negative)} have negative amounts; they were left out "
            'of the calculated results.')
    differ = [r['injection'] for r in rows if isinstance(r['lab_co2_conversion_pct'], (int, float))
        and r['co2_conversion_pct'] is not None and abs(r['lab_co2_conversion_pct'] - r['co2_conversion_pct']) > 0.5]
    if differ:
        warnings.append("CATALYST's CO2 conversion differs from the workbook's own value by more than 0.5 points in "
            f"injection(s) {', '.join(str(n) for n in differ[:10])}. Check the workbook's formulas in those rows.")
    if rows and feed_co2 is None:
        warnings.append('No reference (feed) CO2 amount was found, so CO2 conversion is estimated from the products '
            'instead of from CO2 consumption.')
    if sel_species and rows:
        metadata['Lab selectivity formulas'] = ('kept as the workbook calculates them; they weigh products differently '
            'from the carbon basis CATALYST uses, so the two sets of numbers differ')
    if not injection_rows:
        warnings.append('No injections yet (this is an empty template). The setup was read; conversion and selectivity '
            'are calculated once injection results are filled in.')
    return _gc_found(conditions, metadata, summarise(rows), rows, warnings)


def is_formula(value):
    """'CH3OCH3', 'CO2', 'H2' are formulas; 'Methanol' or 'Carbon Dioxide' are not."""
    return isinstance(value, str) and bool(re.fullmatch(r'(?:[A-Z][a-z]?\d*)+', value.strip())) and \
        all(el in ELEMENTS for el in re.findall(r'[A-Z][a-z]?', value))


ELEMENTS = {'H', 'He', 'C', 'N', 'O', 'Ar', 'S', 'Cl', 'F', 'Ne', 'Kr', 'Xe'}


def standard_metrics(amounts, feed_co2=None, areas=None, ref_areas=None):
    """One injection, the CATALYST way (see CALCULATION)."""
    result = dict(co2_conversion_pct=None, co2_conversion_from_products_pct=None, h2_conversion_pct=None,
        carbon_balance_pct=None, selectivity_pct={})
    if any(v < 0 for v in amounts.values()):
        return result
    carbon = {s: carbon_atoms(s) * v for s, v in amounts.items() if s.upper() not in INERT and s != 'CO2' and carbon_atoms(s)}
    total = sum(carbon.values())
    co2 = amounts.get('CO2')
    if total > 0:
        result['selectivity_pct'] = {s: round(100 * c / total, 4) for s, c in carbon.items()}
        if isinstance(co2, (int, float)):
            result['co2_conversion_from_products_pct'] = round(100 * total / (co2 + total), 4)
    if isinstance(co2, (int, float)) and feed_co2:
        result['co2_conversion_pct'] = round(100 * (feed_co2 - co2) / feed_co2, 4)
        result['carbon_balance_pct'] = round(100 * (co2 + total) / feed_co2, 4)
    elif result['co2_conversion_from_products_pct'] is not None:
        result['co2_conversion_pct'] = result['co2_conversion_from_products_pct']
    areas, ref_areas = areas or {}, ref_areas or {}
    if isinstance(areas.get('H2'), (int, float)) and ref_areas.get('H2'):
        result['h2_conversion_pct'] = round(100 * (ref_areas['H2'] - areas['H2']) / ref_areas['H2'], 4)
    return result


def summarise(rows):
    if not rows:
        return {}
    last = rows[-3:]
    summary = dict(injections=len(rows), averaged_over_last=len(last), calculation=CALCULATION)
    def mean(key):
        values = [r[key] for r in last if r.get(key) is not None]
        return round(sum(values) / len(values), 3) if values else None
    for key in ('co2_conversion_pct', 'h2_conversion_pct', 'carbon_balance_pct'):
        if mean(key) is not None:
            summary[key] = mean(key)
    for s in sorted({s for r in last for s in r['selectivity_pct']}):
        values = [r['selectivity_pct'].get(s, 0.0) for r in last if r['selectivity_pct']]
        if values:
            summary[f'selectivity_{s}_pct'] = round(sum(values) / len(values), 3)
    times = [r['time_min'] for r in rows if isinstance(r['time_min'], (int, float))]
    if times:
        summary['last_injection_time_min'] = max(times)
    return summary


def _gc_found(conditions, metadata, results, rows, warnings):
    derived = []
    if rows:
        species = sorted({s for r in rows for s in r['amounts_umol']})
        products = sorted({s for r in rows for s in r['selectivity_pct']})
        lab = sorted({s for r in rows for s in r['lab_selectivity_pct']})
        header = (['injection', 'time_min'] + [f'amount_{s}_umol' for s in species] + ['catalyst_CO2_conversion_pct',
            'catalyst_CO2_conversion_from_products_pct', 'catalyst_H2_conversion_pct', 'catalyst_carbon_balance_pct']
            + [f'catalyst_selectivity_{s}_pct' for s in products] + ['lab_CO2_conversion_pct', 'lab_H2_conversion_pct']
            + [f'lab_selectivity_{s}_pct' for s in lab])
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(header)
        for r in rows:
            writer.writerow([r['injection'], r['time_min']] + [r['amounts_umol'].get(s, '') for s in species]
                + [r['co2_conversion_pct'], r['co2_conversion_from_products_pct'], r['h2_conversion_pct'], r['carbon_balance_pct']]
                + [r['selectivity_pct'].get(s, '') for s in products]
                + [r['lab_co2_conversion_pct'], r['lab_h2_conversion_pct']] + [r['lab_selectivity_pct'].get(s, '') for s in lab])
        derived.append(dict(suffix='CATALYST results.csv', content=out.getvalue().encode('utf-8'),
            description='per-injection amounts with CATALYST conversion, carbon balance and selectivity, and the lab\'s own values'))
    conditions.setdefault('instrument', 'GC')
    return Found(reader='nu-gc-workbook/2', label='Northwestern GC analysis workbook', technique='RXN', date=None,
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
