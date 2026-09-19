"""Regression checks for scientific source preservation and review integrity."""
from decimal import localcontext
import unittest

from catalyst_ingest.readers import InputError, read_artifact
from catalyst_ingest.preview import preview_artifact
from catalyst_ingest.toolkit import preview_toolkit_bundle
from catalyst_desktop.contracts import approved_payload
from catalyst_desktop.model import Source, Revision, build_preview, make_profile, table, digest
from desktop_fixtures import physical_context
from test_desktop import review
from test_ingestion import raw_fixture, processed_fixture, workbook
from test_toolkit_bundle import fixture


ENTITY = 'university-of-rochester'


class SourcePreservationAuditTests(unittest.TestCase):
    def test_json_reader_retains_lexical_decimal_precision(self):
        source = b'[{"value":0.123456789012345678901234567890,"flag":true,"label":"001"}]'
        cells = read_artifact(source, 'data.json')['sheets']['Table']['cells']
        self.assertEqual(cells['A2']['lexical_value'], '0.123456789012345678901234567890')
        self.assertIs(cells['B2']['value'], True)
        self.assertNotIn('lexical_value', cells['B2'])
        self.assertEqual(cells['C2']['value'], '001')

    def test_saved_formula_result_never_becomes_a_raw_gc_observation(self):
        artifact = read_artifact(raw_fixture(), 'raw.xlsx')
        artifact['sheets']['Page 1']['cells']['H6'].update(
            value=None, formula='=1+1', cached_value=2, lexical_value='2')
        result = preview_artifact(artifact, ENTITY, 'reactor')
        measurement = next(m for m in result['data']['rows'][0]['measurements'] if m['source']['cell'] == 'H6')
        self.assertIsNone(measurement['numeric_value_decimal'])
        self.assertIn('NONNUMERIC_MEASUREMENTS', {i['code'] for i in result['issues']})

    def test_processed_settings_preserve_precision_under_any_decimal_context(self):
        artifact = read_artifact(processed_fixture(), 'processed.xlsx')
        artifact['sheets']['Settings']['cells']['B8'].update(
            value=125.123456789, lexical_value='125.123456789012345678901234567890')
        with localcontext() as precision:
            precision.prec = 6
            result = preview_artifact(artifact, ENTITY, 'reactor')
        mass = next(f for f in result['data']['normalized_literal_fields'] if f['source_field'] == 'catalyst_mass_mg')
        self.assertEqual(mass['value_decimal'], '0.125123456789012345678901234567890')

    def test_literal_partner_calculation_is_retained_without_claiming_reproduction(self):
        artifact = read_artifact(processed_fixture(), 'processed.xlsx')
        artifact['sheets']['Settings']['cells']['B34'] = dict(value=100, source_type='n', lexical_value='100')
        result = preview_artifact(artifact, ENTITY, 'reactor')
        value = next(f for f in result['data']['normalized_literal_fields'] if f['source_field'] == 'calculated_GHSV_mL_g_hr')
        self.assertEqual(value['source_value'], 100)
        self.assertIsNone(value['value_decimal'])
        self.assertEqual(value['canonical_field'], 'feed_volumetric_flow_per_catalyst_mass_mL_g_h')

    def test_spreadsheet_formatting_only_cells_do_not_create_phantom_columns(self):
        original = workbook({'Table': {'A1': 'mass', 'B1': '', 'A2': 72, 'B2': ''}})
        source = Source.from_bytes('formatted.xlsx', original)
        headers, rows = table(source, 'Table')
        self.assertEqual(headers, ['mass'])
        self.assertEqual(len(rows), 1)
        _, _, revision = review(source, rules=[dict(source='mass', target='mass_g', unit='mg')])
        revision.approve('Synthetic reviewer', 'Checked source cells', True)
        nonempty = Source.from_bytes('data.xlsx', workbook({'Table': {'A1': 'mass', 'A2': 72, 'B2': 'unlabeled value'}}))
        with self.assertRaises(InputError):
            table(nonempty, 'Table')


