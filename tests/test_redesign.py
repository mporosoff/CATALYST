"""Readable IDs, the simplified record model and the SciSure store (synthetic data only)."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest

from catalyst_desktop import ids, records
from catalyst_desktop.scisure import SciSureClient
from catalyst_desktop.settings import Settings
from catalyst_desktop.store import Store, StoreError, FileItem
from fake_scisure import FakeSciSure

PROFILE = dict(name='Marc D. Porosoff', initials='MDP', lab='university-of-rochester')
SLAC = dict(name='Jane Lee', initials='JL', lab='slac')
RECIPE = dict(method='Incipient wetness impregnation', metals='Mo 10, K 1', support='γ-Al2O3 (Sasol)',
    calcination_T_C='450', calcination_time_h='4', steps='Impregnate, dry at 110 °C, calcine.')


class IdTests(unittest.TestCase):
    def test_sample_ids_are_readable_and_sequential(self):
        day = date(2026, 9, 25)
        self.assertEqual(ids.next_sample_id([], 'Rochester', 'mdp', day), 'UR-MDP-260925-01')
        existing = ['UR-MDP-260925-01', 'UR-MDP-260925-03', 'UR-JS-260925-02', 'UR-MDP-260924-02']
        self.assertEqual(ids.next_sample_id(existing, 'UR', 'MDP', day), 'UR-MDP-260925-02')
        info = ids.parse_sample_id('ASTAR-JLW-261003-14')
        self.assertEqual((info['lab'], info['initials'], info['date'], info['number']), ('astar', 'JLW', '2026-10-03', 14))
        self.assertIsNone(ids.parse_sample_id('XX-MDP-260925-01'))
        self.assertIsNone(ids.parse_sample_id('UR-MDP-261399-01'))

    def test_data_and_procedure_ids(self):
        self.assertEqual(ids.next_data_id('UR-MDP-260925-01', 'XRD', ['UR-MDP-260925-01-XRD-01']), 'UR-MDP-260925-01-XRD-02')
        self.assertEqual(ids.next_data_id('UR-MDP-260925-01', 'Reactor / GC performance', []), 'UR-MDP-260925-01-RXN-01')
        self.assertEqual(ids.next_procedure_id('slac', ['PRC-UR-001', 'PRC-SLAC-001']), 'PRC-SLAC-002')

    def test_profile_helpers(self):
        self.assertEqual(ids.suggest_initials('Marc D. Porosoff'), 'MDP')
        self.assertEqual(ids.lab_from_email('someone@chem.northwestern.edu'), 'northwestern')
        self.assertIsNone(ids.lab_from_email('someone@gmail.com'))
        with self.assertRaises(ids.IdError):
            ids.clean_initials('M')


class RecordTests(unittest.TestCase):
    def test_composition_and_deviations(self):
        self.assertEqual(records.suggest_composition(RECIPE), '10 wt% Mo + 1 wt% K on γ-Al2O3')
        procedure = dict(id='PRC-UR-001', version=2, name='Mo impregnation', recipe=records.clean_recipe(RECIPE)[0])
        sample, problems = records.sample_record(sample_id='UR-MDP-260925-01', profile=PROFILE, synthesis_date='2026-09-25',
            procedure=procedure, recipe=dict(RECIPE, calcination_T_C='500'), composition='10 wt% Mo + 1 wt% K on γ-Al2O3')
        self.assertEqual(problems, [])
        self.assertEqual([(d['field'], d['procedure'], d['sample']) for d in sample['deviations']],
            [('calcination_T_C', 450.0, 500.0)])

    def test_components_are_explicit_rows(self):
        rows = [dict(component='Mo2C', loading='', unit='wt%'), dict(component='K', loading='1.5', unit='wt%'),
            dict(component='Cu', loading='3', unit='mol%'), dict(component='', loading='', unit='wt%')]
        recipe, problems = records.clean_recipe(dict(components=rows, support='SiO2 (Aerosil 200)'))
        self.assertEqual(problems, [])
        self.assertEqual(records.suggest_composition(recipe), 'Mo2C + 1.5 wt% K + 3 mol% Cu on SiO2')
        self.assertEqual(records.suggest_composition(dict(components=[dict(component='Mo2C', loading='')], support='none')), 'Mo2C')
        _, problems = records.clean_recipe(dict(components=[dict(component='Ni', loading='ten', unit='wt%')]))
        self.assertIn('Loading for Ni', problems[0])
        # Procedures saved by 1.0.0 used free text; they still load and compare without false deviations.
        legacy = dict(metals='Mo 10, K 1', support='γ-Al2O3')
        sample, _ = records.sample_record(sample_id='UR-MDP-260925-01', profile=PROFILE, synthesis_date='2026-09-25',
            procedure=dict(id='PRC-UR-001', version=1, name='x', recipe=legacy),
            recipe=dict(components=[dict(component='Mo', loading='10', unit='wt%'), dict(component='K', loading='1', unit='wt%')],
                support='γ-Al2O3'), composition='')
        self.assertEqual(sample['deviations'], [])
        self.assertEqual(sample['composition'], '10 wt% Mo + 1 wt% K on γ-Al2O3')

    def test_commercial_reference_material(self):
        record, problems = records.sample_record(sample_id='UR-MDP-260928-01', profile=PROFILE, synthesis_date='2026-09-28',
            procedure=None, recipe=dict(components=[dict(component='Cu', loading='60', unit='wt%'),
                dict(component='ZnO', loading='30', unit='wt%')], support='Al2O3'), composition='', source='commercial',
            commercial=dict(supplier='Clariant', product='MegaMax 800', lot='L-2231'))
        self.assertEqual(problems, [])
        self.assertEqual((record['received_date'], record['deviations']), ('2026-09-28', []))
        info = records.parse_sample_experiment_name(records.sample_experiment_name(record))
        self.assertEqual((info['source'], info['origin'], info['procedure']), ('commercial', 'Commercial · Clariant MegaMax 800', ''))
        self.assertIn(('Lot / batch number', 'L-2231'), records.summary_lines(record))
        _, problems = records.sample_record(sample_id='UR-MDP-260928-01', profile=PROFILE, synthesis_date='2026-09-28',
            procedure=None, recipe={}, composition='Pt/Al2O3', source='commercial', commercial={})
        self.assertEqual(len(problems), 2)

    def test_names_and_headers_round_trip(self):
        name = records.sample_experiment_name(dict(id='UR-MDP-260925-01', composition='5 wt% Mo | K',
            procedure=dict(id='PRC-UR-001', version=2)))
        info = records.parse_sample_experiment_name(name)
        self.assertEqual((info['composition'], info['procedure'], info['initials']), ('5 wt% Mo / K', 'PRC-UR-001 v2', 'MDP'))
        data, problems = records.data_record(data_id='UR-MDP-260925-01-XRD-01', sample_id='UR-MDP-260925-01',
            technique='XRD', profile=SLAC, measured_date='2026-09-30', files=['a.xy'])
        header = records.parse_section_header(records.section_header(data))
        self.assertEqual((header['kind'], header['technique'], header['lab'], header['initials']), ('data', 'XRD', 'SLAC', 'JL'))

    def test_problems_are_plain_language(self):
        _, problems = records.sample_record(sample_id='UR-MDP-260925-01', profile=PROFILE, synthesis_date='2026-09-25',
            procedure=None, recipe=dict(calcination_T_C='hot'), composition='')
        self.assertEqual(len(problems), 3)
        self.assertIn('Calcination temperature', ' '.join(problems))


class SettingsTests(unittest.TestCase):
    def test_profile_and_drafts_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            s = Settings(path)
            self.assertFalse(s.has_profile())
            s.set_profile('Marc Porosoff', 'mp', 'university-of-rochester')
            s.save_draft('upload', dict(technique='XRD', files=['C:/data/a.xy']))
            s.remember_sample('UR-MP-260925-01')
            again = Settings(path)
            self.assertTrue(again.has_profile())
            self.assertEqual(again.profile['initials'], 'MP')
            self.assertEqual(again.draft('upload')['technique'], 'XRD')
            self.assertEqual(again.recent_samples, ['UR-MP-260925-01'])
            self.assertNotIn('synthetic-token', path.read_text())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSciSure()
        self.store = Store(SciSureClient('synthetic-token', transport=self.fake), app_version='test')
        self.store.connect()
        self.assertIsNone(self.store.workspace)
        self.store.create_workspace()

    def procedure(self):
        def build(pid):
            return records.procedure_record(procedure_id=pid or 'PRC-UR-000', version=1, name='Mo impregnation',
                profile=PROFILE, recipe=RECIPE)[0]
        saved = self.store.create_procedure(build, [FileItem(name='SOP.pdf', content=b'%PDF synthetic')])
        versions = self.store.procedure_versions(saved['procedure'])
        return dict(saved['procedure'], version=versions[-1]['version'], recipe=versions[-1]['record']['recipe'])

    def new_sample(self, profile=PROFILE, procedure=None, day='2026-09-25'):
        procedure = procedure or self.procedure()
        def build(sid):
            return records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=profile, synthesis_date=day,
                procedure=procedure, recipe=RECIPE, composition=records.suggest_composition(RECIPE))[0]
        return self.store.create_sample(build, [])

    def test_full_workflow_is_searchable_and_verified(self):
        procedure = self.procedure()
        self.assertEqual(procedure['id'], 'PRC-UR-001')
        first = self.new_sample(procedure=procedure)
        second = self.new_sample(procedure=procedure)
        self.assertEqual((first['sample']['id'], second['sample']['id']), ('UR-MDP-260925-01', 'UR-MDP-260925-02'))
        listed = self.store.list_samples()
        self.assertEqual({s['id'] for s in listed}, {'UR-MDP-260925-01', 'UR-MDP-260925-02'})
        self.assertEqual(listed[0]['procedure'], 'PRC-UR-001 v1')
        sample = next(s for s in listed if s['id'] == 'UR-MDP-260925-01')
        def build(did):
            return records.data_record(data_id=did or 'x', sample_id=sample['id'], technique='XRD', profile=SLAC,
                measured_date='2026-09-30', conditions=dict(radiation='Cu Kα'), files=['scan.xy'])[0]
        saved = self.store.add_data(sample, build, [FileItem(name='scan.xy', content=b'10 100\n11 120\n')])
        self.assertEqual(saved['record']['id'], 'UR-MDP-260925-01-XRD-01')
        self.store.add_shipment(sample, lambda sid: records.shipment_record(shipment_id=sid, sample_id=sample['id'],
            profile=PROFILE, to_lab='SLAC', ship_date='2026-09-28', amount='200 mg')[0])
        opened = self.store.open_sample(sample)
        self.assertEqual(opened['record']['composition'], '10 wt% Mo + 1 wt% K on γ-Al2O3')
        self.assertEqual([d['id'] for d in opened['data']], ['UR-MDP-260925-01-XRD-01'])
        self.assertEqual(opened['data'][0]['record']['files'][0]['name'], 'scan.xy')
        self.assertEqual(opened['shipments'][0]['record']['to_lab'], 'SLAC')
        original = opened['data'][0]['files'][0]
        self.assertEqual(self.store.download(opened['data'][0]['section_id'], original,
            opened['data'][0]['record']['files'][0]['sha256']), b'10 100\n11 120\n')

    def test_unfinished_upload_is_reported_not_hidden(self):
        sample = self.new_sample()['sample']
        # A section with originals but no record = interrupted save.
        sid = self.store._section(sample['experiment_id'], 'CATALYST data | ' + sample['id'] + '-XRD-01 | XRD | 2026-09-30 | UR | MDP')
        self.store._upload(sid, 'scan.xy', b'1 2')
        opened = self.store.open_sample(sample)
        self.assertEqual(len(opened['incomplete']), 1)
        self.assertEqual(opened['data'], [])

    def test_saving_is_not_duplicated_on_retry(self):
        sample = self.new_sample()['sample']
        record = records.data_record(data_id=sample['id'] + '-XRD-01', sample_id=sample['id'], technique='XRD',
            profile=PROFILE, measured_date='2026-09-30', files=['a'])[0]
        files = [FileItem(name='a.xy', content=b'data')]
        self.store._save_section(sample['experiment_id'], record, files, lambda _: None)
        self.store._save_section(sample['experiment_id'], record, files, lambda _: None)
        names = [f['realName'] for f in self.fake.files.values() if f['experimentID'] == sample['experiment_id']]
        self.assertEqual(names.count('a.xy'), 1)  # the original is found and reused, never sent twice
        sections = [s for s in self.fake.sections.values() if s['experimentID'] == sample['experiment_id']]
        self.assertEqual(len(sections), 2)

    def test_missing_workspace_and_permission_messages(self):
        other = Store(SciSureClient('synthetic-token', transport=FakeSciSure()))
        other.connect()
        with self.assertRaisesRegex(StoreError, 'coordinator'):
            other.list_samples()
        sample = self.new_sample()['sample']
        self.fake.deny_other_writes, self.fake.user_id = True, 8
        with self.assertRaisesRegex(StoreError, 'share'):
            self.store.add_shipment(sample, lambda sid: records.shipment_record(shipment_id=sid, sample_id=sample['id'],
                profile=SLAC, to_lab='UR', ship_date='2026-10-01')[0])

    def test_interrupted_saves_finish_the_same_id_on_retry(self):
        procedure = self.procedure()
        def build(sid):
            return records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=PROFILE, synthesis_date='2026-09-25',
                procedure=procedure, recipe=RECIPE, composition='10 wt% Mo')[0]
        broken = FileItem(name='photo.jpg', content=b'jpg')
        broken.read = lambda: (_ for _ in ()).throw(StoreError('network dropped'))
        with self.assertRaises(StoreError):
            self.store.create_sample(build, [broken])
        self.assertEqual(self.store.last_reserved, 'UR-MDP-260925-01')
        # A new session (fresh Store) retries with the ID remembered in the draft.
        again = Store(self.store.client); again.connect()
        saved = again.create_sample(build, [FileItem(name='photo.jpg', content=b'jpg')], reserved_id='UR-MDP-260925-01')
        self.assertEqual(saved['sample']['id'], 'UR-MDP-260925-01')
        self.assertEqual([s['id'] for s in again.list_samples()], ['UR-MDP-260925-01'])
        self.assertIsNotNone(again.open_sample(again.list_samples()[0])['record'])
        # The same for a data upload in the same session.
        sample = again.list_samples()[0]
        data = lambda did: records.data_record(data_id=did or 'x', sample_id=sample['id'], technique='XRD', profile=SLAC,
            measured_date='2026-09-30', files=['a'])[0]
        with self.assertRaises(StoreError):
            again.add_data(sample, data, [FileItem(name='a.xy', content=b'1'), broken])
        again.add_data(sample, data, [FileItem(name='a.xy', content=b'1'), FileItem(name='photo.jpg', content=b'jpg')])
        opened = again.open_sample(sample)
        self.assertEqual(([d['id'] for d in opened['data']], opened['incomplete']), (['UR-MDP-260925-01-XRD-01'], []))

    def test_files_are_checked_before_upload(self):
        sample = self.new_sample()['sample']
        build = lambda did: records.data_record(data_id=did or 'x', sample_id=sample['id'], technique='XRD',
            profile=PROFILE, measured_date='2026-09-30', files=['a'])[0]
        with self.assertRaisesRegex(StoreError, 'same name'):
            self.store.add_data(sample, build, [FileItem(name='a.xy', content=b'1'), FileItem(name='A.XY', content=b'2')])
        with self.assertRaisesRegex(StoreError, 'reserved'):
            self.store.add_data(sample, build, [FileItem(name='catalyst-record.json', content=b'1')])


if __name__ == '__main__':
    unittest.main()


class UpdateCheckTests(unittest.TestCase):
    def test_versions_and_release_parsing(self):
        import io as _io
        from catalyst_desktop import updates
        self.assertTrue(updates.is_newer('1.0.1', '1.0.0'))
        self.assertTrue(updates.is_newer('v1.10.0', '1.9.3'))
        self.assertFalse(updates.is_newer('1.0.0', '1.0.0'))
        self.assertFalse(updates.is_newer(None, '1.0.0'))

        class Response(_io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        opener = lambda request, timeout: Response(json.dumps(dict(tag_name='v1.2.0', html_url='https://x', body='notes')).encode())
        self.assertEqual(updates.latest_release(opener=opener)['version'], '1.2.0')
        def offline(request, timeout):
            raise OSError('offline')
        self.assertIsNone(updates.latest_release(opener=offline))
