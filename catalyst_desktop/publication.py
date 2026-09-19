"""Approved, checksum-verified transfers and read-back, stored only in SciSure."""
from __future__ import annotations

import re
from urllib.parse import quote

from .model import Revision, Source, InputError, encode, digest, now, MAX_FILE, check_sources
from .scisure import SciSureError, remote_id
from .contracts import approved_payload
from catalyst_ingest.jsonio import strict_loads

PREFIX = 'CATALYST desktop | '
MANIFEST = 'catalyst-review.json'
RECEIPT = 'catalyst-transfer.json'
FILE_SECTION_TYPES = ('FILE', 'FILES')

def require_tenant_file(metadata):
    if metadata.get('origin') == 'ONSITE':
        raise SciSureError('This file uses eLABHybrid institutional storage. Open it in SciSure; CATALYST contacts only the verified SciSure tenant.')

def unique(rows, key):
    if len(rows) > 1:
        raise SciSureError('Duplicate remote records were found. Resolve them in SciSure before continuing.')
    return remote_id(rows[0].get(key)) if rows else None

def validate_approval(revision, approval):
    return approved_payload(revision, approval)


def file_metadata(metadata, size=None, checksum=None, experiment=None):
    if metadata.get('deleted') is True or metadata.get('archived') is True:
        raise SciSureError('A SciSure file was archived or deleted. Refresh before continuing.')
    if type(metadata.get('fileSize')) is not int or not 1 <= metadata['fileSize'] <= MAX_FILE:
        raise SciSureError('A SciSure file has an unsupported size. Transfer is paused.')
    if size is not None and metadata['fileSize'] != size:
        raise SciSureError('A SciSure file size differs from the approved original. Transfer is paused.')
    if experiment is not None and remote_id(metadata.get('experimentID', experiment)) != experiment:
        raise SciSureError('SciSure returned a file from another experiment. Transfer is paused.')
    declared = metadata.get('SHA256Hash')
    # The API does not promise a hash encoding. Compare it only when it is a SHA-256 hex digest.
    if checksum and isinstance(declared, str) and re.fullmatch(r'[0-9a-fA-F]{64}', declared) and declared.lower() != checksum:
        raise SciSureError('SciSure file metadata reports a different checksum. Transfer is paused.')


def download(client, base, metadata, *, size=None, checksum=None, experiment=None):
    require_tenant_file(metadata)
    file_metadata(metadata, size, checksum, experiment)
    content = client.request(f'{base}/{remote_id(metadata.get("experimentFileID"))}', binary=True)
    if len(content) != metadata['fileSize'] or (checksum and digest(content) != checksum):
        raise SciSureError('A downloaded file failed its size or checksum integrity check.')
    file_metadata(metadata, len(content), digest(content), experiment)
    return content


def _file_by_id(files, identifier):
    matches = [f for f in files if remote_id(f.get('experimentFileID')) == identifier]
    if len(matches) != 1:
        raise SciSureError('A SciSure file is missing or duplicated. Refresh before continuing.')
    return matches[0]


def _file_snapshot(files):
    # Ignore display/access fields which may change when downloading, but bind all
    # metadata that establishes a file's identity, version, owner, and integrity.
    keys = ('realName', 'fileSize', 'experimentID', 'SHA256Hash', 'origin',
        'parentExperimentFileID', 'deleted', 'archived')
    records = [(remote_id(f.get('experimentFileID')), {k: f.get(k) for k in keys}) for f in files]
    return encode(sorted(records, key=lambda record: record[0]))


def _review_section(client, destination, sid):
    sections = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
    selected = [s for s in sections if remote_id(s.get('expJournalID')) == sid
        and isinstance(s.get('sectionHeader'), str) and s['sectionHeader'].startswith(PREFIX)
        and s.get('sectionType') in FILE_SECTION_TYPES and s.get('deleted') is False]
    if len(selected) != 1:
        raise SciSureError('The selected review does not belong to this experiment or is no longer active.')
    return selected[0]

