"""Synthetic desktop workflows, direct transport boundaries, and recovery checks."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from catalyst_desktop.model import (Source, Revision, InputError, table, make_profile, build_preview,
    convert, encode, digest, check_sources)
from catalyst_desktop.scisure import SciSureClient, SciSureError, SANDBOX, NoRedirects, remote_id
from catalyst_desktop.publication import Publisher, history, read_review
from catalyst_desktop.credentials import save_token, load_token, forget_token, CredentialError
from test_ingestion import workbook
from test_toolkit_bundle import fixture
from desktop_fixtures import physical_context


def review(source=None, modality='synthesis', context=None, rules=None, version=1):
    source = source or Source.from_bytes('synthetic.csv', b'mass,name\n72,001\n')
    context = context or physical_context()
    rules = rules or [dict(source='mass', target='mass_g', unit='mg'), dict(source='name', target='specimen_id', unit='text')]
    profile = make_profile('Rochester', modality, source.artifact['format'], 'export v1', 'Example', version, 'Table', 1, rules)
    preview = build_preview([source], 'Rochester', modality, context, profile)
    revision = Revision.create(preview, 'Synthetic test')
    return source, profile, revision


class ModelTests(unittest.TestCase):
    def test_units_names_and_original_bytes_are_preserved(self):
        source, profile, revision = review()
        data = revision.value()['preview']
        self.assertEqual(data['standardized']['rows'][0]['mass_g'], '0.072')
        self.assertEqual(data['standardized']['rows'][0]['specimen_id'], '001')
        self.assertEqual(source.content, b'mass,name\n72,001\n')
        self.assertEqual(data['normalization']['profile_sha256'], digest(encode(profile)))
        self.assertFalse(data['scientific_processing']['executed'])
        self.assertEqual(convert('300', 'degC'), '573.15')
        self.assertEqual(convert('1', 'atm absolute'), '101325')

    def test_json_decimal_precision_survives_normalization(self):
        source = Source.from_bytes('numbers.json', b'[{"mass":0.123456789012345678901234567890,"name":"001"}]')
        _, _, revision = review(source)
        self.assertEqual(revision.value()['preview']['standardized']['rows'][0]['mass_g'], '0.00012345678901234567890123456789')

    def test_xlsx_lexical_precision_and_header_row(self):
        source = Source.from_bytes('synthetic.xlsx', workbook({'Data': {'A3': 'mass', 'B3': 'name', 'A4': 72, 'B4': '001'}}))
        profile = make_profile('Rochester', 'synthesis', 'xlsx', 'v1', 'Header at 3', 1, 'Data', 3,
            [dict(source='mass', target='mass_g', unit='mg')])
        preview = build_preview([source], 'Rochester', 'synthesis', physical_context(), profile)
        self.assertEqual(preview['standardized']['rows'][0]['mass_g'], '0.072')
        self.assertEqual(preview['standardized']['rows'][0]['source_row'], 4)

    def test_formula_missing_and_ambiguous_values_block_approval(self):
        for value in ('', '1,25', 'NaN', '-10'):
            with self.subTest(value=value):
                source = Source.from_bytes('synthetic.json', encode([{'mass': value, 'name': 'A'}]))
                _, _, revision = review(source)
                with self.assertRaises(InputError): revision.approve('Tester', 'reviewed', True)
        source = Source.from_bytes('synthetic.xlsx', workbook({'Table': {'A1':'mass','B1':'name','A2':('1+1',2),'B2':'A'}}))
        _, _, revision = review(source)
        with self.assertRaises(InputError): revision.approve('Tester', 'reviewed', True)

    def test_exact_name_aliases_do_not_fuzzily_merge(self):
        source = Source.from_bytes('aliases.csv', b'name\nCO2\nco2\n')
        _, _, revision = review(source, rules=[dict(source='name', target='species', unit='text', aliases={'CO2':'carbon dioxide'})])
        self.assertEqual([r['species'] for r in revision.value()['preview']['standardized']['rows']], ['carbon dioxide','co2'])

    def test_profile_scope_invalid_units_and_duplicate_targets(self):
        source, profile, _ = review()
        with self.assertRaises(InputError): build_preview([source], 'another-partner', 'synthesis', {}, profile)
        for rules in ([dict(source='mass',target='pressure_Pa_abs',unit='bar gauge')],
            [dict(source='mass',target='mass_g',unit='mg'),dict(source='name',target='mass_g',unit='mg')]):
            with self.assertRaises(InputError): make_profile('Rochester','synthesis','csv','v1','test',1,'Table',1,rules)

    def test_context_is_required_and_physical_ranges_checked(self):
        source, profile, _ = review()
        profile['modality'] = 'reactor'
        preview = build_preview([source], 'Rochester','reactor',{'temperatureC':'-274','pressureKpaAbs':'-1'},profile)
        codes = {i['code'] for i in preview['validation']['issues']}
        self.assertIn('CONTEXT_specimenId',codes)
        self.assertIn('CONTEXT_INVALID_temperatureC',codes)
        self.assertIn('CONTEXT_INVALID_pressureKpaAbs',codes)

    def test_spectral_axis_signal_and_declared_units_are_required(self):
        source = Source.from_bytes('spectrum.csv', b'x,y\n1000,0.12\n')
        context = {'specimenId':'S','runId':'R','acquiredBy':'Lab','technique':'FTIR','axisUnit':'nm','signalUnit':'absorbance','calibration':'CAL1'}
        _, _, revision = review(source,'spectroscopy',context,[dict(source='x',target='wavenumber_cm_inverse',unit='1/cm'),dict(source='y',target='signal',unit='as recorded')])
        self.assertIn('AXIS_UNIT_CONFLICT', [i['code'] for i in revision.value()['preview']['validation']['issues']])

    def test_revision_is_immutable_and_approval_requires_acknowledgment(self):
        _, _, revision = review()
        value = revision.value()
        value['preview']['context']['specimenId'] = 'tampered'
        self.assertEqual(revision.value()['preview']['context']['specimenId'], physical_context()['specimenId'])
        with self.assertRaises(InputError): revision.approve('Tester','note',False)
        with self.assertRaises(InputError): revision.approve('','note',True)
        self.assertEqual(revision.approve('Tester','Reviewed',True)['revision_sha256'],revision.sha256)
        with self.assertRaises(InputError): Revision(revision.content+b' ', revision.sha256).value()

    def test_no_persistent_writes_in_model(self):
        with patch('builtins.open', side_effect=AssertionError('Unexpected file operation')):
            source, _, revision = review()
            revision.approve('Tester', 'Reviewed', True)
            self.assertEqual(source.name, 'synthetic.csv')

    def test_duplicate_filenames_and_sparse_header_are_rejected(self):
        source, _, _ = review()
        with self.assertRaises(InputError): check_sources([source, source])
        source = Source.from_bytes('duplicate.csv', b'a,a\n1,2\n')
        with self.assertRaises(InputError): table(source,'Table')

    def test_toolkit_interval_and_processing_provenance_remain_distinct(self):
        artifacts = fixture()
        # The existing fixture contains parsed synthetic artifacts, so construct memory Sources with matching test bytes/digests.
        sources = []
        for index, artifact in enumerate(artifacts):
            content = f'SYNTHETIC fixture {index}'.encode()
            artifact['sha256'] = digest(content)
            artifact['size_bytes'] = len(content)
            sources.append(Source(artifact['filename'], content, artifact))
        context = dict(specimenId='S',runId='R',acquiredBy='Lab',reactorType='packed_bed',temperatureC='300',
            pressureKpaAbs='101.325',catalystMassMg='125',intervalMin='22.4',flowBasis='toolkit convention reviewed',
            calibration='CAL1',processingVersion='Legacy producer version unavailable; import reviewed')
        preview = build_preview(sources,'university-of-rochester','reactor',context,toolkit=True)
        processing = preview['scientific_processing']
        self.assertFalse(processing['executed'])
        self.assertTrue(processing['time_axis']['executed'])
        self.assertEqual(processing['time_axis']['interval_min'],'22.4')
        self.assertIsNone(processing['producer_commit'])
        self.assertIn('TIME_AXIS_REVISION',[i['code'] for i in preview['validation']['issues']])
        self.assertEqual(preview['standardized']['declared_processing_settings']['injection_interval_min'],'2.5')


class FakeSciSure:
    """In-memory simulated API; no network or filesystem persistence."""
    def __init__(self):
        self.sections = []
        self.files = {}
        self.content = {}
        self.next_id = 100
        self.posts = 0
        self.fail_next = None
        self.experiment = dict(experimentID=42,groupID=7,studyID=8,projectID=9,deleted=False,template=False,signatureStatus='None',name='Synthetic experiment')

    def response(self, value):
        return 200, encode(value)

    def __call__(self, url, method, headers, body):
        assert url.startswith(SANDBOX + '/api/v1/')
        assert headers['Authorization'] == 'synthetic-token'
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if method == 'POST':
            self.posts += 1
            if self.fail_next == 'before':
                self.fail_next = None
                raise OSError('synthetic-token should never be displayed')
        if path == '/api/v1/groups/active':
            return self.response(dict(groupID=7,name='Synthetic group'))
        if path == '/api/v1/experiments/42':
            return self.response(self.experiment)
        if path == '/api/v1/experiments':
            return self.response(dict(data=[self.experiment],hasNextPage=False))
        if path == '/api/v1/experiments/42/sections':
            if method == 'GET':
                return self.response(dict(data=self.sections,hasNextPage=False))
            record = json.loads(body)
            self.next_id += 1
            record.update(expJournalID=self.next_id,deleted=False)
            self.sections.append(record)
            self.files[self.next_id] = []
            return self.after(self.next_id)
        segments = path.split('/')
        if path.startswith('/api/v1/experiments/sections/'):
            sid = int(segments[5])
            if len(segments) == 8:
                return 200, self.content[int(segments[7])]
            if method == 'GET':
                return self.response(dict(data=self.files[sid],hasNextPage=False))
            self.next_id += 1
            self.files[sid].append(dict(experimentFileID=self.next_id,realName=query['fileName'][0],fileSize=len(body),parentExperimentFileID=None))
            self.content[self.next_id] = bytes(body)
            return self.after(self.next_id)
        raise AssertionError('Unexpected endpoint: '+path)

    def after(self, result):
        if self.fail_next == 'after':
            self.fail_next = None
            raise OSError('Connection lost after write')
        return self.response(result)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSciSure()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destination = self.client.destination(42)
        self.publisher = Publisher(self.client,self.destination)
        self.source, _, self.revision = review()
        self.approval = self.revision.approve('Tester','Reviewed synthetic values',True)

    def publish(self):
        return self.publisher.publish(self.revision,self.approval,[self.source])

    def test_round_trip_and_repeat_do_not_duplicate(self):
        receipt = self.publish()
        posts = self.api.posts
        self.assertEqual(self.publish()['receipt_id'],receipt['receipt_id'])
        self.assertEqual(self.api.posts,posts)
        self.assertEqual(len(history(self.client,self.destination)),1)
        loaded = read_review(self.client,self.destination,receipt['section_id'],True)
        self.assertEqual(loaded['sources'][0].content,self.source.content)
        self.assertEqual(loaded['revision'].sha256,self.revision.sha256)
        self.assertEqual(loaded['state'],'complete')

    def test_approval_and_source_mutation_block_before_writes(self):
        self.approval['revision_sha256'] = 'wrong'
        with self.assertRaises(InputError): self.publish()
        self.assertEqual(self.api.posts,0)
        self.approval = self.revision.approve('Tester','Reviewed',True)
        self.source = Source.from_bytes('synthetic.csv', b'mass,name\n100,001\n')
        with self.assertRaises(InputError): self.publish()
        self.assertEqual(self.api.posts,0)

    def test_lost_response_is_reconciled_without_duplicate_write(self):
        self.api.fail_next='after'
        with self.assertRaises(SciSureError): self.publish()
        self.assertEqual(len(self.api.sections),1)
        self.publish()
        self.assertEqual(len(self.api.sections),1)

    def test_uncertain_missing_write_is_never_retried_blindly(self):
        self.api.fail_next='before'
        with self.assertRaises(SciSureError): self.publish()
        with self.assertRaises(SciSureError): self.publish()
        self.assertEqual(self.api.posts,1)

    def test_signed_or_moved_experiment_blocks_writes_but_allows_read(self):
        receipt=self.publish()
        self.api.experiment['signatureStatus']='Signed'
        with self.assertRaises(SciSureError): self.publish()
        self.assertEqual(read_review(self.client,self.destination,receipt['section_id'])['state'],'complete')
        self.api.experiment['studyID']=999
        with self.assertRaises(SciSureError): read_review(self.client,self.destination,receipt['section_id'])

    def test_remote_file_corruption_and_false_receipt_are_detected(self):
        receipt=self.publish()
        fid=receipt['files'][0]['file_id']
        self.api.content[fid]=b'X'*len(self.source.content)
        with self.assertRaises(SciSureError): self.publish()
        with self.assertRaises(SciSureError): read_review(self.client,self.destination,receipt['section_id'],True)
        self.api.content[receipt['receipt_id']]=encode({'state':'complete'})
        with self.assertRaises(SciSureError): read_review(self.client,self.destination,receipt['section_id'])

    def test_revision_version_conflict_requires_new_mapping_version(self):
        self.publish()
        _, _, second = review(rules=[dict(source='mass',target='mass_g',unit='g')])
        with self.assertRaises(InputError): self.publisher.publish(second,second.approve('Tester','Reviewed',True),[self.source])
        _, _, third = review(rules=[dict(source='mass',target='mass_g',unit='g')], version=2)
        self.publisher.publish(third,third.approve('Tester','Reviewed',True),[self.source])

    def test_untrusted_destination_or_api_path_never_receives_token(self):
        for origin in ('http://sandbox.elabjournal.com','https://evil.example','https://sandbox.elabjournal.com.evil.example'):
            with self.assertRaises(SciSureError): SciSureClient('synthetic-token',origin,self.api)
        for path in ('https://evil.example','//evil.example/api/v1/a','/api/v1/../auth','/api/v1/%2e%2e/auth'):
            with self.assertRaises(SciSureError): self.client.request(path)
        self.assertNotIn('synthetic-token',repr(self.client))
        self.assertIsNone(NoRedirects().redirect_request(None,None,302,'',{},'https://evil.example'))
        for value in (True,0,-1,'1.2',{},'01'):
            with self.assertRaises(SciSureError): remote_id(value)

    def test_errors_never_echo_credentials(self):
        def fail(*_): raise OSError('synthetic-token')
        client=SciSureClient('synthetic-token',transport=fail)
        with self.assertRaises(SciSureError) as captured: client.request('/api/v1/groups/active')
        self.assertNotIn('synthetic-token',str(captured.exception))

    def test_credentials_only_use_explicit_system_store(self):
        class MemoryVault:
            value=None
            def get_password(self,*_): return self.value
            def set_password(self,*args): self.value=args[-1]
            def delete_password(self,*_): self.value=None
        store=MemoryVault()
        save_token(SANDBOX,'synthetic-token',store)
        self.assertEqual(load_token(SANDBOX,store),'synthetic-token')
        forget_token(SANDBOX,store)
        self.assertIsNone(load_token(SANDBOX,store))
        with patch('catalyst_desktop.credentials.system_store',side_effect=RuntimeError('Unavailable')):
            with self.assertRaises(CredentialError): save_token(SANDBOX,'synthetic-token')


if __name__=='__main__':
    unittest.main()
