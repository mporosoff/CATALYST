"""Documented API contracts and opaque/image round trips, using only synthetic data."""
import base64
from collections import deque
from copy import deepcopy
from decimal import Decimal, localcontext
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import zipfile

from catalyst_desktop.integration import inspect_setup, setup_summary
from catalyst_desktop.model import Source, Revision, InputError, build_preview, convert, encode, check_sources, MAX_FILE
from catalyst_desktop.publication import Publisher, read_review, history
from catalyst_desktop.scisure import SciSureClient, SciSureError
from desktop_fixtures import physical_context, computational_context
from test_desktop import FakeSciSure, review
from test_ingestion import workbook


def client_for(transport, sleep=None):
    return SciSureClient('synthetic-token', transport=transport, sleep=sleep)


class PaginationTests(unittest.TestCase):
    def pages(self, count=205, size=10, change=lambda page, response: response):
        calls = []
        def transport(url, method, headers, body):
            page = int(parse_qs(urlsplit(url).query)['$page'][0])
            calls.append(page)
            data = [dict(experimentID=i) for i in range(page * size, min((page + 1) * size, count))]
            response = dict(data=data, totalRecords=count, maxRecords=size, currentPage=page, recordCount=len(data))
            return 200, encode(change(page, response))
        return client_for(transport), calls

    def test_non_sample_endpoints_with_false_hasnextpage_return_every_record(self):
        client, calls = self.pages(change=lambda _, r: dict(r, hasNextPage=False))
        self.assertEqual(len(client.list('/api/v1/experiments')), 205)
        self.assertEqual(calls, list(range(21)))

    def test_hasnextpage_may_be_missing(self):
        client, _ = self.pages(count=101, size=100)
        self.assertEqual(len(client.list('/api/v1/experiments/42/sections')), 101)

    def test_empty_list_is_complete(self):
        client, calls = self.pages(count=0)
        self.assertEqual(client.list('/api/v1/sampleTypes'), [])
        self.assertEqual(calls, [0])

    def test_missing_totals_never_looks_like_a_complete_catalog(self):
        def remove(_, r):
            r.pop('totalRecords')
            return dict(r, hasNextPage=False)
        client, _ = self.pages(change=remove)
        with self.assertRaisesRegex(SciSureError, 'partial result'): client.list('/api/v1/experiments')

    def test_changing_counts_or_page_size_fail_closed(self):
        for field in ('totalRecords', 'maxRecords', 'currentPage', 'recordCount'):
            with self.subTest(field=field):
                client, _ = self.pages(change=lambda page, r: dict(r, **{field: r[field] + 1}) if page == 1 else r)
                with self.assertRaises(SciSureError): client.list('/api/v1/experiments')

    def test_short_page_and_repeated_records_fail_closed(self):
        def change(page, r):
            if page == 1: r['data'][0] = dict(experimentID=0, name='Renamed between pages')
            return r
        client, _ = self.pages(change=change)
        with self.assertRaisesRegex(SciSureError, 'repeated'): client.list('/api/v1/experiments')
        client, _ = self.pages(change=lambda _, r: dict(r, data=r['data'][:-1]))
        with self.assertRaises(SciSureError): client.list('/api/v1/experiments')

    def test_record_cap_blocks_partial_catalog(self):
        client, calls = self.pages(count=1001)
        with self.assertRaisesRegex(SciSureError, '1,000'): client.list('/api/v1/experiments')
        self.assertEqual(calls, [0])


class RateLimitTests(unittest.TestCase):
    def test_get_respects_retry_after_then_succeeds(self):
        replies = deque([(429, b'synthetic-token', {'Retry-After': '3'}), (200, b'{"groupID":7}')])
        delays = []
        client = client_for(lambda *_: replies.popleft(), delays.append)
        self.assertEqual(client.request('/api/v1/groups/active'), {'groupID': 7})
        self.assertEqual(delays, [3])

    def test_repeated_rate_limits_are_bounded_and_do_not_echo_bodies(self):
        delays = []
        client = client_for(lambda *_: (429, b'synthetic-token'), delays.append)
        with self.assertRaises(SciSureError) as error: client.request('/api/v1/groups/active')
        self.assertEqual(delays, [1, 2])
        self.assertEqual(error.exception.retry_after, 4)
        self.assertNotIn('synthetic-token', str(error.exception))

    def test_post_and_long_get_delays_require_manual_retry(self):
        for method, delay in [('POST', '2'), ('GET', '60')]:
            calls, waits = [], []
            def transport(*_):
                calls.append(method)
                return 429, b'', {'Retry-After': delay}
            with self.subTest(method=method):
                with self.assertRaises(SciSureError) as error:
                    client_for(transport, waits.append).request('/api/v1/experiments/42/sections', method)
                self.assertEqual(error.exception.status, 429)
                self.assertFalse(error.exception.uncertain)
                self.assertEqual(len(calls), 1)
                self.assertEqual(waits, [])

    def test_rejected_write_vs_unknown_server_error(self):
        for status in (400, 401, 403, 404, 409, 413, 415, 422, 500, 503):
            with self.subTest(status=status):
                with self.assertRaises(SciSureError) as error:
                    client_for(lambda *_: (status, b'synthetic-token')).request('/api/v1/samples', 'POST', {})
                self.assertEqual(error.exception.uncertain, status >= 500)
                self.assertNotIn('synthetic-token', str(error.exception))


class TransferContractTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSciSure()
        self.client = client_for(self.api)
        self.destination = self.client.destination(42)
        self.publisher = Publisher(self.client, self.destination)
        self.source, _, self.revision = review()
        self.approval = self.revision.approve('Synthetic reviewer', 'Checked synthetic source', True)

    def publish(self):
        return self.publisher.publish(self.revision, self.approval, [self.source])

    def test_new_file_section_and_legacy_files_are_both_readable(self):
        receipt = self.publish()
        self.assertEqual(self.api.sections[0]['sectionType'], 'FILE')
        self.api.sections[0]['sectionType'] = 'FILES'
        self.assertEqual(len(history(self.client, self.destination)), 1)
        self.assertEqual(read_review(self.client, self.destination, receipt['section_id'])['state'], 'complete')
        count = self.api.posts
        self.publish()
        self.assertEqual(self.api.posts, count)

    def test_manifest_under_wrong_revision_heading_is_rejected(self):
        receipt = self.publish()
        self.api.sections[0]['sectionHeader'] += '-wrong-revision'
        with self.assertRaisesRegex(SciSureError, 'integrity'):
            read_review(self.client, self.destination, receipt['section_id'])

    def test_rejected_publication_can_be_retried_without_poisoned_state(self):
        rejected = []
        def transport(url, method, headers, body):
            if method == 'POST' and not rejected:
                rejected.append(True)
                return 429, b'', {'Retry-After': '1'}
            return self.api(url, method, headers, body)
        self.client._transport = transport
        with self.assertRaises(SciSureError): self.publish()
        self.assertIn('rejected', self.publisher.operations.values())
        self.assertEqual(self.publish()['state'], 'complete')
        self.assertEqual(len(self.api.sections), 1)

    def test_successful_write_with_failed_verification_is_still_unknown(self):
        reads = deque([None, SciSureError('Read forbidden', False, 403), None])
        writes = []
        def find():
            value = reads.popleft()
            if isinstance(value, Exception): raise value
            return value
        def write():
            writes.append(True)
            return 99
        with self.assertRaises(SciSureError): self.publisher._step('synthetic', find, write)
        self.assertEqual(self.publisher.operations['synthetic'], 'unknown')
        with self.assertRaises(SciSureError): self.publisher._step('synthetic', find, write)
        self.assertEqual(len(writes), 1)

    def test_context_trace_mismatch_cannot_be_published(self):
        preview = self.revision.value()['preview']
        preview['traceability']['dataset']['id'] = 'tampered'
        self.revision = Revision.create(preview, 'Invalid identity declaration')
        self.approval = self.revision.approve('Synthetic', 'Review', True)
        with self.assertRaisesRegex(InputError, 'lineage'): self.publish()
        self.assertEqual(self.api.posts, 0)

    def test_reconnected_publisher_retains_unknown_write_guard(self):
        self.api.fail_next = 'before'
        with self.assertRaises(SciSureError): self.publish()
        replacement = Publisher(self.client, self.destination, self.publisher.operations)
        with self.assertRaises(SciSureError): replacement.publish(self.revision, self.approval, [self.source])
        self.assertEqual(self.api.posts, 1)

    def test_hybrid_source_is_explained_without_contacting_another_host(self):
        receipt = self.publish()
        source_id = receipt['files'][0]['file_id']
        for f in self.api.files[receipt['section_id']]:
            if f['experimentFileID'] == source_id: f['origin'] = 'ONSITE'
        with self.assertRaisesRegex(SciSureError, 'eLABHybrid'):
            read_review(self.client, self.destination, receipt['section_id'], True)
        with self.assertRaisesRegex(SciSureError, 'eLABHybrid'): self.publish()