class Publisher:
    def __init__(self, client, destination, operations=None):
        self.client, self.destination = client, dict(destination)
        self.operations = operations if operations is not None else {}

    def _step(self, key, find, write):
        found = find()
        if found is not None:
            self.operations[key] = 'verified'
            return found
        if self.operations.get(key) in ('unknown', 'verified', 'writing'):
            raise SciSureError('A previous write is still unconfirmed or its remote record changed. No duplicate write was sent. Check SciSure.', True)
        self.client.verify_destination(self.destination)
        self.operations[key] = 'writing'
        write_returned = False
        try:
            raw_result = write()
            write_returned = True
            result = remote_id(raw_result)
            verified = find()
            if verified != result:
                raise SciSureError('SciSure has not confirmed the written record yet.', True)
            self.operations[key] = 'verified'
            return result
        except Exception as e:
            self.operations[key] = ('rejected' if not write_returned and isinstance(e, SciSureError)
                and not e.uncertain else 'unknown')
            raise

    def _section(self, revision_id):
        path = f'/api/v1/experiments/{self.destination["experiment_id"]}/sections'
        heading = PREFIX + revision_id
        def find():
            matches = {}
            for query in (path, path + '?archived=true'):
                for section in self.client.list(query):
                    if section.get('sectionHeader') != heading:
                        continue
                    if section.get('deleted') is not False or section.get('sectionType') not in FILE_SECTION_TYPES:
                        raise SciSureError('An archived or changed section already reserves this revision. Reconcile it in SciSure.')
                    sid = remote_id(section.get('expJournalID'))
                    if sid in matches and matches[sid] != section:
                        raise SciSureError('The revision section changed during verification. Refresh before continuing.')
                    matches[sid] = section
            return unique(list(matches.values()), 'expJournalID')
        return self._step('section/' + revision_id, find,
            lambda: self.client.request(path, 'POST', {'sectionType': 'FILE', 'sectionHeader': heading}))

    def _file(self, section, filename, data):
        path = f'/api/v1/experiments/sections/{section}/files'
        expected = digest(data)
        def find():
            rows = [f for f in self.client.list(path) if f.get('realName') == filename]
            identifier = unique(rows, 'experimentFileID')
            if identifier is None:
                return None
            download(self.client, path, rows[0], size=len(data), checksum=expected, experiment=self.destination['experiment_id'])
            return identifier
        return self._step(f'file/{section}/{filename}', find,
            lambda: self.client.request(path + '?fileName=' + quote(filename, safe=''), 'POST', data))

    def _check_catalog(self, payload, revision_sha256, progress):
        from .catalog import load_catalog, check_publication
        catalog = load_catalog(self.client, self.destination['group_id'], progress)
        check_publication(payload['preview']['traceability'], catalog)
        for entry in catalog['entries'] + catalog['pending'] + catalog.get('orphan_sections', []):
            if entry['revision_id'] == payload['id'] and (entry['destination']['experiment_id'] != self.destination['experiment_id']
                    or entry.get('revision_sha256', revision_sha256) != revision_sha256):
                raise InputError('This revision ID already belongs to another destination or different content. Create a new reviewed revision.')
        profile = payload['preview']['normalization'].get('profile', {})
        if profile.get('format') == 'catalyst-mapping/1':
            key = ('entity', 'modality', 'source_format', 'source_version', 'name', 'version')
            for other_profile in catalog['profiles']:
                if all(other_profile.get(k) == profile.get(k) for k in key) and digest(encode(other_profile)) != digest(encode(profile)):
                    raise InputError('This mapping version already has different rules in SciSure. Increase the profile version.')

    def publish(self, revision, approval, sources, progress=lambda _: None):
        payload = validate_approval(revision, approval)
        trace = payload['preview'].get('traceability')
        if not trace:
            raise InputError('This older review has no consortium identities. Create and approve a review with lab and sample lineage before publishing.')
        from .traceability import build_traceability
        rebuilt, issues = build_traceability(payload['preview']['entity'], payload['preview']['modality'], payload['preview']['context'])
        if rebuilt != trace or any(i['severity'] == 'error' for i in issues):
            raise InputError('The revision lineage does not match its context. Build and approve a new preview.')
        check_sources(sources)
        expected = [(a['filename'], a['sha256'], a['size_bytes']) for a in payload['preview']['artifacts']]
        actual = [(s.name, digest(s.content), len(s.content)) for s in sources]
        if expected != actual:
            raise InputError('The selected source files no longer match the approved revision.')
        self.client.verify_destination(self.destination)
        progress('Checking sample, procedure, and dataset identities across the active SciSure group…')
        self._check_catalog(payload, revision.sha256, progress)
        packet = dict(format='catalyst-desktop-transfer/1', state='prepared',
            revision=payload, revision_sha256=revision.sha256, approval=approval, destination=self.destination)
        packet_bytes = encode(packet)
        if len(packet_bytes) > MAX_FILE:
            raise InputError('The review package exceeds the supported 20 MiB transfer size.')
        progress('Preparing the revision section in SciSure…')
        section = self._section(payload['id'])
        existing = self.client.list(f'/api/v1/experiments/sections/{section}/files')
        if unique([f for f in existing if f.get('realName') == MANIFEST], 'experimentFileID') is not None:
            loaded = read_review(self.client, self.destination, section)
            if loaded['revision'].sha256 != revision.sha256 or encode(loaded['approval']) != encode(approval):
                raise InputError('This revision already has different content or approval in SciSure. Create a new reviewed revision.')
            # Canonical ownership was checked by read_review. Display names may
            # change after reconnecting; retries must reuse the original envelope.
            packet['destination'] = loaded['destination']
            packet_bytes = encode(packet)
        progress('Saving the approved review and provenance…')
        manifest_id = self._file(section, MANIFEST, packet_bytes)
        files = []
        for index, source in enumerate(sources, 1):
            filename = f'{index:02d}-' + source.name
            progress(f'Sending and verifying file {index} of {len(sources)}…')
            identifier = self._file(section, filename, source.content)
            files.append(dict(source_name=source.name, remote_name=filename, file_id=identifier,
                sha256=digest(source.content), size_bytes=len(source.content)))
        # Stable receipt permits read-back reconciliation without another write.
        receipt = dict(format='catalyst-desktop-receipt/1', state='complete', revision_id=payload['id'],
            revision_sha256=revision.sha256, destination=packet['destination'], section_id=section,
            manifest_id=manifest_id, files=files)
        # A transfer may take minutes. Recheck shared identities after the upload,
        # before promoting this pending review to a completed catalog reference.
        progress('Rechecking shared identities before completing the transfer…')
        self._check_catalog(payload, revision.sha256, progress)
        progress('Recording the verified transfer receipt…')
        receipt_id = self._file(section, RECEIPT, encode(receipt))
        progress('Verifying the complete saved review and originals…')
        verified = read_review(self.client, self.destination, section, with_sources=True)
        if verified['revision'].sha256 != revision.sha256 or verified['receipt'] != receipt:
            raise SciSureError('The saved transfer changed during final verification. Check SciSure before retrying.', True)
        return dict(receipt, receipt_id=receipt_id, verified_at=now())

