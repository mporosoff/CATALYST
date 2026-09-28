"""Synthetic end-to-end sample reuse and immutable per-record run publication."""
from copy import deepcopy
import json
import re
from types import SimpleNamespace
import unittest
from urllib.parse import urlsplit

from catalyst_desktop.batch import BatchQueue
from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.configuration import SchemaInstaller, plan_configuration
from catalyst_desktop.contracts import approved_payload
from catalyst_desktop.inventory import plan_inventory
from catalyst_desktop.library import search_samples, search_procedures, sample_context, procedure_context
from catalyst_desktop.model import InputError, Source, Revision, build_preview, encode
from catalyst_desktop.publication import Publisher, read_review, MANIFEST, RECEIPT
from catalyst_desktop.runs_gui import RunUI
from catalyst_desktop.scisure import SciSureClient, SciSureError
from test_adaptive_workflow import context
from test_inventory import InventoryAPI


class MultiRunAPI(InventoryAPI):
    """Reuse the production-path transfer fake, isolating sections by experiment."""
    def __init__(self):
        super().__init__()
        self.runs = {number: dict(self.experiment, experimentID=number, name=f'Synthetic run {number}')
            for number in (42, 43, 44)}
        self.section_events = []

    def __call__(self, url, method, headers, body):
        path = urlsplit(url).path
        if path == '/api/v1/experiments':
            return self.response(dict(data=list(self.runs.values())))
        match = re.fullmatch(r'/api/v1/experiments/(\d+)(/sections)?', path)
        if match:
            eid = int(match[1])
            if not match[2]: return self.response(deepcopy(self.runs[eid]))
            if method == 'GET':
                return self.response(dict(data=[s for s in self.sections if s['experimentID'] == eid]))
            # The base synthetic service owns unique section IDs and file storage.
            response = super().__call__(url.replace(f'/experiments/{eid}/sections', '/experiments/42/sections'), method, headers, body)
            self.sections[-1]['experimentID'] = eid
            self.section_events.append((eid, self.sections[-1]['sectionHeader']))
            return response
        return super().__call__(url, method, headers, body)


