"""Synthetic fixtures only. No user research data or credentials."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
import zipfile

from catalyst_ingest.readers import InputError, read_artifact
from catalyst_ingest.preview import PROFILE_ROOT, preview_artifact, review_pair
from catalyst_ingest.__main__ import preserve_original, main


S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def workbook(sheets, extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>' + ''.join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="r{i}"/>' for i, name in enumerate(sheets, 1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships>' + ''.join(
            f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets)+1)) + '</Relationships>')
        for i, (_, cells) in enumerate(sheets.items(), 1):
            xml = []
            for address, value in cells.items():
                if isinstance(value, tuple):
                    f, cache = value
                    xml.append(f'<c r="{address}"><f>{escape(f)}</f><v>{cache if cache is not None else ""}</v></c>')
                elif isinstance(value, (float, int)):
                    xml.append(f'<c r="{address}"><v>{value}</v></c>')
                else:
                    xml.append(f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{S}"><sheetData><row>' + ''.join(xml) + '</row></sheetData></worksheet>')
        for name, content in (extra or {}).items():
            z.writestr(name, content)
    return buffer.getvalue()


def raw_fixture():
    return workbook({'Page 1': {'B1': 'Sequence Name', 'C1': 'Synthetic test run',
        'G4': 'Hydrogen', 'I4': 'Carbon Dioxide', 'K4': 'Ar/O2',
        'G5': 'Amount', 'H5': 'Peak Area', 'I5': 'Amount', 'J5': 'Peak Area', 'K5': 'Amount', 'L5': 'Peak Area',
        'A6': 'test blank 1', 'G6': '0', 'H6': 0, 'I6': '1.25', 'K6': '2',
        'A7': 'test bypass 1', 'G7': '3.5', 'I7': '1', 'K7': '2',
        'A8': 'test Rxn 12', 'G8': '2.5', 'I8': '0.8', 'K8': '2',
        'A9': 'STANDBY'}}, {'xl/styles.xml': f'<styleSheet xmlns="{S}"><fills><fill/></fills></styleSheet>'})


def processed_fixture():
    profile = json.loads((PROFILE_ROOT / 'university-of-rochester/reactor/gc-analysis-xlsx-v1.json').read_text())
    cells = {**profile['signature'], 'F2': 'Synthetic catalyst', 'C2': 1, 'A2': 'no',
             'B2': 'Plotted reaction point; outside SS range', 'U2': ("'Raw Original'!A6", None),
             'H2': ("'Raw Original'!I6/2", None)}
    return workbook({'Processed': cells, 'Raw Original': {'A1': 'Raw sheet copy failed'},
        'Bypass Original': {'A1': 'Copy failed'}, 'Bypass Processed': {},
        'Settings': {'A1': 'Parameter', 'B1': 'Value', 'A8': 'catalyst_mass_mg', 'B8': 125,
                     'A3': 'injection_interval_min', 'B3': 2.5,
                     'A34': 'calculated_GHSV_mL_g_hr', 'B34': ('B32*60/B31', None)}})


class ReaderTests(unittest.TestCase):
    def test_malformed_styles_do_not_discard_raw_data(self):
        result = read_artifact(raw_fixture(), 'raw.xlsx')
        self.assertEqual(result['sheets']['Page 1']['cells']['I6']['value'], '1.25')

    def test_formulas_without_cache_stay_unavailable(self):
        cell = read_artifact(workbook({'A': {'A1': ('1+2', None)}}), 'a.xlsx')['sheets']['A']['cells']['A1']
        self.assertEqual(cell['formula'], '=1+2')
        self.assertIsNone(cell['value']); self.assertIsNone(cell['cached_value'])

    def test_formula_cache_never_becomes_verified_value(self):
        cell = read_artifact(workbook({'A': {'A1': ('1+2', 3)}}), 'a.xlsx')['sheets']['A']['cells']['A1']
        self.assertIsNone(cell['value']); self.assertEqual(cell['cached_value'], 3)

    def test_xml_entities_rejected(self):
        data = workbook({'A': {}}, {'xl/sharedStrings.xml': '<!DOCTYPE x [<!ENTITY a "test">]><x>&a;</x>'})
        with self.assertRaisesRegex(InputError, 'declarations'):
            read_artifact(data, 'x.xlsx')

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(InputError, 'path'):
            read_artifact(workbook({'A': {}}, {'../escape.txt': 'bad'}), 'x.xlsx')

    def test_macros_rejected(self):
        with self.assertRaisesRegex(InputError, 'Macros'):
            read_artifact(workbook({'A': {}}, {'xl/vbaProject.bin': 'bad'}), 'x.xlsx')

    def test_expansion_limit_checked_before_xml_parsing(self):
        with patch('catalyst_ingest.readers.MAX_EXPANDED_BYTES', 10):
            with self.assertRaisesRegex(InputError, 'Expanded'):
                read_artifact(raw_fixture(), 'x.xlsx')

    def test_sparse_huge_coordinates_rejected(self):
        with self.assertRaisesRegex(InputError, 'limit'):
            read_artifact(workbook({'A': {'XFD1048576': 'bad'}}), 'x.xlsx')

    def test_csv_preserves_leading_zero_and_literal_formula(self):
        result = read_artifact(b'id,value\r\n001,=1+2\r\n002,"3,4"\r\n', 'x.csv')
        cells = result['sheets']['Table']['cells']
        self.assertEqual(cells['A2']['value'], '001')
        self.assertEqual(cells['B2']['value'], '=1+2')
        self.assertNotIn('formula', cells['B2'])
        self.assertEqual(cells['B3']['value'], '3,4')

    def test_json_preserves_zero_and_null(self):
        cells = read_artifact(b'[{"v":0},{"v":null}]', 'x.json')['sheets']['Table']['cells']
        self.assertEqual(cells['A2']['value'], 0); self.assertIsNone(cells['A3']['value'])

    def test_json_duplicate_keys_rejected(self):
        with self.assertRaisesRegex(InputError, 'Duplicate'):
            read_artifact(b'[{"v":1,"v":2}]', 'x.json')

    def test_json_nonfinite_rejected(self):
        for content in [b'[{"v":NaN}]', b'[{"v":1e999}]']:
            with self.subTest(content=content), self.assertRaises(InputError):
                read_artifact(content, 'x.json')

    def test_json_excessive_integer_rejected_as_input_error(self):
        with self.assertRaises(InputError):
            read_artifact(b'[{"v":' + b'1' * 5000 + b'}]', 'x.json')

    def test_unknown_extension_rejected(self):
        with self.assertRaisesRegex(InputError, 'Supported'):
            read_artifact(b'anything', 'x.exe')


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.raw = preview_artifact(read_artifact(raw_fixture(), 'raw.xlsx'), 'university-of-rochester', 'reactor')
        self.processed = preview_artifact(read_artifact(processed_fixture(), 'processed.xlsx'), 'university-of-rochester', 'reactor')

    def test_roles_and_zero_are_preserved(self):
        self.assertEqual(self.raw['data']['row_counts'], {'blank': 1, 'bypass': 1, 'reaction': 1, 'standby': 1})
        first = self.raw['data']['rows'][0]['measurements'][0]
        self.assertEqual(first['numeric_value_decimal'], '0')
        self.assertIsNone(first['unit'])

    def test_missing_measurement_does_not_become_zero(self):
        row = self.raw['data']['rows'][0]
        self.assertFalse(any(m['channel'] == 'Carbon Dioxide' and m['quantity'] == 'peak_area' for m in row['measurements']))

    def test_ambiguous_reference_channel_blocks(self):
        self.assertIn('REFERENCE_CHANNEL_AMBIGUOUS', [i['code'] for i in self.raw['issues']])
        self.assertFalse(self.raw['publication']['eligible'])

    def test_profile_is_keyed_by_entity_and_modality(self):
        for entity, modality in [('different-partner', 'reactor'), ('university-of-rochester', 'spectroscopy')]:
            result = preview_artifact(read_artifact(raw_fixture(), 'raw.xlsx'), entity, modality)
            self.assertIsNone(result['profile']); self.assertIsNone(result['data'])

    def test_csv_without_profile_is_not_silently_normalized(self):
        result = preview_artifact(read_artifact(b'id,amount\na,2\n', 'x.csv'), 'university-of-rochester', 'reactor')
        self.assertEqual(result['issues'][0]['code'], 'MAPPING_REQUIRED')

    def test_literal_unit_conversions_are_exact_and_traceable(self):
        fields = {f['canonical_field']: f for f in self.processed['data']['normalized_literal_fields']}
        self.assertEqual(fields['catalyst_mass_g']['value_decimal'], '0.125')
        self.assertEqual(fields['nominal_injection_interval_s']['value_decimal'], '150.0')
        self.assertEqual(fields['catalyst_mass_g']['source']['cell'], 'B8')

    def test_missing_sources_and_results_are_flagged(self):
        codes = [i['code'] for i in self.processed['issues']]
        self.assertEqual(codes.count('RAW_DEPENDENCY_MISSING'), 2)
        self.assertIn('FORMULA_RESULTS_UNAVAILABLE', codes)
        self.assertIsNone(self.processed['data']['scientific_results'])

    def test_mass_based_flow_does_not_become_volume_based_ghsv(self):
        fields = self.processed['data']['normalized_literal_fields']
        f = next(f for f in fields if f['source_field'] == 'calculated_GHSV_mL_g_hr')
        self.assertEqual(f['canonical_field'], 'feed_volumetric_flow_per_catalyst_mass_mL_g_h')
        self.assertIsNone(f['value_decimal'])

    def test_no_unconfirmed_join(self):
        pair = review_pair(self.raw, self.processed)
        self.assertEqual(pair['status'], 'unconfirmed'); self.assertEqual(pair['role_discrepancies'], [])

    def test_confirmed_pair_detects_blank_classification(self):
        pair = review_pair(self.raw, self.processed, same_run_confirmed=True, labels_superseded=True)
        self.assertEqual(len(pair['role_discrepancies']), 1)
        self.assertEqual(pair['role_discrepancies'][0]['raw_role_candidate'], 'blank')
        self.assertIsNone(pair['identity_correction']['canonical_specimen_id'])

    def test_store_is_repeatable_and_checks_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            first = preserve_original(b'123', 'raw.xlsx', Path(folder))
            second = preserve_original(b'123', 'raw.xlsx', Path(folder))
            self.assertEqual(first, second)
            (Path(folder) / first['key']).write_bytes(b'changed')
            with self.assertRaisesRegex(InputError, 'integrity'):
                preserve_original(b'123', 'raw.xlsx', Path(folder))

    def test_cli_end_to_end_with_original_preservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'raw.xlsx').write_bytes(raw_fixture())
            (root / 'processed.xlsx').write_bytes(processed_fixture())
            output = root / 'preview.json'
            result = main([str(root/'raw.xlsx'), str(root/'processed.xlsx'), '--entity', 'university-of-rochester', '--modality', 'reactor',
                           '--output', str(output), '--store-root', str(root/'private'), '--same-run', '--row-labels-superseded'])
            self.assertEqual(result, 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertFalse(report['publication']['eligible'])
            self.assertEqual(len(list((root/'private/raw').iterdir())), 2)
            self.assertEqual(report['relationship']['status'], 'user_confirmed_same_run')


if __name__ == '__main__':
    unittest.main()
