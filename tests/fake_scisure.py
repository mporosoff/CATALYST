"""In-memory stand-in for the SciSure endpoints CATALYST uses. Synthetic data only."""
import json
import re
from urllib.parse import urlsplit, parse_qsl, unquote

from catalyst_desktop.model import digest


class FakeSciSure:
    def __init__(self, user=('Marc', 'Porosoff', 'marc.porosoff@rochester.edu'), deny_other_writes=False):
        self.projects, self.studies, self.experiments, self.sections, self.files = {}, {}, {}, {}, {}
        self.counter = 100
        self.user = user
        self.user_id = 7
        self.deny_other_writes = deny_other_writes
        self.calls = []

    def _id(self):
        self.counter += 1
        return self.counter

    @staticmethod
    def _page(rows, query):
        page, size = int(query.get('$page', 0)), int(query.get('$records', 100))
        data = rows[page * size:(page + 1) * size]
        return dict(data=data, totalRecords=len(rows), maxRecords=size, currentPage=page, recordCount=len(data))

    def __call__(self, url, method, headers, body):
        parts = urlsplit(url)
        path, query = parts.path, dict(parse_qsl(parts.query))
        self.calls.append((method, path))
        def ok(value):
            return 200, json.dumps(value).encode()
        if path == '/api/v1/groups/active':
            return ok(dict(groupID=1, name='Synthetic CATALYST group'))
        if path == '/api/v1/users/getCurrentUserInfo':
            return ok(dict(firstName=self.user[0], lastName=self.user[1], email=self.user[2], groupId=1, isBlocked=False))
        if path == '/api/v1/projects':
            if method == 'POST':
                pid = self._id(); payload = json.loads(body)
                self.projects[pid] = dict(projectID=pid, groupID=1, active=True, name=payload['name'], notes=payload.get('notes'))
                return ok(pid)
            return ok(self._page(list(self.projects.values()), query))
        if path == '/api/v1/studies':
            if method == 'POST':
                sid = self._id(); payload = json.loads(body)
                self.studies[sid] = dict(studyID=sid, groupID=1, deleted=False, **payload)
                return ok(sid)
            rows = [s for s in self.studies.values() if 'projectID' not in query or s['projectID'] == int(query['projectID'])]
            return ok(self._page(rows, query))
        if path == '/api/v1/experiments':
            if method == 'POST':
                eid = self._id(); payload = json.loads(body)
                self.experiments[eid] = dict(experimentID=eid, groupID=1, deleted=False, template=False, userID=self.user_id,
                    created='2026-09-25T12:00:00', studyID=payload['studyID'], name=payload['name'])
                return ok(eid)
            rows = [e for e in self.experiments.values() if 'studyID' not in query or e['studyID'] == int(query['studyID'])]
            return ok(self._page(rows, query))
        m = re.fullmatch(r'/api/v1/experiments/(\d+)/sections', path)
        if m:
            eid = int(m[1])
            if method == 'POST':
                if self.deny_other_writes and self.experiments[eid]['userID'] != self.user_id:
                    return 403, b''
                sid = self._id(); payload = json.loads(body)
                self.sections[sid] = dict(expJournalID=sid, experimentID=eid, deleted=False, firstName=self.user[0],
                    lastName=self.user[1], **payload)
                return ok(sid)
            return ok(self._page([s for s in self.sections.values() if s['experimentID'] == eid], query))
        m = re.fullmatch(r'/api/v1/experiments/sections/(\d+)/files(?:/(\d+))?', path)
        if m:
            sid = int(m[1])
            if m[2]:
                return 200, self.files[int(m[2])]['content']
            if method == 'POST':
                fid = self._id()
                self.files[fid] = dict(experimentFileID=fid, sectionID=sid, experimentID=self.sections[sid]['experimentID'],
                    realName=unquote(query['fileName']), fileSize=len(body), SHA256Hash=digest(body), content=body,
                    deleted=False, archived=False, origin='CLOUD')
                return ok(fid)
            rows = [{k: v for k, v in f.items() if k != 'content'} for f in self.files.values() if f['sectionID'] == sid]
            return ok(self._page(rows, query))
        return 404, b''