class SampleFirstPublicationTests(unittest.TestCase):
    def setUp(self):
        self.api = MultiRunAPI()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destinations = {eid: self.client.destination(eid, 7) for eid in self.api.runs}
        self.app = SimpleNamespace(client=self.client, group_id=7, destination=self.destinations[44], transfer_operations={})
        self.queue = BatchQueue()

    def publish_for(self, revision):
        return RunUI.publisher_for_revision(self.app, revision)

    def revision(self, kind, number, eid, sources=(), catalog=None, **changes):
        fields = context(kind, number, **changes)
        value = build_preview(list(sources), 'UR', 'XRD' if kind == 'measurement' else kind, fields, raw_only=True)
        value['publication_destination'] = deepcopy(self.destinations[eid])
        if fields.get('inventoryMode') == 'native':
            value['native_inventory_plan'] = plan_inventory(self.client, 7, value['traceability'], fields,
                catalog or dict(entries=[]), self.destinations[eid])
        revision = Revision.create(value, f'Synthetic {kind} {number}')
        approval = revision.approve('Synthetic reviewer', 'Reviewed sample, method, originals and selected run.', True)
        return revision, approval

    def stage(self, kind, number, eid, sources=(), catalog=None, **changes):
        revision, approval = self.revision(kind, number, eid, sources, catalog, **changes)
        return self.queue.add(revision, list(sources), approval)

    def staged_definitions(self, native=False, procedure=True):
        if native: SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))
        sample = self.stage('sample', 1, 42, inventoryMode='native' if native else 'records')
        method = self.stage('procedure', 2, 42) if procedure else None
        library = dict(entries=self.queue.catalog_entries())
        refs = sample_context(search_samples(library)[0])
        if procedure: refs.update(procedure_context(search_procedures(library)[0]))
        return sample, method, library, refs

    def send(self):
        return self.queue.publish(Publisher(self.client, self.app.destination), dict(entries=[], pending=[]),
            publisher_factory=self.publish_for)

    def test_native_sample_and_method_defined_first_then_two_measurement_runs(self):
        sample, method, library, refs = self.staged_definitions(native=True)
        measurements = []
        for number, eid in ((3, 43), (4, 44)):
            source = Source.from_bytes(f'synthetic-{number}.xrd', f'SYNTHETIC original {number}'.encode(), parse=False)
            measurements.append(self.stage('measurement', number, eid, [source], library, inventoryMode='native', **refs))
        self.assertEqual(self.api.posts, 0, 'Reviewing and staging must not write research records.')
        self.app.destination = self.destinations[44]
        completed = self.send()
        self.assertEqual(len(completed), 4)
        self.assertEqual([item.receipt['destination']['experiment_id'] for item in completed], [42, 42, 43, 44])
        self.assertEqual(self.api.sample_writes, 1)
        self.assertEqual(self.api.link_writes, 2)
        file_runs = [eid for eid, header in self.api.section_events if header.startswith('CATALYST desktop | ')]
        self.assertEqual(file_runs, [42, 42, 43, 44])
        sample_id = completed[0].receipt['inventory']['sample_id']
        for queued in measurements:
            item = self.queue.get(queued.id)
            receipt = item.receipt
            self.assertEqual(receipt['inventory']['sample_id'], sample_id)
            self.assertEqual(receipt['inventory']['link']['section_type'], 'SAMPLESIN')
            loaded = read_review(self.client, receipt['destination'], receipt['section_id'], True)
            self.assertEqual(loaded['revision'].value()['preview']['publication_destination'], receipt['destination'])
            self.assertEqual(loaded['sources'][0].content, item.sources[0].content)
        catalog = load_catalog(self.client, 7)
        self.assertEqual(len(catalog['entries']), 4)
        self.assertEqual(len(search_samples(catalog)), 1)
        self.assertEqual(len(search_procedures(catalog)), 1)

    def test_unapproved_definition_blocks_entire_multi_run_batch_before_write(self):
        sample, method, library, refs = self.staged_definitions()
        source = Source.from_bytes('synthetic.xrd', b'SYNTHETIC XRD', parse=False)
        self.stage('measurement', 3, 43, [source], library, **refs)
        self.queue.revoke(sample.id)
        with self.assertRaises(InputError): self.send()
        self.assertEqual(self.api.posts, 0)

    def test_all_reviewed_runs_are_preflighted_before_first_record_is_sent(self):
        sample, method, library, refs = self.staged_definitions()
        self.stage('measurement', 3, 43, [Source.from_bytes('synthetic.xrd', b'SYNTHETIC XRD', parse=False)], library, **refs)
        self.api.runs[43]['signatureStatus'] = 'Signed'
        with self.assertRaisesRegex(SciSureError, 'signed or locked'): self.send()
        self.assertEqual(self.api.posts, 0)
        self.assertTrue(all(item.status == 'approved' for item in self.queue.items))

    def test_direct_publisher_cannot_redirect_an_approved_record_to_current_run(self):
        revision, approval = self.revision('sample', 1, 42)
        with self.assertRaisesRegex(InputError, 'another run'):
            Publisher(self.client, self.destinations[44]).publish(revision, approval, [])
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.publish_for(revision).destination['experiment_id'], 42)

    def test_download_rejects_packet_redirect_away_from_immutable_reviewed_run(self):
        revision, approval = self.revision('sample', 1, 42)
        receipt = self.publish_for(revision).publish(revision, approval, [])
        section = next(s for s in self.api.sections if s['expJournalID'] == receipt['section_id'])
        section['experimentID'] = 43
        for metadata in self.api.files[receipt['section_id']]:
            if metadata['realName'] in (MANIFEST, RECEIPT):
                document = json.loads(self.api.content[metadata['experimentFileID']])
                document['destination'] = deepcopy(self.destinations[43])
                content = encode(document)
                self.api.content[metadata['experimentFileID']] = content
                metadata['fileSize'] = len(content)
        with self.assertRaises(SciSureError):
            read_review(self.client, self.destinations[43], receipt['section_id'])

    def test_disagreeing_native_plan_and_reviewed_destination_fail_before_any_write(self):
        SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))
        revision, approval = self.revision('sample', 1, 42, inventoryMode='native')
        value = revision.value()['preview']
        value['publication_destination'] = deepcopy(self.destinations[43])
        conflicting = Revision.create(value, 'Conflicting synthetic destinations')
        approval = conflicting.approve('Reviewer', 'Synthetic mismatch.', True)
        with self.assertRaises(InputError): approved_payload(conflicting, approval)
        with self.assertRaises(InputError): self.publish_for(conflicting)
        with self.assertRaises(InputError): Publisher(self.client, self.destinations[43]).publish(conflicting, approval, [])
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.api.sample_writes, 0)

    def test_historical_measurement_without_method_round_trips_with_sample_reference(self):
        sample, _, library, refs = self.staged_definitions(procedure=False)
        source = Source.from_bytes('historical-xrd.dat', b'SYNTHETIC historical measurement', parse=False)
        measured = self.stage('measurement', 3, 43, [source], library,
            methodStatus='not-recorded', methodId='', methodVersion='', **refs)
        value = approved_payload(measured.revision, measured.approval)
        dataset = value['preview']['traceability']['dataset']
        self.assertEqual(dataset['method_status'], 'not-recorded')
        self.assertIsNone(dataset.get('method'))
        self.send()
        item = self.queue.get(measured.id)
        loaded = read_review(self.client, self.destinations[43], item.receipt['section_id'], True)
        self.assertEqual(loaded['sources'][0].content, source.content)
        catalog = load_catalog(self.client, 7)
        self.assertEqual(len(catalog['entries']), 2)
        self.assertFalse(search_procedures(catalog))
        measurement_entry = next(e for e in catalog['entries'] if e['revision_id'] == measured.revision.value()['id'])
        self.assertEqual(measurement_entry['trace']['sample_ref']['source_revision_id'], sample.revision.value()['id'])

    def test_not_recorded_method_does_not_allow_missing_sample_or_conflicting_method(self):
        sample, _, library, refs = self.staged_definitions(procedure=False)
        source = Source.from_bytes('historical-xrd.dat', b'SYNTHETIC historical measurement', parse=False)
        for changes in (dict(methodStatus='not-recorded', methodId='still-selected', methodVersion='1'),
                dict(methodStatus='not-recorded', methodId='', methodVersion='', specimenId='')):
            with self.subTest(changes=changes), self.assertRaises(InputError):
                revision, approval = self.revision('measurement', 3, 43, [source], library, **(refs | changes))
                approved_payload(revision, approval)
        self.assertEqual(self.api.posts, 0)


if __name__ == '__main__': unittest.main()
