"""Reviewed, additive native inventory binding using the documented SciSure API.

Sample creation and experiment links are distinct operations. Every write is
checked by reading its exact identity; an uncertain response never invites an
automatic duplicate create. Inventory balances and existing samples are never
modified by this module.
"""
from copy import deepcopy
import re
import uuid
from urllib.parse import urlencode

from .model import InputError, encode, digest
from .scisure import SciSureError, remote_id, tenant_origin
from .traceability import LABS, lab_id, identity_lab, lab_name
from .configuration import plan_configuration, material_payload

PLAN_FORMAT = 'catalyst-native-inventory-plan/1'
RECEIPT_FORMAT = 'catalyst-native-inventory-receipt/1'
NATIVE_KEYS = ('nativeSampleId', 'nativeSampleTypeId', 'nativeSampleSnapshotSha256', 'nativeSampleTenant')
LINK_PREFIX = 'CATALYST inventory | '


def supporting_document_field(meta):
    return meta.get('sampleDataType') == 'FILE' and bool(re.fullmatch(
        r'CATALYST_documents(?:_[0-9a-f]{32})?', str(meta.get('key') or '')))


def _type(client, type_id, group_id):
    value = client.object(f'/api/v1/sampleTypes/{remote_id(type_id)}')
    if (value.get('sampleTypeID') != type_id or value.get('groupID') != group_id
            or value.get('deleted') is not False):
        raise SciSureError('The native sample type is archived or belongs to another group. Select an active sample in this group.')
    return value


def _creation_guard(client, group_id):
    # The API exposes both an active group and the token account's group. Do not
    # infer which group a new record will use when those scopes disagree.
    client.assert_group(group_id)
    account = client.object('/api/v1/users/getCurrentUserInfo')
    if remote_id(account.get('groupId')) != group_id or account.get('isBlocked') is not False:
        raise SciSureError('The token account and active group do not agree, or the account is blocked. Reconnect with the intended working group before creating a native sample.')


def _sample(client, identifier, group_id):
    sid = remote_id(identifier)
    sample = client.object(f'/api/v1/samples/{sid}?' + urlencode({'$expand': 'meta'}))
    if sample.get('sampleID') != sid or sample.get('archived') is not False:
        raise SciSureError('The native sample changed identity or was archived. Refresh the sample library.')
    _type(client, remote_id(sample.get('sampleTypeID')), group_id)
    if 'meta' not in sample:
        sample = dict(sample, meta=client.list(f'/api/v1/samples/{sid}/meta'))
    if not isinstance(sample['meta'], list) or not all(isinstance(m, dict) for m in sample['meta']):
        raise SciSureError('Native sample metadata is incomplete. Refresh the sample library.')
    return sample


def sample_snapshot(sample):
    """Bind scientific identity/metadata without pinning changing location or stock."""
    keys = ('sampleID', 'sampleTypeID', 'name', 'altID', 'description', 'note',
        'parentSampleID', 'created', 'creatorID', 'archived')
    value = {key: sample.get(key) for key in keys}
    # IDs that identify field bindings matter; display/log fields do not.
    value['meta'] = sorted(({k: m.get(k) for k in ('sampleTypeMetaID', 'key', 'sampleDataType', 'value',
        'samples', 'files', 'chemicalFile')} for m in sample.get('meta', [])
        if not supporting_document_field(m)), key=encode)
    return digest(encode(value))