class ToolkitScientificAuditTests(unittest.TestCase):
    def codes(self, artifacts):
        return {i['code'] for i in preview_toolkit_bundle(artifacts, ENTITY, 'reactor')['issues']}

    def test_extra_embedded_formula_invalidates_raw_match(self):
        artifacts = fixture()
        artifacts[1]['sheets']['Raw Original']['cells']['Z10'] = dict(value=None, formula='=1', cached_value=1)
        self.assertIn('EMBEDDED_RAW_MISMATCH', self.codes(artifacts))

    def test_workbook_settings_comparison_uses_lexical_source_evidence(self):
        artifacts = fixture({'c5_unknown_response_factor': '1.00000000000000001'})
        artifacts[1]['sheets']['Settings']['cells'].update(
            A2={'value': 'c5_unknown_response_factor'},
            B2={'value': 1.0, 'source_type': 'n', 'lexical_value': '1.00000000000000002'})
        result = preview_toolkit_bundle(artifacts, ENTITY, 'reactor')
        precision = next(i for i in result['issues'] if i['code'] == 'WORKBOOK_SETTING_PRECISION')
        self.assertEqual(precision['workbook_value'], '1.00000000000000002')

    def test_impossible_toolkit_values_are_preserved_but_block_approval(self):
        changes = [lambda rows: rows[2].update(conversion='1.1'),
            lambda rows: rows[2].update(CO2='-1'), lambda rows: rows[2].update(time_on_stream_h='-1')]
        for change in changes:
            with self.subTest(change=change):
                self.assertIn('TOOLKIT_VALUE_RANGE', self.codes(fixture(flow_change=change)))
        for settings in ({'conversion_%': '101'}, {'sel_CO_%': '-1'}):
            self.assertIn('TOOLKIT_VALUE_RANGE', self.codes(fixture(settings)))
        # A carbon balance above 100% is a reported diagnostic, not a fraction bounded by 1.
        self.assertNotIn('TOOLKIT_VALUE_RANGE', self.codes(fixture({'carbon_balance_%': '101.2'})))

    def test_missing_selected_results_and_inconsistent_row_flags_are_rejected(self):
        for field in ('inj_num', 'conversion', 'time_on_stream_h'):
            self.assertIn('INCLUDED_RESULT_MISSING', self.codes(fixture(flow_change=lambda rows: rows[2].update({field: ''}))))
        self.assertIn('ROW_FLAGS_CONFLICT', self.codes(fixture(flow_change=lambda rows: rows[0].update(is_bypass='True'))))
        self.assertIn('SUMMARY_RESULT_MISSING', self.codes(fixture({'conversion_%': ''})))
        self.assertIn('STEADY_STATE_EMPTY', self.codes(fixture({'ss_inj_start': '13', 'ss_inj_end': '14'})))


class StoredReviewAuditTests(unittest.TestCase):
    def toolkit_preview(self):
        sources = []
        for index, artifact in enumerate(fixture()):
            content = f'Synthetic {index}'.encode()
            artifact.update(sha256=digest(content), size_bytes=len(content))
            sources.append(Source(artifact['filename'], content, artifact))
        context = physical_context(reactorType='packed bed', temperatureC='300', pressureKpaAbs='101.325',
            catalystMassMg='125', intervalMin='22.4', flowBasis='Synthetic reference', calibration='Synthetic calibration',
            processingVersion='Synthetic reviewed processing', identityNote='Synthetic is source label 001.')
        return build_preview(sources, 'Rochester', 'reactor', context, toolkit=True)

    def test_malformed_mapping_rules_return_validation_errors(self):
        for rules in ([{}], [None], [dict(source=[], target='mass_g', unit='mg')],
                [dict(source='mass', target='mass_g', unit='mg', aliases={' ': 'x'})]):
            with self.subTest(rules=rules), self.assertRaises(InputError):
                make_profile('Rochester', 'synthesis', 'csv', '1', 'mapping', 1, 'Table', 1, rules)

    def test_invalid_selected_source_returns_a_validation_error(self):
        source, profile, _ = review()
        for index in (-1, True, '0', 1):
            with self.subTest(index=index), self.assertRaises(InputError):
                build_preview([source], 'Rochester', 'synthesis', physical_context(), profile, source_index=index)

    def test_stored_numeric_corruption_does_not_pass_new_checksums(self):
        _, _, original = review()
        for value in ('NaN', 'Infinity', '-1', '1e100000', 'not numeric'):
            preview = original.value()['preview']
            preview['standardized']['rows'][0]['mass_g'] = value
            revision = Revision.create(preview, 'Synthetic corrupted stored data')
            approval = revision.approve('Synthetic', 'Testing stored validation', True)
            with self.subTest(value=value), self.assertRaises(InputError):
                approved_payload(revision, approval)

    def test_stored_subject_label_must_match_declared_lineage(self):
        _, _, original = review()
        preview = original.value()['preview']
        preview['standardized']['rows'][0]['specimen_id'] = 'another sample'
        revision = Revision.create(preview, 'Synthetic corrupted identity')
        approval = revision.approve('Synthetic', 'Testing stored validation', True)
        with self.assertRaises(InputError):
            approved_payload(revision, approval)

    def test_long_exact_converted_values_remain_valid(self):
        source = Source.from_bytes('small.csv', b'mass,name\n1e-300,001\n')
        _, _, revision = review(source)
        approval = revision.approve('Synthetic', 'Checked small nonzero value', True)
        approved_payload(revision, approval)

    def test_stored_toolkit_results_remain_bound_to_imported_evidence(self):
        valid = Revision.create(self.toolkit_preview(), 'Synthetic toolkit data')
        approved_payload(valid, valid.approve('Synthetic', 'Reviewed source', True))
        for mutate in (
                lambda p: p['standardized']['rows'][2].update(review_time_s='999'),
                lambda p: p['standardized']['rows'][2]['quantities'][1].update(value_decimal='999'),
                lambda p: p['toolkit_source_review']['artifact_sha256s'].reverse(),
                lambda p: p['toolkit_source_review']['issues'].append(
                    dict(code='EMBEDDED_RAW_MISMATCH', severity='error', message='Conflict'))):
            preview = self.toolkit_preview()
            mutate(preview)
            revision = Revision.create(preview, 'Synthetic corrupted toolkit data')
            approval = revision.approve('Synthetic', 'Testing stored validation', True)
            with self.subTest(mutate=mutate), self.assertRaises(InputError):
                approved_payload(revision, approval)


if __name__ == '__main__':
    unittest.main()
