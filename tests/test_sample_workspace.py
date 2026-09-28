"""Sample discovery preserves exact attribution across independent experiments."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from catalyst_desktop.model import InputError, Revision, Source
from catalyst_desktop.sample_workspace import (merged_samples, sample_records, native_history, record_details,
    standardized_view, original_table_view, verified_original_table)
from catalyst_desktop.scisure import SciSureError
from test_adaptive_workflow import preview
from desktop_fixtures import uid
from test_ingestion import workbook


def entry(kind, number=1, experiment=42, **context):
    value = preview(kind, number, **context)
    return dict(trace=value['traceability'], context=value['context'], revision_id='review-' + str(number),
        revision_sha256=str(number) * 64, section_id=100 + number,
        destination=dict(tenant='https://sandbox.elabjournal.com', group_id=7, experiment_id=experiment,
            experiment_name='Experiment ' + str(experiment)))


def native(identifier=8, name='SYNTHETIC sample', canonical=''):
    return dict(source='native', native=dict(tenant='https://sandbox.elabjournal.com', group_id=7,
        sample_id=identifier, sample_type_id=3, snapshot_sha256='a' * 64),
        sample=dict(sampleID=identifier, sampleTypeID=3, name=name, altID=canonical, description='Synthetic material'),
        label=name)


class HistoryClient:
    origin = 'https://sandbox.elabjournal.com'
    def __init__(self):
        self.rows = [dict(expJournalID=5, sectionHeader='Synthetic input', sectionType='SAMPLESIN',
            experiment=dict(experimentID=42, name='Synthetic run'), created='2026-09-23')]
        self.members = [dict(sampleID=8, archived=False)]
        self.calls = []
    def assert_group(self, group):
        if group != 7: raise SciSureError('Wrong group')
    def destination(self, identifier, group, writable):
        self.assert_group(group)
        assert writable is False
        return dict(experiment_id=identifier, group_id=group, tenant=self.origin, experiment_name='Synthetic run')
    def list(self, route):
        self.calls.append(route)
        if route == '/api/v1/samples/8/experiments/sections': return deepcopy(self.rows)
        if route == '/api/v1/experiments/42/sections':
            return [dict(expJournalID=5, sectionType='SAMPLESIN', deleted=False)]
        if route == '/api/v1/experiments/sections/5/samples': return deepcopy(self.members)
        raise AssertionError(route)


class SampleWorkspaceTests(unittest.TestCase):
    def test_exact_canonical_or_pinned_native_identity_merges(self):
        sample = entry('sample')
        catalog = dict(entries=[sample])
        rows = merged_samples(catalog, [native(canonical=uid('UR', 'SMP'))])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source_label'], 'LIMS + CATALYST')
        sample['context'].update(nativeSampleId='8', nativeSampleTenant=HistoryClient.origin)
        rows = merged_samples(catalog, [native()])
        self.assertEqual(len(rows), 1)

    def test_equal_names_never_merge_unrelated_samples(self):
        rows = merged_samples(dict(entries=[entry('sample')]), [native(), native(9)])
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row['subject']['id'] for row in rows}), 3)

    def test_conflicting_native_identity_is_not_silently_collapsed(self):
        catalog = dict(entries=[entry('sample')])
        with self.assertRaises(InputError):
            merged_samples(catalog, [native(canonical=uid('UR', 'SMP')), native(9, canonical=uid('UR', 'SMP'))])

    def test_search_includes_native_identifier_and_description(self):
        self.assertEqual(len(merged_samples({}, [native(8)], '8 material')), 1)
        self.assertEqual(merged_samples({}, [native(8)], 'absent'), [])

    def test_sample_records_span_experiments_without_attributing_neighbor_records(self):
        entries = [entry('sample', 1), entry('measurement', 2, experiment=88),
            entry('measurement', 3, experiment=88, specimenId=uid('UR', 'SMP', 99)),
            entry('procedure', 4)]
        catalog = dict(entries=entries)
        rows = merged_samples(catalog)
        records = sample_records(catalog, rows[0])
        self.assertEqual({item['entry']['revision_id'] for item in records}, {'review-1', 'review-2'})
        self.assertEqual({item['entry']['destination']['experiment_id'] for item in records}, {42, 88})

    def test_explicit_model_relationship_is_included(self):
        sample = entry('sample')
        model = entry('computation', 2, relatedSampleIds=uid('UR', 'SMP'),
            modelRelation='compared with', modelLinkEvidence='Synthetic comparison')
        catalog = dict(entries=[sample, model])
        row = next(row for row in merged_samples(catalog) if row['subject']['id'] == uid('UR', 'SMP'))
        self.assertEqual([item['relationship'] for item in sample_records(catalog, row)], ['Related model', 'Sample record'])

    def test_native_history_verifies_membership_but_never_lists_unattributed_files(self):
        client = HistoryClient()
        rows = native_history(client, 7, native())
        self.assertEqual(rows[0]['relationship'], 'Used sample')
        self.assertFalse(any('/files' in route for route in client.calls))
        client.members = [dict(sampleID=999, archived=False)]
        with self.assertRaises(SciSureError): native_history(client, 7, native())

    def test_native_history_rejects_wrong_tenant_and_duplicate_or_oversized_results(self):
        client = HistoryClient()
        wrong = native(); wrong['native']['tenant'] = 'https://other.example'
        with self.assertRaises(InputError): native_history(client, 7, wrong)
        client.rows *= 2
        with self.assertRaises(SciSureError): native_history(client, 7, native())
        client.rows *= 126
        with self.assertRaises(InputError): native_history(client, 7, native())

    def test_readable_record_summary_includes_originals_without_raw_json(self):
        loaded = dict(revision=Revision.create(preview('measurement'), 'Synthetic XRD'),
            destination=dict(experiment_name='Synthetic run'), state='complete')
        summary = record_details(loaded)
        self.assertIn('Synthetic XRD', summary)
        self.assertIn('synthetic.dat', summary)
        self.assertIn('Experiment: Synthetic run', summary)
        self.assertNotIn('"preview":', summary)

    def test_standardized_table_preserves_decimal_quantities_and_is_bounded(self):
        value = dict(context={}, standardized=dict(rows=[dict(injection=index, quantities=[
            dict(field='rate', unit='mol g-1 s-1', value_decimal='0.12345678901234567890123456789'),
            dict(field='rate', unit='mmol g-1 s-1', value_decimal=None)]) for index in range(1005)]))
        original = deepcopy(value)
        view = standardized_view(value, 2000)
        self.assertEqual(len(view['rows']), 1000)
        self.assertEqual(view['total_rows'], 1005)
        self.assertEqual(view['columns'], ['injection', 'rate [mol g-1 s-1]', 'rate [mmol g-1 s-1]'])
        self.assertEqual(view['rows'][0], [0, '0.12345678901234567890123456789', None])
        self.assertIn('1,005', view['message'])
        self.assertEqual(value, original)

    def test_standardized_signal_unit_and_empty_states_are_explicit(self):
        view = standardized_view(dict(context=dict(signalUnit='counts'), standardized=dict(rows=[dict(signal='123.40')])))
        self.assertEqual(view['columns'], ['signal [counts]'])
        self.assertEqual(view['rows'], [['123.40']])
        originals = standardized_view(preview('measurement'))
        metadata = standardized_view(preview('sample'))
        self.assertIn('without creating a standardized table', originals['message'])
        self.assertIn('descriptive information', metadata['message'])
        self.assertEqual(originals['rows'], [])

    def test_original_tables_keep_coordinates_headers_precision_and_formula_text(self):
        csv = original_table_view(Source.from_bytes('source.csv', b'angle,intensity\n20.000,00123\n'), 'Table')
        self.assertEqual(csv['columns'], ['Source row', 'A', 'B'])
        self.assertEqual(csv['rows'], [[1, 'angle', 'intensity'], [2, '20.000', '00123']])
        json = original_table_view(Source.from_bytes('source.json', b'[{"energy":0.12345678901234567890123456789}]'), 'Table')
        self.assertEqual(json['rows'][1][1], '0.12345678901234567890123456789')
        xlsx_source = Source.from_bytes('source.xlsx', workbook({'Results': {'A3': 'energy', 'A4': 72, 'B4': ('A4*2', 144)}}))
        xlsx = original_table_view(xlsx_source, 'Results')
        self.assertEqual(xlsx['rows'][0][0], 3)
        self.assertEqual(xlsx['rows'][1][1], '72')
        self.assertEqual(xlsx['rows'][1][2], '=A4*2')
        self.assertIn('never calculated', xlsx['message'])
        with self.assertRaises(InputError): original_table_view(xlsx_source, 'Missing')

    def test_original_preview_rereads_verified_bytes_and_rejects_replaced_record(self):
        source = Source.from_bytes('source.csv', b'angle,intensity\n20,100\n', parse=False)
        revision = Revision.create(preview('measurement', sources=[source]), 'Synthetic original')
        loaded = dict(revision=revision, sources=[source])
        selected = entry('measurement', 2)
        with patch('catalyst_desktop.sample_workspace.read_review', return_value=loaded) as read:
            result = verified_original_table('client', selected, revision.sha256, 0)
            read.assert_called_once_with('client', selected['destination'], selected['section_id'], True)
            self.assertEqual(result.content, source.content)
            self.assertEqual(result.artifact['format'], 'csv')
            with self.assertRaises(InputError): verified_original_table('client', selected, '0' * 64, 0)
            with self.assertRaises(InputError): verified_original_table('client', selected, revision.sha256, 1)


if __name__ == '__main__': unittest.main()