def read_native_samples(client, group_id, query='', progress=lambda _: None):
    """Search active native inventory, with detail reads proving group membership."""
    group_id = remote_id(group_id)
    client.assert_group(group_id)
    if not isinstance(query, str) or len(query) > 256:
        raise InputError('Use a sample search of at most 256 characters.')
    path = '/api/v1/samples?' + urlencode({'search': query.strip(), 'archived': 'false'})
    listed = client.list(path)
    if len(listed) > 250:
        raise InputError('More than 250 native samples match. Enter a more specific sample name or ID.')
    rows, types, fields, seen = [], {}, {}, set()
    for item in listed:
        sid, tid = remote_id(item.get('sampleID')), remote_id(item.get('sampleTypeID'))
        if sid in seen:
            raise SciSureError('SciSure returned duplicate native sample identities. Refresh the library.')
        seen.add(sid)
        if tid not in types:
            types[tid] = client.object(f'/api/v1/sampleTypes/{tid}')
        stype = types[tid]
        if stype.get('sampleTypeID') != tid:
            raise SciSureError('The native sample type changed while searching.')
        if stype.get('groupID') != group_id or stype.get('deleted') is not False or item.get('archived') is not False:
            continue
        progress('Reading native sample ' + str(sid) + '…')
        sample = _sample(client, sid, group_id)
        if (stype.get('name') == 'CATALYST Material v2'
                and '[CATALYST schema catalyst-scisure-configuration/2]' in str(stype.get('description') or '')):
            if tid not in fields: fields[tid] = client.list(f'/api/v1/sampleTypes/{tid}/meta')
        rows.append(dict(source='native', sample=sample, native=dict(tenant=client.origin,
            group_id=group_id, sample_id=sid, sample_type_id=tid, snapshot_sha256=sample_snapshot(sample)),
            sample_type=deepcopy(stype), fields=deepcopy(fields.get(tid, [])),
            label=f"{sample.get('name') or 'Unnamed sample'} · {stype.get('name') or 'Sample'} · SciSure {sid}"))
    client.assert_group(group_id)
    return sorted(rows, key=lambda row: (row['label'].casefold(), row['native']['sample_id']))


load_native_samples = read_native_samples


def native_sample_context(row, lab):
    """Register an alias to an existing native sample without creating a duplicate."""
    if row.get('source') != 'native':
        raise InputError('Choose a sample from the native inventory search.')
    native, sample = row['native'], row['sample']
    tenant = tenant_origin(native['tenant'])
    sid, tid = remote_id(native['sample_id']), remote_id(native['sample_type_id'])
    if sample.get('sampleID') != sid or sample.get('sampleTypeID') != tid or sample_snapshot(sample) != native['snapshot_sha256']:
        raise InputError('The selected native sample snapshot changed. Search again.')
    canonical = sample.get('altID') or ''
    try:
        owner = identity_lab(canonical, 'SMP')
    except InputError:
        owner = lab_id(lab)
        canonical = f"CAT-{LABS[owner][0]}-SMP-{uuid.uuid5(uuid.NAMESPACE_URL, tenant + '/api/v1/samples/' + str(sid)).hex}"
    result = dict(workflowVersion='2', recordType='sample', uploadMode='metadata', inventoryMode='native',
        specimenId=canonical, localSampleId=sample.get('name') or str(sid),
        sampleDescription=sample.get('description') or '', sampleCreatedLab=owner,
        nativeSampleId=str(sid), nativeSampleTypeId=str(tid), nativeSampleTenant=tenant,
        nativeSampleSnapshotSha256=native['snapshot_sha256'])
    stype = row.get('sample_type') or {}
    if (stype.get('name') == 'CATALYST Material v2'
            and '[CATALYST schema catalyst-scisure-configuration/2]' in str(stype.get('description') or '')):
        for key, target, datatype in (('material_state', 'materialState', 'TEXTAREA'),
                ('parent_catalyst_sample_id', 'parentSampleId', 'TEXT')):
            bindings = [field for field in row.get('fields', []) if field.get('key') == key
                and field.get('sampleTypeID') == tid and field.get('sampleDataType') == datatype]
            if len(bindings) != 1: continue
            values = [meta.get('value') for meta in sample['meta'] if meta.get('key') == key
                and meta.get('sampleTypeMetaID') == bindings[0].get('sampleTypeMetaID')
                and meta.get('sampleDataType') == datatype]
            if len(values) == 1 and isinstance(values[0], str): result[target] = values[0]
    return result


