"""Scientific boundary and cross-artifact checks using synthetic data only."""

import csv
import io
import json
import unittest

from catalyst_ingest.readers import InputError, read_artifact
from catalyst_ingest.preview import PROFILE_ROOT
from catalyst_ingest.toolkit import preview_toolkit_bundle
from test_ingestion import raw_fixture, workbook

ENTITY = 'university-of-rochester'
PROFILE = json.loads((PROFILE_ROOT / ENTITY / 'reactor/toolkit-gc-bundle-v1.json').read_text())


def csv_artifact(name, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return read_artifact(stream.getvalue().encode(), name)


def fixture(summary_override=None, flow_change=None, raw_change=None):
    raw = read_artifact(raw_fixture(), 'source.xlsx')
    raw_cells = {a: c['value'] for a, c in raw['sheets']['Page 1']['cells'].items()}
    if raw_change:
        raw_cells.update(raw_change)
    analysis = read_artifact(workbook({'Raw Original': raw_cells, 'Settings': {'A1': 'Parameter', 'B1': 'Value'},
                                      'Processed': {'H2': ('1/2', None)}}), 'example_gc_analysis.xlsx')
    summary = {name: '0' for name in PROFILE['summary_required']}
    summary.update(catalyst_id='Synthetic', source_file='source.xlsx', output_prefix='example', reaction_type='rwgs',
                   inlet_flow_source='bypass', bypass_source='same_file', bypass_file='same input file',
                   catalyst_mass_mg='125', injection_interval_min='2.5', conversion_std_pct='0',
                   inlet_Ar_sccm='15', inlet_CO2_sccm='10', inlet_H2_sccm='30',
                   bypass_points_used='1', bypass_selected_points='1', n_bypass='1', n_blank_excluded='1',
                   n_reaction='2', plot_reaction_points='1', ss_inj_start='12', ss_inj_end='12',
                   space_velocity_reference_temperature_K='298', space_velocity_reference_pressure_atm='1')
    summary['conversion_%'] = '20'; summary['carbon_balance_%'] = '101.2'
    summary.update(summary_override or {})
    rows = []
    for label, inj, bypass, blank, include in [('test blank 1','1',False,True,False), ('test bypass 1','1',True,False,False),
                                             ('test Rxn 12','12',False,False,True), ('STANDBY','',False,False,False)]:
        rows.append(dict(catalyst_id='Synthetic', label=label, inj_num=inj, is_bypass=str(bypass), source_kind='main',
                         H2='', CO2='8', Ar='15', is_blank=str(blank), time_on_stream_h='0' if include else '',
                         analysis_include=str(include), row_status='Reaction included' if include else 'Excluded', conversion='0.2' if include else ''))
    if flow_change:
        flow_change(rows)
    return [raw, analysis, csv_artifact('example_gc_summary.csv', [summary]), csv_artifact('example_gc_flows.csv', rows)]


class ToolkitBundleTests(unittest.TestCase):
    def preview(self, artifacts):
        return preview_toolkit_bundle(artifacts, ENTITY, 'reactor')

    def test_numeric_results_do_not_depend_on_excel_formula_cache(self):
        result = self.preview(fixture())
        summary = {q['field']: q for q in result['data']['summary_quantities']}
        self.assertEqual(summary['catalyst_mass']['value_decimal'], '0.125')
        self.assertEqual(summary['nominal_injection_interval']['value_decimal'], '150.0')
        self.assertEqual(summary['steady_state_conversion']['value_decimal'], '0.20')
        self.assertEqual(summary['steady_state_carbon_balance']['value_decimal'], '1.012')
        self.assertTrue(result['validation']['embedded_raw_values_match'])
        self.assertFalse(result['processing']['executed'])
        self.assertIsNone(result['processing']['producer_commit'])
        self.assertFalse(result['publication']['eligible'])
        self.assertEqual(result['data']['steady_state_row_count'], 1)
        self.assertIsNone(result['data']['rows'][0]['quantities'][0]['value_decimal'])
        warnings = {i['code'] for i in result['issues']}
        self.assertIn('UNCACHED_WORKBOOK_FORMULAS', warnings)
        self.assertNotIn('FORMULA_RESULTS_UNAVAILABLE', warnings)

    def test_wrong_embedded_source_is_not_verified(self):
        result = self.preview(fixture(raw_change={'I8': '0.7'}))
        self.assertFalse(result['validation']['embedded_raw_values_match'])
        self.assertIn('EMBEDDED_RAW_MISMATCH', [i['code'] for i in result['issues']])

    def test_row_order_and_counts_are_checked(self):
        result = self.preview(fixture({'n_bypass': '2'}, lambda rows: rows.reverse()))
        self.assertTrue({'ROW_LINK_MISMATCH', 'ROW_COUNT_MISMATCH'} <= {i['code'] for i in result['issues']})

    def test_nonreaction_rows_cannot_silently_join_analysis(self):
        result = self.preview(fixture(flow_change=lambda rows: rows[3].update(analysis_include='True')))
        self.assertIn('EXCLUDED_ROLE_INCLUDED', [i['code'] for i in result['issues']])

    def test_mixed_revisions_or_missing_artifacts_are_rejected(self):
        artifacts = fixture()
        artifacts[-1]['filename'] = 'different_gc_flows.csv'
        with self.assertRaises(InputError): self.preview(artifacts)
        with self.assertRaises(InputError): self.preview(fixture()[:-1])

    def test_scope_and_bypass_recipe_are_explicit(self):
        with self.assertRaises(InputError): preview_toolkit_bundle(fixture(), 'another-entity', 'reactor')
        with self.assertRaises(InputError): self.preview(fixture({'reaction_type': 'co2_hydrogenation'}))
        with self.assertRaises(InputError): self.preview(fixture({'bypass_source': 'separate_file'}))

    def test_nonfinite_or_ambiguous_values_and_booleans_are_rejected(self):
        for value in ('NaN', 'inf', '1,25'):
            with self.subTest(value=value), self.assertRaises(InputError):
                self.preview(fixture({'catalyst_mass_mg': value}))
        with self.assertRaises(InputError):
            self.preview(fixture(flow_change=lambda rows: rows[0].update(is_bypass='yes')))

    def test_duplicate_and_ragged_headers_are_rejected(self):
        for content in (b'a,a\n1,2\n', b'a,b\n1\n'):
            artifacts = fixture()
            artifacts[2] = read_artifact(content, 'example_gc_summary.csv')
            with self.assertRaises(InputError): self.preview(artifacts)

    def test_workbook_settings_cannot_disagree_silently(self):
        artifacts = fixture()
        artifacts[1]['sheets']['Settings']['cells'].update(A2={'value': 'catalyst_mass_mg'}, B2={'value': 100})
        self.assertIn('WORKBOOK_SETTING_MISMATCH', [i['code'] for i in self.preview(artifacts)['issues']])

    def test_serialization_rounding_is_recorded_without_changing_values(self):
        artifacts = fixture({'c5_unknown_response_factor': '0.0018326957690202206'})
        artifacts[1]['sheets']['Settings']['cells'].update(A2={'value': 'c5_unknown_response_factor'}, B2={'value': 0.001832695769020221})
        result = self.preview(artifacts)
        issue = next(i for i in result['issues'] if i['code'] == 'WORKBOOK_SETTING_PRECISION')
        self.assertEqual(issue['severity'], 'info')
        self.assertEqual(result['data']['declared_processing_settings']['c5_unknown_response_factor'], '0.0018326957690202206')

    def test_small_real_disagreement_does_not_pass_precision_check(self):
        artifacts = fixture({'c5_unknown_response_factor': '0.0018326957'})
        artifacts[1]['sheets']['Settings']['cells'].update(A2={'value': 'c5_unknown_response_factor'}, B2={'value': 0.0018326958})
        self.assertIn('WORKBOOK_SETTING_MISMATCH', [i['code'] for i in self.preview(artifacts)['issues']])


if __name__ == '__main__':
    unittest.main()
