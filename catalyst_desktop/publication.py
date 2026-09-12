"""Approved, checksum-verified transfers and read-back, stored only in SciSure."""
from __future__ import annotations

import json
from urllib.parse import quote

from .model import Revision, Source, InputError, encode, digest, now, MAX_FILE, check_sources
from .scisure import SciSureError, remote_id

PREFIX = 'CATALYST desktop | '
MANIFEST = 'catalyst-review.json'
RECEIPT = 'catalyst-transfer.json'

def unique(rows, key):
    if len(rows) > 1:
        raise SciSureError('Duplicate remote records were found. Resolve them in SciSure before continuing.')
    return remote_id(rows[0][key]) if rows else None

def validate_approval(revision, approval):
    payload = revision.value()
    if (not isinstance(approval, dict) or approval.get('revision_id') != payload['id']
            or approval.get('revision_sha256') != revision.sha256 or approval.get('acknowledged') is not True
            or not approval.get('reviewer') or not approval.get('note')
            or any(i['severity'] == 'error' for i in payload['preview']['validation']['issues'])):
        raise InputError('This exact revision must be approved before transfer.')
    return payload

class Publisher:
    def __init__(self, client, destination):
        self.client, self.destination = client, dict(destination)
        self.operations = {}

    def _step(self, key, find, write):
        found = find()
        if found is not None:
            self.operations[key] = 'verified'
            return found
        if self.operations.get(key) in ('unknown', 'verified', 'writing'):
            raise SciSureError('A previous write is still unconfirmed or its remote record changed. No duplicate write was sent. Check SciSure.', True)
        self.client.verify_destination(self.destination)
        self.operations[key] = 'writing'
        try:
            result = remote_id(write())
            verified = find()
            if verified != result:
                raise SciSureError('SciSure has not confirmed the written record yet.', True)
            self.operations[key] = 'verified'
            return result
        except Exception:
            self.operations[key] = 'unknown'
            raise

    def _section(self, revision_id):
        path = f'/api/v1/experiments/{self.destination["experiment_id"]}/sections'
        heading = PREFIX + revision_id
        return self._step('section/' + revision_id,
            lambda: unique([s for s in self.client.list(path) if s.get('sectionHeader') == heading
                and s.get('sectionType') == 'FILES' and not s.get('deleted')], 'expJournalID'),
            lambda: self.client.request(path, 'POST', {'sectionType': 'FILES', 'sectionHeader': heading}))

    def _file(self, section, filename, data):
        path = f'/api/v1/experiments/sections/{section}/files'
        expected = digest(data)
        def find():
            rows = [f for f in self.client.list(path) if f.get('realName') == filename]
            identifier = unique(rows, 'experimentFileID')
            if identifier is None:
                return None
            if rows[0].get('fileSize') != len(data):
                raise SciSureError('An existing SciSure file has a different size. Transfer is paused.')
            if digest(self.client.request(f'{path}/{identifier}', binary=True)) != expected:
                raise SciSureError('An existing SciSure file has a different checksum. Transfer is paused.')
            return identifier
        return self._step(f'file/{section}/{filename}', find,
            lambda: self.client.request(path + '?fileName=' + quote(filename, safe=''), 'POST', data))

    def publish(self, revision, approval, sources, progress=lambda _: None):
        payload = validate_approval(revision, approval)
        trace = payload['preview'].get('traceability')
        if not trace:
            raise InputError('This older review has no consortium identities. Create and approve a review with lab and sample lineage before publishing.')
        check_sources(sources)
        expected = [(a['filename'], a['sha256'], a['size_bytes']) for a in payload['preview']['artifacts']]
        actual = [(s.name, digest(s.content), len(s.content)) for s in sources]
        if expected != actual:
            raise InputError('The selected source files no longer match the approved revision.')
        self.client.verify_destination(self.destination)
        from .catalog import load_catalog, check_publication
        progress('Checking sample, procedure, and dataset identities across the active SciSure group…')
        catalog = load_catalog(self.client, self.destination['group_id'], progress)
        check_publication(trace, catalog)
        # Detect accidental reuse of a profile version with different rules anywhere in the active group.
        profile = payload['preview']['normalization'].get('profile', {})
        if profile.get('format') == 'catalyst-mapping/1':
            key = ('entity', 'modality', 'source_format', 'source_version', 'name', 'version')
            for other_profile in catalog['profiles']:
                if all(other_profile.get(k) == profile.get(k) for k in key) and digest(encode(other_profile)) != digest(encode(profile)):
                    raise InputError('This mapping version already has different rules in SciSure. Increase the profile version.')
        packet = dict(format='catalyst-desktop-transfer/1', state='prepared',
            revision=payload, revision_sha256=revision.sha256, approval=approval, destination=self.destination)
        packet_bytes = encode(packet)
        if len(packet_bytes) > MAX_FILE:
            raise InputError('The review package exceeds the supported 20 MiB transfer size.')
        progress('Preparing the revision section in SciSure…')
        section = self._section(payload['id'])
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
            revision_sha256=revision.sha256, destination=self.destination, section_id=section,
            manifest_id=manifest_id, files=files)
        progress('Recording the verified transfer receipt…')
        receipt_id = self._file(section, RECEIPT, encode(receipt))
        return dict(receipt, receipt_id=receipt_id, verified_at=now())

