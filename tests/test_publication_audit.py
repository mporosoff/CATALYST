"""Regression checks for transfer races, cross-session recovery, and API envelopes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.configuration import SchemaInstaller, plan_configuration
from catalyst_desktop.model import InputError
from catalyst_desktop.publication import MANIFEST, RECEIPT, PREFIX, Publisher, history, read_review
from catalyst_desktop.scisure import SANDBOX, SciSureClient, SciSureError
from test_configuration import SchemaAPI
from test_desktop import FakeSciSure, review


class TransferAuditTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSciSure()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destination = self.client.destination(42)
        self.source, self.profile, self.revision = review()
        self.approval = self.revision.approve('Synthetic reviewer', 'Checked source values', True)
        self.publisher = Publisher(self.client, self.destination)

    def publish(self):
        return self.publisher.publish(self.revision, self.approval, [self.source])

    def test_string_section_and_file_ids_round_trip(self):
        def transport(*args):
            status, body = self.api(*args)
            path = urlsplit(args[0]).path
            if args[1] == 'GET' and (path.endswith('/sections') or path.endswith('/files')):
                value = json.loads(body)
                for item in value['data']:
                    for key in ('expJournalID', 'experimentFileID'):
                        if key in item:
                            item[key] = str(item[key])
                return self.api.response(value)
            return status, body
        self.client._transport = transport
        receipt = self.publish()
        loaded = read_review(self.client, self.destination, receipt['section_id'], True)
        self.assertEqual(loaded['sources'][0].content, self.source.content)
        self.assertEqual(loaded['state'], 'complete')

    def test_changed_file_list_during_read_never_returns_a_stale_complete_review(self):
        receipt = self.publish()
        def transport(url, *args):
            result = self.api(url, *args)
            if urlsplit(url).path.endswith('/' + str(receipt['manifest_id'])):
                original = next(f for f in self.api.files[receipt['section_id']]
                    if f['experimentFileID'] == receipt['files'][0]['file_id'])
                original['realName'] = 'changed-after-list.csv'
            return result
        self.client._transport = transport
        with self.assertRaisesRegex(SciSureError, 'changed during retrieval'):
            read_review(self.client, self.destination, receipt['section_id'])

    def test_section_archived_during_original_download_is_detected(self):
        receipt = self.publish()
        def transport(url, *args):
            result = self.api(url, *args)
            if urlsplit(url).path.endswith('/' + str(receipt['files'][0]['file_id'])):
                self.api.sections[0]['deleted'] = True
            return result
        self.client._transport = transport
        with self.assertRaisesRegex(SciSureError, 'no longer active'):
            read_review(self.client, self.destination, receipt['section_id'], True)

    def test_history_rechecks_destination_after_listing(self):
        receipt = self.publish()
        def transport(url, *args):
            result = self.api(url, *args)
            if urlsplit(url).path == f'/api/v1/experiments/sections/{receipt["section_id"]}/files':
                self.api.experiment['studyID'] = 999
            return result
        self.client._transport = transport
        with self.assertRaisesRegex(SciSureError, 'moved'):
            history(self.client, self.destination)

    def test_late_original_corruption_cannot_be_reported_as_publish_success(self):
        def transport(url, method, *args):
            result = self.api(url, method, *args)
            if method == 'POST' and parse_qs(urlsplit(url).query).get('fileName') == [RECEIPT]:
                receipt = json.loads(args[-1])
                original_id = receipt['files'][0]['file_id']
                self.api.content[original_id] = b'X' * len(self.source.content)
            return result
        self.client._transport = transport
        with self.assertRaisesRegex(SciSureError, 'integrity check'):
            self.publish()

    def test_section_archived_while_writing_receipt_cannot_report_success(self):
        def transport(url, method, *args):
            result = self.api(url, method, *args)
            if method == 'POST' and parse_qs(urlsplit(url).query).get('fileName') == [RECEIPT]:
                self.api.sections[0]['deleted'] = True
            return result
        self.client._transport = transport
        with self.assertRaisesRegex(SciSureError, 'no longer active'):
            self.publish()

    def test_profile_conflict_arriving_during_upload_prevents_completion(self):
        empty = dict(entries=[], pending=[], profiles=[])
        changed = deepcopy(self.profile)
        changed['rules'][0]['unit'] = 'kg'
        collision = dict(entries=[], pending=[], profiles=[changed])
        with patch('catalyst_desktop.catalog.load_catalog', side_effect=[empty, collision]):
            with self.assertRaisesRegex(InputError, 'different rules'):
                self.publish()
        self.assertTrue(self.api.sections)
        self.assertNotIn(RECEIPT, [f['realName'] for f in self.api.files[self.api.sections[0]['expJournalID']]])

    def test_orphan_revision_in_another_experiment_reserves_its_id(self):
        catalog = dict(entries=[], pending=[], profiles=[], orphan_sections=[dict(
            revision_id=self.revision.value()['id'], destination=dict(self.destination, experiment_id=43))])
        with patch('catalyst_desktop.catalog.load_catalog', return_value=catalog):
            with self.assertRaisesRegex(InputError, 'another destination'):
                self.publish()
        self.assertEqual(self.api.posts, 0)

    def test_own_orphan_revision_can_be_resumed_after_restart(self):
        self.api.sections.append(dict(expJournalID=80, sectionHeader=PREFIX + self.revision.value()['id'],
            sectionType='FILE', deleted=False))
        self.api.files[80] = []
        receipt = self.publish()
        self.assertEqual(receipt['section_id'], 80)
        self.assertEqual(len(self.api.sections), 1)
        self.assertEqual(load_catalog(self.client, 7)['entries'][0]['revision_id'], self.revision.value()['id'])

    def test_reconnect_after_experiment_rename_reuses_original_receipt(self):
        receipt = self.publish()
        posts = self.api.posts
        self.api.experiment['name'] = 'Renamed experiment'
        publisher = Publisher(self.client, self.client.destination(42))
        retried = publisher.publish(self.revision, self.approval, [self.source])
        self.assertEqual(retried['receipt_id'], receipt['receipt_id'])
        self.assertEqual(retried['destination'], receipt['destination'])
        self.assertEqual(self.api.posts, posts)

    def test_incomplete_transfer_resumes_after_experiment_rename(self):
        def transport(url, method, *args):
            if method == 'POST' and parse_qs(urlsplit(url).query).get('fileName') == ['01-' + self.source.name]:
                return 403, b''
            return self.api(url, method, *args)
        self.client._transport = transport
        with self.assertRaises(SciSureError):
            self.publish()
        self.client._transport = self.api
        self.api.experiment['name'] = 'Renamed experiment'
        publisher = Publisher(self.client, self.client.destination(42))
        receipt = publisher.publish(self.revision, self.approval, [self.source])
        self.assertEqual(len(self.api.sections), 1)
        self.assertEqual(receipt['state'], 'complete')
        self.assertEqual(receipt['destination']['experiment_name'], self.destination['experiment_name'])

    def test_retry_cannot_replace_an_existing_approval(self):
        self.publish()
        posts = self.api.posts
        approval = self.revision.approve('Another reviewer', 'Different approval', True)
        with self.assertRaisesRegex(InputError, 'different content or approval'):
            self.publisher.publish(self.revision, approval, [self.source])
        self.assertEqual(self.api.posts, posts)

    def test_orphan_and_completed_duplicate_revision_block_catalog_reuse(self):
        self.publish()
        self.api.sections.append(dict(self.api.sections[0], expJournalID=999))
        self.api.files[999] = []
        with self.assertRaisesRegex(SciSureError, 'more than one'):
            load_catalog(self.client, 7)

    def test_archived_file_metadata_is_not_trusted_as_complete(self):
        receipt = self.publish()
        source = next(f for f in self.api.files[receipt['section_id']]
            if f['experimentFileID'] == receipt['files'][0]['file_id'])
        source['archived'] = True
        with self.assertRaisesRegex(SciSureError, 'archived or deleted'):
            read_review(self.client, self.destination, receipt['section_id'])


class ConfigurationEnvelopeAuditTests(unittest.TestCase):
    def setUp(self):
        self.api = SchemaAPI()
        self.client = SciSureClient('synthetic-token', transport=self.api)

    def test_non_object_active_group_fails_with_actionable_error(self):
        self.client._transport = lambda *_: self.api.response([])
        with self.assertRaises(SciSureError):
            plan_configuration(self.client, 7)
        self.assertEqual(self.api.native_posts, 0)

    def test_non_object_account_never_receives_schema_writes(self):
        plan = plan_configuration(self.client, 7)
        def transport(url, *args):
            if urlsplit(url).path == '/api/v1/users/getCurrentUserInfo':
                return self.api.response([])
            return self.api(url, *args)
        self.client._transport = transport
        with self.assertRaises(SciSureError):
            SchemaInstaller(self.client, 7).apply(plan)
        self.assertEqual(self.api.native_posts, 0)

    def test_malformed_controlled_options_are_conflicts_not_type_errors(self):
        ready = SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))
        field = next(f for f in self.api.metas[ready['sample_type_id']] if f['key'] == 'origin_lab')
        for malformed in ([{}], 'Rochester', ['Rochester', 'Rochester']):
            with self.subTest(malformed=malformed):
                field['optionValues'] = malformed
                self.assertTrue(plan_configuration(self.client, 7)['conflicts'])

    def test_malformed_field_section_is_a_conflict(self):
        ready = SchemaInstaller(self.client, 7).apply(plan_configuration(self.client, 7))
        field = self.api.metas[ready['sample_type_id']][0]
        field['sampleTypeSection'] = ['invalid']
        self.assertTrue(plan_configuration(self.client, 7)['conflicts'])


class TenantIntegrationAuditTests(unittest.TestCase):
    """Different servers may legitimately return the same numeric remote IDs."""
    FIRST = 'https://first.scisure.example'
    SECOND = 'https://second.scisure.example'

    def client(self, api, origin):
        calls = []
        def transport(url, method, headers, body):
            self.assertTrue(url.startswith(origin + '/api/v1/'))
            calls.append((url, method))
            # Adapt only the synthetic fixture's expected hostname, without DNS.
            return api(SANDBOX + url[len(origin):], method, headers, body)
        return SciSureClient('synthetic-token', origin=origin, transport=transport), calls

    def test_custom_tenant_round_trip_and_catalog_are_shared_by_connected_clients(self):
        api = FakeSciSure()
        writer, _ = self.client(api, self.FIRST)
        source, _, revision = review()
        destination = writer.destination(42)
        approval = revision.approve('Synthetic reviewer', 'Checked original', True)
        receipt = Publisher(writer, destination).publish(revision, approval, [source])
        self.assertEqual(receipt['destination']['tenant'], self.FIRST)
        reader, _ = self.client(api, self.FIRST)
        loaded = read_review(reader, reader.destination(42, writable=False), receipt['section_id'], True)
        self.assertEqual(loaded['sources'][0].content, source.content)
        self.assertEqual(loaded['destination']['tenant'], self.FIRST)
        self.assertEqual(load_catalog(reader, 7)['entries'][0]['revision_id'], revision.value()['id'])

    def test_destination_from_another_server_is_rejected_before_any_request(self):
        first, _ = self.client(FakeSciSure(), self.FIRST)
        second, requests = self.client(FakeSciSure(), self.SECOND)
        destination = first.destination(42)
        source, _, revision = review()
        approval = revision.approve('Synthetic reviewer', 'Checked original', True)
        for operation in (
                lambda: history(second, destination),
                lambda: read_review(second, destination, 101),
                lambda: Publisher(second, destination).publish(revision, approval, [source])):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(SciSureError, 'different SciSure tenant'):
                    operation()
        self.assertEqual(requests, [])

    def test_copied_foreign_packet_does_not_match_same_numeric_ids(self):
        api = FakeSciSure()
        first, _ = self.client(api, self.FIRST)
        source, _, revision = review()
        approval = revision.approve('Synthetic reviewer', 'Checked original', True)
        receipt = Publisher(first, first.destination(42)).publish(revision, approval, [source])
        # Model a copied database: IDs and bytes agree, but provenance names FIRST.
        second, _ = self.client(api, self.SECOND)
        with self.assertRaisesRegex(SciSureError, 'format or integrity'):
            read_review(second, second.destination(42), receipt['section_id'], True)
        with self.assertRaises(SciSureError):
            load_catalog(second, 7)

    def test_reviewed_configuration_cannot_be_applied_to_another_server(self):
        first_api, second_api = SchemaAPI(), SchemaAPI()
        first, _ = self.client(first_api, self.FIRST)
        second, _ = self.client(second_api, self.SECOND)
        first_plan = plan_configuration(first, 7)
        self.assertEqual(first_plan['workspace']['tenant'], self.FIRST)
        with self.assertRaisesRegex(InputError, 'changed since review'):
            SchemaInstaller(second, 7).apply(first_plan)
        self.assertEqual(second_api.native_posts, 0)
        ready = SchemaInstaller(second, 7).apply(plan_configuration(second, 7))
        self.assertTrue(ready['ready'])
        self.assertEqual(ready['workspace']['tenant'], self.SECOND)
        self.assertEqual(first_api.native_posts, 0)


if __name__ == '__main__':
    unittest.main()