def _definition(trace, context, catalog):
    if trace.get('record_type') == 'sample' and trace.get('material'):
        return dict(trace=deepcopy(trace), context=deepcopy(context), source_revision_id=None, source_revision_sha256=None)
    ref = trace.get('sample_ref') or {}
    rows = [entry for entry in (catalog or {}).get('entries', [])
        if entry.get('revision_id') == ref.get('source_revision_id')
        and entry.get('revision_sha256') == ref.get('source_revision_sha256')
        and entry.get('trace', {}).get('material', {}).get('id') == ref.get('id')]
    if len(rows) != 1 or not ref.get('source_revision_id') or not ref.get('source_revision_sha256'):
        raise InputError('Select the original registered sample before preparing its native inventory link.')
    row = rows[0]
    return dict(trace=deepcopy(row['trace']), context=deepcopy(row.get('context', {})),
        source_revision_id=row['revision_id'], source_revision_sha256=row['revision_sha256'])


def _canonical_sample(client, group_id, canonical):
    """Search both active and archived identities; a tombstone blocks recreation."""
    found, types = {}, {}
    for path in ('/api/v1/samples?' + urlencode(dict(search=canonical, archived='false')),
            '/api/v1/samples?archived=true'):
        for sample in client.list(path):
            if sample.get('altID') != canonical:
                continue
            tid = remote_id(sample.get('sampleTypeID'))
            if tid not in types: types[tid] = client.object(f'/api/v1/sampleTypes/{tid}')
            stype = types[tid]
            if stype.get('groupID') != group_id: continue
            sid = remote_id(sample.get('sampleID'))
            if sid in found and found[sid] != sample:
                raise SciSureError('The canonical sample changed during native discovery. Refresh before sending.')
            if sample.get('archived') is not False or stype.get('deleted') is not False:
                raise InputError('An archived native record already reserves this sample ID. Restore or reconcile it in SciSure.')
            found[sid] = sample
    if len(found) > 1:
        raise InputError('Multiple native samples have this canonical ID. Reconcile them in SciSure before sending.')
    return _sample(client, next(iter(found)), group_id) if found else None


def _pinned_sample(client, group_id, context):
    values = [context.get(key) for key in NATIVE_KEYS]
    if not any(values): return None
    if not all(values) or context['nativeSampleTenant'] != client.origin:
        raise InputError('The linked native sample belongs to another server or has incomplete identifiers. Select it again.')
    sample = _sample(client, context['nativeSampleId'], group_id)
    if (sample['sampleTypeID'] != remote_id(context['nativeSampleTypeId'])
            or sample_snapshot(sample) != context['nativeSampleSnapshotSha256']):
        raise InputError('The native sample changed since it was selected. Refresh and review its updated definition.')
    return sample


def _binding(plan):
    return {key: deepcopy(plan[key]) for key in ('ready', 'conflicts', 'configuration_sha256',
        'sample_type_id', 'field_bindings', 'workspace', 'legacy_sample_types') if key in plan}


def _matches_body(sample, body):
    for key in ('sampleTypeID', 'name', 'altID', 'description'):
        if sample.get(key) != body.get(key): return False
    if (sample.get('parentSampleID') or None) != body.get('parentSampleID'): return False
    wanted = {m['sampleTypeMetaID']: m for m in body['sampleMetas']}
    actual = {}
    for meta in sample.get('meta', []):
        key = meta.get('sampleTypeMetaID')
        if key in wanted:
            if key in actual: return False
            actual[key] = meta
    return all(key in actual and all(actual[key].get(field) == item[field]
        for field in ('key', 'sampleDataType', 'value')) for key, item in wanted.items())


def _managed_sample(sample, sample_type):
    name, description = sample_type.get('name'), sample_type.get('description') or ''
    if name not in ('CATALYST Material v1', 'CATALYST Material v2'):
        raise InputError('This canonical ID is assigned to a different native type. Select the existing sample explicitly instead of recreating it.')
    version = name[-1]
    if '[CATALYST schema catalyst-scisure-configuration/' + version + ']' not in description:
        raise InputError('The matching native sample type is not a verified CATALYST schema. Select the existing sample explicitly.')
    identities = [m for m in sample.get('meta', []) if m.get('key') == 'catalyst_sample_id']
    if len(identities) != 1 or identities[0].get('value') != sample.get('altID'):
        raise InputError('The native sample metadata and canonical ID disagree. Reconcile the record in SciSure.')


