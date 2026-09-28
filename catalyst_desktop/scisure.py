"""Bounded direct HTTPS transport. No hosted proxy, redirects, telemetry, or AI."""
from __future__ import annotations

from http.client import HTTPException
from email.utils import parsedate_to_datetime
import ipaddress
import math
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

from .model import encode, MAX_FILE
from catalyst_ingest.jsonio import strict_loads

SANDBOX = 'https://sandbox.elabjournal.com'

class SciSureError(Exception):
    def __init__(self, message, uncertain=False, status=None, retry_after=None):
        super().__init__(message)
        self.uncertain, self.status = uncertain, status
        self.retry_after = retry_after

def tenant_origin(value):
    """Canonical HTTPS origin explicitly chosen by the user; never infer a tenant."""
    message = 'Enter your SciSure server as https://hostname, without a sign-in path, user information, or query. Only standard HTTPS (port 443) is supported.'
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise SciSureError(message)
    value = value.strip()
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise SciSureError(message) from None
    if (parts.scheme.lower() != 'https' or not host or parts.username is not None
            or parts.password is not None or parts.path not in ('', '/') or '?' in value or '#' in value
            or '\\' in value or '%' in value or port not in (None, 443)
            or parts.netloc.lower() not in (host, host + ':443')
            or len(host) > 253 or '.' not in host
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))
            or host.endswith(('.localhost', '.local', '.internal')) or host.rsplit('.', 1)[-1].isdigit()):
        raise SciSureError(message)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SciSureError(message)
    return 'https://' + host


def token_value(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in value.strip())):
        raise SciSureError('Enter the SciSure API token without spaces or line breaks.')
    return value.strip()


