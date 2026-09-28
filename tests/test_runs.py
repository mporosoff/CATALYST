"""Run creation checks with an in-memory implementation of the documented API."""
from copy import deepcopy
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from catalyst_desktop.model import InputError
from catalyst_desktop.runs import RunWorkspace, creation_summary
from catalyst_desktop.scisure import SciSureClient, SciSureError
from catalyst_desktop.runs_gui import RunUI


class RunsAPI:
    def __init__(self):
        self.group = 7
        self.projects = [dict(projectID=10, groupID=7, name='Catalysis', notes='Research', active=True)]
        self.studies = [dict(studyID=20, projectID=10, groupID=7, name='Supported catalysts', deleted=False, approve='NOTREQUIRED')]
        self.experiments = []
        self.writes = []
        self.failure = None
        self.drop_project_response = False
        self.corrupt_readback = False

    def __call__(self, url, method, headers, raw):
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        result = None
        if path == '/api/v1/groups/active': result = dict(groupID=self.group, name='Synthetic group')
        elif method == 'POST':
            data = json.loads(raw)
            self.writes.append((path, data))
            if self.failure == 'unknown': raise OSError('synthetic interruption')
            if self.failure == 'denied': return 403, b''
            if path == '/api/v1/projects':
                result = dict(data, projectID=100 + len(self.projects), groupID=self.group, active=True)
                self.projects.append(result)
                if self.drop_project_response: result = None
            elif path == '/api/v1/studies':
                result = dict(data, studyID=200 + len(self.studies), groupID=self.group, deleted=False)
                self.studies.append(result)
            elif path == '/api/v1/experiments':
                study = next(s for s in self.studies if s['studyID'] == data['studyID'])
                result = 300 + len(self.experiments)
                self.experiments.append(dict(data, experimentID=result, projectID=study['projectID'], groupID=self.group,
                    deleted=False, template=False, signatureStatus='None'))
            else: raise AssertionError(path)
        elif path.rsplit('/', 1)[0] == '/api/v1/experiments':
            result = deepcopy(next(e for e in self.experiments if e['experimentID'] == int(path.rsplit('/', 1)[1])))
            if self.corrupt_readback: result['studyID'] = 99
        else:
            rows = getattr(self, path.rsplit('/', 1)[1])
            for key in ('projectID', 'studyID'):
                if key in query: rows = [r for r in rows if r.get(key) == int(query[key][0])]
            if 'searchName' in query: rows = [r for r in rows if query['searchName'][0].lower() in r.get('name', '').lower()]
            size, page = int(query.get('$records', ['100'])[0]), int(query.get('$page', ['0'])[0])
            result = dict(data=rows[page * size:(page + 1) * size], totalRecords=len(rows), maxRecords=size, currentPage=page)
        return 200, json.dumps(result).encode() if result is not None else b''