class OriginalImageTests(unittest.TestCase):
    def test_images_round_trip_with_exact_bytes_and_imaging_context(self):
        # A real 1-pixel PNG plus synthetic binary representations; no image decoding is requested.
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6CwAAAABJRU5ErkJggg==')
        images = [('sample.png', png), ('detector.tiff', b'II*\0' + bytes(range(256))),
            ('photo.jpg', b'\xff\xd8\xff\xe0' + bytes(range(256))), ('vector.svg', b'<svg><script>doNotExecute()</script></svg>')]
        with patch('catalyst_desktop.model.read_artifact', side_effect=AssertionError('Opaque image was parsed')):
            sources = [Source.from_bytes(name, content, parse=False) for name, content in images]
        context = physical_context(technique='Synthetic microscopy / photo', imageContext='Synthetic sample views', scaleReference='Not quantitative')
        preview = build_preview(sources, 'Rochester', 'imaging', context, raw_only=True)
        revision = Revision.create(preview, 'Synthetic image collection')
        approval = revision.approve('Synthetic reviewer', 'Original images and sample identity reviewed', True)
        self.assertEqual(preview['data_status'], 'original_files_only')
        self.assertFalse(preview['normalization']['executed'])
        self.assertFalse(preview['scientific_processing']['executed'])
        self.assertEqual(preview['standardized']['rows'], [])
        self.assertEqual(preview['traceability']['dataset']['subject_id'], context['specimenId'])
        api = FakeSciSure()
        client = client_for(api)
        destination = client.destination(42)
        receipt = Publisher(client, destination).publish(revision, approval, sources)
        loaded = read_review(client, destination, receipt['section_id'], True)
        self.assertEqual([(s.name, s.content) for s in loaded['sources']], images)
        self.assertTrue(all(s.artifact['format'] == 'binary' for s in loaded['sources']))

    def test_supporting_image_does_not_change_standardized_table(self):
        source, profile, original = review()
        image = Source.from_bytes('image.png', b'\x89PNG\r\n\x1a\n' + bytes(range(256)), parse=False)
        preview = build_preview([source, image], 'Rochester', 'synthesis', physical_context(), profile)
        self.assertEqual(preview['standardized'], original.value()['preview']['standardized'])
        self.assertEqual(len(preview['artifacts']), 2)
        self.assertIn('SUPPORTING_ARTIFACTS', [i['code'] for i in preview['validation']['issues']])

    def test_native_computational_files_preserve_without_flat_json_parsing(self):
        nested = b'{"structure":{"atoms":[{"element":"Ce","x":0.0}]}}'
        source = Source.from_bytes('structure.json', nested, parse=False)
        preview = build_preview([source], 'VT', 'computational', computational_context(), raw_only=True)
        self.assertEqual(Revision.create(preview, 'Synthetic structure').approve('VT reviewer', 'Context checked', True)['acknowledged'], True)
        self.assertEqual(source.content, nested)

    def test_files_only_requires_scientific_context_and_cannot_also_run_toolkit(self):
        source = Source.from_bytes('sample.tiff', b'II*\0synthetic', parse=False)
        preview = build_preview([source], 'Rochester', 'imaging', physical_context(), raw_only=True)
        with self.assertRaises(InputError): Revision.create(preview, 'Missing imaging context').approve('Reviewer', 'Check', True)
        with self.assertRaises(InputError): build_preview([source], 'Rochester', 'reactor', {}, toolkit=True, raw_only=True)

    def test_opaque_file_count_size_and_duplicate_names_are_bounded(self):
        with self.assertRaises(InputError): Source.from_bytes('empty.png', b'', parse=False)
        with self.assertRaises(InputError): Source.from_bytes('too-big.tiff', b'x' * (MAX_FILE + 1), parse=False)
        source = Source.from_bytes('sample.png', b'\x00\xff', parse=False)
        with self.assertRaises(InputError): check_sources([source] * 7)
        with self.assertRaises(InputError): check_sources([source, Source.from_bytes('SAMPLE.PNG', b'other', parse=False)])


class PrecisionTests(unittest.TestCase):
    def test_small_offset_temperature_keeps_all_supported_source_digits(self):
        with localcontext() as ctx:
            ctx.prec = 800
            self.assertEqual(Decimal(convert('1e-300', 'degC')), Decimal('273.15') + Decimal('1e-300'))
        source = Source.from_bytes('temperature.csv', b'temperature\n1e-300\n')
        _, _, revision = review(source, rules=[dict(source='temperature', target='temperature_K', unit='degC')])
        self.assertEqual(revision.value()['preview']['standardized']['rows'][0]['temperature_K'], convert('1e-300', 'degC'))
        revision.approve('Synthetic reviewer', 'Precision checked', True)

    def test_xlsx_error_cell_is_never_an_accepted_text_value(self):
        data = workbook({'Table': {'A1': 'name', 'A2': '001'}})
        updated = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(updated, 'w') as out:
            for item in original.infolist():
                content = original.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    content = content.replace(b'<c r="A2" t="inlineStr"><is><t>001</t></is></c>', b'<c r="A2" t="e"><v>#VALUE!</v></c>')
                out.writestr(item.filename, content)
        source = Source.from_bytes('error.xlsx', updated.getvalue())
        _, _, revision = review(source, rules=[dict(source='name', target='species', unit='text')])
        self.assertIsNone(revision.value()['preview']['standardized']['rows'][0]['species'])
        with self.assertRaises(InputError): revision.approve('Synthetic reviewer', 'Checked', True)


