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
    if any(n.casefold() == records.RECORD_FILE for n in names):
        raise StoreError(f'"{records.RECORD_FILE}" is reserved by CATALYST. Rename that file.')
    for f in files:
        if f.path is not None:
            if not f.path.is_file():
                raise StoreError(f'{f.name} can no longer be found on this computer.')
            size = f.path.stat().st_size
            if size == 0:
                raise StoreError(f'{f.name} is empty.')
            if size > MAX_FILE:
                raise StoreError(f'{f.name} is larger than 20 MB. Split or compress it before uploading.')


class Store:
    def __init__(self, client, app_version='', project=PROJECT):
        self.client, self.app_version, self.project_name = client, app_version, project
        self.group_id = None
        self.workspace = None
        self.account = {}
        self._ops = {}
        self.last_reserved = None

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

    def list_samples(self):
        self._need_workspace()
        result = []
        for row in self._experiments(self.workspace['samples_study']):
            info = records.parse_sample_experiment_name(row.get('name'))
            if info:
                info.update(experiment_id=remote_id(row['experimentID']), created=str(row.get('created') or ''),
                    experiment_name=row.get('name'))
                result.append(info)
        return sorted(result, key=lambda s: (s['date'], s['id']), reverse=True)

    def list_procedures(self):
        self._need_workspace()
        result = []
        for row in self._experiments(self.workspace['procedures_study']):
            info = records.parse_procedure_experiment_name(row.get('name'))
            if info:
                info.update(experiment_id=remote_id(row['experimentID']))
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

    def load_record(self, section):
        """Return the section's record (or None if the upload never finished) and its file list."""
        rows = self.files(section['section_id'])
        record_rows = [f for f in rows if f.get('realName') == records.RECORD_FILE]
        record = None
        if record_rows:
            newest = max(record_rows, key=lambda f: remote_id(f['experimentFileID']))
            try:
                record = strict_loads(self.download(section['section_id'], newest))
            except (ValueError, StoreError):
                record = None
            if not isinstance(record, dict) or record.get('format') != records.RECORD_FORMAT:
                record = None
        originals = [f for f in rows if f.get('realName') != records.RECORD_FILE]
        return record, originals

    def open_sample(self, sample, progress=lambda _: None):
        """Everything saved for one sample: its record, data records and shipping log."""
        progress(f'Opening {sample["id"]}…')
        sections = self.sections(sample['experiment_id'])
        def load(section):
            record, files = self.load_record(section)
            return dict(section, record=record, files=files, complete=record is not None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            loaded = list(pool.map(load, sections))
        result = dict(sample=sample, record=None, sample_files=[], data=[], shipments=[], incomplete=[])
        for item in loaded:
            if not item['complete']:
                result['incomplete'].append(item)
            elif item['kind'] == 'sample' and item['id'] == sample['id']:
                result['record'], result['sample_files'], result['sample_section'] = item['record'], item['files'], item['section_id']
            elif item['kind'] == 'data':
                result['data'].append(item)
            elif item['kind'] == 'shipment':
                result['shipments'].append(item)
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
        return dict(section_id=section_id, record=record)

    def sample_ids_for(self, lab, initials, when):
        prefix = ids.sample_prefix(lab, initials, when)
        return [s['id'] for s in self.list_samples() if s['id'].startswith(prefix)]

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
        listed = self.list_samples()
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
        clashes = [s for s in self.list_samples() if s['id'] == sample_id and s['experiment_id'] != experiment_id]
        saved = self._save_section(experiment_id, record, files, progress)
        self._ops.pop('reserved/' + prefix, None)
        self.last_reserved = None
        info = records.parse_sample_experiment_name(name)
        info.update(experiment_id=experiment_id, created='')
        return dict(sample=info, record=saved['record'], duplicate_warning=bool(clashes))

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
        key = 'reserved-procedure/' + lab + '/' + probe['name']
        procedure_id = self._ops.get(key)
        name = records.procedure_experiment_name(procedure_id, probe['name']) if procedure_id else None
        if not procedure_id or any(p['id'] == procedure_id and records.procedure_experiment_name(p['id'], p['name']) != name
                for p in existing):
            procedure_id = ids.next_procedure_id(lab, [p['id'] for p in existing])
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
