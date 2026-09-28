"""Append-only, reviewed sample-local document groups, without an experiment."""
import re
import uuid
from urllib.parse import urlencode

from .inventory import _sample, sample_snapshot
from .model import InputError, MAX_FILE, check_sources, digest
from .scisure import SciSureError, remote_id

DOCUMENT_FIELD = 'CATALYST_documents'


def _written_id(value, label):
    try: return remote_id(value)
    except SciSureError:
        raise SciSureError('The ' + label + ' identity was not confirmed after writing. Check SciSure before retrying.', True) from None


def sample_documents(client, group_id, sample_id):
    group_id, sample_id = remote_id(group_id), remote_id(sample_id)
    client.assert_group(group_id)
    sample = _sample(client, sample_id, group_id)
    rows = []
    for meta in sample['meta']:
        if meta.get('sampleDataType') != 'FILE' or meta.get('archived') is True: continue
        for file in _files(meta):
            rows.append(dict(sample_id=sample_id, field=meta.get('key', ''), file_id=remote_id(file.get('fileID')),
                name=file.get('realName') or file.get('name') or 'sample-document'))
    client.assert_group(group_id)
    return dict(sample=sample, documents=rows)


def _files(meta):
    files = meta.get('files') or []
    if not isinstance(files, list) or not all(isinstance(file, dict) for file in files):
        raise SciSureError('Sample attachment links are incomplete. Refresh the sample.')
    ids = [remote_id(file.get('fileID')) for file in files]
    if len(ids) != len(set(ids)):
        raise SciSureError('The sample document field contains duplicate links. Review it in SciSure.')
    return files


def _file_ids(sample, key):
    matches = [m for m in sample['meta'] if m.get('key') == key]
    if len(matches) > 1 or (matches and (matches[0].get('sampleDataType') != 'FILE' or matches[0].get('archived') is True)):
        raise SciSureError('The sample document field is duplicated or has another type. Review it in SciSure.')
    files = _files(matches[0]) if matches else []
    ids = [remote_id(f.get('fileID')) for f in files]
    if len(ids) != len(set(ids)): raise SciSureError('The sample document field contains duplicate links. Review it in SciSure.')
    return sorted(ids)


def plan_documents(client, group_id, sample_id, sources, *, plan_id=None):
    """Review one append-only, sample-local document group; no schema mutation."""
    group_id, sample_id = remote_id(group_id), remote_id(sample_id)
    identifier = uuid.uuid4().hex if plan_id is None else plan_id
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise InputError('The document review identity is invalid. Start a new document review.')
    check_sources(sources)
    client.assert_group(group_id)
    sample = _sample(client, sample_id, group_id)
    key = DOCUMENT_FIELD + '_' + identifier
    if any(meta.get('key') == key for meta in sample['meta']):
        raise SciSureError('This reviewed document group already exists on the sample. Refresh its documents to check the completed or unconfirmed transfer. No duplicate was sent.', True)
    client.assert_group(group_id)
    return dict(format='catalyst-sample-documents/2', tenant=client.origin, group_id=group_id,
        sample_id=sample_id, sample_name=sample.get('name') or str(sample_id), sample_type_id=sample['sampleTypeID'],
        sample_snapshot=sample_snapshot(sample), document_group_id=identifier, field_key=key,
        files=[dict(source.artifact) for source in sources])


