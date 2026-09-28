"""Reviewed direct sample documents through documented APIs, with synthetic storage."""
from copy import deepcopy
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from catalyst_desktop.inventory import sample_snapshot
from catalyst_desktop.model import InputError, Source, digest
from catalyst_desktop.sample_documents import (DOCUMENT_FIELD, SampleDocumentTransfer, plan_documents,
    sample_documents, download_document)
from catalyst_desktop.scisure import SciSureError
from test_api_contract import client_for
from test_inventory import InventoryAPI


class DocumentsAPI(InventoryAPI):
    def __init__(self, field=True, existing=True):
        super().__init__()
        self.types = [dict(sampleTypeID=3, name='Synthetic sample type', groupID=7, deleted=False)]
        self.metas = {3: [dict(sampleTypeMetaID=10, sampleTypeID=3, key=DOCUMENT_FIELD,
            sampleDataType='FILE', required=False)] if field else []}
        self.samples = [dict(sampleID=8, sampleTypeID=3, name='Synthetic catalyst', altID='',
            description='Synthetic description', archived=False,
            meta=[dict(sampleTypeMetaID=9, key='composition', sampleDataType='TEXT', value='Synthetic composition')])]
        self.storage = {}
        self.uploads = self.document_links = 0
        self.doc_outcome = None
        self.on_download = None
        self.upload_patch = {}
        self.before_group_write = None
        self.document_requests = []
        self.corrupt_download = False
        if existing:
            self.add_storage(20, 'existing.pdf', b'Existing synthetic document')
            self.samples[0]['meta'].append(dict(sampleTypeMetaID=10 if field else None,
                key=DOCUMENT_FIELD, sampleDataType='FILE', files=[dict(fileID=20, realName='existing.pdf')]))

    def add_storage(self, identifier, name, content):
        self.storage[identifier] = dict(content=content, meta=dict(fileID=identifier, realName=name,
            groupID=7, size=len(content), location='ELABJOURNAL', SHA256Hash=digest(content)))

    def __call__(self, url, method, headers, body):
        path, query = urlsplit(url).path, parse_qs(urlsplit(url).query)
        if path == '/api/v1/files':
            if method == 'GET':
                rows = []
                for file in self.storage.values():
                    meta = deepcopy(file['meta'])
                    if meta['realName'] != query.get('fileName', [''])[0] or str(meta['groupID']) != query.get('groupID', [''])[0]: continue
                    # FileInStorageLarge uses filename; upload FileInStorage uses realName.
                    meta['filename'] = meta.pop('realName')
                    rows.append(meta)
                return self.response(dict(data=rows))
            assert method == 'POST'
            self.uploads += 1
            if self.doc_outcome == 'rejected':
                self.doc_outcome = None
                return 403, b''
            identifier = 500 + self.uploads
            self.add_storage(identifier, query['fileName'][0], body)
            if self.doc_outcome == 'upload_unknown':
                self.doc_outcome = None
                raise OSError('Synthetic response loss after successful upload')
            return self.response(dict(self.storage[identifier]['meta'], **self.upload_patch))
        if path.startswith('/api/v1/files/'):
            assert method == 'GET'
            identifier = int(path.split('/')[-1])
            content = self.storage[identifier]['content']
            if self.on_download:
                action, self.on_download = self.on_download, None
                action()
            if self.corrupt_download: content = b'X' * len(content)
            return 200, content
        if path == '/api/v1/samples/8/meta' and method in ('POST', 'PUT'):
            self.document_requests.append((method, path))
            assert method == 'POST', 'Documents must never replace existing sample fields'
            if self.before_group_write:
                callback, self.before_group_write = self.before_group_write, None
                callback()
            self.document_links += 1
            value = json.loads(body)
            assert set(value) == {'key', 'sampleDataType', 'fileIDs'}
            assert value['sampleDataType'] == 'FILE'
            metas = self.samples[0]['meta']
            if self.doc_outcome == 'link_unknown_before':
                self.doc_outcome = None
                raise OSError('Synthetic connection lost before known link response')
            metas.append(dict(key=value['key'], sampleDataType='FILE',
                files=[dict(fileID=identifier, realName=self.storage[identifier]['meta']['realName']) for identifier in value['fileIDs']]))
            if self.doc_outcome == 'link_unknown':
                self.doc_outcome = None
                raise OSError('Synthetic lost link response')
            if self.doc_outcome == 'wrong_readback': metas[-1]['files'].pop()
            if self.doc_outcome == 'scientific_change': metas[0]['value'] = 'Changed during linking'
            return self.response(123)
        return super().__call__(url, method, headers, body)


