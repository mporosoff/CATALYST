"""Tenant selection, token boundaries and API-contract regressions; synthetic only."""
from collections import deque
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from catalyst_desktop.credentials import CredentialError, SERVICE, forget_token, load_token, save_token
from catalyst_desktop.integration import inspect_setup
from catalyst_desktop.publication import Publisher, read_review
from catalyst_desktop.scisure import SANDBOX, SciSureClient, SciSureError, tenant_origin
from test_api_contract import NativeSetupAPI
from test_desktop import FakeSciSure, review


OTHER = 'https://research.example.edu'


class TenantTests(unittest.TestCase):
    def test_default_and_explicit_https_origins_are_canonical(self):
        self.assertEqual(SciSureClient('synthetic-token').origin, SANDBOX)
        for value in (OTHER, OTHER + '/', ' HTTPS://RESEARCH.EXAMPLE.EDU:443/ '):
            self.assertEqual(tenant_origin(value), OTHER)
        self.assertEqual(tenant_origin('https://my-lab.scisure.com'), 'https://my-lab.scisure.com')

    def test_unsafe_or_ambiguous_origins_never_receive_a_token(self):
        calls = []
        for value in (None, {}, '', 'research.example.edu', 'http://research.example.edu',
                'https://research.example.edu/sign-in', OTHER + '?', OTHER + '#', OTHER + '//',
                'https://name:password@research.example.edu', OTHER + '@other.example.edu',
                OTHER + ':8443', OTHER + ':', OTHER + ':0443', OTHER + ':wrong',
                'https://localhost', 'https://lab.localhost', 'https://lab.local', 'https://lab.internal',
                'https://127.0.0.1', 'https://192.168.1.1', 'https://[::1]', 'https://2130706433',
                'https://research.example.edu\\@other.example.edu', 'https://%72esearch.example.edu',
                'https://reseаrch.example.edu', 'https://research..example.edu', 'https://-lab.example.edu',
                'https://research.example.edu.', OTHER + '\n', 'https://research.\texample.edu'):
            with self.subTest(value=value):
                with self.assertRaises(SciSureError):
                    SciSureClient('synthetic-token', value, lambda *_: calls.append(True))
        self.assertEqual(calls, [])

    def test_custom_tenant_complete_upload_download_and_token_boundary(self):
        api, requests = FakeSciSure(), []
        def transport(url, method, headers, body):
            self.assertEqual(urlsplit(url).netloc, 'research.example.edu')
            requests.append((url, method))
            return api(SANDBOX + url[len(OTHER):], method, headers, body)
        client = SciSureClient('synthetic-token', OTHER, transport)
        self.assertEqual(client.check_connection()['group_id'], 7)
        destination = client.destination(42)
        source, _, revision = review()
        approval = revision.approve('Synthetic reviewer', 'Synthetic review', True)
        receipt = Publisher(client, destination).publish(revision, approval, [source])
        restored = read_review(client, destination, receipt['section_id'], True)
        self.assertEqual(restored['sources'][0].content, source.content)
        self.assertEqual(receipt['destination']['tenant'], OTHER)
        self.assertTrue(any(method == 'POST' for _, method in requests))
        count = len(requests)
        with self.assertRaisesRegex(SciSureError, 'different SciSure tenant'):
            client.verify_destination(dict(destination, tenant=SANDBOX))
        self.assertEqual(len(requests), count)

    def test_redirect_never_forwards_token_or_replays_upload(self):
        class RedirectingOpener:
            def __init__(self): self.calls = []
            def open(self, request, timeout):
                self.calls.append(request)
                raise HTTPError(request.full_url, 302, 'Found', {'Location': 'https://other.example.edu/api/v1/experiments'}, None)
        for method in ('GET', 'POST'):
            client = SciSureClient('synthetic-token', OTHER)
            client._opener = opener = RedirectingOpener()
            with self.assertRaisesRegex(SciSureError, 'redirect') as caught:
                client.request('/api/v1/experiments', method)
            self.assertEqual(caught.exception.uncertain, method == 'POST')
            self.assertEqual(len(opener.calls), 1)
            self.assertTrue(opener.calls[0].full_url.startswith(OTHER + '/api/v1/'))


class CredentialBoundaryTests(unittest.TestCase):
    class Vault:
        def __init__(self): self.values = {}
        def get_password(self, service, key): return self.values.get((service, key))
        def set_password(self, service, key, value): self.values[service, key] = value
        def delete_password(self, service, key): del self.values[service, key]
        def __bool__(self): return False

    def test_saved_tokens_remain_separate_and_sandbox_keys_stay_compatible(self):
        vault = self.Vault()
        vault.values[SERVICE, SANDBOX] = 'synthetic-sandbox-token'
        with patch('catalyst_desktop.credentials.system_store', side_effect=AssertionError('Unexpected system vault')):
            save_token('HTTPS://RESEARCH.EXAMPLE.EDU:443/', ' synthetic-other-token ', vault)
            self.assertEqual(load_token(OTHER, vault), 'synthetic-other-token')
            self.assertEqual(load_token(SANDBOX + '/', vault), 'synthetic-sandbox-token')
            forget_token(OTHER + '/', vault)
            self.assertIsNone(load_token(OTHER, vault))
            self.assertEqual(load_token(SANDBOX, vault), 'synthetic-sandbox-token')

    def test_invalid_credentials_and_origins_do_not_reach_store(self):
        vault = self.Vault()
        for value in ('', 'bad token', 'bad\ntoken', None, 'x' * 8193):
            with self.assertRaises(CredentialError): save_token(OTHER, value, vault)
        for operation in (lambda: save_token('http://research.example.edu', 'synthetic-token', vault),
                lambda: load_token('http://research.example.edu', vault),
                lambda: forget_token('http://research.example.edu', vault)):
            with self.assertRaises(CredentialError): operation()
        self.assertEqual(vault.values, {})
        vault.values[SERVICE, OTHER] = 'bad\ntoken'
        with self.assertRaises(CredentialError): load_token(OTHER, vault)