class SampleDocumentTransfer:
    def __init__(self, client, operations):
        self.client, self.operations = client, operations

    def _write(self, key, path, method, data):
        if key in self.operations:
            old = self.operations[key]
            if old.get('status') == 'returned': return old['value']
            raise SciSureError('A previous sample-document write is unconfirmed. Check the sample and File Storage in SciSure before retrying. No duplicate was sent.', True)
        self.operations[key] = dict(status='writing')
        try:
            value = self.client.request(path, method, data)
        except SciSureError as exc:
            if not exc.uncertain: self.operations.pop(key, None)
            else: self.operations[key] = dict(status='unknown')
            raise
        self.operations[key] = dict(status='returned', value=value)
        return value

    def apply(self, plan, sources, progress=lambda _: None):
        client, group, sid = self.client, plan['group_id'], plan['sample_id']
        if plan.get('format') != 'catalyst-sample-documents/2':
            raise InputError('Create a new document review with this version of CATALYST.')
        if plan['tenant'] != client.origin: raise SciSureError('The selected sample belongs to another server.')
        if [dict(source.artifact) for source in sources] != plan['files']:
            raise InputError('The selected files changed. Review the documents again.')
        fresh = plan_documents(client, group, sid, sources, plan_id=plan['document_group_id'])
        if fresh != plan: raise SciSureError('The sample changed. Review the documents again before sending.')
        ids, uploaded = [], []
        for source in sources:
            client.assert_group(group)
            progress('Uploading sample document ' + source.name + '…')
            key = (client.origin, group, sid, 'sample-document', source.name, source.artifact['sha256'])
            value = self._write(key, '/api/v1/files?' + urlencode(dict(fileName=source.name)), 'POST', source.content)
            if not isinstance(value, dict):
                raise SciSureError('The uploaded file identity could not be verified. Check File Storage in SciSure.', True)
            fid = _written_id(value.get('fileID'), 'uploaded file')
            if (value.get('groupID') != group or value.get('realName') != source.name or value.get('size') != len(source.content)
                    or value.get('location') not in (None, 'ELABJOURNAL')):
                raise SciSureError('The uploaded file metadata does not match the reviewed sample document.', True)
            data = client.request(f'/api/v1/files/{fid}', binary=True)
            if digest(data) != source.artifact['sha256']:
                raise SciSureError('The uploaded sample document failed checksum verification.', True)
            if fid in ids:
                raise SciSureError('SciSure returned the same identity for different uploaded documents. Check File Storage.', True)
            ids.append(fid)
            uploaded.append(dict(file_id=fid, name=source.name, sha256=digest(data)))
        current = _sample(client, sid, group)
        if sample_snapshot(current) != plan['sample_snapshot']:
            raise SciSureError('The sample changed during upload. Files remain in File Storage; review the sample before linking them.', True)
        if any(meta.get('key') == plan['field_key'] for meta in current['meta']):
            raise SciSureError('This document group already exists. Check the sample before retrying. No duplicate was sent.', True)
        client.assert_group(group)
        # POST creates a new sample-local field. It never replaces a shared FILE
        # field, so another researcher's concurrent document additions are retained.
        payload = dict(key=plan['field_key'], sampleDataType='FILE', fileIDs=sorted(ids))
        key = (client.origin, group, sid, 'document-group', plan['document_group_id'])
        result = self._write(key, f'/api/v1/samples/{sid}/meta', 'POST', payload)
        _written_id(result, 'sample document group')
        confirmed = _sample(client, sid, group)
        if (_file_ids(confirmed, plan['field_key']) != sorted(ids)
                or sample_snapshot(confirmed) != plan['sample_snapshot']):
            raise SciSureError('The sample document group could not be verified. Check the sample in SciSure.', True)
        client.assert_group(group)
        return dict(sample_id=sid, sample_name=plan['sample_name'], files=uploaded, field=plan['field_key'])


def download_document(client, group_id, row):
    before = sample_documents(client, group_id, row['sample_id'])
    if row not in before['documents']: raise SciSureError('This document is no longer linked to the sample. Refresh its documents.')
    def metadata():
        files = client.list('/api/v1/files?' + urlencode(dict(fileName=row['name'], groupID=group_id)))
        matches = [f for f in files if f.get('fileID') == row['file_id'] and f.get('groupID') == group_id
            and (f.get('filename') or f.get('realName')) == row['name']]
        if len(matches) != 1: raise SciSureError('This sample file is unavailable or changed in the active group.')
        return {key: matches[0].get(key) for key in ('fileID', 'groupID', 'filename', 'realName', 'size',
            'location', 'SHA256Hash', 'archived', 'deleted', 'parentFileID', 'rootFileID')}
    meta = metadata()
    if meta.get('archived') is True or meta.get('deleted') is True:
        raise SciSureError('This sample document is archived or deleted. Refresh its documents.')
    if meta.get('location') not in (None, 'ELABJOURNAL') or type(meta.get('size')) is not int or not 0 < meta['size'] <= MAX_FILE:
        raise SciSureError('Download this file through SciSure; its storage location or size is not supported here.')
    client.assert_group(group_id)
    data = client.request(f'/api/v1/files/{remote_id(row["file_id"])}', binary=True)
    if len(data) != meta['size']: raise SciSureError('The sample document download is incomplete.')
    checksum = meta.get('SHA256Hash')
    if isinstance(checksum, str) and re.fullmatch(r'[0-9a-fA-F]{64}', checksum) and digest(data) != checksum.lower():
        raise SciSureError('The sample document checksum did not match.')
    after = sample_documents(client, group_id, row['sample_id'])
    if row not in after['documents']: raise SciSureError('The sample document link changed during download. Refresh and try again.')
    if metadata() != meta: raise SciSureError('The file metadata changed during download. Refresh and try again.')
    client.assert_group(group_id)
    return data