class NativeSetupAPI(FakeSciSure):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.draft = False
        self.deny_fields = False

    def __call__(self, url, method, headers, body):
        self.calls.append((urlsplit(url).path, method))
        path = urlsplit(url).path
        values = {
            '/api/v1/users/getCurrentUserInfo': dict(userID=5, fullName='Synthetic account', groupId=7, isBlocked=False, permissions={'samples': ['view']}),
            '/api/v1/experiments/42/collaborators': dict(data=[dict(userID=6, fullName='Synthetic collaborator')]),
            '/api/v1/sampleTypes': dict(data=[dict(sampleTypeID=10)]),
            '/api/v1/sampleTypes/10': dict(sampleTypeID=10, groupID=7, name='Synthetic catalyst', quantityRequired=True, defaultUnit='MilliGram'),
            '/api/v1/sampleTypes/10/meta': dict(data=[dict(sampleTypeMetaID=11, sampleTypeID=10, key='Origin lab', sampleDataType='COMBO', required=True, optionValues=['Rochester', 'SLAC']),
                dict(sampleTypeMetaID=12, sampleTypeID=10, key='Synthetic script', sampleDataType='TEXT', required=False, validationScript='doNotExecute()')]),
            '/api/v1/samples/20': dict(sampleID=20, name='Synthetic sample', sampleTypeID=10, altID=physical_context()['specimenId'], parentSampleID=19, parents=[dict(sampleID=19)], archived=False),
            '/api/v1/protocols/version/30': dict(protID=31, protVersionID=30, version=2, name='Synthetic SOP', groupID=7, draft=self.draft, deleted=False),
        }
        if path.endswith('/meta') and self.deny_fields: return 403, b'synthetic-token'
        if path in values: return self.response(deepcopy(values[path]))
        return super().__call__(url, method, headers, body)


class NativeSetupTests(unittest.TestCase):
    def test_reads_tenant_specific_bindings_and_exact_protocol_version_without_writes(self):
        api = NativeSetupAPI()
        api.experiment['signatureStatus'] = 'Signed'
        report = inspect_setup(client_for(api), 7, experiment_id=42, sample_id=20, protocol_version_id=30)
        self.assertEqual(report['sample_types'][0]['fields'][0]['id'], 11)
        self.assertEqual(report['sample_types'][0]['fields'][0]['options'], ['Rochester', 'SLAC'])
        self.assertTrue(report['sample_types'][0]['quantity_required'])
        self.assertTrue(report['sample_types'][0]['fields'][1]['has_custom_validation'])
        self.assertEqual(report['sample_reference']['external_id'], physical_context()['specimenId'])
        self.assertEqual(report['protocol_reference']['version_id'], 30)
        self.assertTrue(report['protocol_reference']['published'])
        self.assertFalse(report['native_inventory_writes_enabled'])
        self.assertEqual({method for _, method in api.calls}, {'GET'})
        self.assertIn('Origin lab (COMBO)', setup_summary(report))
        self.assertNotIn('doNotExecute', json.dumps(report))
        self.assertNotIn('synthetic-token', json.dumps(report))

    def test_draft_protocol_is_not_reported_as_published(self):
        api = NativeSetupAPI()
        api.draft = True
        report = inspect_setup(client_for(api), 7, protocol_version_id=30)
        self.assertFalse(report['protocol_reference']['published'])

    def test_field_permission_failure_is_visible_and_not_a_readiness_pass(self):
        api = NativeSetupAPI()
        api.deny_fields = True
        report = inspect_setup(client_for(api), 7)
        self.assertIsNone(report['sample_types'][0]['fields'])
        self.assertIn('not verified', [c['status'] for c in report['checks']])
        self.assertFalse(report['native_inventory_writes_enabled'])

    def test_active_group_change_prevents_inspection(self):
        api = NativeSetupAPI()
        with self.assertRaisesRegex(SciSureError, 'active group changed'):
            inspect_setup(client_for(api), 999)
        self.assertEqual(len(api.calls), 1)
