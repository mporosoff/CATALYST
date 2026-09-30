"""Shared test protocols, protocol differences, analysis links and computed results on data records."""
import unittest

from catalyst_desktop import dataset, ids, records
from catalyst_desktop.scisure import SciSureClient
from catalyst_desktop.store import Store, FileItem
from fake_scisure import FakeSciSure
from test_redesign import PROFILE, RECIPE

TEST = dict(reaction_T_C='250', pressure_bar='30', total_flow_mL_min='40', GHSV_h='11,000', catalyst_mass_g='0.25',
    feed='CO2 10 + H2 30 mL/min', pretreatment_gas='H2', pretreatment_flow_mL_min='60', pretreatment_ramp_C_min='10',
    pretreatment_T_C='250', pretreatment_time_h='1')


class ProtocolTests(unittest.TestCase):
    def test_ids_and_record(self):
        self.assertEqual(ids.next_procedure_id('nu', ['PRC-NU-001', 'TST-NU-001'], prefix='TST'), 'TST-NU-002')
        self.assertEqual(ids.next_procedure_id('nu', ['TST-NU-001']), 'PRC-NU-001')
        self.assertTrue(ids.is_test_protocol('TST-NU-001'))
        record, problems = records.procedure_record(procedure_id='TST-NU-001', version=1, name='CO2 to methanol',
            profile=PROFILE, recipe=dict(TEST, GHSV_h='11000'), category='testing')
        self.assertEqual((problems, record['category'], record['recipe']['reaction_T_C']), ([], 'testing', 250))
        self.assertIn('Test protocol TST-NU-001', records.readable_header(record))
        self.assertIn(('Reaction pressure (bar)', '30'), records.summary_lines(record))
        _, problems = records.procedure_record(procedure_id='TST-NU-001', version=1, name='x', profile=PROFILE,
            recipe=dict(GHSV_h='fast'), category='testing')
        self.assertTrue(any('GHSV' in p for p in problems))

    def test_protocol_conditions_and_differences(self):
        recipe = records.clean_recipe(dict(TEST, GHSV_h='11000'), records.TEST_FIELDS)[0]
        expected = records.protocol_conditions(recipe)
        self.assertEqual(expected['temperature_C'], '250')
        self.assertEqual(expected['catalyst_mass_mg'], '250')
        self.assertEqual(expected['GHSV'], '11000 h-1')
        self.assertEqual(expected['pretreatment'], 'H2 60 mL/min, 10 °C/min to 250 °C, 1 h')
        protocol = dict(id='TST-NU-001', version=1, name='x', recipe=recipe)
        record, problems = records.data_record(data_id='UR-MDP-260925-01-RXN-01', sample_id='UR-MDP-260925-01',
            technique='RXN', profile=PROFILE, measured_date='2026-09-30', conditions=dict(expected, temperature_C='270'),
            files=['a.xlsx'], protocol=protocol, results=dict(co2_conversion_pct=8.2, selectivity_CH3OH_pct=75.0))
        self.assertEqual(problems, [])
        self.assertEqual([(d['field'], d['protocol'], d['run']) for d in record['protocol_deviations']],
            [('temperature_C', '250', '270')])
        lines = dict(records.summary_lines(record))
        self.assertEqual(lines['Differs from protocol'], 'Temperature (°C): 250 → 270')
        self.assertEqual(lines['Selectivity to CH3OH (%)'], '75')

    def test_analysis_links_are_checked(self):
        base = dict(sample_id='UR-MDP-260925-01', technique='XAS', profile=PROFILE, measured_date='2026-09-30', files=['w.png'])
        _, problems = records.data_record(data_id='UR-MDP-260925-01-XAS-02', derived_from=['not an id'], **base)
        self.assertTrue(problems)
        _, problems = records.data_record(data_id='UR-MDP-260925-01-XAS-02', derived_from=['UR-MDP-260925-01-XAS-02'], **base)
        self.assertIn('itself', problems[0])
        record, problems = records.data_record(data_id='UR-MDP-260925-01-XAS-02', derived_from=['UR-MDP-260925-01-XAS-01'], **base)
        self.assertEqual((problems, record['derived_from']), ([], ['UR-MDP-260925-01-XAS-01']))

    def test_store_and_export(self):
        store = Store(SciSureClient('synthetic-token', transport=FakeSciSure()), app_version='test')
        store.connect(); store.create_workspace()
        store.create_procedure(lambda pid: records.procedure_record(procedure_id=pid or 'PRC-UR-000', version=1,
            name='Mo', profile=PROFILE, recipe=RECIPE)[0], [])
        saved = store.create_procedure(lambda pid: records.procedure_record(procedure_id=pid or 'TST-UR-000', version=1,
            name='CO2 to methanol', profile=PROFILE, recipe=TEST, category='testing')[0], [])
        self.assertEqual(saved['procedure']['id'], 'TST-UR-001')
        listed = {p['id']: p['category'] for p in store.list_procedures()}
        self.assertEqual(listed, {'PRC-UR-001': 'synthesis', 'TST-UR-001': 'testing'})
        procedure = next(p for p in store.list_procedures() if p['id'] == 'PRC-UR-001')
        sample = store.create_sample(lambda sid: records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=PROFILE,
            synthesis_date='2026-09-25', procedure=dict(procedure, version=1, recipe=RECIPE), recipe=RECIPE,
            composition='')[0], [])['sample']
        protocol = dict(saved['procedure'], version=1, recipe=store.procedure_versions(saved['procedure'])[0]['record']['recipe'])
        store.add_data(sample, lambda did: records.data_record(data_id=did, sample_id=sample['id'], technique='RXN',
            profile=PROFILE, measured_date='2026-09-30', conditions=dict(temperature_C='270'), files=['a', 'b'],
            protocol=protocol, results=dict(co2_conversion_pct=8.2, calculation='carbon basis'),
            extracted=[dict(file='run.xlsx', reader='nu-gc-workbook/1', label='GC', metadata={}, warnings=[])])[0],
            [FileItem(name='run.xlsx', content=b'x'), FileItem(name='run - CATALYST results.csv', content=b'a,b\n')])
        rows = dataset.data_rows(store.open_sample(sample), [])
        self.assertEqual((rows[0]['protocol_id'], rows[0]['result_co2_conversion_pct']), ('TST-UR-001', 8.2))
        self.assertIn('Temperature', rows[0]['differs_from_protocol'])
        self.assertNotIn('result_calculation', rows[0])


if __name__ == '__main__':
    unittest.main()
