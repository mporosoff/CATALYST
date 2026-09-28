"""Versioned native SciSure configuration, discovery, and additive schema setup."""
from .model import InputError, encode, digest
from .scisure import SciSureError, remote_id
from .traceability import LABS, lab_id, lab_name, identity_lab

FORMAT = 'catalyst-scisure-configuration/2'
TYPE_NAME = 'CATALYST Material v2'
MARKER = '[CATALYST schema catalyst-scisure-configuration/2]'
LEGACY_TYPE_NAME = 'CATALYST Material v1'
LEGACY_MARKER = '[CATALYST schema catalyst-scisure-configuration/1]'
MIGRATION_GUIDANCE = (
    'Version 2 is a separate sample type for new registrations. Existing version 1 types, '
    'fields and samples remain unchanged; no conversion or duplicate sample creation is performed. '
    'Keep existing sample IDs and reuse verified existing inventory records. Synthesis executions '
    'and procedure versions remain linked experiment records, rather than required sample fields.'
)


def material_fields():
    """Keep only stable material facts here; acquisition/results belong to experiments."""
    definitions = [
        ('catalyst_sample_id', 'TEXT', True, 'material.id', 'Immutable consortium sample ID; also stored in native altID.'),
        ('sample_created_lab', 'COMBO', True, 'material.creator_lab', 'Lab that created this container/aliquot/treated sample.'),
        ('sample_description', 'TEXTAREA', True, 'material.description', 'Researcher-supplied sample description or composition; no synthesis history is required.'),
        ('material_state', 'TEXTAREA', False, 'material.state', 'Optional physical state or treatment defining this material.'),
        ('parent_catalyst_sample_id', 'TEXT', False, 'material.parent_sample_id', 'Canonical parent; native parentSampleID must reference its resolved SciSure sample.'),
        ('catalyst_schema_version', 'TEXT', True, None, 'Native material binding schema version.'),
    ]
    fields = []
    for key, datatype, required, source, notes in definitions:
        field = dict(key=key, sampleDataType=datatype, required=required, notes=notes)
        if key == 'sample_created_lab':
            field['optionValues'] = [label for _, label in LABS.values()]
        fields.append(dict(definition=field, source=source,
            encoding='lab_display_name' if key == 'sample_created_lab' else 'text',
            constant='2' if source is None else None))
    return fields


def configuration_document():
    return dict(format=FORMAT, version=2, expected_group_name='CATALYST',
        sample_type=dict(name=TYPE_NAME, description=MARKER + ' Physical catalyst materials, aliquots and treated specimens. '
            'Register the sample once; synthesis executions, methods, acquisition data and processing results '
            'belong to linked experiments. No default stock mass is assigned.',
            quantityRequired=False, defaultQuantityType='Mass', defaultUnit='MilliGram',
            thresholdEnabled=False, defaultThresholdAction='Nothing'),
        fields=material_fields(),
        structure=dict(project='CATALYST', studies=[label for _, label in LABS.values()],
            study_basis='Acquisition/executing lab; sample owner is stored with the sample definition.',
            experiment_basis='One sample registration, procedure definition, synthesis execution or measurement/calculation acquisition; revisions remain with that work.',
            autoCollaborate=True, approval_policy='Preserve the existing project/study signing and approval policy.',
            protocols='Shared scientific procedure plus exact published protVersionID; never invent procedure content.',
            computational_models='ELN records and versioned model manifests, not physical inventory Samples.'),
        links=dict(synthesis={'used': 'Resolved reagent/parent Samples when supplied', 'generated': 'Resulting physical sample'},
            measurement={'used': 'Measured physical sample', 'generated': 'Only a newly identified physical product/derivative'},
            computation={'used': 'None by default; physical comparison links are explicit evidence, not consumption', 'generated': 'No physical Sample'}),
        storage=dict(originals='Revision-specific FILE section; preserve bytes and embedded metadata.',
            standardized='Approved versioned review attachment; optional processed exports are separate artifacts.',
            provenance='Keep normalization, scientific processing, validation, approval and publication receipt distinct.',
            image_handling='Original file attachment; optional IMAGE sections may supplement but never replace originals.',
            quantity='Optional inventory balance, entered separately; never infer stock mass from reactor loading.',
            native_ids='Discover from this tenant and record in memory/remote publication receipts; never hard-code example IDs.'),
        migration=dict(from_sample_type=LEGACY_TYPE_NAME, strategy='additive', guidance=MIGRATION_GUIDANCE),
        installation_scope='Creates only the dedicated version 2 material sample type and missing metadata fields. '
            'Does not modify version 1 or unrelated types, create samples, projects, studies or protocols, '
            'sign records, or change permissions.')


