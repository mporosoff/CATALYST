"""Synthetic tests of the selected native schema and additive, guarded installation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit, parse_qs

from catalyst_desktop.configuration import (configuration_document, material_fields, plan_configuration,
    configuration_summary, SchemaInstaller, material_payload, TYPE_NAME, LEGACY_TYPE_NAME, LEGACY_MARKER)
from catalyst_desktop.model import InputError
from catalyst_desktop.scisure import SciSureError
from catalyst_desktop.traceability import build_traceability
from desktop_fixtures import physical_context, computational_context, uid
from test_desktop import FakeSciSure
from test_api_contract import client_for
from test_adaptive_workflow import context as workflow_context


class SchemaAPI(FakeSciSure):
    def __init__(self):
        super().__init__()
        self.types = []
        self.metas = {}
        self.native_posts = 0
        self.outcome = None
        self.group_name = 'CATALYST'
        self.account_group = 7

    def __call__(self, url, method, headers, body):
        path = urlsplit(url).path
        if path == '/api/v1/groups/active': return self.response(dict(groupID=7, name=self.group_name))
        if path == '/api/v1/users/getCurrentUserInfo':
            return self.response(dict(userID=5, groupId=self.account_group, isBlocked=False, isGroupAdmin=True))
        if path.startswith('/api/v1/sampleTypes'):
            if method == 'POST':
                self.native_posts += 1
                if self.outcome == 'before':
                    self.outcome = None
                    raise OSError('Synthetic connection loss')
                if self.outcome == 'rejected':
                    self.outcome = None
                    return 403, b''
                record = json.loads(body)
            if path == '/api/v1/sampleTypes':
                if method == 'GET':
                    archived = parse_qs(urlsplit(url).query).get('archived') == ['true']
                    return self.response(dict(data=[t for t in self.types if t['deleted'] is archived]))
                self.next_id += 1
                record.update(sampleTypeID=self.next_id, groupID=7, deleted=False)
                self.types.append(record)
                self.metas[self.next_id] = []
                result = self.next_id
            else:
                segments = path.split('/')
                sid = int(segments[4])
                if len(segments) == 5: return self.response(next(t for t in self.types if t['sampleTypeID'] == sid))
                if method == 'GET': return self.response(dict(data=self.metas[sid]))
                self.next_id += 1
                record.update(sampleTypeMetaID=self.next_id, sampleTypeID=sid)
                self.metas[sid].append(record)
                result = self.next_id
            if self.outcome == 'after':
                self.outcome = None
                raise OSError('Synthetic response lost after creation')
            return self.response(result)
        return super().__call__(url, method, headers, body)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.api = SchemaAPI()
        self.client = client_for(self.api)
        self.installer = SchemaInstaller(self.client, 7)
        self.field_count = len(material_fields())
        self.creation_count = self.field_count + 1

    def install(self):
        return self.installer.apply(plan_configuration(self.client, 7))

    def test_configuration_is_concrete_versioned_and_has_no_tenant_example_ids(self):
        document = configuration_document()
        self.assertEqual(document['sample_type']['name'], TYPE_NAME)
        self.assertEqual(document['version'], 2)
        self.assertEqual({f['definition']['key'] for f in document['fields'] if f['definition']['required']},
            {'catalyst_sample_id', 'sample_created_lab', 'sample_description', 'catalyst_schema_version'})
        self.assertFalse(document['sample_type']['quantityRequired'])
        self.assertNotIn('defaultQuantityAmount', document['sample_type'])
        self.assertNotIn('groupID', document['sample_type'])
        self.assertEqual(len(document['structure']['studies']), 6)
        self.assertTrue(document['structure']['autoCollaborate'])
        self.assertEqual(json.loads((Path(__file__).parents[1] / 'docs/scisure-configuration.json').read_text(encoding='utf-8')), document)

    def test_plan_is_read_only_and_installer_resolves_returned_ids(self):
        plan = plan_configuration(self.client, 7)
        self.assertEqual(self.api.native_posts, 0)
        self.assertEqual(len(plan['actions']), self.creation_count)
        self.assertIn('Add sample_created_lab (COMBO; required)', configuration_summary(plan))
        ready = self.installer.apply(plan)
        self.assertTrue(ready['ready'])
        self.assertEqual(self.api.native_posts, self.creation_count)
        self.assertEqual(ready['sample_type_id'], self.api.types[0]['sampleTypeID'])
        self.assertEqual(set(ready['field_bindings'].values()), {f['sampleTypeMetaID'] for f in self.api.metas[ready['sample_type_id']]})
        self.assertEqual(self.installer.apply(ready), ready)
        self.assertEqual(self.api.native_posts, self.creation_count)

    def test_wrong_group_never_receives_schema(self):
        self.api.group_name = 'Unrelated lab'
        with self.assertRaisesRegex(SciSureError, 'restricted'): plan_configuration(self.client, 7)
        self.assertEqual(self.api.native_posts, 0)

    def test_primary_working_group_disagreement_blocks_creation(self):
        plan = plan_configuration(self.client, 7)
        self.api.account_group = 9
        with self.assertRaisesRegex(SciSureError, 'do not agree'): self.installer.apply(plan)
        self.assertEqual(self.api.native_posts, 0)

    def test_existing_foreign_or_archived_type_is_not_adopted(self):
        self.install()
        for patch in ({'deleted': True}, {'groupID': 8}, {'description': 'Someone else created this type'}, {'quantityRequired': True}):
            with self.subTest(patch=patch):
                before = deepcopy(self.api.types[0])
                self.api.types[0].update(patch)
                plan = plan_configuration(self.client, 7)
                self.assertTrue(plan['conflicts'])
                with self.assertRaises(InputError): self.installer.apply(plan)
                self.api.types[0] = before
        self.assertEqual(self.api.native_posts, self.creation_count)

    def test_duplicate_type_or_field_is_never_silently_merged(self):
        ready = self.install()
        self.api.types.append(dict(self.api.types[0], sampleTypeID=999))
        with self.assertRaisesRegex(InputError, 'Duplicate'): plan_configuration(self.client, 7)
        self.api.types.pop()
        fields = self.api.metas[ready['sample_type_id']]
        fields.append(dict(fields[0], sampleTypeMetaID=999))
        self.assertTrue(plan_configuration(self.client, 7)['conflicts'])

    def test_incompatible_required_fields_options_and_validation_are_conflicts(self):
        ready = self.install()
        fields = self.api.metas[ready['sample_type_id']]
        original = deepcopy(fields)
        for change in ('required', 'options', 'type', 'script', 'foreign', 'extra_required'):
            with self.subTest(change=change):
                if change == 'required': fields[0]['required'] = False
                elif change == 'options': next(f for f in fields if f['key'] == 'sample_created_lab')['optionValues'].append('Unregistered lab')
                elif change == 'type': fields[0]['sampleDataType'] = 'NUMERIC'
                elif change == 'script': fields[0]['validationScript'] = 'neverExecute()'
                elif change == 'foreign': fields[0]['sampleTypeID'] = 999
                else: fields.append(dict(key='institution_field', required=True, sampleDataType='TEXT', sampleTypeID=ready['sample_type_id'], sampleTypeMetaID=999))
                plan = plan_configuration(self.client, 7)
                self.assertTrue(plan['conflicts'])
                with self.assertRaises(InputError): self.installer.apply(plan)
                fields[:] = deepcopy(original)

    def test_changed_reviewed_plan_requires_new_review(self):
        plan = plan_configuration(self.client, 7)
        self.install()
        with self.assertRaisesRegex(InputError, 'changed since review'): self.installer.apply(plan)
        self.assertEqual(self.api.native_posts, self.creation_count)

    def test_partial_created_type_is_discovered_and_only_missing_fields_added(self):
        plan = plan_configuration(self.client, 7)
        self.api.outcome = 'after'
        with self.assertRaises(SciSureError): self.installer.apply(plan)
        self.assertEqual(len(self.api.types), 1)
        refreshed = plan_configuration(self.client, 7)
        self.assertEqual(len(refreshed['actions']), self.field_count)
        self.assertTrue(self.installer.apply(refreshed)['ready'])
        self.assertEqual(len(self.api.types), 1)
        self.assertEqual(self.api.native_posts, self.creation_count)

    def test_missing_unknown_write_stays_guarded_across_installer_recreation(self):
        plan = plan_configuration(self.client, 7)
        self.api.outcome = 'before'
        with self.assertRaises(SciSureError): self.installer.apply(plan)
        installer = SchemaInstaller(self.client, 7, self.installer.operations)
        with self.assertRaisesRegex(SciSureError, 'No duplicate'): installer.apply(plan)
        self.assertEqual(self.api.native_posts, 1)

    def test_known_rejection_can_be_retried_manually(self):
        plan = plan_configuration(self.client, 7)
        self.api.outcome = 'rejected'
        with self.assertRaises(SciSureError): self.installer.apply(plan)
        self.assertTrue(self.installer.apply(plan)['ready'])
        self.assertEqual(self.api.native_posts, self.creation_count + 1)

    def test_material_payload_binds_each_required_field_and_keeps_stock_mass_unknown(self):
        ready = self.install()
        trace, issues = build_traceability('Rochester', 'synthesis', physical_context())
        self.assertFalse(issues)
        trace['material']['description'] = 'Synthetic material composition supplied for registration'
        payload = material_payload(trace, ready)
        self.assertEqual(payload['altID'], trace['material']['id'])
        self.assertEqual(payload['name'], '001')
        self.assertNotIn('quantitySettings', payload)
        self.assertNotIn('parentSampleID', payload)
        metas = {m['key']: m for m in payload['sampleMetas']}
        self.assertEqual(metas['sample_created_lab']['value'], 'Rochester')
        self.assertEqual(metas['sample_description']['value'], trace['material']['description'])
        self.assertEqual(metas['catalyst_schema_version']['value'], '2')
        self.assertFalse(any(key.startswith(('synthesis_', 'shared_procedure_', 'catalyst_batch_')) for key in metas))
        for field in material_fields():
            key = field['definition']['key']
            if field['definition']['required']:
                self.assertEqual(metas[key]['sampleTypeMetaID'], ready['field_bindings'][key])
                self.assertEqual(metas[key]['sampleDataType'], field['definition']['sampleDataType'])

    def test_derivative_requires_the_matching_native_parent(self):
        ready = self.install()
        context = physical_context(materialKind='aliquot', parentSampleId=uid('Rochester', 'SMP', 2))
        trace, issues = build_traceability('Rochester', 'synthesis', context)
        self.assertFalse(issues)
        trace['material']['description'] = 'Synthetic aliquot composition'
        with self.assertRaises(InputError): material_payload(trace, ready)
        parent = dict(sampleID=91, sampleTypeID=ready['sample_type_id'], altID=context['parentSampleId'], archived=False)
        self.assertEqual(material_payload(trace, ready, parent_sample=parent)['parentSampleID'], 91)
        for change in ({'altID': 'wrong'}, {'archived': True}, {'sampleTypeID': 999}):
            with self.assertRaises(InputError): material_payload(trace, ready, parent_sample=dict(parent, **change))

    def test_computational_model_is_not_created_as_inventory(self):
        ready = self.install()
        trace, _ = build_traceability('VT', 'computational', computational_context())
        with self.assertRaisesRegex(InputError, 'not physical'): material_payload(trace, ready)

    def test_standalone_sample_requires_only_registration_facts(self):
        ready = self.install()
        trace, issues = build_traceability('Rochester', 'sample', workflow_context('sample'))
        self.assertFalse(issues)
        self.assertNotIn('batch', trace)
        payload = material_payload(trace, ready)
        self.assertEqual(payload['name'], 'SYNTHETIC sample')
        self.assertEqual(payload['description'], 'Synthetic supported material')
        self.assertEqual({m['key'] for m in payload['sampleMetas']},
            {'catalyst_sample_id', 'sample_created_lab', 'sample_description', 'catalyst_schema_version'})
        self.assertNotIn('quantitySettings', payload)
        self.assertNotIn('parentSampleID', payload)

    def test_optional_sample_state_and_parent_preserve_supplied_values(self):
        ready = self.install()
        trace, issues = build_traceability('Rochester', 'sample', workflow_context('sample',
            materialState='Synthetic dried powder', parentSampleId=uid('SLAC', 'SMP', 2)))
        self.assertFalse(issues)
        parent = dict(sampleID=91, sampleTypeID=ready['sample_type_id'], altID=uid('SLAC', 'SMP', 2), archived=False)
        payload = material_payload(trace, ready, parent_sample=parent)
        metas = {item['key']: item['value'] for item in payload['sampleMetas']}
        self.assertEqual(metas['material_state'], 'Synthetic dried powder')
        self.assertEqual(metas['parent_catalyst_sample_id'], parent['altID'])
        self.assertEqual(payload['parentSampleID'], 91)

    def test_missing_legacy_description_is_never_inferred_from_synthesis(self):
        ready = self.install()
        trace, _ = build_traceability('Rochester', 'synthesis', physical_context())
        for description in (None, '', '   ', {'composition': 'invalid type'}):
            with self.subTest(description=description):
                trace['material']['description'] = description
                with self.assertRaisesRegex(InputError, 'description / composition'):
                    material_payload(trace, ready)

    def test_sample_name_uses_only_matching_alias_and_prefers_creating_lab(self):
        ready = self.install()
        trace, _ = build_traceability('Rochester', 'sample', workflow_context('sample'))
        sid = trace['material']['id']
        trace['aliases'] = [dict(canonical_id=uid('UR', 'SMP', 2), local_label='Unrelated', lab='university-of-rochester'),
            dict(canonical_id=sid, local_label='Receiving lab label', lab='slac'),
            dict(canonical_id=sid, local_label='Creator lab label', lab='university-of-rochester')]
        self.assertEqual(material_payload(trace, ready)['name'], 'Creator lab label')
        trace['aliases'] = []
        self.assertEqual(material_payload(trace, ready)['name'], sid)

    def test_invalid_or_self_referencing_canonical_parent_is_rejected(self):
        ready = self.install()
        trace, _ = build_traceability('Rochester', 'sample', workflow_context('sample'))
        for parent_id in ('local-label', trace['material']['id']):
            with self.subTest(parent=parent_id):
                trace['material']['parent_sample_id'] = parent_id
                parent = dict(sampleID=91, sampleTypeID=ready['sample_type_id'], altID=parent_id, archived=False)
                with self.assertRaises(InputError): material_payload(trace, ready, parent_sample=parent)

    def test_legacy_installation_is_reported_and_preserved_during_additive_setup(self):
        legacy = dict(sampleTypeID=90, groupID=7, deleted=False, name=LEGACY_TYPE_NAME,
            description=LEGACY_MARKER + ' Original synthesis-heavy schema', quantityRequired=False)
        legacy_fields = [dict(sampleTypeMetaID=91, sampleTypeID=90, key='synthesis_record', required=True)]
        self.api.types.append(deepcopy(legacy))
        self.api.metas[90] = deepcopy(legacy_fields)
        plan = plan_configuration(self.client, 7)
        self.assertEqual(self.api.native_posts, 0)
        self.assertEqual(plan['legacy_sample_types'], [dict(sample_type_id=90, name=LEGACY_TYPE_NAME,
            archived=False, compatible_identity=True)])
        self.assertIn('Left unchanged', configuration_summary(plan))
        self.assertIn('no conversion or duplicate sample creation', plan['migration'])
        ready = self.installer.apply(plan)
        self.assertTrue(ready['ready'])
        self.assertEqual(self.api.types[0], legacy)
        self.assertEqual(self.api.metas[90], legacy_fields)
        self.assertEqual(len(self.api.types), 2)
        self.assertEqual(self.api.native_posts, self.creation_count)
        self.assertEqual(plan_configuration(self.client, 7), ready)
        trace, _ = build_traceability('Rochester', 'sample', workflow_context('sample', parentSampleId=uid('UR', 'SMP', 2)))
        parent = dict(sampleID=93, sampleTypeID=90, altID=uid('UR', 'SMP', 2), archived=False)
        self.assertEqual(material_payload(trace, ready, parent_sample=parent)['parentSampleID'], 93)

    def test_unverified_legacy_type_cannot_supply_native_parent(self):
        for patch in ({'deleted': True}, {'groupID': 9}, {'description': 'Unmarked external type'}):
            with self.subTest(patch=patch):
                self.api.types = [dict(sampleTypeID=90, groupID=7, deleted=False, name=LEGACY_TYPE_NAME,
                    description=LEGACY_MARKER) | patch]
                self.api.metas = {90: []}
                self.installer.operations = {}
                ready = self.install()
                self.assertFalse(ready['legacy_sample_types'][0]['compatible_identity'])
                trace, _ = build_traceability('Rochester', 'sample', workflow_context('sample', parentSampleId=uid('UR', 'SMP', 2)))
                parent = dict(sampleID=93, sampleTypeID=90, altID=uid('UR', 'SMP', 2), archived=False)
                with self.assertRaisesRegex(InputError, 'does not match'):
                    material_payload(trace, ready, parent_sample=parent)

    def test_changed_legacy_migration_discovery_requires_new_setup_review(self):
        plan = plan_configuration(self.client, 7)
        self.api.types.append(dict(sampleTypeID=90, groupID=7, deleted=False,
            name=LEGACY_TYPE_NAME, description=LEGACY_MARKER))
        self.api.metas[90] = []
        with self.assertRaisesRegex(InputError, 'changed since review'):
            self.installer.apply(plan)
        self.assertEqual(self.api.native_posts, 0)


if __name__ == '__main__':
    unittest.main()
