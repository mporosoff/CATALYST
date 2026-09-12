"""Bounded direct HTTPS transport. No hosted proxy, redirects, telemetry, or AI."""
from __future__ import annotations

import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

from .model import encode, MAX_FILE

SANDBOX = 'https://sandbox.elabjournal.com'

class SciSureError(Exception):
    def __init__(self, message, uncertain=False, status=None):
        super().__init__(message)
        self.uncertain, self.status = uncertain, status

def tenant_origin(value):
    if value.rstrip('/') != SANDBOX:
        raise SciSureError('This release connects only to the verified sandbox.elabjournal.com tenant.')
    return SANDBOX

def remote_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[1-9]\d{0,15}', str(value)):
        raise SciSureError('SciSure returned an invalid record identifier.')
    n = int(value)
    if n > 9007199254740991:
        raise SciSureError('SciSure returned an unsupported record identifier.')
    return n

class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class SciSureClient:
    def __init__(self, token, origin=SANDBOX, transport=None):
        self.origin = tenant_origin(origin)
        if not isinstance(token, str) or not token.strip() or len(token) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token.strip()):
            raise SciSureError('Enter the SciSure API token without spaces or line breaks.')
        self._token = token.strip()
        self._transport = transport
        self._opener = None

    def __repr__(self):
        return f'SciSureClient(origin={self.origin!r}, token=<redacted>)'

    def request(self, path, method='GET', data=None, binary=False):
        parts = urlsplit(path)
        if (parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith('/api/v1/')
                or '..' in unquote(parts.path) or '\\' in unquote(parts.path) or any(ord(c) < 32 for c in path)
                or method not in ('GET', 'POST')):
            raise SciSureError('Unsupported SciSure operation.')
        write = method != 'GET'
        headers = {'Authorization': self._token, 'X-Requested-With': 'Swagger',
                   'Accept': 'application/octet-stream' if binary else 'application/json'}
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else encode(data)
            if len(body) > MAX_FILE:
                raise SciSureError('The upload exceeds the 20 MiB transfer limit.')
            headers['Content-Type'] = 'application/octet-stream' if isinstance(data, bytes) else 'application/json'
        try:
            if self._transport is not None:
                status, content = self._transport(self.origin + path, method, headers, body)
            else:
                if self._opener is None:
                    self._opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))
                request = Request(self.origin + path, data=body, method=method, headers=headers)
                with self._opener.open(request, timeout=30) as response:
                    status = response.status
                    content = response.read(MAX_FILE + 1)
            if not 200 <= status < 300:
                raise SciSureError(f'SciSure returned HTTP {status}. Check permissions and the selected destination.', write, status)
            if len(content) > MAX_FILE:
                raise SciSureError('SciSure response exceeds the supported size.', write)
            if binary:
                return content
            return json.loads(content.decode('utf-8')) if content else None
        except SciSureError:
            raise
        except HTTPError as e:
            status = e.code
            e.close()
            raise SciSureError(f'SciSure returned HTTP {status}. Check the token, permissions, and destination.', write, status) from None
        except (OSError, URLError, ValueError, TimeoutError):
            raise SciSureError('The transfer outcome is unknown. Check transfer status before retrying.' if write
                else 'SciSure could not be read. Check the connection and try again.', write) from None

    def list(self, path):
        rows = []
        for page in range(10):
            response = self.request(path + ('&' if '?' in path else '?') + urlencode({'$page': page, '$records': 100}))
            if not isinstance(response, dict) or not isinstance(response.get('data'), list) or type(response.get('hasNextPage')) is not bool:
                raise SciSureError('SciSure returned an unsupported paginated response.')
            rows.extend(response['data'])
            if not response['hasNextPage']:
                return rows
        raise SciSureError('More than 1,000 records were returned. Choose a smaller test workspace.')

    def check_connection(self):
        group = self.request('/api/v1/groups/active')
        group_id = remote_id(group.get('groupID'))
        experiments = [e for e in self.list('/api/v1/experiments')
            if e.get('groupID') == group_id and e.get('deleted') is False and e.get('template') is False]
        return dict(group_id=group_id, group_name=group.get('name') or group.get('groupName') or str(group_id), experiments=experiments)

    def destination(self, experiment_id, group_id=None, writable=True):
        eid = remote_id(experiment_id)
        active = self.request('/api/v1/groups/active')
        current_group = remote_id(active.get('groupID'))
        if group_id is not None and current_group != group_id:
            raise SciSureError('The token account’s active group changed. Verify the destination again.')
        e = self.request(f'/api/v1/experiments/{eid}')
        if (e.get('experimentID') != eid or e.get('groupID') != current_group or e.get('deleted') is not False
                or e.get('template') is not False):
            raise SciSureError('This experiment is unavailable in the selected group.')
        if writable and e.get('signatureStatus') != 'None':
            raise SciSureError('This experiment is signed or locked. Choose an unsigned test experiment.')
        return dict(tenant=self.origin, group_id=current_group, experiment_id=eid,
            study_id=remote_id(e.get('studyID')), project_id=remote_id(e.get('projectID')),
            experiment_name=str(e.get('name', eid)), signature_status=e.get('signatureStatus'))

    def verify_destination(self, destination, writable=True):
        if destination.get('tenant') != self.origin:
            raise SciSureError('The destination belongs to a different SciSure tenant.')
        current = self.destination(destination['experiment_id'], destination['group_id'], writable)
        if any(current[k] != destination[k] for k in ('study_id', 'project_id')):
            raise SciSureError('The experiment has moved. Verify the destination again.')
        return current