def _workspace(client, group_id):
    active = client.object('/api/v1/groups/active')
    if remote_id(active.get('groupID')) != group_id:
        raise SciSureError('The active group changed. Reconnect and prepare configuration again.')
    name = active.get('name') or active.get('groupName') or ''
    if not isinstance(name, str) or name.strip().casefold() != 'catalyst':
        raise SciSureError('Configuration is restricted to the CATALYST group. Select it as your working group in SciSure and reconnect.')
    return dict(tenant=client.origin, group_id=remote_id(group_id), group_name=name)


def _sample_types(client):
    # Search active and archived types; an archived collision must not be silently recreated.
    records = {}
    for path in ('/api/v1/sampleTypes', '/api/v1/sampleTypes?archived=true'):
        for item in client.list(path):
            sid = remote_id(item.get('sampleTypeID'))
            if sid in records and records[sid] != item:
                raise SciSureError('Sample type discovery changed between reads. Refresh before configuration.')
            records[sid] = item
    return list(records.values())


def _type(client, records=None):
    records = _sample_types(client) if records is None else records
    matches = [t for t in records if str(t.get('name') or '').casefold() == TYPE_NAME.casefold()]
    if len(matches) > 1:
        raise InputError('Duplicate CATALYST material types exist. Resolve their identity in SciSure before configuration.')
    if not matches:
        return None
    sid = remote_id(matches[0]['sampleTypeID'])
    detail = client.object(f'/api/v1/sampleTypes/{sid}')
    if detail.get('sampleTypeID') != sid:
        raise SciSureError('SciSure returned a different sample type than requested. Configuration is paused.')
    return detail


def _legacy_types(client, records, group_id):
    """Discover prior installations without modifying or adopting their stricter schema."""
    legacy = []
    for record in records:
        if str(record.get('name') or '').casefold() != LEGACY_TYPE_NAME.casefold():
            continue
        sid = remote_id(record.get('sampleTypeID'))
        detail = client.object(f'/api/v1/sampleTypes/{sid}')
        if detail.get('sampleTypeID') != sid:
            raise SciSureError('SciSure returned a different legacy sample type than requested. Configuration is paused.')
        compatible = (detail.get('groupID') == group_id and detail.get('deleted') is False
            and LEGACY_MARKER in str(detail.get('description') or '')
            and str(detail.get('name') or '').casefold() == LEGACY_TYPE_NAME.casefold())
        legacy.append(dict(sample_type_id=sid, name=detail.get('name'),
            archived=detail.get('deleted') is not False, compatible_identity=compatible))
    return sorted(legacy, key=lambda item: item['sample_type_id'])


def _type_conflicts(actual, group_id):
    expected = configuration_document()['sample_type']
    errors = []
    if actual.get('groupID') != group_id or actual.get('deleted') is not False:
        errors.append('The matching material type is archived or belongs to another group.')
    if MARKER not in str(actual.get('description') or ''):
        errors.append('A same-name type is not marked as this CATALYST schema; it will not be adopted or overwritten.')
    for key in ('name', 'quantityRequired', 'defaultQuantityType', 'defaultUnit', 'thresholdEnabled'):
        if actual.get(key) != expected[key]:
            errors.append('Material type setting differs: ' + key + '.')
    return errors


def _field_conflicts(actual, expected):
    errors = []
    for key in ('key', 'sampleDataType', 'required'):
        if actual.get(key) != expected.get(key): errors.append(expected['key'] + ': incompatible ' + key + '.')
    options = actual.get('optionValues') or []
    if (not isinstance(options, list) or not all(isinstance(value, str) for value in options)
            or len(set(options)) != len(options) or set(options) != set(expected.get('optionValues') or [])):
        errors.append(expected['key'] + ': option values differ from the controlled schema.')
    section = actual.get('sampleTypeSection')
    if (actual.get('validationScript') or actual.get('MaskRegExp') or (section is not None
            and (not isinstance(section, dict) or section.get('sectionConditions')))):
        errors.append(expected['key'] + ': custom validation/conditions require explicit review; no script will be executed.')
    return errors


