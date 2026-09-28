"""Synthetic native inventory search, reviewed creation, and guarded sample links."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

from catalyst_desktop.configuration import SchemaInstaller, plan_configuration
from catalyst_desktop.inventory import (NativeInventory, plan_inventory, validate_inventory_plan,
    read_native_samples, native_sample_context, sample_snapshot, inventory_summary)
from catalyst_desktop.inventory import validate_inventory_receipt
from catalyst_desktop.model import InputError
from catalyst_desktop.scisure import SciSureError
from catalyst_desktop.traceability import build_traceability
from test_adaptive_workflow import context
from test_configuration import SchemaAPI
from test_api_contract import client_for
from desktop_fixtures import uid


class InventoryAPI(SchemaAPI):
    def __init__(self):
        super().__init__()
        self.samples = []
        self.sample_writes = 0
        self.link_writes = 0
        self.sample_links = {}
        self.inventory_outcome = None
        self.inventory_log = []

    def __call__(self, url, method, headers, body):
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path == '/api/v1/samples' or path.startswith('/api/v1/samples/'):
            if path == '/api/v1/samples':
                if method == 'GET':
                    archived = query.get('archived') == ['true']
                    search = query.get('search', [''])[0].casefold()
                    rows = [row for row in self.samples if row['archived'] is archived
                        and (archived or not search or search in json.dumps(row).casefold())]
                    return self.response(dict(data=deepcopy(rows)))
                self.sample_writes += 1
                if self.inventory_outcome == 'sample_before':
                    self.inventory_outcome = None
                    raise OSError('Synthetic unknown sample write')
                if self.inventory_outcome == 'sample_rejected':
                    self.inventory_outcome = None
                    return 403, b''
                row = json.loads(body)
                self.inventory_log.append((method, path, deepcopy(row)))
                self.next_id += 1
                row.update(sampleID=self.next_id, archived=False, created='2026-09-23T12:00:00', creatorID=5)
                row['meta'] = row.pop('sampleMetas')
                self.samples.append(row)
                if self.inventory_outcome == 'sample_after':
                    self.inventory_outcome = None
                    raise OSError('Synthetic response loss after sample creation')
                if self.inventory_outcome == 'sample_wrong_readback':
                    self.inventory_outcome = None
                    row['meta'][0]['value'] = 'wrong'
                return self.response(row['sampleID'])
            sid = int(path.split('/')[4])
            row = next(row for row in self.samples if row['sampleID'] == sid)
            return self.response(dict(data=deepcopy(row['meta']))) if path.endswith('/meta') else self.response(deepcopy(row))
        if path.startswith('/api/v1/experiments/sections/') and path.endswith('/samples'):
            section_id = int(path.split('/')[5])
            if method == 'GET':
                return self.response(dict(data=[deepcopy(row) for row in self.samples
                    if row['sampleID'] in self.sample_links.get(section_id, [])]))
            if method != 'PUT': raise AssertionError('Expected documented PUT sample attachment')
            self.link_writes += 1
            if self.inventory_outcome == 'link_rate_limit':
                self.inventory_outcome = None
                return 429, b'', {'Retry-After': '60'}
            if self.inventory_outcome == 'link_before':
                self.inventory_outcome = None
                raise OSError('Synthetic unknown link write')
            self.sample_links.setdefault(section_id, []).extend(json.loads(body))
            self.inventory_log.append((method, path, json.loads(body)))
            if self.inventory_outcome == 'link_after':
                self.inventory_outcome = None
                raise OSError('Synthetic lost link response')
            return 204, b''
        if path == '/api/v1/experiments/42/sections' and method == 'POST':
            result = super().__call__(url, method, headers, body)
            self.sections[-1]['experimentID'] = 42
            return result
        return super().__call__(url, method, headers, body)


def sample_record(number=1, **kwargs):
    fields = context('sample', number, inventoryMode='native', specimenId=uid('UR', 'SMP', number), **kwargs)
    trace, errors = build_traceability('UR', 'sample', fields)
    assert not [error for error in errors if error['severity'] == 'error'], errors
    return trace, fields


def measurement_record(sample, kind='measurement', number=3):
    fields = context(kind, number, inventoryMode='native', specimenId=sample[0]['material']['id'],
        sampleSourceRevisionId='review-source', sampleSourceRevisionSha256='a' * 64)
    trace, errors = build_traceability('UR', 'XRD' if kind == 'measurement' else 'synthesis', fields)
    assert not [error for error in errors if error['severity'] == 'error'], errors
    catalog = dict(entries=[dict(trace=sample[0], context=sample[1], revision_id='review-source', revision_sha256='a' * 64)])
    return trace, fields, catalog


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.api = InventoryAPI()
        self.client = client_for(self.api)
        self.destination = self.client.destination(42)
        self.native = NativeInventory(self.client, self.destination)
        self.ready = SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))

    def plan(self, record=None, catalog=None):
        trace, fields = record or sample_record()
        return self.native.prepare(trace, fields, catalog or {}), trace, fields, catalog or {}

    def apply(self, record=None, catalog=None):
        return self.native.apply(*self.plan(record, catalog))

    def test_plan_is_read_only_and_binds_minimal_metadata(self):
        plan, trace, fields, _ = self.plan()
        self.assertEqual(self.api.sample_writes, 0)
        self.assertEqual(plan['sample']['action'], 'create')
        body = plan['sample']['body']
        self.assertEqual(len(body['sampleMetas']), 4)
        self.assertNotIn('quantitySettings', body)
        self.assertEqual(body['name'], 'SYNTHETIC sample')
        self.assertIn('create sample if absent', inventory_summary(plan))
        validate_inventory_plan(plan, trace, fields)

    def test_creation_and_repeated_apply_never_duplicate(self):
        request = self.plan()
        first = self.native.apply(*request)
        second = self.native.apply(*request)
        self.assertEqual(first, second)
        self.assertEqual(self.api.sample_writes, 1)
        self.assertEqual(first['sample_id'], self.api.samples[0]['sampleID'])
        self.assertIsNone(first['link'])

    def test_check_is_read_only_and_detects_changed_native_material(self):
        request = self.plan()
        self.native.check(*request)
        self.assertEqual(self.api.sample_writes, 0)
        self.native.apply(*request)
        self.api.samples[0]['description'] = 'Different material'
        with self.assertRaisesRegex(InputError, 'properties differ'): self.plan()
        self.assertEqual(self.api.sample_writes, 1)

    def test_reused_native_metadata_must_match_the_registered_definition(self):
        self.apply()
        self.api.samples[0]['meta'][2]['value'] = 'Different composition'
        with self.assertRaisesRegex(InputError, 'properties differ'): self.plan()
        self.assertEqual(self.api.sample_writes, 1)

    def test_receipt_rejects_changed_destination_sample_and_relationship(self):
        request = self.plan()
        receipt = self.native.apply(*request)
        validate_inventory_receipt(receipt, request[0])
        for change in (dict(tenant='https://different.example.com'), dict(experiment_id=43),
                dict(sample_type_id=999), dict(canonical_sample_id=uid('UR', 'SMP', 22)),
                dict(link=dict(section_id=2, section_type='SAMPLESOUT', sample_id=receipt['sample_id'])),
                dict(sample_snapshot_sha256='wrong')):
            with self.assertRaises(InputError): validate_inventory_receipt(dict(receipt, **change), request[0])
        reuse = self.plan()
        reused_receipt = self.native.apply(*reuse)
        with self.assertRaises(InputError):
            validate_inventory_receipt(dict(reused_receipt, sample_id=999), reuse[0])
        with self.assertRaises(InputError):
            validate_inventory_receipt(dict(reused_receipt, sample_snapshot_sha256='f' * 64), reuse[0])

    def test_plan_summary_exposes_existing_native_name_and_description(self):
        self.apply()
        plan, *_ = self.plan()
        summary = inventory_summary(plan)
        self.assertIn('SYNTHETIC sample', summary)
        self.assertIn('Synthetic supported material', summary)

    def test_pinned_native_sample_cannot_migrate_to_another_tenant(self):
        self.apply()
        selected = native_sample_context(read_native_samples(self.client, 7)[0], 'UR')
        selected['datasetId'] = uid('UR', 'DS', 3)
        selected['nativeSampleTenant'] = 'https://other.example.com'
        trace, _ = build_traceability('UR', 'sample', selected)
        with self.assertRaisesRegex(InputError, 'another server'): self.native.prepare(trace, selected, {})
        self.assertEqual(self.api.sample_writes, 1)

    def test_native_creation_blocks_account_group_mismatch_before_any_write(self):
        request = self.plan()
        before = (self.api.posts, self.api.sample_writes, self.api.native_posts)
        self.api.account_group = 70
        with self.assertRaisesRegex(SciSureError, 'token account and active group'): self.native.check(*request)
        with self.assertRaisesRegex(SciSureError, 'token account and active group'): self.native.apply(*request)
        self.assertEqual((self.api.posts, self.api.sample_writes, self.api.native_posts), before)

    def test_native_picker_carries_known_bound_state_and_parent(self):
        parent = self.apply(sample_record())
        self.apply(sample_record(2, parentSampleId=uid('UR', 'SMP'), materialState='Synthetic reduced state'))
        row = next(row for row in read_native_samples(self.client, 7) if row['sample']['sampleID'] != parent['sample_id'])
        fields = native_sample_context(row, 'UR')
        self.assertEqual(fields['parentSampleId'], uid('UR', 'SMP'))
        self.assertEqual(fields['materialState'], 'Synthetic reduced state')
        row['fields'] = []
        unbound = native_sample_context(row, 'UR')
        self.assertNotIn('parentSampleId', unbound)
        self.assertNotIn('materialState', unbound)

    def test_rate_limited_link_is_not_retried_automatically(self):
        trace, fields, catalog = measurement_record(sample_record())
        request = (self.native.prepare(trace, fields, catalog), trace, fields, catalog)
        self.api.inventory_outcome = 'link_rate_limit'
        with self.assertRaises(SciSureError) as caught: self.native.apply(*request)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(self.api.link_writes, 1)
        self.assertEqual(self.api.sample_links, {})

    def test_measurement_creates_once_then_attaches_used_sample(self):
        record = sample_record()
        trace, fields, catalog = measurement_record(record)
        plan = self.native.prepare(trace, fields, catalog)
        result = self.native.apply(plan, trace, fields, catalog)
        self.assertEqual(result['link']['section_type'], 'SAMPLESIN')
        self.assertEqual(self.api.sample_writes, 1)
        self.assertEqual(self.api.link_writes, 1)
        self.assertEqual(self.native.apply(plan, trace, fields, catalog), result)
        self.assertEqual(self.api.link_writes, 1)

    def test_synthesis_uses_generated_section(self):
        trace, fields, catalog = measurement_record(sample_record(), 'synthesis')
        result = self.native.apply(self.native.prepare(trace, fields, catalog), trace, fields, catalog)
        self.assertEqual(result['link']['section_type'], 'SAMPLESOUT')

    def test_batch_measurement_plan_accepts_sample_created_by_earlier_record(self):
        record = sample_record()
        trace, fields, catalog = measurement_record(record)
        plan = self.native.prepare(trace, fields, catalog)
        created = self.apply(record)
        result = self.native.apply(plan, trace, fields, catalog)
        self.assertEqual(result['sample_id'], created['sample_id'])
        self.assertEqual(self.api.sample_writes, 1)

    def test_explicit_external_sample_is_reused_without_configuration(self):
        self.api.types.append(dict(sampleTypeID=600, groupID=7, deleted=False, name='Existing instruments samples'))
        self.api.samples.append(dict(sampleID=700, sampleTypeID=600, name='External original', archived=False,
            altID='EXT-10', description='Synthetic external material', meta=[]))
        rows = read_native_samples(self.client, 7, 'External')
        selected = native_sample_context(rows[0], 'UR')
        self.assertEqual(selected['nativeSampleId'], '700')
        selected['datasetId'] = uid('UR', 'DS')
        trace, errors = build_traceability('UR', 'sample', selected)
        self.assertEqual(errors, [])
        result = self.apply((trace, selected))
        self.assertEqual(result['sample_id'], 700)
        self.assertEqual(self.api.sample_writes, 0)
        self.assertEqual(self.api.samples[0]['altID'], 'EXT-10')

    def test_native_search_excludes_foreign_group_and_archived_samples(self):
        self.api.types.extend([dict(sampleTypeID=600, groupID=70, deleted=False, name='Other group'),
            dict(sampleTypeID=601, groupID=7, deleted=False, name='Selected group')])
        self.api.samples.extend([dict(sampleID=700, sampleTypeID=600, name='Foreign', archived=False),
            dict(sampleID=701, sampleTypeID=601, name='Archived', archived=True)])
        self.assertEqual(read_native_samples(self.client, 7), [])

    def test_sample_pin_changes_only_for_identity_not_inventory_balance(self):
        self.apply()
        row = read_native_samples(self.client, 7)[0]
        snapshot = row['native']['snapshot_sha256']
        self.api.samples[0].update(quantity='12 mg', storageLayerID=20, checkedOut=True)
        self.assertEqual(read_native_samples(self.client, 7)[0]['native']['snapshot_sha256'], snapshot)
        self.api.samples[0]['description'] = 'Changed composition'
        self.assertNotEqual(read_native_samples(self.client, 7)[0]['native']['snapshot_sha256'], snapshot)
        selected = native_sample_context(row, 'UR')
        selected['datasetId'] = uid('UR', 'DS', 9)
        trace, _ = build_traceability('UR', 'sample', selected)
        with self.assertRaisesRegex(InputError, 'changed since'): self.native.prepare(trace, selected, {})

    def test_unknown_sample_write_is_not_repeated_but_lost_response_reconciles(self):
        request = self.plan()
        self.api.inventory_outcome = 'sample_before'
        with self.assertRaises(SciSureError): self.native.apply(*request)
        with self.assertRaisesRegex(SciSureError, 'No duplicate'): self.native.apply(*request)
        self.assertEqual(self.api.sample_writes, 1)
        self.native = NativeInventory(self.client, self.destination)
        self.api.inventory_outcome = 'sample_after'
        with self.assertRaises(SciSureError): self.native.apply(*request)
        result = self.native.apply(*request)
        self.assertEqual(result['sample_id'], self.api.samples[0]['sampleID'])
        self.assertEqual(self.api.sample_writes, 2)

    def test_rejected_sample_write_may_be_retried_after_permission_fixed(self):
        request = self.plan()
        self.api.inventory_outcome = 'sample_rejected'
        with self.assertRaises(SciSureError) as caught: self.native.apply(*request)
        self.assertFalse(caught.exception.uncertain)
        self.native.apply(*request)
        self.assertEqual(self.api.sample_writes, 2)

    def test_link_loss_never_duplicates_or_retries_unknown_missing_link(self):
        trace, fields, catalog = measurement_record(sample_record())
        request = (self.native.prepare(trace, fields, catalog), trace, fields, catalog)
        self.api.inventory_outcome = 'link_before'
        with self.assertRaises(SciSureError): self.native.apply(*request)
        with self.assertRaisesRegex(SciSureError, 'No duplicate'): self.native.apply(*request)
        self.assertEqual(self.api.link_writes, 1)
        self.native = NativeInventory(self.client, self.destination)
        self.api.inventory_outcome = 'link_after'
        with self.assertRaises(SciSureError): self.native.apply(*request)
        self.native.apply(*request)
        self.assertEqual(self.api.link_writes, 2)

    def test_duplicate_and_archived_ids_block_recreation(self):
        result = self.apply()
        self.api.samples[0]['archived'] = True
        with self.assertRaisesRegex(InputError, 'archived native record'): self.plan()
        self.api.samples[0]['archived'] = False
        self.api.samples.append(dict(deepcopy(self.api.samples[0]), sampleID=result['sample_id'] + 1))
        with self.assertRaisesRegex(InputError, 'Multiple native samples'): self.plan()
        self.assertEqual(self.api.sample_writes, 1)

    def test_wrong_readback_never_becomes_verified(self):
        request = self.plan()
        self.api.inventory_outcome = 'sample_wrong_readback'
        with self.assertRaises(InputError): self.native.apply(*request)
        self.assertIn('unknown', self.native.operations.values())
        with self.assertRaises(InputError): self.native.apply(*request)
        self.assertEqual(self.api.sample_writes, 1)

    def test_plan_tampering_or_moved_destination_blocks_before_writes(self):
        original, trace, fields, catalog = self.plan()
        for mutate in (lambda p: p['sample']['body'].update(name='Unreviewed'),
                lambda p: p.update(canonical_sample_id=uid('UR', 'SMP', 20)),
                lambda p: p.update(link=dict(section_type='SAMPLESIN', heading='Unreviewed'))):
            plan = deepcopy(original)
            mutate(plan)
            with self.assertRaises(InputError): self.native.apply(plan, trace, fields, catalog)
        changed = deepcopy(original)
        changed['experiment_id'] = 999
        with self.assertRaisesRegex(InputError, 'another experiment'): self.native.apply(changed, trace, fields, catalog)
        self.assertEqual(self.api.sample_writes, 0)

    def test_changed_source_or_schema_blocks_prepared_plan(self):
        trace, fields, catalog = measurement_record(sample_record())
        plan = self.native.prepare(trace, fields, catalog)
        catalog['entries'][0]['trace']['material']['description'] = 'Changed material'
        with self.assertRaisesRegex(InputError, 'reviewed source changed'): self.native.apply(plan, trace, fields, catalog)
        plan, trace, fields, catalog = self.plan()
        self.api.metas[self.ready['sample_type_id']][0]['required'] = False
        with self.assertRaises(InputError): self.native.apply(plan, trace, fields, catalog)
        self.assertEqual(self.api.sample_writes, 0)

    def test_parent_must_be_registered_before_child_and_is_linked_by_native_id(self):
        parent = sample_record()
        child = sample_record(2, parentSampleId=parent[0]['material']['id'])
        with self.assertRaisesRegex(InputError, 'Register the parent'): self.plan(child)
        parent_receipt = self.apply(parent)
        child_receipt = self.apply(child)
        self.assertNotEqual(child_receipt['sample_id'], parent_receipt['sample_id'])
        self.assertEqual(self.api.samples[1]['parentSampleID'], parent_receipt['sample_id'])

    def test_computation_procedure_and_unselected_native_mode_have_no_inventory_plan(self):
        for kind in ('computation', 'procedure'):
            fields = context(kind, inventoryMode='native')
            trace, _ = build_traceability('UR', 'computational' if kind == 'computation' else 'procedure', fields)
            with self.assertRaises(InputError): self.native.prepare(trace, fields, {})
        trace, fields = sample_record()
        fields.pop('inventoryMode')
        with self.assertRaises(InputError): self.native.prepare(trace, fields, {})
        self.assertEqual(self.api.sample_writes, 0)

    def test_local_openapi_documents_every_native_write_shape(self):
        path = Path(__file__).parents[2] / 'inspection' / 'scisure-openapi.json'
        if not path.is_file():
            self.skipTest('The local SciSure OpenAPI snapshot is not available on this machine.')
        api = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('post', api['paths']['/api/v1/samples'])
        link = api['paths']['/api/v1/experiments/sections/{sectionID}/samples']['put']
        body = api['components']['requestBodies'][link['requestBody']['$ref'].split('/')[-1]]
        schema = body['content']['application/json']['schema']
        self.assertEqual(schema['type'], 'array')
        self.assertEqual(schema['items']['type'], 'integer')
        self.assertIn('204', link['responses'])


if __name__ == '__main__': unittest.main()