def plan_inventory(client, group_id, trace, context, catalog, destination):
    """Read a concrete, reviewable create/reuse and Used/Generated link plan."""
    if context.get('inventoryMode') != 'native':
        raise InputError('Choose native inventory linking before preparing an inventory plan.')
    kind = trace.get('record_type')
    if kind not in ('sample', 'measurement', 'synthesis'):
        raise InputError('Native inventory linking applies to physical samples, measurements, and synthesis executions.')
    group_id = remote_id(group_id)
    if destination.get('group_id') != group_id or destination.get('tenant') != client.origin:
        raise InputError('The inventory plan requires the selected server, group, and experiment.')
    client.verify_destination(destination)
    definition = _definition(trace, context, catalog)
    material = definition['trace']['material']
    canonical = material['id']
    identity_lab(canonical, 'SMP')
    native = _pinned_sample(client, group_id, definition['context'])
    explicit = native is not None
    if not explicit: native = _canonical_sample(client, group_id, canonical)
    binding, body, parent = None, None, None
    if native is not None and not explicit:
        sample_type = _type(client, native['sampleTypeID'], group_id)
        _managed_sample(native, sample_type)
        if sample_type['name'] == 'CATALYST Material v1':
            expected = dict(sample_created_lab=lab_name(material['creator_lab']),
                material_state=material.get('state') or '', material_kind=material.get('kind') or '',
                parent_catalyst_sample_id=material.get('parent_sample_id') or '')
            for key, value in expected.items():
                matches = [m.get('value') for m in native['meta'] if m.get('key') == key]
                if (matches or ['']) != [value]:
                    raise InputError('The legacy native sample differs from the registered material. Select and review the existing native sample explicitly.')
    managed_v2 = native is not None and not explicit and sample_type['name'] == 'CATALYST Material v2'
    if native is None or managed_v2:
        if native is None: _creation_guard(client, group_id)
        configuration = plan_configuration(client, group_id)
        if not configuration.get('ready'):
            raise InputError('Prepare and install the CATALYST Material v2 configuration before creating a native sample. Existing native samples can be selected without creating a new type.')
        binding = _binding(configuration)
        if material.get('parent_sample_id'):
            parent = _canonical_sample(client, group_id, material['parent_sample_id'])
            if parent is None:
                raise InputError('Register the parent in native inventory before preparing this child sample.')
            _managed_sample(parent, _type(client, parent['sampleTypeID'], group_id))
        body = material_payload(definition['trace'], binding, parent_sample=parent)
        if managed_v2:
            if not _matches_body(native, body):
                raise InputError('The native sample properties differ from the registered sample definition. Select and review the existing native sample explicitly or reconcile its metadata in SciSure.')
            # Reuse plans bind the actual snapshot; creation details are not writes.
            binding, body, parent = None, None, None
    section_type = {'measurement': 'SAMPLESIN', 'synthesis': 'SAMPLESOUT'}.get(kind)
    result = dict(format=PLAN_FORMAT, tenant=client.origin, group_id=group_id,
        experiment_id=remote_id(destination['experiment_id']), record_type=kind,
        canonical_sample_id=canonical, definition=definition,
        sample=dict(action='reuse' if native else 'create', explicit=explicit,
            sample_id=native['sampleID'] if native else None,
            sample_type_id=native['sampleTypeID'] if native else binding['sample_type_id'],
            snapshot_sha256=sample_snapshot(native) if native else None,
            name=(native.get('name') or '') if native else body['name'],
            description=(native.get('description') or '') if native else body['description'],
            body=body, configuration=binding,
            parent=({key: parent.get(key) for key in ('sampleID', 'sampleTypeID', 'altID', 'archived')} if parent else None)),
        link=dict(section_type=section_type, heading=LINK_PREFIX + trace['dataset']['id']) if section_type else None)
    client.verify_destination(destination)
    validate_inventory_plan(result, trace, context)
    return result


