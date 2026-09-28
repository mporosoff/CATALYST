"""Full synthetic API round trips for reusable methods and adaptive review downloads."""
from copy import deepcopy
import io
import json
import unittest
import zipfile

from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.exports import review_archive
from catalyst_desktop.library import search_procedures, search_samples, procedure_context, sample_context
from catalyst_desktop.model import Source, Revision, InputError, build_preview, make_profile
from catalyst_desktop.publication import Publisher, read_review, RECEIPT
from catalyst_desktop.scisure import SciSureClient
from desktop_fixtures import uid
from test_adaptive_workflow import context, preview
from test_desktop import FakeSciSure


class WorkflowPublicationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSciSure()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destination = self.client.destination(42, 7)
        self.publisher = Publisher(self.client, self.destination)

    def publish(self, value, sources=()):
        revision = Revision.create(value, 'Synthetic adaptive record')
        approval = revision.approve('Synthetic reviewer', 'Checked exact synthetic facts and selected saved references.', True)
        receipt = self.publisher.publish(revision, approval, list(sources))
        loaded = read_review(self.client, self.destination, receipt['section_id'], with_sources=True)
        self.assertEqual(loaded['revision'].sha256, revision.sha256)
        self.assertEqual(loaded['state'], 'complete')
        return loaded

    def register(self):
        procedure = self.publish(preview('procedure', 1))
        sample = self.publish(preview('sample', 2))
        catalog = load_catalog(self.client, 7)
        fields = context('measurement', 3)
        fields.update(procedure_context(search_procedures(catalog, kind='measurement', modality='XRD')[0]))
        fields.update(sample_context(search_samples(catalog)[0]))
        return procedure, sample, fields

    def test_metadata_definition_downloads_have_full_details_and_verified_receipts(self):
        for kind, number in [('procedure', 1), ('sample', 2)]:
            with self.subTest(kind=kind):
                loaded = self.publish(preview(kind, number))
                self.assertEqual(loaded['sources'], [])
                self.assertEqual(loaded['receipt']['files'], [])
                with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
                    packet = json.loads(archive.read('CATALYST-review.json'))
                    receipt = json.loads(archive.read('CATALYST-complete.json'))
                    details = packet['revision']['preview']
                    self.assertEqual(details['context']['recordType'], kind)
                    self.assertEqual(details['traceability']['record_type'], kind)
                    self.assertEqual(details['data_status'], 'metadata_only')
                    self.assertEqual(receipt['revision_sha256'], packet['revision_sha256'])
                    self.assertFalse(any(name.startswith('originals/') for name in archive.namelist()))
                    self.assertNotIn('standardized.csv', archive.namelist())
                with self.assertRaises(InputError): review_archive(dict(loaded, state='incomplete'))

    def test_mapped_and_original_measurements_reuse_two_saved_definitions(self):
        procedure, sample, fields = self.register()
        native = Source.from_bytes('synthetic-native.xrd', b'SYNTHETIC native diffraction data', parse=False)
        raw = self.publish(build_preview([native], 'UR', 'XRD', fields, raw_only=True), [native])
        table = Source.from_bytes('synthetic-table.csv', b'angle,intensity\n20,10\n25,15\n')
        profile = make_profile('UR', 'XRD', 'csv', 'synthetic-1', 'Synthetic XRD conversion', 1, 'Table', 1,
            [dict(source='angle', target='two_theta_deg', unit='degree (2theta)'),
                dict(source='intensity', target='signal', unit='as recorded')])
        fields.update(datasetId=uid('UR', 'DS', 4), uploadMode='mapped', signalUnit='counts')
        mapped = self.publish(build_preview([table], 'UR', 'XRD', fields, profile), [table])
        catalog = load_catalog(self.client, 7)
        self.assertEqual(len(catalog['entries']), 4)
        self.assertEqual(len(search_procedures(catalog)), 1)
        self.assertEqual(len(search_samples(catalog)), 1)
        self.assertEqual(sum('procedure' in row['trace'] for row in catalog['entries']), 1)
        self.assertEqual(sum('material' in row['trace'] for row in catalog['entries']), 1)
        for loaded in (raw, mapped):
            trace = loaded['revision'].value()['preview']['traceability']
            self.assertEqual(trace['sample_ref']['source_revision_sha256'], sample['revision'].sha256)
            self.assertEqual(trace['dataset']['method']['source_revision_sha256'], procedure['revision'].sha256)
            self.assertNotIn('material', trace)
            self.assertNotIn('batch', trace)
            with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
                packet = json.loads(archive.read('CATALYST-review.json'))
                self.assertEqual(packet['revision']['preview']['traceability'], trace)
        with zipfile.ZipFile(io.BytesIO(review_archive(mapped))) as archive:
            data = json.loads(archive.read('standardized.json'))
            self.assertEqual(data['rows'][0]['canonical_subject_id'], uid('UR', 'SMP'))
            self.assertEqual(data['rows'][0]['two_theta_deg'], '20')
            self.assertEqual(archive.read('originals/01-synthetic-table.csv'), table.content)
            self.assertIn('standardized.csv', archive.namelist())

    def test_missing_changed_or_wrong_definition_source_pins_block_before_writes(self):
        procedure, sample, fields = self.register()
        native = Source.from_bytes('synthetic.xrd', b'SYNTHETIC data', parse=False)
        cases = [
            dict(procedureSourceRevisionId=''), dict(procedureSourceRevisionSha256='0' * 64),
            dict(sampleSourceRevisionId=''), dict(sampleSourceRevisionSha256='0' * 64),
            dict(procedureSourceRevisionId=sample['revision'].value()['id'], procedureSourceRevisionSha256=sample['revision'].sha256),
            dict(sampleSourceRevisionId=procedure['revision'].value()['id'], sampleSourceRevisionSha256=procedure['revision'].sha256),
        ]
        posts = self.api.posts
        for extra in cases:
            with self.subTest(fields=tuple(extra)), self.assertRaises(InputError):
                self.publish(build_preview([native], 'UR', 'XRD', dict(fields, **extra), raw_only=True), [native])
            self.assertEqual(self.api.posts, posts)

    def test_referenced_completion_cannot_be_removed_and_replaced_by_a_pending_definition(self):
        procedure, _, fields = self.register()
        receipt_id = next(
            file['experimentFileID'] for file in self.api.files[procedure['receipt']['section_id']]
            if file['realName'] == RECEIPT)
        sid = procedure['receipt']['section_id']
        self.api.files[sid] = [file for file in self.api.files[sid] if file['experimentFileID'] != receipt_id]
        catalog = load_catalog(self.client, 7)
        self.assertEqual(len(search_procedures(catalog)), 0)
        native = Source.from_bytes('synthetic.xrd', b'SYNTHETIC data', parse=False)
        posts = self.api.posts
        with self.assertRaises(InputError):
            self.publish(build_preview([native], 'UR', 'XRD', fields, raw_only=True), [native])
        self.assertEqual(self.api.posts, posts)

    def test_changed_saved_definition_and_mismatched_download_sources_are_rejected(self):
        _, _, fields = self.register()
        posts = self.api.posts
        with self.assertRaises(InputError):
            self.publish(preview('procedure', 9, procedureText='Changed instructions without incrementing version'))
        self.assertEqual(self.api.posts, posts)
        native = Source.from_bytes('synthetic.xrd', b'SYNTHETIC data', parse=False)
        loaded = self.publish(build_preview([native], 'UR', 'XRD', fields, raw_only=True), [native])
        with self.assertRaises(InputError): review_archive(dict(loaded, sources=[]))
        changed = deepcopy(loaded)
        changed['receipt']['files'][0]['sha256'] = '0' * 64
        with self.assertRaises(InputError): review_archive(changed)


if __name__ == '__main__':
    unittest.main()
