"""Approved native inventory actions through the real file publisher and reader."""
from copy import deepcopy
import io
import json
import unittest
import zipfile

from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.configuration import SchemaInstaller, plan_configuration
from catalyst_desktop.contracts import approved_payload
from catalyst_desktop.exports import review_archive
from catalyst_desktop.inventory import plan_inventory, read_native_samples, native_sample_context
from catalyst_desktop.library import search_samples, search_procedures, sample_context, procedure_context
from catalyst_desktop.model import Source, Revision, InputError, build_preview
from catalyst_desktop.publication import Publisher, read_review, RECEIPT
from catalyst_desktop.scisure import SciSureClient, SciSureError
from catalyst_desktop.batch import BatchQueue
from test_inventory import InventoryAPI
from test_adaptive_workflow import context, preview
from desktop_fixtures import uid


class InventoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.api = InventoryAPI()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destination = self.client.destination(42, 7)
        self.ready = SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))
        self.publisher = Publisher(self.client, self.destination)

    def revision(self, kind, number, catalog=None, sources=(), **changes):
        fields = context(kind, number, **changes)
        modality = 'XRD' if kind == 'measurement' else kind
        value = build_preview(list(sources), 'UR', modality, fields, raw_only=True)
        if fields.get('inventoryMode') == 'native':
            value['native_inventory_plan'] = plan_inventory(self.client, 7, value['traceability'], fields,
                catalog or dict(entries=[]), self.destination)
        revision = Revision.create(value, 'Synthetic native ' + kind)
        return revision, revision.approve('Synthetic reviewer', 'Reviewed inventory creation, reuse and experiment relationship.', True)

    def test_native_sample_publish_download_and_retry(self):
        revision, approval = self.revision('sample', 1, inventoryMode='native')
        receipt = self.publisher.publish(revision, approval, [])
        loaded = read_review(self.client, self.destination, receipt['section_id'], True)
        self.assertEqual(loaded['receipt']['inventory']['sample_id'], self.api.samples[0]['sampleID'])
        self.assertEqual(self.api.sample_writes, 1)
        self.publisher.publish(revision, approval, [])
        self.assertEqual(self.api.sample_writes, 1)
        with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
            archived = json.loads(archive.read('CATALYST-complete.json'))
            self.assertEqual(archived['inventory'], loaded['receipt']['inventory'])
        corrupted = deepcopy(loaded)
        corrupted['receipt']['inventory']['sample_type_id'] += 1
        with self.assertRaises(InputError): review_archive(corrupted)

    def test_batch_plans_deferred_native_sample_once_and_links_used(self):
        queue = BatchQueue()
        sample, approval = self.revision('sample', 1, inventoryMode='native')
        first = queue.add(sample, [], approval)
        procedure = Revision.create(preview('procedure', 2), 'Synthetic method')
        queue.add(procedure, [], procedure.approve('Reviewer', 'Reviewed method.', True))
        catalog = dict(entries=queue.catalog_entries())
        fields = sample_context(search_samples(catalog)[0]) | procedure_context(search_procedures(catalog)[0])
        source = Source.from_bytes('synthetic.xrd', b'Synthetic native data', parse=False)
        measurement, approval = self.revision('measurement', 3, catalog, [source], inventoryMode='native', **fields)
        self.assertEqual(measurement.value()['preview']['native_inventory_plan']['sample']['action'], 'create')
        queue.add(measurement, [source], approval)
        queue.publish(self.publisher, dict(entries=[], pending=[]))
        self.assertTrue(all(item.status == 'complete' for item in queue.items))
        self.assertEqual(self.api.sample_writes, 1)
        self.assertEqual(self.api.link_writes, 1)
        receipt = queue.items[-1].receipt['inventory']
        self.assertEqual(receipt['link']['section_type'], 'SAMPLESIN')
        self.assertEqual(receipt['sample_id'], queue.get(first.id).receipt['inventory']['sample_id'])

    def test_missing_or_unselected_inventory_plan_cannot_be_approved_for_transfer(self):
        for native in (True, False):
            revision, _ = self.revision('sample', 1, inventoryMode='native')
            value = revision.value()['preview']
            if native: value.pop('native_inventory_plan')
            else: value['context']['inventoryMode'] = 'records'
            revised = Revision.create(value, 'Invalid inventory intent')
            approval = revised.approve('Reviewer', 'Invalid synthetic plan.', True)
            before = self.api.posts
            with self.assertRaises(InputError): self.publisher.publish(revised, approval, [])
            self.assertEqual(self.api.posts, before)
            self.assertEqual(self.api.sample_writes, 0)

    def test_existing_native_sample_reuses_id_and_stale_plan_blocks_all_writes(self):
        revision, approval = self.revision('sample', 1, inventoryMode='native')
        self.publisher.publish(revision, approval, [])
        row = read_native_samples(self.client, 7)[0]
        fields = native_sample_context(row, 'UR')
        sample, approval = self.revision('sample', 4, **fields)
        before = self.api.posts
        self.api.samples[0]['description'] = 'Changed after review'
        with self.assertRaises((InputError, SciSureError)):
            self.publisher.publish(sample, approval, [])
        self.assertEqual(self.api.posts, before)
        self.assertEqual(self.api.sample_writes, 1)

    def test_transport_only_allows_narrow_sample_membership_put(self):
        for path, body in [('/api/v1/samples/1', [1]), ('/api/v1/experiments/sections/1/samples', []),
                ('/api/v1/experiments/sections/1/samples', [True]), ('/api/v1/experiments/sections/1/samples', [1, 1]),
                ('/api/v1/experiments/sections/1/samples?other=1', [1])]:
            with self.subTest(path=path, body=body), self.assertRaises(SciSureError):
                self.client.request(path, 'PUT', body)


if __name__ == '__main__': unittest.main()