class SampleDocumentTests(unittest.TestCase):
    def setUp(self):
        self.api = DocumentsAPI()
        self.client = client_for(self.api)
        self.sources = [Source.from_bytes('new.pdf', b'Synthetic new document', parse=False)]
        self.operations = {}
        self.transfer = SampleDocumentTransfer(self.client, self.operations)

    def plan(self): return plan_documents(self.client, 7, 8, self.sources)

    def linked_ids(self):
        return sorted({file['fileID'] for meta in self.api.samples[0]['meta']
            if meta.get('sampleDataType') == 'FILE' for file in meta.get('files', [])})

    def test_review_is_read_only_and_append_preserves_existing_links_and_scientific_fields(self):
        before = sample_snapshot(self.api.samples[0])
        original = deepcopy(self.api.samples[0]['meta'])
        plan = self.plan()
        self.assertEqual((self.api.uploads, self.api.document_links, self.api.native_posts), (0, 0, 0))
        self.assertRegex(plan['field_key'], r'^CATALYST_documents_[0-9a-f]{32}$')
        receipt = self.transfer.apply(plan, self.sources)
        self.assertEqual(self.linked_ids(), [20, 501])
        self.assertEqual(self.api.samples[0]['meta'][:len(original)], original)
        self.assertEqual(receipt['files'][0]['sha256'], self.sources[0].artifact['sha256'])
        self.assertEqual(sample_snapshot(self.api.samples[0]), before)
        self.assertEqual(self.api.native_posts, 0)
        self.assertEqual(self.api.document_requests, [('POST', '/api/v1/samples/8/meta')])
        new = self.api.samples[0]['meta'][-1]
        self.assertNotIn('sampleTypeMetaID', new)
        self.assertEqual([file['fileID'] for file in new['files']], [501])

    def test_no_attachment_field_or_sample_type_schema_is_needed(self):
        self.api = DocumentsAPI(field=False, existing=False)
        self.client = client_for(self.api)
        self.transfer = SampleDocumentTransfer(self.client, self.operations)
        self.transfer.apply(self.plan(), self.sources)
        self.assertEqual(self.api.native_posts, 0)
        self.assertEqual(self.api.metas[3], [])
        self.assertEqual(self.linked_ids(), [501])

    def test_apply_reuses_review_uuid_without_generating_retry_identity(self):
        plan = self.plan()
        with patch('catalyst_desktop.sample_documents.uuid.uuid4', side_effect=AssertionError('Must reuse reviewed UUID')):
            self.transfer.apply(plan, self.sources)
        self.assertEqual(self.api.samples[0]['meta'][-1]['key'], plan['field_key'])
        with self.assertRaisesRegex(SciSureError, 'already exists'):
            self.transfer.apply(plan, self.sources)
        self.assertEqual((self.api.uploads, self.api.document_links), (1, 1))

    def test_explicit_re_review_retains_same_identity_and_rejects_existing_key(self):
        plan = self.plan()
        self.assertEqual(plan_documents(self.client, 7, 8, self.sources, plan_id=plan['document_group_id']), plan)
        self.api.samples[0]['meta'].append(dict(key=plan['field_key'], sampleDataType='FILE', files=[]))
        with self.assertRaisesRegex(SciSureError, 'already exists'):
            self.transfer.apply(plan, self.sources)
        self.assertEqual(self.api.uploads, 0)

    def test_stale_scientific_sample_blocks_before_upload(self):
        for mutate in (lambda: self.api.samples[0].update(description='Changed sample'),
                lambda: self.api.samples[0]['meta'][0].update(value='Changed composition')):
            self.setUp(); plan = self.plan(); mutate()
            with self.assertRaises(SciSureError): self.transfer.apply(plan, self.sources)
            self.assertEqual(self.api.uploads, 0)
            self.assertEqual(self.api.document_links, 0)

    def test_concurrent_documents_during_upload_are_retained(self):
        plan = self.plan()
        def other_upload():
            self.api.add_storage(600, 'other.pdf', b'Concurrent synthetic document')
            self.api.samples[0]['meta'][-1]['files'].append(dict(fileID=600, realName='other.pdf'))
        self.api.on_download = other_upload
        self.transfer.apply(plan, self.sources)
        self.assertEqual(self.linked_ids(), [20, 501, 600])
        self.assertEqual(self.api.document_links, 1)

    def test_concurrent_addition_after_final_read_is_not_deleted(self):
        plan = self.plan()
        def last_moment_addition():
            self.api.add_storage(600, 'last-moment.pdf', b'Another researcher added this')
            self.api.samples[0]['meta'][-1]['files'].append(dict(fileID=600, realName='last-moment.pdf'))
            self.api.samples[0]['meta'].append(dict(key=DOCUMENT_FIELD + '_' + 'f' * 32,
                sampleDataType='FILE', files=[dict(fileID=600, realName='last-moment.pdf')]))
        self.api.before_group_write = last_moment_addition
        self.transfer.apply(plan, self.sources)
        self.assertEqual(self.linked_ids(), [20, 501, 600])
        legacy = next(meta for meta in self.api.samples[0]['meta'] if meta.get('key') == DOCUMENT_FIELD)
        self.assertEqual([file['fileID'] for file in legacy['files']], [20, 600])
        self.assertEqual(len([meta for meta in self.api.samples[0]['meta'] if meta.get('sampleDataType') == 'FILE']), 3)

    def test_unrelated_type_field_changes_do_not_block_schema_free_upload(self):
        plan = self.plan()
        self.api.on_download = lambda: self.api.metas[3][0].update(fileMask='*.txt', sampleTypeMetaID=999)
        self.transfer.apply(plan, self.sources)
        self.assertEqual(self.linked_ids(), [20, 501])

    def test_unknown_file_upload_is_not_repeated_even_with_fresh_review(self):
        plan = self.plan(); self.api.doc_outcome = 'upload_unknown'
        with self.assertRaises(SciSureError) as result: self.transfer.apply(plan, self.sources)
        self.assertTrue(result.exception.uncertain)
        for candidate in (plan, self.plan()):
            with self.assertRaisesRegex(SciSureError, 'No duplicate'):
                self.transfer.apply(candidate, self.sources)
        self.assertEqual(self.api.uploads, 1)
        self.assertEqual(self.api.document_links, 0)

    def test_rejected_upload_can_be_reviewed_and_retried(self):
        plan = self.plan(); self.api.doc_outcome = 'rejected'
        with self.assertRaises(SciSureError): self.transfer.apply(plan, self.sources)
        self.transfer.apply(plan, self.sources)
        self.assertEqual(self.api.uploads, 2)
        self.assertEqual(self.linked_ids(), [20, 502])

    def test_unknown_group_write_and_bad_readback_do_not_duplicate_post(self):
        for outcome in ('link_unknown', 'link_unknown_before', 'wrong_readback', 'scientific_change'):
            self.setUp(); plan = self.plan(); self.api.doc_outcome = outcome
            with self.assertRaises(SciSureError): self.transfer.apply(plan, self.sources)
            with self.assertRaises(SciSureError): self.transfer.apply(plan, self.sources)
            self.assertEqual(self.api.uploads, 1)
            self.assertEqual(self.api.document_links, 1)

    def test_wrong_upload_metadata_or_bytes_never_links_file(self):
        for change in ({'groupID': 9}, {'realName': 'wrong.pdf'}, {'size': 1}, {'location': 'ONSITE'}, {'fileID': 0}):
            self.setUp(); self.api.upload_patch = change
            with self.assertRaises(SciSureError): self.transfer.apply(self.plan(), self.sources)
            self.assertEqual(self.api.document_links, 0)
        self.setUp(); self.api.corrupt_download = True
        with self.assertRaisesRegex(SciSureError, 'checksum'): self.transfer.apply(self.plan(), self.sources)
        self.assertEqual(self.api.document_links, 0)

    def test_download_uses_exact_link_and_documented_filename_metadata(self):
        row = sample_documents(self.client, 7, 8)['documents'][0]
        self.assertEqual(download_document(self.client, 7, row), self.api.storage[20]['content'])
        wrong = dict(row, file_id=999)
        with self.assertRaisesRegex(SciSureError, 'no longer linked'): download_document(self.client, 7, wrong)

    def test_download_rejects_changed_membership_metadata_and_bad_checksum(self):
        def remove(): self.api.samples[0]['meta'][-1]['files'] = []
        def resize(): self.api.storage[20]['meta']['size'] += 1
        for action in (remove, resize):
            self.setUp(); row = sample_documents(self.client, 7, 8)['documents'][0]
            self.api.on_download = action
            with self.assertRaises(SciSureError): download_document(self.client, 7, row)
        self.setUp(); row = sample_documents(self.client, 7, 8)['documents'][0]
        self.api.storage[20]['meta']['SHA256Hash'] = '0' * 64
        with self.assertRaisesRegex(SciSureError, 'checksum'): download_document(self.client, 7, row)

    def test_only_exact_managed_file_fields_are_excluded_from_scientific_snapshot(self):
        sample = deepcopy(self.api.samples[0]); sample['meta'] = []
        baseline = sample_snapshot(sample)
        for key, datatype, ignored in ((DOCUMENT_FIELD, 'FILE', True),
                (DOCUMENT_FIELD + '_' + 'a' * 32, 'FILE', True),
                (DOCUMENT_FIELD + '_' + 'a' * 31, 'FILE', False),
                (DOCUMENT_FIELD + '_arbitrary', 'FILE', False),
                (DOCUMENT_FIELD + '_' + 'a' * 32, 'TEXT', False),
                ('Scientific calibration files', 'FILE', False)):
            sample['meta'] = [dict(key=key, sampleDataType=datatype, files=[dict(fileID=20)])]
            with self.subTest(key=key, datatype=datatype):
                self.assertEqual(sample_snapshot(sample) == baseline, ignored)

    def test_transport_rejects_file_field_put_even_with_previously_allowed_shape(self):
        value = dict(sampleTypeMetaID=10, key=DOCUMENT_FIELD, sampleDataType='FILE', fileIDs=[20])
        with self.assertRaises(SciSureError): self.client.request('/api/v1/samples/8/meta', 'PUT', value)
        self.assertEqual(self.api.document_links, 0)

    def test_tampered_plan_key_invalid_uuid_or_legacy_format_never_write(self):
        plan = self.plan()
        for change in ({'field_key': DOCUMENT_FIELD}, {'document_group_id': 'bad'}, {'format': 'catalyst-sample-documents/1'}):
            with self.assertRaises((InputError, SciSureError)):
                self.transfer.apply(dict(plan, **change), self.sources)
        self.assertEqual(self.api.uploads, 0)


if __name__ == '__main__': unittest.main()
