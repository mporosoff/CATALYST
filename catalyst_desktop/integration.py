"""Read-only native SciSure setup inspection; never creates inventory or alters roles."""
from .model import now
from .scisure import SciSureError, remote_id

FIELD_TYPES = {'TEXT', 'TEXTAREA', 'RADIO', 'CHECKBOX', 'NUMERIC', 'COMBO', 'DATE', 'DATETIME',
    'FILE', 'SAMPLELINK', 'PROJECT', 'CHEMICAL'}


def inspect_setup(client, group_id, progress=lambda _: None, *, experiment_id=None, sample_id=None, protocol_version_id=None):
    report = dict(checked_at=now(), tenant=client.origin, group_id=group_id,
        scope='Records visible to this token in its active group; not proof of consortium-wide access',
        native_inventory_writes_enabled=False, checks=[], sample_types=[])
    def check(name, action):
        try:
            value = action()
            report['checks'].append(dict(name=name, status='read succeeded'))
            return value
        except SciSureError as e:
            report['checks'].append(dict(name=name, status='not verified', detail=str(e)))
            return None
    active = client.request('/api/v1/groups/active')
    if not isinstance(active, dict) or active.get('groupID') != group_id:
        raise SciSureError('The active group changed. Reconnect before inspecting setup.')
    user = check('Current SciSure account', lambda: client.request('/api/v1/users/getCurrentUserInfo'))
    if isinstance(user, dict):
        report['token_account'] = dict(user_id=remote_id(user.get('userID')), name=user.get('fullName') or
            ' '.join(str(user.get(k) or '') for k in ('firstName', 'lastName')), blocked=user.get('isBlocked'),
            group_id=user.get('groupId'), permissions=user.get('permissions'))
    if experiment_id is not None:
        destination = check('Selected experiment', lambda: client.destination(experiment_id, group_id, writable=False))
        if destination:
            collaborators = check('Experiment collaborators', lambda: client.list(f'/api/v1/experiments/{remote_id(experiment_id)}/collaborators'))
            report['experiment'] = dict(destination, visible_collaborators=[dict(user_id=u.get('userID'),
                name=u.get('fullName') or ' '.join(str(u.get(k) or '') for k in ('firstName', 'lastName')))
                for u in collaborators] if collaborators is not None else None)
    types = check('Sample types', lambda: client.list('/api/v1/sampleTypes'))
    if types is not None:
        if len(types) > 100:
            raise SciSureError('More than 100 sample types are visible. Inspection requires a narrower configured scope.')
        for item in types:
            sid = remote_id(item.get('sampleTypeID'))
            progress('Inspecting native fields for SciSure sample type ' + str(sid) + '…')
            detail = check(f'Sample type {sid}', lambda: client.request(f'/api/v1/sampleTypes/{sid}'))
            fields = check(f'Sample type {sid} fields', lambda: client.list(f'/api/v1/sampleTypes/{sid}/meta'))
            if not isinstance(detail, dict): continue
            report['sample_types'].append(dict(id=sid, name=detail.get('name'), group_id=detail.get('groupID'),
                deleted=detail.get('deleted'), quantity_required=detail.get('quantityRequired'),
                quantity_type=detail.get('defaultQuantityType'), unit=detail.get('defaultUnit'),
                fields=[dict(id=remote_id(f.get('sampleTypeMetaID')), key=f.get('key'), type=f.get('sampleDataType'),
                    required=f.get('required'), supported_type=f.get('sampleDataType') in FIELD_TYPES,
                    options=f.get('optionValues'), has_default=f.get('defaultValue') not in (None, ''),
                    has_custom_validation=bool(f.get('validationScript') or f.get('MaskRegExp')))
                    for f in fields] if fields is not None else None))
    if sample_id is not None:
        sid = remote_id(sample_id)
        sample = check('Native sample reference', lambda: client.request(f'/api/v1/samples/{sid}?%24expand=meta,parents'))
        if isinstance(sample, dict):
            if sample.get('sampleID') != sid:
                raise SciSureError('SciSure returned a different sample than requested.')
            report['sample_reference'] = dict(id=sid, name=sample.get('name'), external_id=sample.get('altID'),
                sample_type_id=sample.get('sampleTypeID'), archived=sample.get('archived'),
                parents=sample.get('parents'), parent_sample_id=sample.get('parentSampleID'))
    if protocol_version_id is not None:
        pid = remote_id(protocol_version_id)
        protocol = check('Native protocol version', lambda: client.request(f'/api/v1/protocols/version/{pid}?%24expand=signingStatus'))
        if isinstance(protocol, dict):
            if protocol.get('protVersionID') != pid:
                raise SciSureError('SciSure returned a different protocol version than requested.')
            report['protocol_reference'] = dict(protocol_id=protocol.get('protID'), version_id=pid,
                version=protocol.get('version'), name=protocol.get('name'), group_id=protocol.get('groupID'),
                published=protocol.get('draft') is False and protocol.get('deleted') is False,
                signing_status=protocol.get('signingStatus'))
    report['required_next_steps'] = [
        'Prepare the selected CATALYST Material v1 configuration to discover and verify its native sampleTypeMetaID bindings.',
        'Supply every required field and quantity; SciSure sample creation does not guarantee creation of required metadata.',
        'Resolve shared procedures to published protVersionIDs, then test native Used/Generated sample links.',
        'Verify experiment collaboration and group/subgroup roles for all six labs using their intended accounts.',
        'Perform an approved synthetic write/read-back test; successful reads alone do not verify write permission.',
    ]
    if client.request('/api/v1/groups/active').get('groupID') != group_id:
        raise SciSureError('The active group changed during inspection. Discard this result and reconnect.')
    return report


def setup_summary(report):
    account = report.get('token_account', {})
    lines = [f'SciSure group: {report["group_id"]}', f'Account: {account.get("name", "Not verified")} (ID {account.get("user_id", "unknown")})',
        report['scope'], '', 'Native inventory writes are not enabled by this inspection.', '']
    for check in report['checks']:
        lines.append(check['name'] + ': ' + check['status'] + (' — ' + check['detail'] if check.get('detail') else ''))
    for sample_type in report['sample_types']:
        lines.extend(['', f'{sample_type["name"]} (sample type {sample_type["id"]})',
            f'Quantity required: {sample_type["quantity_required"]}; unit: {sample_type["unit"]}'])
        fields = sample_type['fields']
        lines.append('Required fields: ' + (', '.join(f'{f["key"]} ({f["type"]})' for f in fields if f['required'])
            or 'none declared') if fields is not None else 'Fields could not be verified.')
    if report.get('sample_reference'):
        s = report['sample_reference']
        lines.extend(['', f'Native sample: {s["name"]} · ID {s["id"]}', f'External/canonical ID field: {s["external_id"]}', f'Archived: {s["archived"]}'])
    if report.get('protocol_reference'):
        p = report['protocol_reference']
        lines.extend(['', f'Protocol: {p["name"]} · version {p["version"]} · version ID {p["version_id"]}', f'Published and not deleted: {p["published"]}'])
    lines.extend(['', 'Still to configure and test:', *report['required_next_steps']])
    return '\n'.join(lines)