def history(client, destination):
    client.verify_destination(destination, writable=False)
    sections = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
    results = []
    for s in sections:
        heading = s.get('sectionHeader', '')
        if not isinstance(heading, str) or not heading.startswith(PREFIX) or s.get('sectionType') not in FILE_SECTION_TYPES or s.get('deleted') is not False:
            continue
        sid = remote_id(s['expJournalID'])
        files = client.list(f'/api/v1/experiments/sections/{sid}/files')
        mid = unique([f for f in files if f.get('realName') == MANIFEST], 'experimentFileID')
        rid = unique([f for f in files if f.get('realName') == RECEIPT], 'experimentFileID')
        results.append(dict(section_id=sid, revision_id=heading[len(PREFIX):],
            manifest_id=mid, receipt_id=rid, status='Receipt available' if rid else 'Incomplete transfer'))
    client.verify_destination(destination, writable=False)
    return results

def read_review(client, destination, section_id, with_sources=False):
    client.verify_destination(destination, writable=False)
    sid = remote_id(section_id)
    selected = _review_section(client, destination, sid)
    base = f'/api/v1/experiments/sections/{sid}/files'
    files = client.list(base)
    snapshot = _file_snapshot(files)
    mid = unique([f for f in files if f.get('realName') == MANIFEST], 'experimentFileID')
    if mid is None:
        raise SciSureError('The review file is missing from this incomplete transfer.')
    try:
        packet = strict_loads(download(client, base, _file_by_id(files, mid), experiment=destination['experiment_id']))
        if not isinstance(packet, dict):
            raise ValueError
        if packet.get('format') != 'catalyst-desktop-transfer/1' or packet.get('state') != 'prepared':
            raise ValueError
        revision = Revision(encode(packet['revision']), packet['revision_sha256'])
        validate_approval(revision, packet['approval'])
        if selected['sectionHeader'] != PREFIX + revision.value()['id']:
            raise ValueError
        if any(packet['destination'][k] != destination[k] for k in ('tenant', 'group_id', 'experiment_id', 'study_id', 'project_id')):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SciSureError('The stored review failed its format or integrity checks.') from None
    sources = []
    rid = unique([f for f in files if f.get('realName') == RECEIPT], 'experimentFileID')
    receipt = None
    if rid:
        require_tenant_file(_file_by_id(files, rid))
        try:
            receipt = strict_loads(download(client, base, _file_by_id(files, rid), experiment=destination['experiment_id']))
            if (not isinstance(receipt, dict) or receipt.get('format') != 'catalyst-desktop-receipt/1' or receipt.get('state') != 'complete'
                    or receipt.get('revision_sha256') != revision.sha256 or receipt.get('revision_id') != revision.value()['id']
                    or receipt.get('section_id') != sid or receipt.get('manifest_id') != mid
                    or receipt.get('destination') != packet['destination']):
                raise ValueError
            expected_files = revision.value()['preview']['artifacts']
            if not isinstance(receipt.get('files'), list) or len(receipt['files']) != len(expected_files):
                raise ValueError
            for i, (expected, actual) in enumerate(zip(expected_files, receipt['files']), 1):
                matching = [f for f in files if f.get('realName') == f'{i:02d}-' + expected['filename']]
                if not isinstance(actual, dict) or unique(matching, 'experimentFileID') != remote_id(actual.get('file_id')):
                    raise ValueError
                file_metadata(matching[0], expected['size_bytes'], expected['sha256'], destination['experiment_id'])
                if (actual['source_name'] != expected['filename'] or actual['sha256'] != expected['sha256']
                        or actual['size_bytes'] != expected['size_bytes'] or actual['remote_name'] != f'{i:02d}-' + expected['filename']
                        or not any(remote_id(f.get('experimentFileID')) == actual['file_id'] and f.get('realName') == actual['remote_name']
                            and f.get('fileSize') == actual['size_bytes'] for f in files)):
                    raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SciSureError('The transfer receipt does not match the stored review and files.') from None
    if with_sources:
        for i, metadata in enumerate(revision.value()['preview']['artifacts'], 1):
            name = f'{i:02d}-' + metadata['filename']
            fid = unique([f for f in files if f.get('realName') == name], 'experimentFileID')
            if fid is None:
                raise SciSureError('An original is missing from this incomplete transfer. If the original app session is still open, use Send / check transfer there. Otherwise reconcile the incomplete record in SciSure before starting a new revision.')
            content = download(client, base, _file_by_id(files, fid),
                size=metadata['size_bytes'], checksum=metadata['sha256'], experiment=destination['experiment_id'])
            sources.append(Source.from_bytes(metadata['filename'], content, parse=metadata.get('format') != 'binary'))
            check_sources(sources)
    current_section = _review_section(client, destination, sid)
    if (current_section['sectionHeader'] != selected['sectionHeader']
            or _file_snapshot(client.list(base)) != snapshot):
        raise SciSureError('The saved review or its files changed during retrieval. Refresh before continuing.')
    client.verify_destination(destination, writable=False)
    return dict(revision=revision, approval=packet['approval'], destination=packet['destination'],
        sources=sources, receipt=receipt, state='complete' if receipt else 'incomplete',
        integrity='original checksums verified' if with_sources else 'review and receipt metadata checked; original bytes not downloaded')