def history(client, destination):
    client.verify_destination(destination, writable=False)
    sections = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
    results = []
    for s in sections:
        heading = s.get('sectionHeader', '')
        if not heading.startswith(PREFIX) or s.get('sectionType') != 'FILES' or s.get('deleted'):
            continue
        sid = remote_id(s['expJournalID'])
        files = client.list(f'/api/v1/experiments/sections/{sid}/files')
        mid = unique([f for f in files if f.get('realName') == MANIFEST], 'experimentFileID')
        rid = unique([f for f in files if f.get('realName') == RECEIPT], 'experimentFileID')
        results.append(dict(section_id=sid, revision_id=heading[len(PREFIX):],
            manifest_id=mid, receipt_id=rid, status='Receipt available' if rid else 'Incomplete transfer'))
    return results

def read_review(client, destination, section_id, with_sources=False):
    client.verify_destination(destination, writable=False)
    sid = remote_id(section_id)
    sections = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
    if not any(s.get('expJournalID') == sid and s.get('sectionHeader', '').startswith(PREFIX)
            and s.get('sectionType') == 'FILES' and not s.get('deleted') for s in sections):
        raise SciSureError('The selected review does not belong to this experiment.')
    base = f'/api/v1/experiments/sections/{sid}/files'
    files = client.list(base)
    mid = unique([f for f in files if f.get('realName') == MANIFEST], 'experimentFileID')
    if mid is None:
        raise SciSureError('The review file is missing from this incomplete transfer.')
    try:
        packet = json.loads(client.request(f'{base}/{mid}', binary=True))
        if packet.get('format') != 'catalyst-desktop-transfer/1':
            raise ValueError
        revision = Revision(encode(packet['revision']), packet['revision_sha256'])
        validate_approval(revision, packet['approval'])
        if any(packet['destination'][k] != destination[k] for k in ('tenant', 'group_id', 'experiment_id', 'study_id', 'project_id')):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise SciSureError('The stored review failed its format or integrity checks.') from None
    sources = []
    rid = unique([f for f in files if f.get('realName') == RECEIPT], 'experimentFileID')
    receipt = None
    if rid:
        try:
            receipt = json.loads(client.request(f'{base}/{rid}', binary=True))
            if (receipt.get('format') != 'catalyst-desktop-receipt/1' or receipt.get('state') != 'complete'
                    or receipt.get('revision_sha256') != revision.sha256 or receipt.get('revision_id') != revision.value()['id']
                    or receipt.get('section_id') != sid or receipt.get('manifest_id') != mid
                    or receipt.get('destination') != packet['destination']):
                raise ValueError
            expected_files = revision.value()['preview']['artifacts']
            if len(receipt.get('files', [])) != len(expected_files):
                raise ValueError
            for i, (expected, actual) in enumerate(zip(expected_files, receipt['files']), 1):
                if (actual['source_name'] != expected['filename'] or actual['sha256'] != expected['sha256']
                        or actual['size_bytes'] != expected['size_bytes'] or actual['remote_name'] != f'{i:02d}-' + expected['filename']
                        or not any(f.get('experimentFileID') == actual['file_id'] and f.get('realName') == actual['remote_name']
                            and f.get('fileSize') == actual['size_bytes'] for f in files)):
                    raise ValueError
        except (ValueError, TypeError, KeyError):
            raise SciSureError('The transfer receipt does not match the stored review and files.') from None
    if with_sources:
        for i, metadata in enumerate(revision.value()['preview']['artifacts'], 1):
            name = f'{i:02d}-' + metadata['filename']
            fid = unique([f for f in files if f.get('realName') == name], 'experimentFileID')
            if fid is None:
                raise SciSureError('An original file is missing. Re-select the original files to resume this revision.')
            content = client.request(f'{base}/{fid}', binary=True)
            if len(content) != metadata['size_bytes'] or digest(content) != metadata['sha256']:
                raise SciSureError('A downloaded original does not match its recorded checksum.')
            sources.append(Source.from_bytes(metadata['filename'], content))
        check_sources(sources)
    return dict(revision=revision, approval=packet['approval'], destination=packet['destination'],
        sources=sources, receipt=receipt, state='complete' if receipt else 'incomplete')