def validate_inventory_plan(plan, trace, context):
    """Pure contract check for an approved plan stored in a review package."""
    try:
        if (not isinstance(plan, dict) or plan.get('format') != PLAN_FORMAT
                or context.get('inventoryMode') != 'native' or trace.get('record_type') not in ('sample', 'measurement', 'synthesis')
                or plan['record_type'] != trace['record_type']): raise ValueError()
        if tenant_origin(plan['tenant']) != plan['tenant']: raise ValueError()
        remote_id(plan['group_id']); remote_id(plan['experiment_id'])
        definition, sample = plan['definition'], plan['sample']
        material = definition['trace']['material']
        if plan['canonical_sample_id'] != material['id']: raise ValueError()
        identity_lab(material['id'], 'SMP')
        if trace['record_type'] == 'sample':
            if definition['trace'] != trace or definition['context'] != context: raise ValueError()
        else:
            ref = trace['sample_ref']
            if (material['id'] != ref['id'] or definition['source_revision_id'] != ref.get('source_revision_id')
                    or definition['source_revision_sha256'] != ref.get('source_revision_sha256')): raise ValueError()
        expected_link = {'measurement': 'SAMPLESIN', 'synthesis': 'SAMPLESOUT'}.get(trace['record_type'])
        if plan['link'] != (dict(section_type=expected_link, heading=LINK_PREFIX + trace['dataset']['id']) if expected_link else None):
            raise ValueError()
        if type(sample['explicit']) is not bool: raise ValueError()
        if bool(any(definition['context'].get(key) for key in NATIVE_KEYS)) != sample['explicit']: raise ValueError()
        if not isinstance(sample['name'], str) or not isinstance(sample['description'], str): raise ValueError()
        remote_id(sample['sample_type_id'])
        if sample['action'] == 'create':
            if sample['sample_id'] is not None or sample['snapshot_sha256'] is not None or sample['explicit']: raise ValueError()
            configuration = sample['configuration']
            if (configuration['workspace']['tenant'] != plan['tenant']
                    or configuration['workspace']['group_id'] != plan['group_id']
                    or configuration['sample_type_id'] != sample['sample_type_id']): raise ValueError()
            expected = material_payload(definition['trace'], configuration, parent_sample=sample['parent'])
            if (sample['body'] != expected or sample['name'] != expected['name']
                    or sample['description'] != expected['description']): raise ValueError()
        elif sample['action'] == 'reuse':
            remote_id(sample['sample_id'])
            if not re.fullmatch(r'[0-9a-f]{64}', sample['snapshot_sha256']): raise ValueError()
            if sample['body'] is not None or sample['configuration'] is not None or sample['parent'] is not None: raise ValueError()
            pinned = definition['context']
            if sample['explicit'] and (pinned.get('nativeSampleId') != str(sample['sample_id'])
                    or pinned.get('nativeSampleTypeId') != str(sample['sample_type_id'])
                    or pinned.get('nativeSampleTenant') != plan['tenant']
                    or pinned.get('nativeSampleSnapshotSha256') != sample['snapshot_sha256']): raise ValueError()
        else: raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, SciSureError) as exc:
        raise InputError('The native inventory plan does not match this reviewed record. Prepare a new preview.') from exc
    return plan


def inventory_summary(plan):
    sample = plan['sample']
    lines = ['Native inventory: ' + ('reuse existing sample' if sample['action'] == 'reuse' else 'create sample if absent'),
        'Sample name: ' + sample['name'], 'Description: ' + sample['description'],
        'Canonical sample: ' + plan['canonical_sample_id'],
        f"SciSure sample: {sample['sample_id'] or 'assigned after reviewed creation'}",
        f"Sample type: {sample['sample_type_id']}"]
    if plan.get('link'):
        lines.append('Experiment relationship: ' + ('Used sample' if plan['link']['section_type'] == 'SAMPLESIN' else 'Generated sample'))
    else:
        lines.append('Register the sample; no measurement or synthesis relationship is added.')
    lines.append('Existing sample properties and inventory quantities remain unchanged.')
    return '\n'.join(lines)


