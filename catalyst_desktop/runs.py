"""Reviewed project, study and experiment creation using the public Journal API."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from urllib.parse import urlencode

from .model import InputError, encode
from .scisure import SciSureError, remote_id


def _text(value, label, limit=250, required=True):
    value = str(value or '').strip()
    if (required and not value) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise InputError(f'Enter {label} ({limit} characters maximum).')
    return value


class RunWorkspace:
    """Keep fresh group/parent checks and never repeat a write with an unknown result."""
    def __init__(self, client, group_id, operations=None):
        self.client, self.group_id = client, remote_id(group_id)
        self.operations = operations if operations is not None else {}

    def _list(self, kind, **query):
        self.client.assert_group(self.group_id)
        path = '/api/v1/' + kind + ('?' + urlencode(query) if query else '')
        rows = self.client.list(path)
        self.client.assert_group(self.group_id)
        return [row for row in rows if row.get('groupID') == self.group_id]

    def projects(self, project_id=None):
        rows = self._list('projects', **({'projectID': remote_id(project_id)} if project_id else {}))
        return [r for r in rows if r.get('active') is True and (project_id is None or r.get('projectID') == project_id)]

    def studies(self, project_id, study_id=None):
        project_id = remote_id(project_id)
        query = {'projectID': project_id}
        if study_id: query['studyID'] = remote_id(study_id)
        rows = self._list('studies', **query)
        return [r for r in rows if r.get('projectID') == project_id and r.get('deleted') is False
            and (study_id is None or r.get('studyID') == study_id)]

    def experiments(self, project_id, study_id, search=''):
        project_id, study_id = remote_id(project_id), remote_id(study_id)
        query = dict(projectID=project_id, studyID=study_id)
        if search.strip(): query['searchName'] = _text(search, 'a run search')
        return [r for r in self._list('experiments', **query)
            if r.get('projectID') == project_id and r.get('studyID') == study_id
            and r.get('deleted') is False and r.get('template') is False]

    def _project(self, project_id):
        rows = self.projects(remote_id(project_id))
        if len(rows) != 1:
            raise InputError('This project is unavailable or archived. Refresh and choose an active project.')
        row = rows[0]
        return {k: row.get(k) for k in ('projectID', 'groupID', 'name', 'active')}

    def _study(self, project_id, study_id):
        rows = self.studies(project_id, remote_id(study_id))
        if len(rows) != 1:
            raise InputError('This study is unavailable or moved. Refresh and choose a study in this project.')
        row = rows[0]
        return {k: row.get(k) for k in ('studyID', 'projectID', 'groupID', 'name', 'deleted', 'approve')}

    def plan(self, kind, name, project_id=None, study_id=None, notes='', approve='NOTREQUIRED'):
        name = _text(name, f'a {kind} name')
        parents = {}
        if kind == 'project':
            body = dict(name=name, notes=_text(notes, 'project notes explaining its purpose', 4000))
            existing = self.projects()
        elif kind in ('study', 'run'):
            parents['project'] = self._project(project_id)
            if kind == 'study':
                if approve not in ('NOTREQUIRED', 'BYSTUDYMANAGER'):
                    raise InputError('Choose the study approval policy.')
                body = dict(name=name, projectID=remote_id(project_id), approve=approve)
                existing = self.studies(project_id)
            else:
                parents['study'] = self._study(project_id, study_id)
                body = dict(name=name, studyID=remote_id(study_id), status='PENDING', autoCollaborate=True)
                existing = self.experiments(project_id, study_id, name)
        else:
            raise InputError('Choose project, study or run.')
        if any(str(row.get('name', '')).strip().casefold() == name.casefold() for row in existing):
            raise InputError(f'A {kind} with this name already exists here. Choose the existing record or use a distinct name.')
        self.client.assert_group(self.group_id)
        return dict(kind=kind, tenant=self.client.origin, group_id=self.group_id, body=body, parents=parents)

    def create(self, plan):
        plan = deepcopy(plan)
        if plan.get('tenant') != self.client.origin or plan.get('group_id') != self.group_id:
            raise InputError('This creation review belongs to another connection. Prepare it again.')
        kind, body, parents = plan['kind'], plan['body'], plan['parents']
        # Parent labels may change after an uncertain write; keep the same write guard.
        key = sha256(encode(dict(kind=kind, tenant=plan['tenant'], group_id=plan['group_id'], body=body))).hexdigest()
        if key in self.operations:
            previous = self.operations[key]
            if previous.get('result') is not None:
                self.client.assert_group(self.group_id)
                result = deepcopy(previous['result'])
                if kind == 'run': return self.client.verify_destination(result)
                rows = self.projects(result['projectID']) if kind == 'project' else self.studies(result['projectID'], result['studyID'])
                if len(rows) != 1 or any(rows[0].get(field) != result.get(field) for field in body):
                    raise InputError(f'The created {kind} changed. Browse existing records and select it again.')
                return rows[0]
            raise SciSureError(f'The earlier {kind} creation needs checking. Browse existing records or check the LIMS before creating another; CATALYST will not repeat this request.', uncertain=True)
        fresh = self.plan(kind, body['name'], parents.get('project', {}).get('projectID'),
            parents.get('study', {}).get('studyID'), body.get('notes', ''), body.get('approve', 'NOTREQUIRED'))
        if fresh != plan:
            raise InputError('The project or study changed after review. Prepare the creation review again.')
        self.client.assert_group(self.group_id)
        path = {'project': '/api/v1/projects', 'study': '/api/v1/studies', 'run': '/api/v1/experiments'}[kind]
        attempt = self.operations[key] = dict(state='request started')
        try:
            response = self.client.request(path, 'POST', data=body)
        except SciSureError as error:
            if not error.uncertain: self.operations.pop(key, None)
            raise
        attempt['state'] = 'created; verification pending'
        try:
            self.client.assert_group(self.group_id)
            if kind == 'run':
                identifier = remote_id(response)
                result = self.client.destination(identifier, self.group_id)
                if (result['project_id'] != parents['project']['projectID'] or result['study_id'] != body['studyID']
                        or result['experiment_name'] != body['name']):
                    raise InputError('Created run does not match its reviewed destination.')
            else:
                id_key = kind + 'ID'
                returned_id = response.get(id_key) if isinstance(response, dict) else response
                if returned_id is not None: returned_id = remote_id(returned_id)
                rows = self.projects() if kind == 'project' else self.studies(body['projectID'])
                rows = [row for row in rows if row.get('name') == body['name'] and
                    (returned_id is None or row.get(id_key) == returned_id)]
                if len(rows) != 1:
                    raise InputError(f'The new {kind} could not be identified uniquely.')
                result = rows[0]
                remote_id(result.get(id_key))
                if kind == 'project' and result.get('notes') != body['notes']:
                    raise InputError('The new project notes could not be verified.')
                if kind == 'study' and result.get('approve') != body['approve']:
                    raise InputError('The new study approval policy could not be verified.')
            self.client.assert_group(self.group_id)
        except (InputError, SciSureError, KeyError, TypeError):
            raise SciSureError(f'The {kind} creation was accepted, but its result could not be verified. Check existing records in the LIMS before continuing; do not create a duplicate.', uncertain=True) from None
        attempt.update(state='verified', result=deepcopy(result))
        return result


def creation_summary(plan):
    body, kind = plan['body'], plan['kind']
    lines = [f"Create one {kind}: {body['name']}", f"Server: {plan['tenant']}", f"Group: {plan['group_id']}"]
    for kind_name, row in plan['parents'].items():
        lines.append(f"{kind_name.title()}: {row['name']} ({row[kind_name + 'ID']})")
    if kind == 'project': lines.extend(['', 'Purpose / notes: ' + body['notes'], 'The new project is owned by your signed-in account.'])
    elif kind == 'study': lines.append('Study approval: ' + ('Study manager' if body['approve'] == 'BYSTUDYMANAGER' else 'Not required'))
    else: lines.extend(['', 'Starts as pending, with collaborators inherited from this study or project.',
        'Creates an empty experiment and selects it for your upload. Your research files are sent only after their separate review.'])
    return '\n'.join(lines)
