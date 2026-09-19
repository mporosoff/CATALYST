"""Independent archive-export integrity and spreadsheet safety regressions."""
from copy import deepcopy
import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from catalyst_desktop.exports import review_archive, save_bytes, standardized_csv
from catalyst_desktop.model import InputError, Revision, Source
import test_exports
import test_data_audit


class ExportAuditTests(unittest.TestCase):
    def test_toolkit_csv_flattens_units_without_changing_structured_data(self):
        standardized = test_data_audit.StoredReviewAuditTests().toolkit_preview()['standardized']
        original = deepcopy(standardized)
        records = list(csv.DictReader(io.StringIO(standardized_csv(standardized).decode('utf-8-sig'))))
        self.assertEqual(standardized, original)
        self.assertEqual(len(records), len(standardized['rows']))
        self.assertNotIn('quantities', records[0])
        self.assertEqual(records[0]['outlet_standard_volumetric_flow/CO2 [mL/min]'], '8')
        self.assertEqual(records[0]['outlet_standard_volumetric_flow/H2 [mL/min]'], '')
        self.assertEqual(records[2]['reactant_conversion [1]'], '0.2')
        self.assertEqual(records[2]['review_time_s'], '0')
        self.assertEqual(records[0]['canonical_subject_id'], standardized['rows'][0]['canonical_subject_id'])

    def test_toolkit_csv_rejects_duplicate_and_colliding_quantity_columns(self):
        quantity = dict(field='flow', unit='mL/min', value_decimal='1')
        conflicting_rows = (
            [dict(quantities=[quantity, quantity])],
            [dict(quantities=[quantity]), {'flow [mL/min]': 'other', 'quantities': []}],
            [dict(quantities=[dict(field='a [b', unit='c', value_decimal='1')]),
             dict(quantities=[dict(field='a', unit='b [c', value_decimal='2')])],
        )
        for rows in conflicting_rows:
            with self.assertRaisesRegex(InputError, 'conflict or repeat'):
                standardized_csv(dict(kind='toolkit_gc_processing_revision', rows=rows))

    def test_quantity_flattening_is_scoped_and_retains_csv_safety(self):
        quantity = dict(field='flow', unit='mL/min', value_decimal='0.1234567890123456789')
        ordinary = dict(kind='other', rows=[dict(quantities=[quantity])])
        records = list(csv.DictReader(io.StringIO(standardized_csv(ordinary).decode('utf-8-sig'))))
        self.assertEqual(list(records[0]), ['quantities'])
        toolkit = dict(kind='toolkit_gc_processing_revision', rows=[dict(original_label='=1+1', quantities=[
            quantity, dict(field='=formula', unit='1', value_decimal='=2+2')])])
        records = list(csv.DictReader(io.StringIO(standardized_csv(toolkit).decode('utf-8-sig'))))
        self.assertEqual(records[0]['original_label'], "'=1+1")
        self.assertEqual(records[0]['flow [mL/min]'], '0.1234567890123456789')
        self.assertEqual(records[0]["'=formula [1]"], "'=2+2")

    def test_headers_cannot_collide_after_formula_protection(self):
        with self.assertRaisesRegex(InputError, 'headers conflict'):
            standardized_csv(dict(rows=[{'=label': 'first', "'=label": 'second'}]))

    def test_extra_headers_and_numeric_named_text_cannot_become_formulas(self):
        _, loaded = test_exports.ExportTests().loaded()
        payload = loaded['revision'].value()
        payload['preview']['standardized']['rows'][0].update({'=1+1': 'text', 'computed_value': '=1+1'})
        revision = Revision.create(payload['preview'], 'Synthetic extra columns')
        loaded.update(revision=revision, approval=revision.approve('Tester', 'Synthetic review', True))
        loaded['receipt'].update(revision_id=revision.value()['id'], revision_sha256=revision.sha256)
        with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
            records = list(csv.DictReader(io.StringIO(archive.read('standardized.csv').decode('utf-8-sig'))))
        self.assertIn("'=1+1", records[0])
        self.assertEqual(records[0]['computed_value'], "'=1+1")

    def test_valid_negative_measurements_keep_their_numeric_representation(self):
        source = Source.from_bytes('negative.csv', b'mass,name\n-72,001\n')
        _, loaded = test_exports.ExportTests().loaded(source, [dict(source='mass', target='signal', unit='as recorded')])
        with zipfile.ZipFile(io.BytesIO(review_archive(loaded))) as archive:
            records = list(csv.DictReader(io.StringIO(archive.read('standardized.csv').decode('utf-8-sig'))))
        self.assertEqual(records[0]['signal'], '-72')

    def test_export_revalidates_receipt_identity_and_original_binding(self):
        _, loaded = test_exports.ExportTests().loaded()
        for change in (lambda r: r.update(revision_sha256='bad'),
                lambda r: r.update(revision_id='other'),
                lambda r: r.update(destination={}),
                lambda r: r.update(manifest_id=True),
                lambda r: r['files'][0].update(sha256='bad'),
                lambda r: r['files'][0].update(size_bytes=1),
                lambda r: r['files'][0].update(file_id=0)):
            mutated = dict(loaded, receipt=deepcopy(loaded['receipt']))
            change(mutated['receipt'])
            with self.assertRaisesRegex(InputError, 'receipt does not match'):
                review_archive(mutated)

    def test_temp_cleanup_failure_does_not_mask_actionable_save_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'existing.bin'
            target.write_bytes(b'existing')
            with patch('catalyst_desktop.exports.os.replace', side_effect=PermissionError), \
                    patch('catalyst_desktop.exports.Path.unlink', side_effect=PermissionError):
                with self.assertRaisesRegex(InputError, 'could not be saved'):
                    save_bytes(target, b'download')
            self.assertEqual(target.read_bytes(), b'existing')


if __name__ == '__main__':
    unittest.main()