def api_parts(path):
    if not isinstance(path, str):
        raise SciSureError('Unsupported SciSure operation.')
    try:
        parts = urlsplit(path)
        decoded = unquote(parts.path)
    except ValueError:
        raise SciSureError('Unsupported SciSure operation.') from None
    if (parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith('/api/v1/')
            or '..' in decoded or '\\' in decoded or '%' in decoded
            or any(ord(c) < 32 or ord(c) == 127 for c in path + decoded)):
        raise SciSureError('Unsupported SciSure operation.')
    return parts

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
    def __init__(self, token, origin=SANDBOX, transport=None, sleep=None):
        self.origin = tenant_origin(origin)
        self._token = token_value(token)
        self._transport = transport
        self._opener = None
        self._sleep = sleep or time.sleep

    def __repr__(self):
        return f'SciSureClient(origin={self.origin!r}, token=<redacted>)'

    def request(self, path, method='GET', data=None, binary=False):
        api_parts(path)
        sample_link = (method == 'PUT' and re.fullmatch(r'/api/v1/experiments/sections/[1-9]\d*/samples', path)
            and isinstance(data, list) and 1 <= len(data) <= 1000
            and all(type(value) is int and 0 < value <= 9007199254740991 for value in data) and len(set(data)) == len(data))
        if (method not in ('GET', 'POST') and not sample_link) or (method == 'GET' and data is not None):
            raise SciSureError('Unsupported SciSure operation.')
        write = method != 'GET'
        headers = {'Authorization': self._token, 'X-Requested-With': 'Swagger',
                   'Accept': 'application/octet-stream' if binary else 'application/json'}
        body = None
        if data is not None:
            body = data if isinstance(data, bytes) else encode(data)
            if len(body) > MAX_FILE:
                raise SciSureError('The upload exceeds the 20 MiB transfer limit.')
            headers['Content-Type'] = 'application/octet-stream' if isinstance(data, bytes) else 'application/json; charset=utf-8'
        try:
            for attempt in range(3):
                response_headers = {}
                try:
                    if self._transport is not None:
                        result = self._transport(self.origin + path, method, headers, body)
                        status, content = result[:2]
                        response_headers = result[2] if len(result) > 2 else {}
                    else:
                        if self._opener is None:
                            self._opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPSHandler(context=ssl.create_default_context()))
                        request = Request(self.origin + path, data=body, method=method, headers=headers)
                        with self._opener.open(request, timeout=30) as response:
                            status, response_headers = response.status, response.headers
                            content = response.read(MAX_FILE + 1)
                except HTTPError as e:
                    status, response_headers, content = e.code, e.headers, b''
                    e.close()
                if status != 429:
                    break
                retry = response_headers.get('Retry-After', response_headers.get('retry-after', str(2 ** attempt)))
                try: delay = max(0, int(retry))
                except (TypeError, ValueError):
                    try:
                        when = parsedate_to_datetime(retry)
                        if when.tzinfo is None:
                            raise ValueError('Retry date must include its time zone.')
                        delay = max(0, math.ceil(when.timestamp() - time.time()))
                    except (TypeError, ValueError, OverflowError):
                        delay = 2 ** attempt
                if write or attempt == 2 or delay > 10:
                    raise SciSureError(f'SciSure rate limited the request. Wait {delay} seconds before checking or retrying.',
                        False, 429, delay)
                self._sleep(delay)
            if not 200 <= status < 300:
                rejected = status in (400, 401, 403, 404, 405, 409, 413, 415, 422)
                message = {
                    401: 'SciSure rejected the API token (HTTP 401). Check that it belongs to this server and has not expired or been revoked.',
                    403: 'The token account does not have permission for this operation (HTTP 403). Ask a SciSure administrator for access to the selected group or experiment.',
                    404: 'The SciSure record or API endpoint is unavailable (HTTP 404). Refresh the experiment list and verify the server URL.',
                }.get(status, f'SciSure returned HTTP {status}. Check the token, permissions, and selected destination.')
                if 300 <= status < 400:
                    message = f'SciSure returned a redirect (HTTP {status}). Enter the exact HTTPS server URL; tokens are never forwarded to a redirected address.'
                raise SciSureError(message, write and not rejected, status)
            if len(content) > MAX_FILE:
                raise SciSureError('SciSure response exceeds the supported size.', write)
            length = response_headers.get('Content-Length', response_headers.get('content-length'))
            if length is not None and (not str(length).isdigit() or int(length) != len(content)):
                raise SciSureError('SciSure returned an incomplete response. Check transfer status before retrying.'
                    if write else 'SciSure returned an incomplete response. Refresh before continuing.', write)
            if binary:
                return content
            return strict_loads(content) if content else None
        except SciSureError:
            raise
        except (OSError, URLError, ValueError, TimeoutError, HTTPException, RecursionError):
            raise SciSureError('The transfer outcome is unknown. Check transfer status before retrying.' if write
                else 'SciSure could not be read. Check the connection and try again.', write) from None

    def list(self, path, limit=1000):
        """Read every page. ``limit`` bounds the total so a partial list is never mistaken for all records."""
        parts = api_parts(path)
        if any(k.casefold() in ('$page', '$records', 'opts.paging.currentpage', 'opts.paging.maxrecords')
                for k, _ in parse_qsl(parts.query, keep_blank_values=True)):
            raise SciSureError('Pagination parameters are managed by the SciSure client.')
        route = parts.path
        # A foreign user/group/type ID is shared by many records. Never use it to deduplicate pages.
        own_key = next((key for pattern, key in (
            (r'/api/v1/experiments/sections/\d+/files', 'experimentFileID'),
            (r'/api/v1/experiments/\d+/sections', 'expJournalID'),
            (r'/api/v1/experiments/\d+/collaborators', 'userID'),
            (r'/api/v1/sampleTypes/\d+/meta', 'sampleTypeMetaID'),
            (r'/api/v1/samples/\d+/meta', 'sampleMetaID'),
            (r'/api/v1/experiments/sections/\d+/samples', 'sampleID'),
            (r'/api/v1/sampleTypes', 'sampleTypeID'), (r'/api/v1/samples', 'sampleID'),
            (r'/api/v1/experiments', 'experimentID'), (r'/api/v1/projects', 'projectID'),
            (r'/api/v1/studies', 'studyID'), (r'/api/v1/protocols', 'protVersionID'), (r'/api/v1/files', 'fileID'),
            (r'/api/v1/users', 'userID')) if re.fullmatch(pattern, route)), None)
        rows, expected_total, page_size, seen = [], None, None, set()
        for page in range(max(1000, limit // 100 + 2)):
            response = self.request(path + ('&' if '?' in path else '?') + urlencode({'$page': page, '$records': 100}))
            if not isinstance(response, dict) or not isinstance(response.get('data'), list):
                raise SciSureError('SciSure returned an unsupported paginated response.')
            data = response['data']
            total, size = response.get('totalRecords'), response.get('maxRecords')
            if type(total) is not int or total < 0 or type(size) is not int or not 1 <= size <= 1000:
                raise SciSureError('SciSure pagination is missing valid totalRecords/maxRecords; a partial result cannot be used.')
            if total > limit:
                raise SciSureError(f'More than {limit:,} records were returned. Narrow the SciSure workspace.')
            if expected_total is None:
                expected_total, page_size = total, size
            if (total != expected_total or size != page_size or response.get('currentPage', page) != page
                    or type(response.get('currentPage', page)) is not int
                    or type(response.get('recordCount', len(data))) is not int
                    or response.get('recordCount', len(data)) != len(data)
                    or len(data) != min(size, max(0, total - page * size))):
                raise SciSureError('SciSure pagination changed or is incomplete. Refresh before continuing.')
            for item in data:
                if not isinstance(item, dict):
                    raise SciSureError('SciSure returned an unsupported list record.')
                fingerprint = encode([own_key, str(item[own_key])]) if own_key and own_key in item else encode(item)
                if fingerprint in seen:
                    raise SciSureError('SciSure repeated a paginated record. Refresh before continuing.')
                seen.add(fingerprint)
            rows.extend(data)
            if len(rows) == total:
                return rows
        raise SciSureError('SciSure pagination did not complete.')

    def object(self, path):
        value = self.request(path)
        if not isinstance(value, dict):
            raise SciSureError('SciSure returned an unsupported record. Refresh before continuing.')
        return value

    def assert_group(self, group_id):
        if remote_id(self.object('/api/v1/groups/active').get('groupID')) != group_id:
            raise SciSureError('The token account’s active group changed. Reconnect before continuing.')

    def check_connection(self):
        group = self.object('/api/v1/groups/active')
        group_id = remote_id(group.get('groupID'))
        experiments = [e for e in self.list('/api/v1/experiments')
            if e.get('groupID') == group_id and e.get('deleted') is False and e.get('template') is False]
        self.assert_group(group_id)
        return dict(group_id=group_id, group_name=group.get('name') or group.get('groupName') or str(group_id), experiments=experiments)

    def destination(self, experiment_id, group_id=None, writable=True):
        eid = remote_id(experiment_id)
        active = self.object('/api/v1/groups/active')
        current_group = remote_id(active.get('groupID'))
        if group_id is not None and current_group != group_id:
            raise SciSureError('The token account’s active group changed. Verify the destination again.')
        e = self.object(f'/api/v1/experiments/{eid}')
        if (e.get('experimentID') != eid or e.get('groupID') != current_group or e.get('deleted') is not False
                or e.get('template') is not False):
            raise SciSureError('This experiment is unavailable in the selected group.')
        if writable and e.get('signatureStatus') != 'None':
            raise SciSureError('This experiment is signed or locked. Choose an unsigned experiment for uploads; it remains available for downloads.')
        self.assert_group(current_group)
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