def validate_inventory_receipt(receipt, plan):
    """Check a stored native receipt against the approved intent and target."""
    try:
        expected_keys = {'format', 'tenant', 'group_id', 'experiment_id', 'canonical_sample_id',
            'sample_id', 'sample_type_id', 'sample_snapshot_sha256', 'link'}
        if not isinstance(receipt, dict) or set(receipt) != expected_keys or receipt['format'] != RECEIPT_FORMAT:
            raise ValueError()
        if any(receipt[key] != plan[key] for key in ('tenant', 'group_id', 'experiment_id', 'canonical_sample_id')):
            raise ValueError()
        sid = remote_id(receipt['sample_id'])
        if receipt['sample_type_id'] != plan['sample']['sample_type_id']:
            raise ValueError()
        remote_id(receipt['sample_type_id'])
        if not re.fullmatch(r'[0-9a-f]{64}', receipt['sample_snapshot_sha256']): raise ValueError()
        if plan['sample']['action'] == 'reuse' and (sid != plan['sample']['sample_id']
                or receipt['sample_snapshot_sha256'] != plan['sample']['snapshot_sha256']): raise ValueError()
        if plan['link'] is None:
            if receipt['link'] is not None: raise ValueError()
        else:
            link = receipt['link']
            if (set(link) != {'section_id', 'section_type', 'sample_id'}
                    or link['sample_id'] != sid or link['section_type'] != plan['link']['section_type']): raise ValueError()
            remote_id(link['sample_id'])
            remote_id(link['section_id'])
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, SciSureError) as exc:
        raise InputError('The native inventory receipt does not match the approved sample and experiment.') from exc
    return receipt


