"""Read-only CATALYST reader shared by the Python API, the command line and the MCP server."""
from __future__ import annotations

import csv
import io
import os
import threading
import time

from catalyst_desktop import records, ids
from catalyst_desktop.dataset import data_rows, sample_row
from catalyst_desktop.scisure import SciSureClient, SANDBOX
from catalyst_desktop.store import Store


class ReadOnlyError(PermissionError):
    pass


class ReadOnlyClient(SciSureClient):
    def request(self, path, method='GET', data=None, binary=False):
        if method != 'GET' or data is not None:
            raise ReadOnlyError('The CATALYST reader is read-only.')
        return super().request(path, method, data, binary)


def _public(item):
    """A data/shipment section without SciSure internals."""
    return dict(item['record'], file_names=[f.get('realName') for f in item.get('files', [])])


class CatalystReader:
    def __init__(self, token=None, server=None, transport=None, cache_seconds=300):
        token = token or os.environ.get('CATALYST_TOKEN')
        if not token:
            raise ValueError('Provide a lab token (or set CATALYST_TOKEN).')
        self.store = Store(ReadOnlyClient(token, server or os.environ.get('CATALYST_SERVER') or SANDBOX, transport=transport))
        self.connection = self.store.connect()
        if not self.store.workspace_ready():
            raise ValueError('This token cannot see the shared CATALYST project.')
        self.cache_seconds = cache_seconds
        self._cache, self._lock = {}, threading.Lock()

    def _cached(self, key, compute):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.cache_seconds:
                return hit[1]
        value = compute()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def refresh(self):
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------ samples
    def _sample_index(self):
        return self._cached('samples', lambda: self.store.list_samples(include_retired=True))

    def samples(self, search=None, lab=None, researcher=None, procedure=None, since=None):
        words = str(search or '').casefold().split()
        lab_key = ids.lab_key(lab) if lab else None
        result = []
        for s in self._sample_index():
            if s.get('status') == 'registered_in_error': continue
            text = f"{s['id']} {s['composition']} {s['procedure']}".casefold()
            if words and not all(w in text for w in words): continue
            if lab_key and s['lab'] != lab_key: continue
            if researcher and s['initials'] != str(researcher).upper(): continue
            if procedure and not s['procedure'].startswith(str(procedure).upper()): continue
            if since and s['date'] < str(since): continue
            result.append(dict(sample_id=s['id'], composition=s['composition'], procedure=s['procedure'],
                source=s.get('source', 'synthesized'), origin=s.get('origin', s['procedure']),
                lab=s['lab_name'], researcher_initials=s['initials'], synthesis_date=s['date']))
        return result

    def _open(self, sample_id):
        sample = next((s for s in self._sample_index() if s['id'] == sample_id), None)
        if sample is None:
            raise KeyError(f'No sample {sample_id}.')
        return self._cached('open/' + sample_id, lambda: self.store.open_sample(sample))

    def sample(self, sample_id):
        entry = self._open(sample_id)
        result = dict(sample=entry['record'], data=[_public(d) for d in entry['data']],
            shipments=[s['record'] for s in entry['shipments']], unfinished_uploads=len(entry['incomplete']))
        if entry.get('withdrawn'):
            result['withdrawn_data'] = [dict(data_id=d['id'], reason=d['record'].get('status_note', '')) for d in entry['withdrawn']]
        if records.status_of(entry['record']) == 'registered_in_error':
            result['warning'] = ('This sample was registered in error and must not be used'
                + (f"; use {entry['record']['replaced_by']} instead." if entry['record'].get('replaced_by') else '.'))
        return result

    # ------------------------------------------------------------------ procedures
    def procedures(self):
        return [dict(procedure_id=p['id'], name=p['name'], type=p.get('category', 'synthesis'))
            for p in self._cached('procedures', self.store.list_procedures)]

    def procedure(self, procedure_id):
        match = next((p for p in self._cached('procedures', self.store.list_procedures) if p['id'] == procedure_id), None)
        if match is None:
            raise KeyError(f'No procedure {procedure_id}.')
        versions = self._cached('procedure/' + procedure_id, lambda: self.store.procedure_versions(match))
        return dict(procedure_id=procedure_id, name=match['name'], type=match.get('category', 'synthesis'),
            versions=[dict(v['record'], file_names=[f.get('realName') for f in v['files']]) for v in versions])

    # ------------------------------------------------------------------ data
    def entries(self, sample_ids=None, technique=None):
        index = self._sample_index()
        if sample_ids is None:
            chosen = [s for s in index if s.get('status') != 'registered_in_error']
        else:
            wanted = set(sample_ids)
            chosen = [s for s in index if s['id'] in wanted]
        code = ids.technique_code(technique) if technique else None
        return [dict(e, data=[d for d in e['data'] if not code or d['technique'] == code])
            for e in (self._open(s['id']) for s in chosen)]

    def data(self, technique=None, sample_ids=None, search=None, lab=None):
        """One flat row per data record, with conditions and the sample's recipe columns."""
        if sample_ids is None and (search or lab):
            sample_ids = [s['sample_id'] for s in self.samples(search=search, lab=lab)]
        entries = self.entries(sample_ids, technique)
        keys = []
        for e in entries:
            for d in e['data']:
                for k in d['record'].get('conditions', {}):
                    if k not in keys: keys.append(k)
        return [row for e in entries for row in data_rows(e, keys)]

    def sample_table(self, sample_ids=None):
        return [sample_row(e) for e in self.entries(sample_ids)]

    def _data_item(self, data_id):
        match = ids.DATA_RE.fullmatch(str(data_id))
        if not match:
            raise KeyError('Use a data ID such as UR-MDP-260925-01-XRD-01.')
        sample_id = data_id[:match.end('n')]
        item = next((d for d in self._open(sample_id)['data'] if d['id'] == data_id), None)
        if item is None:
            raise KeyError(f'No data record {data_id}.')
        return item

    def file_bytes(self, data_id, filename):
        item = self._data_item(data_id)
        row = next((f for f in item['files'] if f.get('realName') == filename), None)
        if row is None:
            raise KeyError(f"{data_id} has no file {filename}. Files: {', '.join(str(f.get('realName')) for f in item['files'])}")
        checksum = next((f['sha256'] for f in item['record'].get('files', []) if f['name'] == filename), None)
        return self.store.download(item['section_id'], row, checksum)

    def file_preview(self, data_id, filename, max_rows=200):
        """Text/table preview of a data file for an AI assistant (first ``max_rows`` rows)."""
        content = self.file_bytes(data_id, filename)
        lower = filename.lower()
        if lower.endswith('.xlsx'):
            from catalyst_ingest.readers import read_artifact, coordinate_parts
            artifact = read_artifact(content, filename)
            out = []
            for name, sheet in artifact['sheets'].items():
                grid = {}
                for address, cell in sheet['cells'].items():
                    r, c = coordinate_parts(address)
                    if r <= max_rows:
                        grid.setdefault(r, {})[c] = '' if cell.get('value') is None else str(cell.get('value'))
                buffer = io.StringIO()
                writer = csv.writer(buffer, delimiter='\t')
                for r in sorted(grid):
                    width = max(grid[r])
                    writer.writerow([grid[r].get(c, '') for c in range(1, width + 1)])
                out.append(f'# sheet: {name}\n' + buffer.getvalue())
            return '\n'.join(out)
        for encoding in ('utf-8-sig', 'latin-1'):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if '\x00' in text[:4096]:
            return f'{filename} is a binary file ({len(content)} bytes); download it with file_bytes().'
        lines = text.splitlines()
        more = f'\n… {len(lines) - max_rows} more lines' if len(lines) > max_rows else ''
        return '\n'.join(lines[:max_rows]) + more

    def summary(self):
        index = [s for s in self._sample_index() if s.get('status') != 'registered_in_error']
        by_lab, by_procedure = {}, {}
        for s in index:
            by_lab[s['lab_name']] = by_lab.get(s['lab_name'], 0) + 1
            key = s['procedure'].split(' v')[0] or 'none'
            by_procedure[key] = by_procedure.get(key, 0) + 1
        return dict(samples=len(index), samples_by_lab=by_lab, samples_by_procedure=by_procedure,
            procedures=len(self.procedures()), techniques=ids.TECHNIQUES,
            id_formats=dict(sample='LAB-INITIALS-YYMMDD-NN', data='<sample>-<TECH>-NN', procedure='PRC-LAB-NNN'))