def plan_configuration(client, group_id):
    workspace = _workspace(client, group_id)
    document = configuration_document()
    records = _sample_types(client)
    result = dict(format='catalyst-scisure-setup-plan/1', workspace=workspace,
        configuration_sha256=digest(encode(document)), actions=[], conflicts=[],
        sample_type_id=None, field_bindings={}, scope=document['installation_scope'],
        legacy_sample_types=_legacy_types(client, records, group_id), migration=MIGRATION_GUIDANCE)
    actual = _type(client, records)
    if actual is None:
        result['actions'].append(dict(kind='create_sample_type', body=document['sample_type']))
        fields = []
    else:
        sid = remote_id(actual.get('sampleTypeID'))
        result['sample_type_id'] = sid
        result['conflicts'].extend(_type_conflicts(actual, group_id))
        fields = client.list(f'/api/v1/sampleTypes/{sid}/meta')
    by_key = {}
    expected_keys = {f['definition']['key'] for f in document['fields']}
    for field in fields:
        key = str(field.get('key') or '')
        if key.casefold() in by_key:
            result['conflicts'].append('Duplicate metadata key: ' + key + '.')
        by_key[key.casefold()] = field
        if field.get('sampleTypeID') != result['sample_type_id']:
            result['conflicts'].append('A field belongs to a different sample type: ' + key + '.')
        if key not in expected_keys and field.get('required'):
            result['conflicts'].append('Additional required field has no CATALYST binding: ' + key + '.')
    for field in document['fields']:
        definition = field['definition']
        actual_field = by_key.get(definition['key'].casefold())
        if actual_field is None:
            result['actions'].append(dict(kind='create_field', body=definition))
        else:
            result['conflicts'].extend(_field_conflicts(actual_field, definition))
            result['field_bindings'][definition['key']] = remote_id(actual_field.get('sampleTypeMetaID'))
    _workspace(client, group_id)
    result['ready'] = not result['actions'] and not result['conflicts']
    return result


def configuration_summary(plan):
    lines = ['CATALYST material schema v2',
        f'Tenant: {plan["workspace"]["tenant"]}',
        f'Group: {plan["workspace"]["group_name"]} ({plan["workspace"]["group_id"]})', '', plan['scope'], '']
    if plan['conflicts']:
        lines.extend(['Configuration cannot be applied:', *plan['conflicts']])
    elif plan['ready']:
        lines.append('Native material schema verified. Sample type ID: ' + str(plan['sample_type_id']))
    else:
        lines.append('Changes to apply:')
        for action in plan['actions']:
            body = action['body']
            lines.append('Create sample type: ' + body['name'] if action['kind'] == 'create_sample_type' else
                f'Add {body["key"]} ({body["sampleDataType"]}; {"required" if body["required"] else "optional"})')
    lines.extend(['', 'Migration: ' + plan.get('migration', MIGRATION_GUIDANCE)])
    for legacy in plan.get('legacy_sample_types', []):
        status = 'existing records can be reused after sample verification' if legacy['compatible_identity'] else 'archived or unverified; manual review needed'
        lines.append(f'Existing {legacy["name"]} (type {legacy["sample_type_id"]}): {status}. Left unchanged.')
    lines.extend(['', 'Existing verified field bindings:'])
    lines.extend(f'{key}: {value}' for key, value in plan['field_bindings'].items())
    lines.extend(['', 'Installing this schema does not upload research data. Native sample registration and experiment links require their own reviewed submission.'])
    return '\n'.join(lines)


class SchemaInstaller:
    """Add only absent schema records; retain ambiguous-write guards within this session."""
    def __init__(self, client, group_id, operations=None):
        self.client, self.group_id = client, group_id
        self.operations = operations if operations is not None else {}

    def _guard(self):
        _workspace(self.client, self.group_id)
        # Sample-type creation targets the account's working/primary group. Require agreement.
        account = self.client.object('/api/v1/users/getCurrentUserInfo')
        if remote_id(account.get('groupId')) != self.group_id or account.get('isBlocked') is not False:
            raise SciSureError('The token account and active CATALYST group do not agree, or the account is blocked. Setup is paused.')

    def _step(self, key, find, path, body):
        existing = find()
        if existing is not None:
            self.operations[key] = 'verified'
            return existing
        if self.operations.get(key) in ('unknown', 'writing', 'verified'):
            raise SciSureError('An earlier setup write remains unconfirmed. No duplicate was sent. Reconcile the schema in SciSure.', True)
        self._guard()
        self.operations[key] = 'writing'
        returned = False
        try:
            response = self.client.request(path, 'POST', body)
            returned = True
            identifier = remote_id(response)
            if find() != identifier:
                raise SciSureError('SciSure has not confirmed the new schema record.', True)
            self.operations[key] = 'verified'
            return identifier
        except Exception as error:
            self.operations[key] = 'rejected' if not returned and isinstance(error, SciSureError) and not error.uncertain else 'unknown'
            raise

    def apply(self, reviewed_plan, progress=lambda _: None):
        current = plan_configuration(self.client, self.group_id)
        if current['conflicts']:
            raise InputError('The existing schema conflicts with CATALYST. Prepare configuration to review the differences.')
        if encode(current) != encode(reviewed_plan):
            raise InputError('SciSure configuration changed since review. Prepare the configuration again before applying it.')
        if current['ready']: return current
        self._guard()
        document = configuration_document()
        def find_type():
            actual = _type(self.client)
            if actual is None: return None
            if _type_conflicts(actual, self.group_id):
                raise InputError('A conflicting material type appeared. Setup is paused.')
            return remote_id(actual['sampleTypeID'])
        progress('Preparing the dedicated CATALYST material type…')
        sid = self._step('type/' + TYPE_NAME, find_type, '/api/v1/sampleTypes', document['sample_type'])
        for field in document['fields']:
            definition = field['definition']
            key = definition['key']
            path = f'/api/v1/sampleTypes/{sid}/meta'
            def find_field():
                matches = [f for f in self.client.list(path) if str(f.get('key') or '').casefold() == key.casefold()]
                if len(matches) > 1 or (matches and _field_conflicts(matches[0], definition)):
                    raise InputError('A conflicting field appeared during setup: ' + key + '.')
                if matches and matches[0].get('sampleTypeID') != sid:
                    raise InputError('A schema field belongs to another sample type.')
                return remote_id(matches[0]['sampleTypeMetaID']) if matches else None
            progress('Preparing native field ' + key + '…')
            self._step(f'field/{sid}/{key}', find_field, path, definition)
        verified = plan_configuration(self.client, self.group_id)
        if not verified['ready']:
            raise SciSureError('Schema setup is incomplete. Prepare configuration again to reconcile the remaining fields.', True)
        return verified