class APIAuditTests(unittest.TestCase):
    def test_pagination_parameters_include_empty_values_and_documented_aliases(self):
        client = SciSureClient('synthetic-token', transport=lambda *_: self.fail('Unexpected transfer'))
        for query in ('%24page=', '%24records', '$PAGE=0', 'opts.paging.currentPage=1', 'opts.paging.maxRecords=1'):
            with self.subTest(query=query):
                with self.assertRaisesRegex(SciSureError, 'Pagination parameters'):
                    client.list('/api/v1/experiments?' + query)

    def test_invalid_paths_are_actionable_and_never_reach_transport(self):
        client = SciSureClient('synthetic-token', transport=lambda *_: self.fail('Unexpected transfer'))
        for path in (None, {}, b'/api/v1/experiments', '//other.example.edu/api/v1/experiments'):
            for operation in (client.request, client.list):
                with self.assertRaises(SciSureError): operation(path)

    def test_http_date_rate_limits_obey_the_server_and_never_repeat_posts(self):
        for method, delay in (('GET', 3), ('GET', 12), ('POST', 3)):
            replies = deque([(429, b'', {'Retry-After': f'Thu, 01 Jan 1970 00:00:{delay:02} GMT'}), (200, b'42')])
            waits = []
            client = SciSureClient('synthetic-token', transport=lambda *_: replies.popleft(), sleep=waits.append)
            with patch('catalyst_desktop.scisure.time.time', return_value=0):
                if method == 'GET' and delay <= 10:
                    self.assertEqual(client.request('/api/v1/experiments', method), 42)
                    self.assertEqual(waits, [delay])
                else:
                    with self.assertRaises(SciSureError) as caught:
                        client.request('/api/v1/experiments', method)
                    self.assertEqual(caught.exception.retry_after, delay)
                    self.assertEqual(waits, [])
                    self.assertEqual(len(replies), 1)

    def test_permission_denial_and_invalid_token_have_distinct_guidance(self):
        for status, expected in ((401, 'belongs to this server'), (403, 'does not have permission')):
            client = SciSureClient('synthetic-token', transport=lambda *_: (status, b'synthetic-token'))
            with self.assertRaisesRegex(SciSureError, expected) as caught:
                client.request('/api/v1/experiments', 'POST', {})
            self.assertFalse(caught.exception.uncertain)
            self.assertNotIn('synthetic-token', str(caught.exception))


class SetupAuditTests(unittest.TestCase):
    def client(self, mutate):
        api = NativeSetupAPI()
        def transport(url, method, headers, body):
            response = api(url, method, headers, body)
            return mutate(urlsplit(url).path, response, api)
        return SciSureClient('synthetic-token', transport=transport)

    def test_setup_rejects_mismatched_sample_type_and_metadata_bindings(self):
        for path, record in (
                ('/api/v1/sampleTypes/10', dict(sampleTypeID=11, groupID=7)),
                ('/api/v1/sampleTypes/10', dict(sampleTypeID=10, groupID=8)),
                ('/api/v1/sampleTypes/10/meta', dict(data=[dict(sampleTypeMetaID=11, sampleTypeID=99)]))):
            client = self.client(lambda route, response, api: api.response(record) if route == path else response)
            with self.assertRaisesRegex(SciSureError, 'different sample type'):
                inspect_setup(client, 7)

    def test_setup_rejects_protocol_from_another_group(self):
        client = self.client(lambda route, response, api: api.response(dict(protVersionID=30, groupID=8))
            if route == '/api/v1/protocols/version/30' else response)
        with self.assertRaisesRegex(SciSureError, 'protocol version or group'):
            inspect_setup(client, 7, protocol_version_id=30)

    def test_malformed_read_is_not_reported_as_success(self):
        client = self.client(lambda route, response, api: (200, b'[]')
            if route == '/api/v1/users/getCurrentUserInfo' else response)
        report = inspect_setup(client, 7)
        self.assertEqual(report['checks'][0]['status'], 'not verified')
        self.assertNotIn('token_account', report)

    def test_malformed_final_group_check_raises_actionable_error(self):
        reads = []
        def mutate(route, response, api):
            if route == '/api/v1/groups/active':
                reads.append(True)
                if len(reads) == 2: return 200, b'[]'
            return response
        with self.assertRaisesRegex(SciSureError, 'unsupported record'):
            inspect_setup(self.client(mutate), 7)


if __name__ == '__main__':
    unittest.main()
