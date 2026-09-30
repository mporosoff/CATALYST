"""Where CATALYST keeps things in SciSure.

Layout (created once by the coordinator with *Set up CATALYST workspace*):

    Project  "CATALYST"                      shared with every lab account
      Study  "CATALYST Samples"              one experiment per sample
               experiment name: "UR-MDP-260925-01 | 5 wt% Mo / γ-Al2O3 | PRC-UR-001 v2"
               FILE section "CATALYST sample | UR-MDP-260925-01"            record + synthesis files
               FILE section "CATALYST data | UR-MDP-260925-01-XRD-01 | …"   record + original data files
               FILE section "CATALYST shipment | UR-MDP-260925-01-SHP-01 | UR>SLAC | …"
      Study  "CATALYST Procedures"           one experiment per master procedure
               experiment name: "PRC-UR-001 | Mo2C carburization"
               FILE section "CATALYST procedure | PRC-UR-001 v1 | …"        one section per version

Every section holds ``catalyst-record.json`` plus the untouched original files.
Originals are uploaded first and the record last, so a section without a record
is an unfinished upload. Every uploaded file is downloaded again and its
SHA-256 checksum compared before the save is reported as complete.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode

from . import ids, records
from .model import encode, digest, MAX_FILE
from .scisure import SciSureError, remote_id
from catalyst_ingest.jsonio import strict_loads

PROJECT = 'CATALYST'
SAMPLES_STUDY = 'CATALYST Samples'
PROCEDURES_STUDY = 'CATALYST Procedures'
CHANGES_LOG = 'CATALYST corrections log'   # experiment in the Samples study; one section per sample correction
LIST_LIMIT = 100_000
MAX_FILES = 50
PERMISSION_HINT = ('Your lab account cannot add to this record. Ask the CATALYST coordinator to share the '
    'CATALYST project with your lab account (collaborator with edit rights).')


class StoreError(Exception):
    pass


class FileItem:
    """A file chosen by the researcher, read from disk only while uploading."""
    def __init__(self, path=None, name=None, content=None):
        self.path = Path(path) if path else None
        self.name = name or (self.path.name if self.path else '')
        self._content = content

    def read(self):
        if self._content is not None:
            return self._content
        with self.path.open('rb') as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise StoreError(f'{self.name} is larger than 20 MB. Split or compress it before uploading.')
        return data


def check_files(files, required=True):
    names = [f.name for f in files]
    if required and not files:
        raise StoreError('Add at least one file.')
    if len(files) > MAX_FILES:
        raise StoreError(f'Add at most {MAX_FILES} files to one record.')
    if len({n.casefold() for n in names}) != len(names):
        raise StoreError('Two files have the same name. Rename one of them.')
    reserved = [n for n in names if records.record_file_revision(n.casefold()) is not None]
    if reserved:
        raise StoreError(f'"{reserved[0]}" is a name reserved by CATALYST. Rename that file.')
    for f in files:
        if f.path is not None:
            if not f.path.is_file():
                raise StoreError(f'{f.name} can no longer be found on this computer.')
            size = f.path.stat().st_size
            if size == 0:
                raise StoreError(f'{f.name} is empty.')
            if size > MAX_FILE:
                raise StoreError(f'{f.name} is larger than 20 MB. Split or compress it before uploading.')


def change_header(record):
    """Corrections-log entry: lets the sample list show a correction without opening every sample."""
    name = records.parse_sample_experiment_name(records.sample_experiment_name(record))
    return records.SEP.join(['CATALYST change', record['id'], records.status_of(record), f"r{records.revision_of(record)}",
        name['composition'] or '—', name['origin'] or '—'])


def parse_change(header):
    parts = [p.strip() for p in str(header or '').split(records.SEP)]
    if len(parts) < 6 or parts[0] != 'CATALYST change' or not parts[3].startswith('r') or not parts[3][1:].isdigit():
        return None
    return dict(id=parts[1], status=parts[2], revision=int(parts[3][1:]),
        composition='' if parts[4] == '—' else parts[4], origin='' if parts[5] == '—' else parts[5])


def same_change(saved, attempt):
    """True when ``saved`` is this very correction, saved by an earlier attempt that lost its connection."""
    keys = ('revision_note', 'status')
    return all(saved.get(k) == attempt.get(k) for k in keys) and \
        (saved.get('revised_by') or {}).get('name') == (attempt.get('revised_by') or {}).get('name')


class Store:
    def __init__(self, client, app_version='', project=PROJECT):
        self.client, self.app_version, self.project_name = client, app_version, project
        self.group_id = None
        self.workspace = None
        self.account = {}
        self._ops = {}
        self.last_reserved = None
        self._log_id = None

    # ------------------------------------------------------------------ connection
    def connect(self):
        group = self.client.object('/api/v1/groups/active')
        self.group_id = remote_id(group.get('groupID'))
        try:
            account = self.client.object('/api/v1/users/getCurrentUserInfo')
        except SciSureError:
            account = {}
        self.account = dict(name=' '.join(str(account.get(k) or '').strip() for k in ('firstName', 'lastName')).strip(),
            email=str(account.get('email') or account.get('userName') or ''))
        self.workspace = self.find_workspace()
        return dict(group_id=self.group_id, group_name=group.get('name') or group.get('groupName') or str(self.group_id),
            account=self.account, workspace=self.workspace)

    def _active(self, rows, key):
        return sorted((r for r in rows if r.get('groupID') in (None, self.group_id)), key=lambda r: remote_id(r.get(key)))

    def find_workspace(self):
        projects = [p for p in self._active(self.client.list('/api/v1/projects'), 'projectID')
            if str(p.get('name', '')).strip().casefold() == self.project_name.casefold() and p.get('active') is not False]
        if not projects:
            return None
        project_id = remote_id(projects[0]['projectID'])
        studies = self.client.list('/api/v1/studies?' + urlencode({'projectID': project_id}))
        found = dict(project_id=project_id, duplicate_projects=len(projects) > 1)
        for key, name in (('samples_study', SAMPLES_STUDY), ('procedures_study', PROCEDURES_STUDY)):
            rows = [s for s in self._active(studies, 'studyID') if s.get('projectID') == project_id
                and s.get('deleted') is not True and str(s.get('name', '')).strip().casefold() == name.casefold()]
            found[key] = remote_id(rows[0]['studyID']) if rows else None
        return found

    def workspace_ready(self):
        w = self.workspace
        return bool(w and w.get('samples_study') and w.get('procedures_study'))

    def create_workspace(self):
        """Coordinator-only: create the shared project and its two studies if missing."""
        w = self.find_workspace()
        if w is None:
            self.client.request('/api/v1/projects', 'POST', dict(name=self.project_name,
                notes='Shared CATALYST consortium data. Managed by the CATALYST desktop app; do not rename.'))
            w = self.find_workspace()
            if w is None:
                raise StoreError('The CATALYST project was requested but could not be found afterwards. Refresh.')
        for key, name in (('samples_study', SAMPLES_STUDY), ('procedures_study', PROCEDURES_STUDY)):
            if not w.get(key):
                self.client.request('/api/v1/studies', 'POST', dict(name=name, projectID=w['project_id'], approve='NOTREQUIRED'))
        self.workspace = self.find_workspace()
        if not self.workspace_ready():
            raise StoreError('The CATALYST studies could not be confirmed. Refresh and check SciSure.')
        return self.workspace

    def _need_workspace(self):
        if not self.workspace_ready():
            raise StoreError('The shared CATALYST workspace was not found for this account. Ask the coordinator to share '
                'the "CATALYST" project with your lab account, or set it up in Settings → Coordinator tools.')

    # ------------------------------------------------------------------ listing
    def _experiments(self, study_id):
        rows = self.client.list('/api/v1/experiments?' + urlencode({'studyID': study_id}), limit=LIST_LIMIT)
        return [r for r in rows if r.get('studyID') == study_id and r.get('deleted') is not True and r.get('template') is not True]

    def list_samples(self, include_retired=False):
        self._need_workspace()
        result, log = [], None
        for row in self._experiments(self.workspace['samples_study']):
            if row.get('name') == CHANGES_LOG:
                log = log if log is not None and remote_id(log['experimentID']) < remote_id(row['experimentID']) else row
                continue
            info = records.parse_sample_experiment_name(row.get('name'))
            if info:
                info.update(experiment_id=remote_id(row['experimentID']), created=str(row.get('created') or ''),
                    experiment_name=row.get('name'), status='active', revision=1)
                result.append(info)
        if log is not None:
            self._log_id = remote_id(log['experimentID'])
            latest = {}
            for section in self.client.list(f"/api/v1/experiments/{self._log_id}/sections"):
                change = parse_change(section.get('sectionHeader'))
                if change and section.get('deleted') is not True and change['revision'] >= latest.get(change['id'], {}).get('revision', 0):
                    latest[change['id']] = change
            for info in result:
                change = latest.get(info['id'])
                if change:
                    info.update(status=change['status'], revision=change['revision'])
                    if change.get('composition'): info['composition'] = change['composition']
                    if change.get('origin') is not None:
                        info['origin'] = change['origin']
                        info['procedure'] = '' if change['origin'].startswith('Commercial') else change['origin']
                        info['source'] = 'commercial' if change['origin'].startswith('Commercial') else 'synthesized'
        if not include_retired:
            result = [s for s in result if s['status'] != 'registered_in_error']
        return sorted(result, key=lambda s: (s['date'], s['id']), reverse=True)

    def list_procedures(self):
        self._need_workspace()
        result = []
        for row in self._experiments(self.workspace['procedures_study']):
            info = records.parse_procedure_experiment_name(row.get('name'))
            if info:
                info.update(experiment_id=remote_id(row['experimentID']),
                    category='testing' if ids.is_test_protocol(info['id']) else 'synthesis')
                result.append(info)
        return sorted(result, key=lambda p: p['id'])

    def sections(self, experiment_id):
        rows = self.client.list(f'/api/v1/experiments/{remote_id(experiment_id)}/sections')
        result = []
        for row in rows:
            info = records.parse_section_header(row.get('sectionHeader'))
            if info and row.get('sectionType') in ('FILE', 'FILES') and row.get('deleted') is not True:
                info.update(section_id=remote_id(row['expJournalID']), header=row.get('sectionHeader'),
                    author=' '.join(str(row.get(k) or '') for k in ('firstName', 'lastName')).strip())
                result.append(info)
        return result

    def files(self, section_id):
        return [f for f in self.client.list(f'/api/v1/experiments/sections/{remote_id(section_id)}/files')
            if f.get('deleted') is not True and f.get('archived') is not True]

    def download(self, section_id, file_row, expected_sha256=None):
        if file_row.get('origin') == 'ONSITE':
            raise StoreError('This file is kept in institutional eLABHybrid storage; open it in SciSure.')
        content = self.client.request(f'/api/v1/experiments/sections/{remote_id(section_id)}/files/'
            f'{remote_id(file_row.get("experimentFileID"))}', binary=True)
        size = file_row.get('fileSize')
        if (type(size) is int and len(content) != size) or (expected_sha256 and digest(content) != expected_sha256):
            raise StoreError(f'{file_row.get("realName")} failed its integrity check after download.')
        return content

    def _record_rows(self, rows):
        """Record files by revision, oldest first. If two people saved the same revision at once, the first upload wins."""
        first = {}
        for row in rows:
            rev = records.record_file_revision(row.get('realName'))
            if rev is not None and (rev not in first or
                    remote_id(row['experimentFileID']) < remote_id(first[rev]['experimentFileID'])):
                first[rev] = row
        return sorted(first.items())

    def _read_record(self, section_id, row, section=None, revision=None):
        try:
            record = strict_loads(self.download(section_id, row))
        except (ValueError, StoreError):
            return None
        if not isinstance(record, dict) or record.get('format') != records.RECORD_FORMAT:
            return None
        if revision is not None and records.revision_of(record) != revision:
            return None  # e.g. a researcher's own file that happens to use a reserved name (allowed before 1.3)
        if section and (record.get('id') != section.get('id') or record.get('kind') != section.get('kind')):
            return None
        return record

    def load_record(self, section):
        """The section's latest readable record (None if the upload never finished) and its current files.

        Only files listed in the record are current. Files a correction marked superseded are returned separately
        in ``section['superseded']``; leftovers from an interrupted save are ignored."""
        rows = self.files(section['section_id'])
        record = None
        for revision, row in reversed(self._record_rows(rows)):
            record = self._read_record(section['section_id'], row, section, revision)
            if record is not None:
                break
        if record is None:
            return None, [f for f in rows if records.record_file_revision(f.get('realName')) is None]
        def pick(names):
            chosen = {}
            for f in sorted(rows, key=lambda f: remote_id(f['experimentFileID'])):
                if f.get('realName') in names:
                    chosen.setdefault(f['realName'], f)
            return [chosen[n] for n in names if n in chosen]
        section['superseded'] = pick(list(record.get('superseded_files') or []))
        return record, pick([f['name'] for f in record.get('files', [])])

    def record_history(self, section):
        """Every saved revision of a record, oldest first."""
        return [r for r in (self._read_record(section['section_id'], row, section, rev)
            for rev, row in self._record_rows(self.files(section['section_id']))) if r]

    def open_sample(self, sample, progress=lambda _: None):
        """Everything saved for one sample: its record, data records and shipping log."""
        progress(f'Opening {sample["id"]}…')
        sections = self.sections(sample['experiment_id'])
        def load(section):
            record, files = self.load_record(section)
            return dict(section, record=record, files=files, complete=record is not None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            loaded = list(pool.map(load, sections))
        result = dict(sample=sample, record=None, sample_files=[], data=[], withdrawn=[], shipments=[], incomplete=[])
        for item in loaded:
            if not item['complete']:
                result['incomplete'].append(item)
            elif item['kind'] == 'sample' and item['id'] == sample['id']:
                result['record'], result['sample_files'], result['sample_section'] = item['record'], item['files'], item['section_id']
                result['sample_item'] = item
            elif item['kind'] == 'data':
                (result['withdrawn'] if records.status_of(item['record']) == 'withdrawn' else result['data']).append(item)
            elif item['kind'] == 'shipment':
                result['shipments'].append(item)
        for item in result['data'] + result['withdrawn']:
            item['date'] = item['record'].get('date', item['date'])  # a correction may have changed the date
        result['data'].sort(key=lambda d: (d['date'], d['id']))
        result['shipments'].sort(key=lambda d: (d['date'], d['id']))
        return result

    def procedure_versions(self, procedure):
        versions = []
        for section in self.sections(procedure['experiment_id']):
            if section['kind'] == 'procedure' and section['id'] == procedure['id']:
                record, files = self.load_record(section)
                if record:
                    versions.append(dict(section, record=record, files=files))
        return sorted(versions, key=lambda v: v['version'])

    # ------------------------------------------------------------------ writing
    def _guarded(self, key, find, write):
        found = find()
        if found is not None:
            return found
        try:
            write()
        except SciSureError as error:
            if error.status == 403:
                raise StoreError(PERMISSION_HINT) from None
            raise
        found = find()
        if found is None:
            raise SciSureError('SciSure accepted the save but has not shown it yet. Refresh before trying again.', True)
        return found

    def _experiment(self, study_id, name):
        def find():
            rows = [r for r in self._experiments(study_id) if r.get('name') == name]
            return remote_id(min(rows, key=lambda r: remote_id(r['experimentID']))['experimentID']) if rows else None
        return self._guarded('experiment/' + name, find, lambda: self.client.request('/api/v1/experiments', 'POST',
            dict(studyID=study_id, name=name, status='PROGRESS', autoCollaborate=True)))

    def _section(self, experiment_id, header):
        def find():
            rows = [s for s in self.client.list(f'/api/v1/experiments/{experiment_id}/sections')
                if s.get('sectionHeader') == header and s.get('deleted') is not True]
            return remote_id(min(rows, key=lambda r: remote_id(r['expJournalID']))['expJournalID']) if rows else None
        return self._guarded('section/' + header, find, lambda: self.client.request(
            f'/api/v1/experiments/{experiment_id}/sections', 'POST', dict(sectionType='FILE', sectionHeader=header)))

    def _upload(self, section_id, name, content):
        path = f'/api/v1/experiments/sections/{section_id}/files'
        expected = digest(content)
        def find():
            rows = [f for f in self.files(section_id) if f.get('realName') == name and f.get('fileSize') == len(content)]
            for row in sorted(rows, key=lambda r: remote_id(r['experimentFileID'])):
                if digest(self.download(section_id, row)) == expected:
                    return remote_id(row['experimentFileID'])
            return None
        return self._guarded(f'file/{section_id}/{name}/{expected}', find,
            lambda: self.client.request(path + '?fileName=' + quote(name, safe=''), 'POST', content))

    def _save_section(self, experiment_id, record, files, progress):
        """Originals first, record last: the record's presence marks the save complete."""
        check_files(files, required=False)
        header = records.section_header(record)
        section_id = self._section(experiment_id, header)
        manifest = []
        for index, item in enumerate(files, 1):
            progress(f'Uploading and checking {item.name} ({index} of {len(files)})…')
            content = item.read()
            self._upload(section_id, item.name, content)
            manifest.append(dict(name=item.name, sha256=digest(content), size_bytes=len(content)))
        record = dict(record, files=manifest)
        progress('Saving the record…')
        self._upload(section_id, records.RECORD_FILE, encode(record))
        result = dict(section_id=section_id, record=record, readable_warning=None)
        progress('Writing the readable copy in SciSure…')
        try:
            self.write_readable(experiment_id, record, section_id)
        except (SciSureError, StoreError, KeyError, TypeError, ValueError) as error:
            # The record itself is saved and complete; the readable copy is a convenience that can be rewritten.
            result['readable_warning'] = str(error)
        return result

    def write_readable(self, experiment_id, record, before_section=None):
        """Create (or refresh) the human-readable text section that sits just above the record's file section."""
        header = records.readable_header(record)
        path = f'/api/v1/experiments/{experiment_id}/sections'
        rows = self.client.list(path)
        existing = [r for r in rows if r.get('sectionHeader') == header and r.get('deleted') is not True
            and r.get('sectionType') == 'PARAGRAPH']
        if existing:
            text_id = remote_id(min(existing, key=lambda r: remote_id(r['expJournalID']))['expJournalID'])
        else:
            body = dict(sectionType='PARAGRAPH', sectionHeader=header)
            anchor = next((r for r in rows if before_section and r.get('expJournalID') == before_section), None)
            if anchor is not None and isinstance(anchor.get('order'), int):
                body['order'] = anchor['order']
            try:
                self.client.request(path, 'POST', body)
            except SciSureError as error:
                if error.status == 403:
                    raise StoreError(PERMISSION_HINT) from None
                raise
            found = [r for r in self.client.list(path) if r.get('sectionHeader') == header and r.get('deleted') is not True]
            if not found:
                raise StoreError('The readable text section could not be confirmed in SciSure.')
            text_id = remote_id(min(found, key=lambda r: remote_id(r['expJournalID']))['expJournalID'])
        self.client.request(f'/api/v1/experiments/sections/{text_id}/content', 'PUT',
            dict(contents=records.readable_html(record)))
        return text_id

    def write_missing_readable(self, progress=lambda _: None):
        """Coordinator tool: add readable copies to records saved before this feature (or whose copy failed)."""
        self._need_workspace()
        logged = self.list_samples(include_retired=True)   # as the sample list currently shows them
        experiments = [s['experiment_id'] for s in logged] + [p['experiment_id'] for p in self.list_procedures()]
        written = failed = 0
        for index, experiment_id in enumerate(experiments, 1):
            progress(f'Checking record {index} of {len(experiments)}…')
            rows = self.client.list(f'/api/v1/experiments/{experiment_id}/sections')
            headers = {r.get('sectionHeader') for r in rows if r.get('sectionType') == 'PARAGRAPH' and r.get('deleted') is not True}
            for section in self.sections(experiment_id):
                record, _files = self.load_record(section)
                if record and record['kind'] == 'sample' and records.revision_of(record) > 1:
                    listed = next((x for x in logged if x['id'] == record['id']), None)
                    if not listed or listed['revision'] != records.revision_of(record) or listed['status'] != records.status_of(record):
                        try:
                            self._log_change(record); written += 1
                        except (SciSureError, StoreError):
                            failed += 1
                if not record or records.readable_header(record) in headers:
                    continue
                try:
                    self.write_readable(experiment_id, record, section['section_id'])
                    written += 1
                except (SciSureError, StoreError):
                    failed += 1
        return dict(written=written, failed=failed, checked=len(experiments))

    # ------------------------------------------------------------------ corrections
    def revise_record(self, experiment_id, section, updated, new_files=(), supersede=(), expected_revision=None,
            progress=lambda _: None):
        """Save a corrected record as the next revision in the same section.

        ``updated`` is the full new record from records.revise(). New files are added (renamed if a file of that
        name exists); files named in ``supersede`` stay in SciSure but are no longer current."""
        check_files(list(new_files), required=False)
        progress('Checking that nobody else changed this record…')
        current, _files = self.load_record(section)
        if current is None:
            raise StoreError('This record could not be read. Refresh and try again.')
        section_id = section['section_id']
        if expected_revision is not None and records.revision_of(current) != expected_revision:
            if records.revision_of(current) == expected_revision + 1 and same_change(current, updated):
                return self._after_revision(experiment_id, section_id, current)  # an earlier attempt already saved it
            by = current.get('revised_by') or {}
            raise StoreError(f"{by.get('name', 'Someone')} corrected this record while you were editing it. Reopen it, "
                'check their change and make your correction again.')
        revision = records.revision_of(current) + 1
        if records.revision_of(updated) != revision:
            raise StoreError('This correction is out of date. Reopen the record and try again.')
        rows = self.files(section_id)
        in_use = {f['name'] for f in current.get('files', [])} | set(current.get('superseded_files') or [])
        leftovers = {f.get('realName') for f in rows} - in_use   # from an interrupted attempt: safe to reuse
        chosen_names, renamed = set(), {}
        active = [f for f in current.get('files', []) if f['name'] not in set(supersede)]
        if len(active) + len(new_files) > MAX_FILES:
            raise StoreError(f'A record can hold at most {MAX_FILES} current files.')
        for index, item in enumerate(new_files, 1):
            content = item.read()
            name = self._free_name(section_id, rows, item.name, content, revision, in_use | chosen_names, leftovers)
            chosen_names.add(name)
            renamed[item.name] = name
            progress(f'Uploading and checking {name} ({index} of {len(new_files)})…')
            self._upload(section_id, name, content)
            active.append(dict(name=name, sha256=digest(content), size_bytes=len(content)))
        record = dict(updated, files=active, superseded_files=sorted(set(current.get('superseded_files') or []) | set(supersede)))
        if renamed and record.get('extracted'):  # keep "read from" links pointing at the names actually saved
            record['extracted'] = [dict(e, file=renamed.get(e.get('file'), e.get('file')),
                derived_files=[renamed.get(n, n) for n in e.get('derived_files', [])]) for e in record['extracted']]
        if renamed and (record.get('results') or {}).get('source_file') in renamed:
            record['results'] = dict(record['results'], source_file=renamed[record['results']['source_file']])
        if record.get('history'):
            record['history'] = record['history'][:-1] + [dict(record['history'][-1], changes=records.changed_fields(current, record))]
        if record['kind'] == 'data' and not active and records.status_of(record) == 'active':
            raise StoreError('A data record needs at least one current file. Add the corrected file, or withdraw the record.')
        progress('Saving the corrected record…')
        content = encode(record)
        file_name = records.record_file_name(revision)
        self._upload(section_id, file_name, content)
        # Two people saving the same revision at the same moment: the first upload wins, the other is told.
        winner = dict(self._record_rows(self.files(section_id))).get(revision)
        if winner is None or digest(self.download(section_id, winner)) != digest(content):
            raise StoreError('Someone else saved a correction to this record at the same moment, so yours was not '
                'applied. Reopen the record, check their change and make your correction again.')
        return self._after_revision(experiment_id, section_id, record)

    def _after_revision(self, experiment_id, section_id, record):
        """Sample list entry and readable copy. The record itself is already saved, so problems here are warnings."""
        result = dict(section_id=section_id, record=record, readable_warning=None)
        problems = []
        if record['kind'] == 'sample':
            try:
                self._log_change(record)
            except (SciSureError, StoreError) as error:
                problems.append(f'the sample list entry could not be updated: {error}')
        try:
            self.write_readable(experiment_id, record, section_id)
        except (SciSureError, StoreError, KeyError, TypeError, ValueError) as error:
            problems.append(str(error))
        result['readable_warning'] = '; '.join(problems) or None
        return result

    def _free_name(self, section_id, rows, name, content, revision, in_use, leftovers):
        stem, dot, ext = name.rpartition('.')
        stem, ext = (stem, '.' + ext) if dot and stem else (name, '')
        candidates = [name] + [f'{stem} (r{revision}){ext}'] + [f'{stem} (r{revision}-{n}){ext}' for n in range(2, 1000)]
        expected = digest(content)
        for candidate in candidates:
            if candidate in in_use:
                continue
            if candidate not in leftovers:
                return candidate
            # A file of this name was left by an interrupted attempt: reuse it only if it is this same file.
            same = [f for f in rows if f.get('realName') == candidate]
            if all(f.get('fileSize') == len(content) and digest(self.download(section_id, f)) == expected for f in same):
                return candidate
        raise StoreError(f'Could not find a free file name for {name}.')

    def _log_change(self, record):
        log_id = self._log_id or self._experiment(self.workspace['samples_study'], CHANGES_LOG)
        self._log_id = log_id
        self._section(log_id, change_header(record))

    def set_status(self, experiment_id, item, profile, reason, status, replaced_by=None, progress=lambda _: None):
        """Withdraw data, retire a sample (registered in error), or restore either one. Nothing is deleted."""
        current = item['record']
        updated = dict(current)
        if current['kind'] == 'sample':
            updated['replaced_by'] = replaced_by if status == 'registered_in_error' else None
        record, problems = records.revise(current, updated, profile, reason, status=status, status_note=reason)
        if problems:
            raise StoreError(' '.join(problems))
        return self.revise_record(experiment_id, item, record, expected_revision=records.revision_of(current),
            progress=progress)

    def retire_sample(self, opened, profile, reason, replaced_by=None, progress=lambda _: None):
        return self.set_status(opened['sample']['experiment_id'], opened['sample_item'], profile, reason,
            'registered_in_error', replaced_by, progress)

    def withdraw_data(self, sample, item, profile, reason, progress=lambda _: None):
        return self.set_status(sample['experiment_id'], item, profile, reason, 'withdrawn', progress=progress)

    def restore(self, experiment_id, item, profile, reason, progress=lambda _: None):
        return self.set_status(experiment_id, item, profile, reason, 'active', progress=progress)

    def create_sample(self, build, files, progress=lambda _: None, reserved_id=None):
        """``build(sample_id)`` returns the finished sample record for the ID assigned now.

        If an earlier attempt created the experiment but failed before finishing, retrying with the same
        details (in this session, or with ``reserved_id`` from a saved draft) finishes that same sample
        instead of burning a new number."""
        self._need_workspace()
        self.last_reserved = None
        check_files(files, required=False)
        progress('Choosing the next free sample ID…')
        probe = build(None)
        by, when = probe['created_by'], records.sample_date(probe)
        prefix = ids.sample_prefix(by['lab'], by['initials'], when)
        listed = self.list_samples(include_retired=True)
        sample_id = None
        for candidate in (reserved_id, self._ops.get('reserved/' + prefix)):
            if candidate and candidate.startswith(prefix):
                name = records.sample_experiment_name(build(candidate))
                if all(row['experiment_name'] == name for row in listed if row['id'] == candidate):
                    sample_id = candidate
                    break
        if not sample_id:
            sample_id = ids.next_sample_id([s['id'] for s in listed if s['id'].startswith(prefix)], by['lab'], by['initials'], when)
        self.last_reserved = sample_id
        self._ops['reserved/' + prefix] = sample_id
        record = build(sample_id)
        name = records.sample_experiment_name(record)
        progress(f'Creating {sample_id} in SciSure…')
        experiment_id = self._experiment(self.workspace['samples_study'], name)
        clashes = [s for s in self.list_samples(include_retired=True) if s['id'] == sample_id and s['experiment_id'] != experiment_id]
        saved = self._save_section(experiment_id, record, files, progress)
        self._ops.pop('reserved/' + prefix, None)
        self.last_reserved = None
        info = records.parse_sample_experiment_name(name)
        info.update(experiment_id=experiment_id, created='')
        return dict(sample=info, record=saved['record'], duplicate_warning=bool(clashes),
            readable_warning=saved['readable_warning'])

    def add_data(self, sample, build, files, progress=lambda _: None, reserved_id=None):
        """``build(data_id)`` returns the data record for the ID assigned now.

        A retry of an interrupted upload (same session, or ``reserved_id`` from a saved draft) finishes the same
        data record: files that already arrived are recognised by checksum and not sent again."""
        self.last_reserved = None
        check_files(files)
        progress('Choosing the data ID…')
        probe = build(None)
        sections = self.sections(sample['experiment_id'])
        by = probe['created_by']
        key = f"reserved-data/{sample['id']}/{probe['technique']}/{probe['date']}/{by['lab']}/{by['initials']}"
        data_id = None
        for candidate in (reserved_id, self._ops.get(key)):
            if candidate and candidate.startswith(f"{sample['id']}-{probe['technique']}-"):
                header = records.section_header(build(candidate))
                matching = [x for x in sections if x['kind'] == 'data' and x['id'] == candidate]
                if all(x['header'] == header and not self.load_record(x)[0] for x in matching):
                    data_id = candidate
                    break
        if not data_id:
            data_id = ids.next_data_id(sample['id'], probe['technique'], [x['id'] for x in sections if x['kind'] == 'data'])
        self._ops[key] = self.last_reserved = data_id
        saved = self._save_section(sample['experiment_id'], build(data_id), files, progress)
        self._ops.pop(key, None)
        self.last_reserved = None
        return saved

    def add_shipment(self, sample, build, progress=lambda _: None):
        existing = {s['id'] for s in self.sections(sample['experiment_id']) if s['kind'] == 'shipment'}
        number = 1
        while f"{sample['id']}-SHP-{number:02d}" in existing:
            number += 1
        return self._save_section(sample['experiment_id'], build(f"{sample['id']}-SHP-{number:02d}"), [], progress)

    def create_procedure(self, build, files, progress=lambda _: None):
        self._need_workspace()
        probe = build(None)
        lab = probe['created_by']['lab']
        existing = self.list_procedures()
        key = 'reserved-procedure/' + lab + '/' + probe.get('category', 'synthesis') + '/' + probe['name']
        procedure_id = self._ops.get(key)
        name = records.procedure_experiment_name(procedure_id, probe['name']) if procedure_id else None
        if not procedure_id or any(p['id'] == procedure_id and records.procedure_experiment_name(p['id'], p['name']) != name
                for p in existing):
            procedure_id = ids.next_procedure_id(lab, [p['id'] for p in existing],
                prefix='TST' if probe.get('category') == 'testing' else 'PRC')
        self._ops[key] = procedure_id
        record = build(procedure_id)
        progress(f'Creating {procedure_id} in SciSure…')
        experiment_id = self._experiment(self.workspace['procedures_study'],
            records.procedure_experiment_name(procedure_id, record['name']))
        saved = self._save_section(experiment_id, dict(record, version=1), files, progress)
        self._ops.pop(key, None)
        return dict(procedure=dict(id=procedure_id, name=record['name'], experiment_id=experiment_id), **saved)

    def add_procedure_version(self, procedure, build, files, progress=lambda _: None):
        versions = [s['version'] for s in self.sections(procedure['experiment_id'])
            if s['kind'] == 'procedure' and s['id'] == procedure['id']]
        record = build(procedure['id'])
        record = dict(record, version=max(versions, default=0) + 1)
        return self._save_section(procedure['experiment_id'], record, files, progress)
