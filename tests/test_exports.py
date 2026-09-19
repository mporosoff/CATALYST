"""Verified local download boundaries using synthetic data only."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from catalyst_desktop.exports import review_archive, save_bytes, suggested_filename
from catalyst_desktop.gui import Application
from catalyst_desktop.model import InputError, Source
from catalyst_desktop.publication import Publisher, read_review
from catalyst_desktop.scisure import SciSureClient
from test_desktop import FakeSciSure, review


class ExportTests(unittest.TestCase):
    def loaded(self, source=None, rules=None):
        api = FakeSciSure()
        client = SciSureClient('synthetic-token', transport=api)
        destination = client.destination(42, 7)
        source, _, revision = review(source=source, rules=rules)
        receipt = Publisher(client, destination).publish(revision, revision.approve('Tester', 'Checked', True), [source])
        return source, read_review(client, destination, receipt['section_id'], True)

    def test_package_contains_exact_original_and_clean_data(self):
        source, loaded = self.loaded()
        with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
            self.assertEqual(archive.read('originals/01-synthetic.csv'), source.content)
            data = json.loads(archive.read('standardized.json'))
            self.assertEqual(data['rows'][0]['mass_g'], '0.072')
            rows = list(csv.DictReader(io.StringIO(archive.read('standardized.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['specimen_id'], '001')
            packet = json.loads(archive.read('CATALYST-review.json'))
            self.assertEqual(packet['revision_sha256'], loaded['revision'].sha256)
            self.assertFalse(any('token' in n.lower() for n in archive.namelist()))

    def test_incomplete_or_wrong_original_cannot_be_exported(self):
        _, loaded = self.loaded()
        with self.assertRaises(InputError): review_archive(dict(loaded, state='incomplete'))
        with self.assertRaises(InputError): review_archive(dict(loaded, sources=[]))
        with self.assertRaises(InputError):
            review_archive(dict(loaded, sources=[Source.from_bytes('synthetic.csv', b'mass,name\n99,001\n')]))

    def test_csv_text_formula_is_inert_and_json_is_exact(self):
        source = Source.from_bytes('text.csv', b'name\n=1+1\n@SUM(A1)\n')
        _, loaded = self.loaded(source, [dict(source='name', target='species', unit='text')])
        with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
            data = json.loads(archive.read('standardized.json'))
            self.assertEqual(data['rows'][0]['species'], '=1+1')
            rows = list(csv.DictReader(io.StringIO(archive.read('standardized.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['species'], "'=1+1")
            self.assertEqual(rows[1]['species'], "'@SUM(A1)")

    def test_failed_save_keeps_existing_file_and_cleans_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'result.bin'
            target.write_bytes(b'original')
            with patch('catalyst_desktop.exports.os.replace', side_effect=PermissionError):
                with self.assertRaises(InputError): save_bytes(target, b'new')
            self.assertEqual(target.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])
            save_bytes(target, b'complete')
            self.assertEqual(target.read_bytes(), b'complete')

    def test_untrusted_name_never_suggests_a_path_or_device(self):
        for name in ('../../good.csv', r'C:\fake\good.csv'):
            self.assertEqual(suggested_filename(name), 'good.csv')
        for name in ('CON', 'AUX.csv', 'x\n.csv', 'bad:name'):
            self.assertEqual(suggested_filename(name), 'SciSure-file')


class AttachmentSnapshotTests(unittest.TestCase):
    def attachment(self):
        api = FakeSciSure()
        client = SciSureClient('synthetic-token', transport=api)
        destination = client.destination(42, 7)
        source, _, revision = review()
        receipt = Publisher(client, destination).publish(revision, revision.approve('Tester', 'Checked', True), [source])
        sid = receipt['section_id']
        metadata = next(f for f in client.list(f'/api/v1/experiments/sections/{sid}/files')
            if f['experimentFileID'] == receipt['files'][0]['file_id'])
        return client, destination, dict(metadata, section_id=sid), source.content

    def test_attachment_download_checks_membership_and_content(self):
        client, destination, file, expected = self.attachment()
        self.assertEqual(Application.attachment_bytes(None, client, destination, file), expected)

    def test_stale_section_cannot_be_downloaded(self):
        client, destination, file, _ = self.attachment()
        with patch.object(client, 'list', return_value=[]):
            with self.assertRaises(InputError): Application.attachment_bytes(None, client, destination, file)

    def test_string_section_and_file_identifiers_remain_downloadable(self):
        client, destination, file, expected = self.attachment()
        original = client.list
        def listing(path):
            records = original(path)
            return [dict(item, **{key: str(item[key]) for key in ('expJournalID', 'experimentFileID')
                if key in item}) for item in records]
        with patch.object(client, 'list', side_effect=listing):
            self.assertEqual(Application.attachment_bytes(None, client, destination, file), expected)

    def test_metadata_changed_during_download_is_rejected(self):
        client, destination, file, _ = self.attachment()
        original = client.list
        reads = 0
        def listing(path):
            nonlocal reads
            result = original(path)
            if path.endswith('/files'):
                reads += 1
                if reads == 2:
                    result = [dict(f, realName='changed') if f['experimentFileID'] == file['experimentFileID'] else f for f in result]
            return result
        with patch.object(client, 'list', side_effect=listing):
            with self.assertRaises(InputError): Application.attachment_bytes(None, client, destination, file)
