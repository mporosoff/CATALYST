"""Read-only reader, MCP connector and dataset export (synthetic data only)."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from catalyst_desktop import records
from catalyst_desktop.dataset import export_dataset, mcp_snippet
from catalyst_desktop.scisure import SciSureClient
from catalyst_desktop.store import Store, FileItem
from catalyst_query import CatalystReader, ReadOnlyError
from catalyst_query.mcp_server import Server, main as mcp_main
from fake_scisure import FakeSciSure

PROFILE = dict(name='Marc D. Porosoff', initials='MDP', lab='university-of-rochester')
SLAC = dict(name='Jane Lee', initials='JL', lab='slac')
RECIPE = dict(method='Incipient wetness impregnation', metals='Mo 10, K 1', support='γ-Al2O3', calcination_T_C='450',
    steps='Impregnate, dry, calcine.')


def seeded():
    fake = FakeSciSure()
    store = Store(SciSureClient('synthetic-token', transport=fake))
    store.connect()
    store.create_workspace()
    proc = store.create_procedure(lambda pid: records.procedure_record(procedure_id=pid or 'PRC-UR-000', version=1,
        name='Mo impregnation', profile=PROFILE, recipe=RECIPE)[0], [])['procedure']
    recipe = store.procedure_versions(proc)[-1]['record']['recipe']
    sample = store.create_sample(lambda sid: records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=PROFILE,
        synthesis_date='2026-09-25', procedure=dict(proc, version=1, recipe=recipe), recipe=dict(RECIPE, calcination_T_C='500'),
        composition='10 wt% Mo, 1 wt% K / γ-Al2O3')[0], [])['sample']
    for tech, who, cond, name, content in (('RXN', PROFILE, dict(temperature_C='300', feed='H2:CO2 3:1'), 'gc.csv',
            b'T_C,conversion\n300,0.12\n=HYPERLINK("x"),0\n'), ('XAS', SLAC, dict(edge='Mo K'), 'mo.xy', b'20000 0.1\n')):
        store.add_data(sample, lambda did: records.data_record(data_id=did or 'x', sample_id=sample['id'], technique=tech,
            profile=who, measured_date='2026-10-01', conditions=cond, files=[name])[0], [FileItem(name=name, content=content)])
    return fake


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.fake = seeded()
        self.seeded_calls = len(self.fake.calls)
        self.reader = CatalystReader(token='synthetic-token', transport=self.fake)

    def test_reader_queries(self):
        self.assertEqual([s['sample_id'] for s in self.reader.samples(search='mo al2o3')], ['UR-MDP-260925-01'])
        self.assertEqual(self.reader.samples(lab='SLAC'), [])
        sample = self.reader.sample('UR-MDP-260925-01')
        self.assertEqual(sample['sample']['deviations'][0]['sample'], 500.0)
        self.assertEqual({d['technique'] for d in sample['data']}, {'RXN', 'XAS'})
        rows = self.reader.data(technique='RXN')
        self.assertEqual((len(rows), rows[0]['cond_temperature_C'], rows[0]['sample_recipe_calcination_T_C']), (1, '300', 500.0))
        self.assertIn('300,0.12', self.reader.file_preview('UR-MDP-260925-01-RXN-01', 'gc.csv'))
        self.assertEqual(self.reader.procedure('PRC-UR-001')['versions'][0]['name'], 'Mo impregnation')

    def test_reader_cannot_write(self):
        with self.assertRaises(ReadOnlyError):
            self.reader.store.client.request('/api/v1/experiments', 'POST', dict(name='x'))
        self.reader.data()
        writes = [c for c in self.fake.calls[self.seeded_calls:] if c[0] != 'GET']
        self.assertEqual(writes, [])

    def test_mcp_protocol(self):
        server = Server(lambda: self.reader)
        lines = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion='2025-06-18')),
            dict(jsonrpc='2.0', method='notifications/initialized'),
            dict(jsonrpc='2.0', id=2, method='tools/list'),
            dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='get_sample', arguments=dict(sample_id='UR-MDP-260925-01'))),
            dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(name='get_sample', arguments=dict(sample_id='NOPE'))),
            [1, 2]]
        out = io.StringIO()
        mcp_main(io.StringIO('\n'.join(json.dumps(l) for l in lines) + '\n'), out, server)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4, None])
        self.assertEqual(replies[4]['error']['code'], -32600)
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'catalyst')
        self.assertIn('find_data', [t['name'] for t in replies[1]['result']['tools']])
        self.assertIn('UR-MDP-260925-01-XAS-01', replies[2]['result']['content'][0]['text'])
        self.assertTrue(replies[3]['result']['isError'])


class ExportTests(unittest.TestCase):
    def test_export_is_tidy_and_spreadsheet_safe(self):
        store = Store(SciSureClient('synthetic-token', transport=seeded()))
        store.connect()
        with tempfile.TemporaryDirectory() as folder:
            result = export_dataset(store, store.list_samples(), folder, originals=True)
            target = Path(result['folder'])
            self.assertEqual((result['samples'], result['data'], result['files']), (1, 2, 2))
            with open(target / 'data_records.csv', encoding='utf-8-sig') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r['technique'] for r in rows}, {'RXN', 'XAS'})
            self.assertEqual(rows[0]['sample_composition'], '10 wt% Mo, 1 wt% K / γ-Al2O3')
            dataset = json.loads((target / 'catalyst_dataset.json').read_text(encoding='utf-8'))
            self.assertEqual(dataset['samples'][0]['data'][0]['conditions']['feed'], 'H2:CO2 3:1')
            self.assertTrue((target / 'originals' / 'UR-MDP-260925-01' / 'UR-MDP-260925-01-RXN-01' / 'gc.csv').is_file())
            self.assertIn('catalyst_query.mcp_server', mcp_snippet('https://sandbox.elabjournal.com'))
            original = target / 'originals' / 'UR-MDP-260925-01' / 'UR-MDP-260925-01-RXN-01' / 'gc.csv'
            self.assertIn(b'=HYPERLINK', original.read_bytes())  # originals are byte-for-byte unchanged
        from catalyst_desktop.dataset import cell
        self.assertEqual((cell('-196'), cell('250–350'), cell('+5 bar'), cell('=SUM(A1)'), cell('-rm'), cell(-3.5)),
            ('-196', '250–350', "'+5 bar", "'=SUM(A1)", "'-rm", -3.5))


if __name__ == '__main__':
    unittest.main()
