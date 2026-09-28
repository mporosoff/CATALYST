"""Tidy, analysis-ready exports of CATALYST data (for people, notebooks and ML pipelines)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
import json
from pathlib import Path
import re

from . import records

DATASET_FORMAT = 'catalyst-dataset/1'


def cell(value):
    """CSV-safe cell: numbers stay numbers; text that a spreadsheet would run as a formula is neutralized."""
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text[:1] in ('=', '@', '\t', '\r'):
        return "'" + text
    if text[:1] in ('+', '-') and not re.fullmatch(r'[+-]\s*\d[\d.,eE+\-\s–]*', text):
        return "'" + text  # e.g. "-cmd", but not "-196" or "+5"
    return text


def _components(recipe):
    recipe = dict(recipe or {})
    if 'components' not in recipe and recipe.get('metals'):
        return records.parse_metals(recipe['metals'])
    return recipe.get('components') or []


def recipe_columns(recipe, prefix='recipe_'):
    """Flat recipe columns, including one numeric column per component, e.g. ``load_Mo (wt%)``."""
    recipe = recipe or {}
    row = {prefix + 'components': records.component_text(_components(recipe))}
    for c in _components(recipe):
        if c.get('loading') is not None:
            row[f"{prefix}load_{c['component']} ({c.get('unit') or 'wt%'})"] = c['loading']
    for key in records.RECIPE_KEYS:
        if key != 'components':
            row[prefix + key] = recipe.get(key)
    return row


def sample_row(entry):
    r = entry['record'] or {}
    info = entry['sample']
    by = r.get('created_by', {})
    proc = r.get('procedure', {})
    commercial = r.get('commercial') or {}
    row = dict(sample_id=info['id'], record_status='ok' if entry['record'] else 'registration not finished',
        source=r.get('source', 'synthesized'), lab=info['lab_code'], made_by=by.get('name', ''), initials=info['initials'],
        date=info['date'], composition=r.get('composition', info.get('composition', '')),
        notebook_label=r.get('label', ''), procedure_id=proc.get('id', ''), procedure_version=proc.get('version'),
        procedure_name=proc.get('name', ''), supplier=commercial.get('supplier', ''), product=commercial.get('product', ''),
        catalog_number=commercial.get('catalog_number', ''), lot=commercial.get('lot', ''), form=commercial.get('form', ''),
        parent_sample_id=r.get('parent_id') or '', amount_g=r.get('amount_g', r.get('amount_made_g')))
    row.update(recipe_columns(r.get('recipe')))
    row['deviations_from_procedure'] = '; '.join(f"{d['label']}: {records.format_value(d['field'], d['procedure'])} -> "
        f"{records.format_value(d['field'], d['sample'])}" for d in r.get('deviations', []))
    row['other_deviations'] = r.get('deviation_notes', '')
    row['notes'] = r.get('notes', '')
    row['data_records'] = len(entry['data'])
    row['techniques'] = ', '.join(sorted({d['technique'] for d in entry['data']}))
    row['shipments'] = '; '.join(f"{s['record']['date']} {s['record']['from_lab']}>{s['record']['to_lab']}" for s in entry['shipments'])
    return row


SAMPLE_KEYS_IN_DATA = ('composition', 'source', 'lab', 'date', 'procedure_id', 'procedure_version', 'supplier', 'product')


def data_rows(entry, condition_keys):
    base = sample_row(entry)
    rows = []
    for item in entry['data']:
        r = item['record']
        by = r['created_by']
        row = dict(data_id=r['id'], sample_id=r['sample_id'], technique=r['technique'], technique_label=r['technique_label'],
            measured_date=r['date'], measured_lab=by.get('lab', ''), measured_by=by.get('name', ''), title=r.get('title', ''))
        for key in condition_keys:
            row['cond_' + key] = r.get('conditions', {}).get(key, '')
        row.update(pooled_with=', '.join(r.get('pooled_with', [])), notes=r.get('notes', ''),
            files='; '.join(f['name'] for f in r.get('files', [])))
        for key in SAMPLE_KEYS_IN_DATA:
            row['sample_' + key] = base[key]
        row.update({'sample_' + k: v for k, v in base.items() if k.startswith('recipe_')})
        rows.append(row)
    return rows


def procedure_rows(procedures):
    rows = []
    for p in procedures:
        for r in p['versions']:
            row = dict(procedure_id=r['id'], version=r['version'], name=r['name'], lab=r['created_by']['lab'],
                written_by=r['created_by']['name'], created=r['created_at'][:10], description=r.get('description', ''))
            row.update(recipe_columns(r['recipe']))
            rows.append(row)
    return rows


def file_rows(entries):
    rows = []
    for e in entries:
        for d in e['data']:
            for f in d['files']:
                rows.append(dict(sample_id=e['sample']['id'], data_id=d['id'], technique=d['technique'],
                    file=f.get('realName'), size_bytes=f.get('fileSize') or 0,
                    saved_as=f"originals/{e['sample']['id']}/{d['id']}/{f.get('realName')}"))
    return rows


def write_csv(path, rows):
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns or ['empty'])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: cell(row.get(k)) for k in columns})


def collect(store, samples, technique=None, progress=lambda _: None, workers=4):
    """Open every sample (records, data and shipments). Optionally keep only one technique's data."""
    done = [0]
    def open_one(sample):
        entry = store.open_sample(sample)
        done[0] += 1
        progress(f'Reading sample {done[0]} of {len(samples)}…')
        if technique:
            entry['data'] = [d for d in entry['data'] if d['technique'] == technique]
        return entry
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(open_one, samples))