class NativeInventory:
    def __init__(self, client, destination, operations=None):
        self.client, self.destination = client, dict(destination)
        self.operations = operations if operations is not None else {}

    def prepare(self, trace, context, catalog):
        return plan_inventory(self.client, self.destination['group_id'], trace, context, catalog, self.destination)

    def _step(self, key, find, write, *, returns_id=True):
        found = find()
        if found is not None:
            self.operations[key] = 'verified'
            return found
        if self.operations.get(key) in ('unknown', 'writing', 'verified'):
            raise SciSureError('A native inventory write is still unconfirmed. No duplicate was sent. Check SciSure before retrying.', True)
        self.client.verify_destination(self.destination)
        self.operations[key] = 'writing'
        returned = False
        try:
            result = write()
            returned = True
            if returns_id: result = remote_id(result)
            verified = find()
            if verified is None or (returns_id and result != verified):
                raise SciSureError('SciSure has not confirmed the native inventory write yet. Check its status before retrying.', True)
            self.operations[key] = 'verified'
            return verified
        except Exception as exc:
            self.operations[key] = ('rejected' if not returned and isinstance(exc, SciSureError) and not exc.uncertain else 'unknown')
            raise

    def _section(self, link):
        path = f'/api/v1/experiments/{self.destination["experiment_id"]}/sections'
        def find():
            rows = {}
            for route in (path, path + '?archived=true'):
                for row in self.client.list(route):
                    if row.get('sectionHeader') != link['heading']: continue
                    if (row.get('sectionType') != link['section_type'] or row.get('deleted') is not False
                            or row.get('experimentID') != self.destination['experiment_id']):
                        raise InputError('The native inventory section was archived or changed. Reconcile it in SciSure.')
                    sid = remote_id(row.get('expJournalID'))
                    if sid in rows and rows[sid] != row: raise SciSureError('The native inventory section changed during verification.')
                    rows[sid] = row
            if len(rows) > 1: raise InputError('Duplicate native inventory sections require reconciliation in SciSure.')
            return next(iter(rows)) if rows else None
        return self._step('inventory-section/' + link['heading'], find,
            lambda: self.client.request(path, 'POST', dict(sectionType=link['section_type'], sectionHeader=link['heading'])))

    def check(self, plan, trace, context, catalog):
        """Recheck the complete plan before any publication writes are started."""
        validate_inventory_plan(plan, trace, context)
        if any(plan[key] != self.destination[key] for key in ('tenant', 'group_id', 'experiment_id')):
            raise InputError('The reviewed native inventory plan targets another experiment. Prepare and approve a new review.')
        fresh = self.prepare(trace, context, catalog)
        planned_sample, current_sample = plan['sample'], fresh['sample']
        # An earlier batch record may have created exactly this approved sample.
        equivalent = False
        if planned_sample['action'] == 'create' and current_sample['action'] == 'reuse':
            equivalent = _matches_body(_sample(self.client, current_sample['sample_id'], plan['group_id']), planned_sample['body'])
            fresh = dict(fresh, sample=planned_sample)
        if fresh != plan or (planned_sample['action'] == 'create' and current_sample['action'] == 'reuse' and not equivalent):
            raise InputError('The native sample, schema, or reviewed source changed. Refresh and approve an updated preview.')
        return fresh

    def apply(self, plan, trace, context, catalog, progress=lambda _: None):
        progress('Rechecking the approved native inventory plan…')
        self.check(plan, trace, context, catalog)
        planned_sample = plan['sample']
        if planned_sample['action'] == 'create':
            body = planned_sample['body']
            def find_sample():
                value = _canonical_sample(self.client, plan['group_id'], plan['canonical_sample_id'])
                if value is None: return None
                if not _matches_body(value, body):
                    raise InputError('An existing native sample has different properties from the approved creation. No duplicate was sent.')
                return value['sampleID']
            def create_sample():
                _creation_guard(self.client, plan['group_id'])
                return self.client.request('/api/v1/samples', 'POST', body)
            sid = self._step('native-sample/' + plan['canonical_sample_id'], find_sample, create_sample)
        else:
            sid = planned_sample['sample_id']
        sample = _sample(self.client, sid, plan['group_id'])
        if planned_sample['action'] == 'reuse' and sample_snapshot(sample) != planned_sample['snapshot_sha256']:
            raise InputError('The native sample changed during transfer. Refresh and review it again.')
        link_receipt = None
        if plan['link']:
            progress('Linking the native sample to the experiment…')
            section_id = self._section(plan['link'])
            path = f'/api/v1/experiments/sections/{section_id}/samples'
            def find_link():
                rows = self.client.list(path)
                ids = [remote_id(row.get('sampleID')) for row in rows]
                if any(identifier != sid for identifier in ids) or len(ids) > 1:
                    raise InputError('The CATALYST inventory section contains another or duplicate sample. Reconcile it in SciSure.')
                if sid not in ids: return None
                if rows[0].get('archived') is not False:
                    raise InputError('The linked native sample was archived. No new link was written.')
                return sid
            self._step(f'inventory-link/{section_id}/{sid}', find_link,
                lambda: self.client.request(path, 'PUT', [sid]), returns_id=False)
            # Verify section ownership again after writing its sample membership.
            if self._section(plan['link']) != section_id:
                raise SciSureError('The native inventory section changed during transfer.', True)
            link_receipt = dict(section_id=section_id, section_type=plan['link']['section_type'], sample_id=sid)
        final_sample = _sample(self.client, sid, plan['group_id'])
        if sample_snapshot(final_sample) != sample_snapshot(sample):
            raise SciSureError('The native sample changed during final verification. Check SciSure before retrying.', True)
        self.client.verify_destination(self.destination)
        receipt = dict(format=RECEIPT_FORMAT, tenant=plan['tenant'], group_id=plan['group_id'],
            experiment_id=plan['experiment_id'], canonical_sample_id=plan['canonical_sample_id'],
            sample_id=sid, sample_type_id=sample['sampleTypeID'], sample_snapshot_sha256=sample_snapshot(sample), link=link_receipt)
        return validate_inventory_receipt(receipt, plan)
