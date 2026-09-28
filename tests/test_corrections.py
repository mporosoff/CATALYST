"""Correcting records after they are published: revisions, history, retiring samples and withdrawing data."""
import unittest

from catalyst_desktop import dataset, records
from catalyst_desktop.scisure import SciSureClient
from catalyst_desktop.store import Store, StoreError, FileItem
from fake_scisure import FakeSciSure
from test_redesign import PROFILE, SLAC, RECIPE

COORDINATOR = dict(name='Alex Coordinator', initials='AC', lab='northwestern')


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSciSure()
        self.store = Store(SciSureClient('synthetic-token', transport=self.fake), app_version='test')
        self.store.connect()
        self.store.create_workspace()
        procedure = records.procedure_record(procedure_id='PRC-UR-001', version=1, name='Mo impregnation',
            profile=PROFILE, recipe=RECIPE)[0]
        saved = self.store.create_procedure(lambda pid: dict(procedure, id=pid), [])
        self.procedure = dict(saved['procedure'], version=1, recipe=procedure['recipe'])

    def sample(self, composition=None):
        def build(sid):
            return records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=PROFILE, synthesis_date='2026-09-25',
                procedure=self.procedure, recipe=RECIPE, composition=composition or records.suggest_composition(RECIPE))[0]
        return self.store.create_sample(build, [FileItem(name='notebook.jpg', content=b'jpeg')])['sample']

    def data(self, sample, content=b'10 100\n'):
        def build(did):
            return records.data_record(data_id=did, sample_id=sample['id'], technique='XRD', profile=SLAC,
                measured_date='2026-09-30', files=['scan.xy'])[0]
        return self.store.add_data(sample, build, [FileItem(name='scan.xy', content=content)])

    def correct_sample(self, sample, note='Loading was 12 wt%, not 10.', **changes):
        opened = self.store.open_sample(sample)
        current = opened['record']
        updated = dict(current, **changes)
        record, problems = records.revise(current, updated, PROFILE, note)
        self.assertEqual(problems, [])
        return opened, self.store.revise_record(sample['experiment_id'], opened['sample_item'], record,
            expected_revision=records.revision_of(current))

    def test_who_may_correct(self):
        record = dict(created_by=dict(lab='UR'))
        self.assertTrue(records.can_change(record, PROFILE))
        self.assertFalse(records.can_change(record, SLAC))
        self.assertTrue(records.can_change(record, SLAC, coordinator=True))
        current = self.store.open_sample(self.sample())['record']
        _, problems = records.revise(current, dict(current), PROFILE, '  ')
        self.assertIn('why', problems[0])

    def test_sample_correction_keeps_history_and_updates_the_list(self):
        sample = self.sample()
        self.correct_sample(sample, composition='12 wt% Mo + 1 wt% K on γ-Al2O3')
        opened = self.store.open_sample(sample)
        record = opened['record']
        self.assertEqual((record['revision'], record['composition'], record['id']), (2, '12 wt% Mo + 1 wt% K on γ-Al2O3', sample['id']))
        self.assertEqual(record['created_by']['initials'], 'MDP')
        self.assertEqual([h['revision'] for h in record['history']], [1, 2])
        self.assertIn('Composition', record['history'][-1]['changes'])
        history = self.store.record_history(opened['sample_item'])
        self.assertEqual([r['composition'] for r in history], ['10 wt% Mo + 1 wt% K on γ-Al2O3', '12 wt% Mo + 1 wt% K on γ-Al2O3'])
        self.assertEqual([f['realName'] for f in opened['sample_files']], ['notebook.jpg'])
        # The list shows the correction without opening the sample, even from a fresh connection.
        fresh = Store(SciSureClient('synthetic-token', transport=self.fake)); fresh.connect()
        listed = fresh.list_samples()
        self.assertEqual((listed[0]['composition'], listed[0]['revision']), ('12 wt% Mo + 1 wt% K on γ-Al2O3', 2))
        self.assertNotIn('corrections', ' '.join(s['id'] for s in listed).casefold())
        # One readable copy, rewritten, never a second stale one.
        texts = [s for s in self.fake.sections.values() if s['experimentID'] == sample['experiment_id']
            and s['sectionType'] == 'PARAGRAPH']
        self.assertEqual(len(texts), 1)
        self.assertIn('12 wt% Mo', texts[0]['contents'])
        self.assertIn('Loading was 12 wt%, not 10.', texts[0]['contents'])

    def test_conflicting_corrections_are_refused(self):
        sample = self.sample()
        opened = self.store.open_sample(sample)
        stale = opened['record']
        self.correct_sample(sample, composition='12 wt% Mo + 1 wt% K on γ-Al2O3')
        record, _ = records.revise(stale, dict(stale, notes='x'), PROFILE, 'typo')
        with self.assertRaisesRegex(StoreError, 'corrected this record while you were editing'):
            self.store.revise_record(sample['experiment_id'], opened['sample_item'], record, expected_revision=1)

    def test_replacing_a_data_file_keeps_the_old_one_superseded(self):
        sample = self.sample()
        self.data(sample)
        item = self.store.open_sample(sample)['data'][0]
        current = item['record']
        record, _ = records.revise(current, dict(current, conditions=dict(radiation='Cu Kα')), SLAC, 'Wrong file uploaded.')
        self.store.revise_record(sample['experiment_id'], item, record,
            new_files=[FileItem(name='scan.xy', content=b'10 999\n')], supersede=['scan.xy'], expected_revision=1)
        item = self.store.open_sample(sample)['data'][0]
        self.assertEqual([f['name'] for f in item['record']['files']], ['scan (r2).xy'])
        self.assertEqual(item['record']['superseded_files'], ['scan.xy'])
        self.assertEqual([f['realName'] for f in item['files']], ['scan (r2).xy'])
        self.assertEqual([f['realName'] for f in item['superseded']], ['scan.xy'])
        self.assertEqual(self.store.download(item['section_id'], item['files'][0]), b'10 999\n')
        rows = dataset.data_rows(self.store.open_sample(sample), [])
        self.assertEqual((rows[0]['files'], rows[0]['revision']), ('scan (r2).xy', 2))
        # A data record can't be left with no current file.
        current = item['record']
        record, _ = records.revise(current, dict(current), SLAC, 'remove all')
        with self.assertRaisesRegex(StoreError, 'at least one current file'):
            self.store.revise_record(sample['experiment_id'], item, record, supersede=['scan (r2).xy'], expected_revision=2)

    def test_withdrawn_data_is_hidden_from_the_sample_and_exports(self):
        sample = self.sample()
        self.data(sample)
        self.data(sample, b'second')
        opened = self.store.open_sample(sample)
        self.store.withdraw_data(sample, opened['data'][0], SLAC, 'Detector was miscalibrated.')
        opened = self.store.open_sample(sample)
        self.assertEqual([d['id'] for d in opened['data']], [sample['id'] + '-XRD-02'])
        self.assertEqual([d['id'] for d in opened['withdrawn']], [sample['id'] + '-XRD-01'])
        self.assertEqual(opened['withdrawn'][0]['record']['status_note'], 'Detector was miscalibrated.')
        self.assertEqual(len(dataset.data_rows(opened, [])), 1)
        # IDs are never reused.
        self.assertEqual(self.data(sample)['record']['id'], sample['id'] + '-XRD-03')

    def test_retired_sample_links_to_its_replacement_and_its_id_is_not_reused(self):
        wrong = self.sample()
        right = self.sample()
        opened = self.store.open_sample(wrong)
        self.store.retire_sample(opened, PROFILE, 'Synthesis date was wrong.', replaced_by=right['id'])
        listed = self.store.list_samples()
        self.assertEqual([s['id'] for s in listed], [right['id']])
        retired = [s for s in self.store.list_samples(include_retired=True) if s['status'] == 'registered_in_error']
        self.assertEqual([s['id'] for s in retired], [wrong['id']])
        record = self.store.open_sample(retired[0])['record']
        self.assertEqual((record['status'], record['replaced_by']), ('registered_in_error', right['id']))
        self.assertIn('Registered in error', dict(records.summary_lines(record))['Status'])
        self.assertEqual(self.sample()['id'], 'UR-MDP-260925-03')

    def test_record_file_names_are_reserved(self):
        self.assertEqual(records.record_file_name(1), 'catalyst-record.json')
        self.assertEqual(records.record_file_name(3), 'catalyst-record-r3.json')
        self.assertEqual(records.record_file_revision('catalyst-record-r3.json'), 3)
        self.assertIsNone(records.record_file_revision('catalyst-record-final.json'))
        sample = self.sample()
        with self.assertRaisesRegex(StoreError, 'reserved'):
            self.store.add_data(sample, lambda did: None, [FileItem(name='Catalyst-Record-r2.json', content=b'{}')])

    def revise_data(self, sample, item, files=(), supersede=(), note='fix', **changes):
        current = item['record']
        record, _ = records.revise(current, dict(current, **changes), SLAC, note)
        return self.store.revise_record(sample['experiment_id'], item, record, new_files=list(files),
            supersede=list(supersede), expected_revision=records.revision_of(current))

    def test_interrupted_correction_leaves_no_stray_current_file(self):
        sample = self.sample()
        self.data(sample)
        item = self.store.open_sample(sample)['data'][0]
        real_upload = self.store._upload
        def fail_on_record(section_id, name, content):
            if name.startswith('catalyst-record'):
                raise StoreError('connection dropped')
            return real_upload(section_id, name, content)
        self.store._upload = fail_on_record
        with self.assertRaises(StoreError):
            self.revise_data(sample, item, [FileItem(name='extra.xy', content=b'1 2')])
        self.store._upload = real_upload
        item = self.store.open_sample(sample)['data'][0]
        self.assertEqual([f['realName'] for f in item['files']], ['scan.xy'])  # the leftover is not offered as data
        self.revise_data(sample, item, [FileItem(name='extra.xy', content=b'1 2')])
        item = self.store.open_sample(sample)['data'][0]
        self.assertEqual([f['name'] for f in item['record']['files']], ['scan.xy', 'extra.xy'])  # leftover reused
        names = [f['realName'] for f in self.fake.files.values() if f.get('realName', '').startswith('extra')]
        self.assertEqual(names, ['extra.xy'])

    def test_new_file_names_never_collide(self):
        sample = self.sample()
        self.data(sample)
        item = self.store.open_sample(sample)['data'][0]
        self.revise_data(sample, item, [FileItem(name='scan.xy', content=b'a'), FileItem(name='scan (r2).xy', content=b'b')])
        item = self.store.open_sample(sample)['data'][0]
        names = [f['name'] for f in item['record']['files']]
        self.assertEqual(len(set(names)), 3, names)
        for row, entry in zip(item['files'], item['record']['files']):
            self.assertEqual(self.store.download(item['section_id'], row, entry['sha256']) is not None, True)

    def test_simultaneous_corrections_first_wins_and_second_is_told(self):
        sample = self.sample()
        opened = self.store.open_sample(sample)
        current = opened['record']
        first, _ = records.revise(current, dict(current, notes='A'), PROFILE, 'A fix')
        second, _ = records.revise(current, dict(current, notes='B'), PROFILE, 'B fix')
        real_load = self.store.load_record
        self.store.load_record = lambda section: (current, [])  # both writers checked before either saved
        self.store.revise_record(sample['experiment_id'], opened['sample_item'], first, expected_revision=1)
        with self.assertRaisesRegex(StoreError, 'same moment'):
            self.store.revise_record(sample['experiment_id'], opened['sample_item'], second, expected_revision=1)
        self.store.load_record = real_load
        record = self.store.open_sample(sample)['record']
        self.assertEqual((record['notes'], record['history'][-1]['note']), ('A', 'A fix'))

    def test_retry_after_a_lost_reply_is_not_a_conflict(self):
        sample = self.sample()
        opened = self.store.open_sample(sample)
        current = opened['record']
        record, _ = records.revise(current, dict(current, notes='x'), PROFILE, 'typo')
        self.store.revise_record(sample['experiment_id'], opened['sample_item'], record, expected_revision=1)
        again, _ = records.revise(current, dict(current, notes='x'), PROFILE, 'typo')
        saved = self.store.revise_record(sample['experiment_id'], opened['sample_item'], again, expected_revision=1)
        self.assertEqual(saved['record']['revision'], 2)

    def test_unreadable_latest_revision_falls_back(self):
        sample = self.sample()
        self.data(sample)
        item = self.store.open_sample(sample)['data'][0]
        self.store._upload(item['section_id'], 'catalyst-record-r2.json', b'{"not": "a record"')
        opened = self.store.open_sample(sample)
        self.assertEqual((len(opened['data']), opened['incomplete']), (1, []))
        self.assertEqual(opened['data'][0]['record']['revision'] if 'revision' in opened['data'][0]['record'] else 1, 1)

    def test_log_failure_is_a_warning_and_can_be_repaired(self):
        sample = self.sample()
        self.store._log_change = lambda record: (_ for _ in ()).throw(StoreError('no access'))
        opened, saved = self.correct_sample(sample, composition='12 wt% Mo + 1 wt% K on γ-Al2O3')
        self.assertIn('sample list', saved['readable_warning'])
        del self.store._log_change
        self.assertEqual(self.store.list_samples()[0]['composition'], '10 wt% Mo + 1 wt% K on γ-Al2O3')
        self.assertGreaterEqual(self.store.write_missing_readable()['written'], 1)
        self.assertEqual(self.store.list_samples()[0]['composition'], '12 wt% Mo + 1 wt% K on γ-Al2O3')

    def test_restore_and_ai_reader_skip_retired(self):
        from catalyst_query.reader import CatalystReader
        wrong, right = self.sample(), self.sample()
        self.data(wrong)
        self.store.retire_sample(self.store.open_sample(wrong), PROFILE, 'Entered twice.')
        reader = CatalystReader(token='synthetic-token', transport=self.fake)
        self.assertEqual([s['sample_id'] for s in reader.samples()], [right['id']])
        self.assertEqual(reader.summary()['samples'], 1)
        self.assertEqual(reader.data(), [])
        self.assertIn('registered in error', reader.sample(wrong['id'])['warning'])
        retired = next(s for s in self.store.list_samples(include_retired=True) if s['id'] == wrong['id'])
        opened = self.store.open_sample(retired)
        self.store.restore(wrong['experiment_id'], opened['sample_item'], COORDINATOR, 'Retired by mistake.')
        record = self.store.open_sample(retired)['record']
        self.assertEqual((record['status'], record.get('replaced_by')), ('active', None))
        self.assertEqual({s['id'] for s in self.store.list_samples()}, {wrong['id'], right['id']})


if __name__ == '__main__':
    unittest.main()