def build_dataset(store, samples, technique=None, progress=lambda _: None):
    """Read everything that an export would contain, without writing anything. Used for the preview and the export."""
    entries = collect(store, samples, technique, progress)
    if technique:
        entries = [e for e in entries if e['data']]
    progress('Reading procedures…')
    procedures = []
    for procedure in store.list_procedures():
        versions = store.procedure_versions(procedure)
        procedures.append(dict(id=procedure['id'], name=procedure['name'], versions=[v['record'] for v in versions]))
    condition_keys = []
    for entry in entries:
        for item in entry['data']:
            for key in item['record'].get('conditions', {}):
                if key not in condition_keys:
                    condition_keys.append(key)
    files = file_rows(entries)
    tables = dict(samples=[sample_row(e) for e in entries],
        data_records=[row for e in entries for row in data_rows(e, condition_keys)],
        procedures=procedure_rows(procedures), original_files=files)
    return dict(entries=entries, procedures=procedures, tables=tables, technique=technique, server=store.client.origin,
        file_count=len(files), file_bytes=sum(f['size_bytes'] for f in files))


def write_dataset(store, built, folder, originals=False, progress=lambda _: None):
    base = f"CATALYST-export-{datetime.now():%Y%m%d-%H%M%S}"
    target, n = Path(folder) / base, 1
    while target.exists():
        target, n = Path(folder) / f'{base}-{n}', n + 1
    target.mkdir(parents=True)
    entries, tables = built['entries'], built['tables']
    write_csv(target / 'samples.csv', tables['samples'])
    write_csv(target / 'data_records.csv', tables['data_records'])
    write_csv(target / 'procedures.csv', tables['procedures'])
    dataset = dict(format=DATASET_FORMAT, exported_at=datetime.now().isoformat(timespec='seconds'),
        server=built['server'], technique_filter=built['technique'],
        samples=[dict(e['record'] or dict(id=e['sample']['id'], record_status='registration not finished'),
            data=[dict(d['record'], original_files_folder=f"originals/{e['sample']['id']}/{d['id']}" if originals else None)
                for d in e['data']],
            shipments=[s['record'] for s in e['shipments']]) for e in entries],
        procedures=built['procedures'])
    (target / 'catalyst_dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding='utf-8')
    files = 0
    if originals:
        total = built['file_count']
        for e in entries:
            for d in e['data']:
                checksums = {f['name']: f['sha256'] for f in d['record'].get('files', [])}
                out = target / 'originals' / e['sample']['id'] / d['id']
                out.mkdir(parents=True, exist_ok=True)
                for row in d['files']:
                    files += 1
                    progress(f"Downloading file {files} of {total}: {row.get('realName')}…")
                    (out / Path(str(row.get('realName'))).name).write_bytes(
                        store.download(d['section_id'], row, checksums.get(row.get('realName'))))
    (target / 'README.txt').write_text(README, encoding='utf-8')
    return dict(folder=str(target), samples=len(entries), data=sum(len(e['data']) for e in entries), files=files)


def export_dataset(store, samples, folder, technique=None, originals=False, progress=lambda _: None):
    return write_dataset(store, build_dataset(store, samples, technique, progress), folder, originals, progress)


README = """CATALYST dataset export
=======================

samples.csv         one row per sample: identity, source (made in lab or commercial: supplier, product, lot),
                    composition, procedure + version, the recipe actually used (recipe_* columns, with one
                    recipe_load_<component> (<unit>) column per metal/phase), automatic deviations from the master
                    procedure, and a summary of its data.
data_records.csv    one row per measurement / test, with its conditions (cond_* columns) and the sample's key
                    recipe columns repeated (sample_*) so each row can be used directly for modeling.
procedures.csv      every version of every shared master procedure.
catalyst_dataset.json  everything above, nested (sample -> data records -> conditions, files; shipments; procedures).
originals/          (optional) the untouched original files: originals/<sample ID>/<data ID>/<file>.

Sample IDs are LAB-INITIALS-YYMMDD-NN; data IDs are <sample ID>-<TECHNIQUE>-NN.
Numbers in recipe_* columns are in the units named in the column (°C, h, °C/min, g).
"""


def mcp_snippet(server):
    """Configuration block for an MCP-capable AI assistant (read-only CATALYST connector)."""
    return json.dumps({'mcpServers': {'catalyst': {
        'command': 'python', 'args': ['-m', 'catalyst_query.mcp_server'],
        'env': {'CATALYST_SERVER': server, 'CATALYST_TOKEN': 'PASTE-LAB-TOKEN',
            'PYTHONPATH': 'PATH-TO-THE-CATALYST-FOLDER'}}}}, indent=2)
