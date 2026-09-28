"""Searchable, in-memory choices backed by completed SciSure records.

Choices retain their exact source review. A measurement contributes aliases and
dataset links, never a replacement definition of the sample it references.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import uuid
from urllib.parse import urlencode

from .model import InputError, encode
from .scisure import SciSureError, remote_id
from .traceability import LABS, lab_id, lab_name


def _source(entry):
    return {key: deepcopy(entry.get(key)) for key in
        ('revision_id', 'revision_sha256', 'section_id', 'destination')}


def _procedure_definitions(entry):
    trace = entry['trace']
    if trace.get('procedure'):
        p = trace['procedure']
        yield p, dict(id=p['id'], version=p['version'], name=p.get('name') or p['id'],
            type=p.get('type', ''), kind=p.get('type', ''), modality=p.get('modality', ''),
            lab=p.get('owner_lab', trace.get('source_lab', '')), reference=p.get('reference', ''),
            instructions=p.get('instructions', ''), registered=True)
    if trace.get('batch', {}).get('procedure'):
        p = trace['batch']['procedure']
        # Old methods with only an ID/version are not procedure definitions.
        if p.get('reference'):
            yield p, dict(id=p['id'], version=p['version'], name=p['id'], type='synthesis',
                kind='synthesis', modality='synthesis', lab=trace['batch'].get('origin_lab', ''),
                reference=p['reference'], instructions='', registered=False)


def search_procedures(catalog, query='', kind='', modality='', lab=''):
    """List completed reusable definitions; search IDs, names, versions and labs.

    ``kind`` accepts synthesis, measurement or computation. Empty filters mean
    all values. Each row has ``procedure``, ``entry``, ``sources`` and ``label``.
    """
    by_id = {}
    for entry in (catalog or {}).get('entries', []):
        for definition, procedure in _procedure_definitions(entry):
            key = (procedure['id'], procedure['version'])
            row = by_id.get(key)
            if row is None:
                row = by_id[key] = dict(key=key, procedure=deepcopy(procedure), definition=deepcopy(definition),
                    entry=entry, sources=[], labs=set(), source='saved')
            else:
                prior = row['definition']
                # Legacy definitions know only the exact shared document. A
                # subsequent registration may add a name and searchable type.
                fields = set(prior) & set(definition)
                if any(prior[k] != definition[k] for k in fields):
                    raise InputError('A saved procedure ID/version has conflicting definitions: ' + key[0])
                if ((procedure['registered'] and not row['procedure']['registered'])
                        or (row['entry'].get('staged') and not entry.get('staged')
                            and procedure['registered'] == row['procedure']['registered'])):
                    row.update(procedure=deepcopy(procedure), definition=deepcopy(definition), entry=entry)
            source = _source(entry)
            if source not in row['sources']:
                row['sources'].append(source)
            if procedure['lab']:
                row['labs'].add(procedure['lab'])
    terms = query.strip().casefold().split()
    requested_lab = lab_id(lab) if lab else ''
    result = []
    for row in by_id.values():
        p = row['procedure']
        if kind and p['type'] != kind:
            continue
        if modality and p['modality'] not in ('', modality):
            continue
        if requested_lab and requested_lab not in row['labs']:
            continue
        searchable = ' '.join([p['id'], p['name'], str(p['version']), p['type'], p['modality'],
            p['reference'], *row['labs'], *[lab_name(v) for v in row['labs']]]).casefold()
        if not all(term in searchable for term in terms):
            continue
        row['staged'] = bool(row['entry'].get('staged'))
        row['label'] = f"{p['name']} · v{p['version']} · {p['type']} · {p['id']}"
        if row['staged']:
            row['label'] += ' · queued'
        row['sources'].sort(key=lambda s: str(s['revision_id']))
        result.append(row)
    return sorted(result, key=lambda r: (r['procedure']['name'].casefold(), r['key']))


def procedure_context(row):
    """Pin a saved method without copying previous run conditions into a new run."""
    if row.get('source') != 'saved':
        raise InputError('Register and review this native procedure before linking a dataset to it.')
    p, entry = row['procedure'], row['entry']
    return dict(methodId=p['id'], methodVersion=str(p['version']), procedureId=p['id'],
        procedureVersion=str(p['version']), procedureSourceRevisionId=entry['revision_id'],
        procedureSourceRevisionSha256=entry['revision_sha256'])


def search_samples(catalog, query=''):
    """Return original sample/model definitions with all completed dataset links."""
    entries = (catalog or {}).get('entries', [])
    by_id = {}
    for entry in entries:
        trace = entry['trace']
        subject = trace.get('material') or trace.get('model')
        if not subject:
            continue
        sid = subject['id']
        existing = by_id.get(sid)
        if existing and existing['subject'] != subject:
            raise InputError('A saved sample or model has conflicting definitions: ' + sid)
        if not existing:
            by_id[sid] = dict(entry=entry, subject=deepcopy(subject), aliases=set(), datasets=set(),
                sources=[], origin_lab=trace.get('batch', {}).get('origin_lab') or subject['creator_lab'])
        elif existing['entry'].get('staged') and not entry.get('staged'):
            existing['entry'] = entry
        by_id[sid]['sources'].append(_source(entry))
    for entry in entries:
        trace = entry['trace']
        dataset = trace.get('dataset', {})
        sid = dataset.get('subject_id') or trace.get('sample_ref', {}).get('id')
        row = by_id.get(sid)
        if not row:
            continue
        row['aliases'].update(a['lab'] + ': ' + a['local_label'] for a in trace.get('aliases', [])
            if a.get('canonical_id') == sid)
        if dataset.get('id'):
            row['datasets'].add(dataset['id'])
    terms = query.strip().casefold().split()
    result = []
    for sid, row in sorted(by_id.items()):
        subject = row['subject']
        searchable = ' '.join([sid, row['origin_lab'], lab_name(row['origin_lab']),
            subject.get('batch_id', ''), subject.get('description', ''), subject.get('state', ''),
            *row['aliases'], *row['datasets']]).casefold()
        if all(term in searchable for term in terms):
            label = row['entry'].get('context', {}).get('localSampleId') or sid
            row['label'] = f"{label} · {lab_name(row['origin_lab'])} · {sid}"
            row['staged'] = bool(row['entry'].get('staged'))
            if row['staged']:
                row['label'] += ' · queued'
            result.append(row)
    return result


def sample_context(row):
    """Return a reference to the immutable subject, not a duplicate definition."""
    entry = row['entry']
    return dict(specimenId=row['subject']['id'],
        localSampleId=entry.get('context', {}).get('localSampleId', ''),
        sampleSourceRevisionId=entry['revision_id'], sampleSourceRevisionSha256=entry['revision_sha256'])


def validate_reference_sources(trace, catalog):
    """Resolve pinned references against complete records in a freshly read catalog."""
    if trace.get('schema_version') != 'catalyst-traceability/2':
        return
    entries = (catalog or {}).get('entries', [])
    refs = []
    if trace.get('sample_ref'):
        refs.append(('sample', trace['sample_ref']))
    method = trace.get('dataset', {}).get('method')
    if method and not trace.get('procedure'):
        refs.append(('procedure', method))
    for kind, ref in refs:
        revision_id, sha = ref.get('source_revision_id'), ref.get('source_revision_sha256')
        matches = [e for e in entries if e.get('revision_id') == revision_id
            and e.get('revision_sha256') == sha]
        if not revision_id or not sha or len(matches) != 1:
            raise InputError(f'The selected {kind} source review is missing, changed, or incomplete. Refresh the library and select it again.')
        source = matches[0]
        if kind == 'sample':
            subject = source['trace'].get('material') or source['trace'].get('model') or {}
            found = subject.get('id') == ref.get('id')
        else:
            found = any(p['id'] == ref.get('id') and str(p['version']) == str(ref.get('version'))
                for _, p in _procedure_definitions(source))
        if not found:
            raise InputError(f'The selected {kind} does not match its pinned source review. Refresh the library and select it again.')


def load_native_procedures(client, group_id, progress=lambda _: None):
    """Read finalized native protocols visible in the selected group scope.

    The group-scoped endpoint includes protocols shared into that scope; owning
    group need not equal active group. Every listed version is read separately.
    No protocol or inventory write endpoint is used here.
    """
    group_id = remote_id(group_id)
    client.assert_group(group_id)
    protocols = client.list('/api/v1/protocols?' + urlencode(dict(scope='group', groupIDs=group_id)))
    if len(protocols) > 250:
        raise SciSureError('More than 250 native procedures are visible. Narrow the group before loading the procedure library.')
    rows, seen = [], set()
    for listed in protocols:
        if listed.get('draft') is not False or listed.get('deleted') is not False:
            continue
        pid, vid = remote_id(listed.get('protID')), remote_id(listed.get('protVersionID'))
        if vid in seen:
            raise SciSureError('A native protocol version appears more than once. Refresh the procedure library.')
        seen.add(vid)
        progress('Reading native SciSure procedure version ' + str(vid) + '…')
        protocol = client.object(f'/api/v1/protocols/version/{vid}?%24expand=signingStatus')
        if (remote_id(protocol.get('protID')) != pid or remote_id(protocol.get('protVersionID')) != vid
                or protocol.get('draft') is not False or protocol.get('deleted') is not False
                or any(protocol.get(k) != listed.get(k) for k in ('version', 'name', 'groupID'))):
            raise SciSureError('A native procedure changed during retrieval. Refresh the procedure library.')
        version = remote_id(protocol.get('version'))
        if not isinstance(protocol.get('name'), str) or not protocol['name'].strip():
            raise SciSureError('A native procedure is missing its name. Review it in SciSure.')
        identity = f'{client.origin}/api/v1/protocols/{pid}'
        reference = f'{client.origin}/api/v1/protocols/version/{vid}'
        # Hash the content-bearing version fields, excluding mutable view counts
        # and signing state. The exact raw details remain available for review.
        snapshot = {k: deepcopy(protocol.get(k)) for k in
            ('protID', 'protVersionID', 'version', 'name', 'description', 'groupID', 'steps', 'vars')}
        source = dict(tenant=client.origin, group_id=group_id, protocol_id=pid, version_id=vid,
            snapshot_sha256=hashlib.sha256(encode(snapshot)).hexdigest(), reference=reference)
        p = dict(id=identity, version=str(version), name=protocol['name'], type='', kind='', modality='',
            lab='', reference=reference, instructions='', registered=False)
        rows.append(dict(key=(identity, str(version)), procedure=p, source='native', native=source,
            detail=deepcopy(protocol), entry=None, sources=[source], labs=set(),
            label=f"{p['name']} · v{version} · SciSure protocol {pid} / version {vid}"))
    client.assert_group(group_id)
    return sorted(rows, key=lambda r: (r['procedure']['name'].casefold(), r['key']))


def native_procedure_context(row, kind, modality, lab):
    """Prepare a procedure registration after the researcher classifies it."""
    if row.get('source') != 'native' or kind not in ('synthesis', 'measurement', 'computation'):
        raise InputError('Select a native procedure and choose how it is used before registering it.')
    if not modality:
        raise InputError('Choose the data type for this procedure.')
    p = row['procedure']
    identifier = f"CAT-{LABS[lab_id(lab)][0]}-PRC-{uuid.uuid5(uuid.NAMESPACE_URL, p['id']).hex}"
    return dict(workflowVersion='2', recordType='procedure', uploadMode='metadata', procedureId=identifier,
        procedureVersion=p['version'], procedureName=p['name'], procedureType=kind,
        procedureModality=modality, procedureReference=p['reference'], procedureText='')