def material_payload(trace, plan, *, parent_sample=None):
    """Build a native create payload from a verified binding; this function performs no writes."""
    if not plan.get('ready') or plan.get('conflicts') or plan.get('configuration_sha256') != digest(encode(configuration_document())):
        raise InputError('Use the verified CATALYST material schema before preparing native sample data.')
    material = trace.get('material')
    if not isinstance(material, dict):
        raise InputError('Computational models are not physical inventory samples.')
    sid = material.get('id')
    if not isinstance(sid, str):
        raise InputError('Use the canonical sample ID from the reviewed sample definition.')
    if identity_lab(sid, 'SMP') != lab_id(material.get('creator_lab')):
        raise InputError('The canonical sample ID and creating lab must agree.')
    description = material.get('description')
    if not isinstance(description, str) or not description.strip():
        raise InputError('A sample description / composition is required for native registration. '
            'Add it to the sample definition; synthesis history does not supply a sample description.')
    parent = material.get('parent_sample_id')
    if parent:
        if not isinstance(parent, str):
            raise InputError('Use the canonical parent sample ID from the reviewed sample definition.')
        identity_lab(parent, 'SMP')
        if parent == sid:
            raise InputError('A sample cannot be its own parent.')
    if bool(parent) != (parent_sample is not None):
        raise InputError('Resolve the declared canonical parent to its verified native sample ID first.')
    compatible_types = {plan['sample_type_id']} | {
        item['sample_type_id'] for item in plan.get('legacy_sample_types', [])
        if item.get('compatible_identity') and not item.get('archived')}
    if parent_sample is not None and (parent_sample.get('altID') != parent or parent_sample.get('archived') is not False
            or parent_sample.get('sampleTypeID') not in compatible_types):
        raise InputError('The native parent is archived or does not match the declared canonical material/type.')
    metas = []
    for field in material_fields():
        value = trace
        if field['source'] is None:
            value = field['constant']
        else:
            for key in field['source'].split('.'):
                value = value.get(key) if isinstance(value, dict) else None
        definition = field['definition']
        if value is None or (isinstance(value, str) and not value.strip()):
            if definition['required']: raise InputError('Required material field is missing: ' + definition['key'])
            continue
        if not isinstance(value, str):
            raise InputError('Material field must be text: ' + definition['key'])
        if field['encoding'] == 'lab_display_name': value = lab_name(value)
        if definition.get('optionValues') and value not in definition['optionValues']:
            raise InputError('Unsupported controlled material value: ' + definition['key'])
        if definition['key'] not in plan.get('field_bindings', {}):
            raise InputError('Verify the native metadata binding before registration: ' + definition['key'])
        metas.append(dict(key=definition['key'], sampleDataType=definition['sampleDataType'],
            sampleTypeMetaID=remote_id(plan['field_bindings'][definition['key']]), value=str(value)))
    aliases = [alias for alias in trace.get('aliases', []) if isinstance(alias, dict)
        and alias.get('canonical_id') == sid and isinstance(alias.get('local_label'), str)
        and alias['local_label'].strip()]
    aliases.sort(key=lambda alias: alias.get('lab') != material['creator_lab'])
    name = aliases[0]['local_label'].strip() if aliases else sid
    result = dict(sampleTypeID=remote_id(plan['sample_type_id']), name=name, altID=sid, sampleMetas=metas,
        description=description)
    if parent_sample is not None: result['parentSampleID'] = remote_id(parent_sample.get('sampleID'))
    return result