class RunTests(unittest.TestCase):
    def setUp(self):
        self.api = RunsAPI()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.workspace = RunWorkspace(self.client, 7)

    def test_plan_is_read_only_and_new_run_inherits_collaborators(self):
        plan = self.workspace.plan('run', 'XRD run 01', 10, 20)
        self.assertEqual(self.api.writes, [])
        self.assertIn('collaborators inherited', creation_summary(plan))
        result = self.workspace.create(plan)
        self.assertEqual(result['experiment_name'], 'XRD run 01')
        self.assertEqual((result['project_id'], result['study_id']), (10, 20))
        self.assertEqual(self.api.writes, [('/api/v1/experiments', dict(name='XRD run 01', studyID=20,
            status='PENDING', autoCollaborate=True))])
        self.assertEqual(self.workspace.create(plan), result)
        self.assertEqual(len(self.api.writes), 1)

    def test_project_study_then_run_are_explicit_independent_actions(self):
        project = self.workspace.create(self.workspace.plan('project', 'New project', notes='Purpose of this work'))
        study = self.workspace.create(self.workspace.plan('study', 'New study', project['projectID'], approve='BYSTUDYMANAGER'))
        run = self.workspace.create(self.workspace.plan('run', 'New run', project['projectID'], study['studyID']))
        self.assertEqual(run['project_id'], project['projectID'])
        self.assertEqual(study['approve'], 'BYSTUDYMANAGER')
        self.assertEqual(len(self.api.writes), 3)

    def test_project_success_without_response_body_is_verified_by_fresh_query(self):
        self.api.drop_project_response = True
        project = self.workspace.create(self.workspace.plan('project', 'New project', notes='Required notes'))
        self.assertEqual(project['name'], 'New project')

    def test_missing_project_notes_prevents_write(self):
        with self.assertRaisesRegex(InputError, 'project notes'):
            self.workspace.plan('project', 'Project without purpose')
        self.assertFalse(self.api.writes)

    def test_changed_parent_requires_new_review_without_write(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.studies[0]['name'] = 'Changed study'
        with self.assertRaisesRegex(InputError, 'changed after review'):
            self.workspace.create(plan)
        self.assertFalse(self.api.writes)

    def test_changed_group_and_archived_project_prevent_creation(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.group = 8
        with self.assertRaises(SciSureError): self.workspace.create(plan)
        self.api.group = 7
        self.api.projects[0]['active'] = False
        with self.assertRaises(InputError): self.workspace.create(plan)
        self.assertFalse(self.api.writes)

    def test_study_must_belong_to_selected_project(self):
        self.api.projects.append(dict(projectID=11, groupID=7, name='Other', active=True))
        with self.assertRaisesRegex(InputError, 'study is unavailable'):
            self.workspace.plan('run', 'Run', 11, 20)

    def test_foreign_group_records_are_filtered(self):
        self.api.projects.append(dict(projectID=11, groupID=8, name='Foreign', active=True))
        self.assertEqual([r['projectID'] for r in self.workspace.projects()], [10])

    def test_duplicate_name_requires_existing_selection(self):
        with self.assertRaisesRegex(InputError, 'already exists'):
            self.workspace.plan('project', 'catalysis', notes='Notes')
        self.assertFalse(self.api.writes)

    def test_uncertain_creation_is_never_repeated(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.failure = 'unknown'
        with self.assertRaises(SciSureError) as first: self.workspace.create(plan)
        self.assertTrue(first.exception.uncertain)
        self.api.failure = None
        with self.assertRaisesRegex(SciSureError, 'will not repeat'): self.workspace.create(plan)
        self.assertEqual(len(self.api.writes), 1)

    def test_readback_mismatch_blocks_retry_after_accepted_creation(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.corrupt_readback = True
        with self.assertRaisesRegex(SciSureError, 'accepted') as first: self.workspace.create(plan)
        self.assertTrue(first.exception.uncertain)
        with self.assertRaises(SciSureError): self.workspace.create(plan)
        self.assertEqual(len(self.api.writes), 1)

    def test_explicit_permission_rejection_can_be_retried_after_access_fixed(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.failure = 'denied'
        with self.assertRaises(SciSureError) as first: self.workspace.create(plan)
        self.assertFalse(first.exception.uncertain)
        self.api.failure = None
        self.assertEqual(self.workspace.create(plan)['experiment_name'], 'Run')

    def test_unknown_creation_guard_survives_parent_rename_and_new_client(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.api.failure = 'unknown'
        with self.assertRaises(SciSureError): self.workspace.create(plan)
        self.api.failure = None
        self.api.studies[0]['name'] = 'Renamed study'
        renewed = RunWorkspace(SciSureClient('renewed-synthetic-token', transport=self.api), 7, self.workspace.operations)
        with self.assertRaisesRegex(SciSureError, 'will not repeat'):
            renewed.create(renewed.plan('run', 'Run', 10, 20))
        self.assertEqual(len(self.api.writes), 1)

    def test_reusing_successful_creation_checks_run_has_not_moved(self):
        plan = self.workspace.plan('run', 'Run', 10, 20)
        self.workspace.create(plan)
        self.api.experiments[0]['studyID'] = 55
        with self.assertRaisesRegex(SciSureError, 'moved'): self.workspace.create(plan)
        self.assertEqual(len(self.api.writes), 1)

    def routing_fixture(self):
        first = self.workspace.create(self.workspace.plan('run', 'First run', 10, 20))
        second = self.workspace.create(self.workspace.plan('run', 'Second run', 10, 20))
        app = SimpleNamespace(client=self.client, group_id=7, destination=second, transfer_operations={})
        return app, first, second

    def publisher(self, app, **preview):
        return RunUI.publisher_for_revision(app, SimpleNamespace(value=lambda: dict(preview=preview)))

    def test_record_uses_its_own_reviewed_run_after_destination_switch(self):
        app, first, second = self.routing_fixture()
        publisher = self.publisher(app, publication_destination=first)
        self.assertEqual(publisher.destination, first)
        self.assertNotEqual(publisher.destination, app.destination)
        self.assertIs(publisher.operations, app.transfer_operations[(first['tenant'], 7, first['experiment_id'])])

    def test_legacy_record_uses_current_destination(self):
        app, first, second = self.routing_fixture()
        self.assertEqual(self.publisher(app).destination, second)

    def test_native_only_review_resolves_its_pinned_run(self):
        app, first, second = self.routing_fixture()
        target = {k: first[k] for k in ('tenant', 'group_id', 'experiment_id')}
        self.assertEqual(self.publisher(app, native_inventory_plan=target).destination, first)

    def test_foreign_or_conflicting_destinations_are_rejected(self):
        app, first, second = self.routing_fixture()
        with self.assertRaises(InputError): self.publisher(app, publication_destination=dict(first, tenant='https://other.example.org'))
        with self.assertRaises(InputError): self.publisher(app, publication_destination=dict(first, group_id=8))
        with self.assertRaises(InputError): self.publisher(app, publication_destination=first, native_inventory_plan=second)

    def test_staged_record_destination_must_still_be_unsigned(self):
        app, first, second = self.routing_fixture()
        self.api.experiments[0]['signatureStatus'] = 'Signed'
        with self.assertRaisesRegex(SciSureError, 'signed or locked'):
            self.publisher(app, publication_destination=first)


if __name__ == '__main__': unittest.main()
