"""Read-only CATALYST connector for AI assistants (Model Context Protocol, stdio transport).

Run:    python -m catalyst_query.mcp_server
Env:    CATALYST_TOKEN (a lab token), CATALYST_SERVER (default: SciSure sandbox)

Dependency-free: speaks JSON-RPC 2.0, one message per line on stdin/stdout. It only reads.
"""
from __future__ import annotations

import json
import sys
import traceback

from catalyst_desktop import __version__

PROTOCOL = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
S = lambda **p: dict(type='object', properties=p, additionalProperties=False)
STR = lambda d: dict(type='string', description=d)

TOOLS = [
    dict(name='catalyst_summary', description='Overview of the CATALYST consortium database: how many samples per lab and '
        'procedure, the list of technique codes, and the ID formats. Call this first.', inputSchema=S()),
    dict(name='list_samples', description='Search catalyst samples. All filters optional; search matches sample ID, '
        'composition and procedure (all words must match).', inputSchema=S(search=STR('words, e.g. "Mo K Al2O3"'),
        lab=STR('Rochester, A*STAR, SLAC, Northwestern, Virginia Tech or Oxeon'), researcher=STR('initials, e.g. MDP'),
        procedure=STR('procedure ID, e.g. PRC-UR-001'), since=STR('synthesis date YYYY-MM-DD or later'))),
    dict(name='get_sample', description='Everything about one sample: the recipe actually used, automatic deviations from its '
        'master procedure, every data record from every lab (with conditions and file names), and its shipping log.',
        inputSchema=dict(S(sample_id=STR('e.g. UR-MDP-260925-01')), required=['sample_id'])),
    dict(name='list_procedures', description='Shared master synthesis procedures (PRC-…, type "synthesis") and reactor '
        'test protocols (TST-…, type "testing").', inputSchema=S()),
    dict(name='get_procedure', description='All versions of one synthesis procedure or test protocol with its full '
        'template (recipe or test conditions).', inputSchema=dict(S(procedure_id=STR('e.g. PRC-UR-001 or TST-NU-001')),
        required=['procedure_id'])),
    dict(name='find_data', description='Flat table (one row per measurement) with conditions and the sample\'s recipe '
        'columns — good for comparing labs or building a training set. Narrow it with technique/search/lab; reading '
        'many samples can take a while.', inputSchema=S(technique=STR('technique code, e.g. RXN, XRD, XAS, BET'),
        search=STR('sample search words'), lab=STR('sample lab'), limit=dict(type='integer', description='max rows (default 200)'))),
    dict(name='read_data_file', description='Text preview of an original data file (CSV/TXT/XY/JSON/XLSX), first rows only.',
        inputSchema=dict(S(data_id=STR('e.g. UR-MDP-260925-01-RXN-01'), filename=STR('exact file name from get_sample'),
            max_rows=dict(type='integer', description='default 200')), required=['data_id', 'filename'])),
]


class Server:
    def __init__(self, reader_factory=None):
        self._reader, self._factory = None, reader_factory

    @property
    def reader(self):
        if self._reader is None:
            if self._factory:
                self._reader = self._factory()
            else:
                from .reader import CatalystReader
                self._reader = CatalystReader()
        return self._reader

    def call(self, name, args):
        r = self.reader
        if name == 'catalyst_summary':
            return r.summary()
        if name == 'list_samples':
            return r.samples(**{k: v for k, v in args.items() if v})
        if name == 'get_sample':
            return r.sample(args['sample_id'].strip())
        if name == 'list_procedures':
            return r.procedures()
        if name == 'get_procedure':
            return r.procedure(args['procedure_id'].strip())
        if name == 'find_data':
            limit = int(args.get('limit') or 200)
            rows = r.data(technique=args.get('technique') or None, search=args.get('search') or None, lab=args.get('lab') or None)
            return dict(rows=rows[:limit], total_rows=len(rows), truncated=len(rows) > limit)
        if name == 'read_data_file':
            return r.file_preview(args['data_id'].strip(), args['filename'], int(args.get('max_rows') or 200))
        raise KeyError(f'Unknown tool {name}.')

    def handle(self, message):
        if not isinstance(message, dict):
            return dict(jsonrpc='2.0', id=None, error=dict(code=-32600, message='Invalid Request'))
        method, mid = message.get('method'), message.get('id')
        if mid is None:
            return None  # notification (e.g. notifications/initialized)
        try:
            if method == 'initialize':
                asked = (message.get('params') or {}).get('protocolVersion')
                version = asked if asked in SUPPORTED else PROTOCOL
                result = dict(protocolVersion=version, capabilities=dict(tools={}),
                    serverInfo=dict(name='catalyst', version=__version__),
                    instructions='Read-only access to CATALYST catalyst samples, recipes, measurements and files in SciSure. '
                        'Start with catalyst_summary, then list_samples / get_sample.')
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = dict(tools=TOOLS)
            elif method == 'tools/call':
                params = message.get('params') or {}
                try:
                    value = self.call(params.get('name'), params.get('arguments') or {})
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
                    result = dict(content=[dict(type='text', text=text)], isError=False)
                except Exception as error:  # tool errors are reported to the model, not as protocol errors
                    result = dict(content=[dict(type='text', text=f'{type(error).__name__}: {error}')], isError=True)
            else:
                return dict(jsonrpc='2.0', id=mid, error=dict(code=-32601, message=f'Unknown method {method}'))
            return dict(jsonrpc='2.0', id=mid, result=result)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            return dict(jsonrpc='2.0', id=mid, error=dict(code=-32603, message=str(error)))


def main(stdin=sys.stdin, stdout=sys.stdout, server=None):
    server = server or Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = dict(jsonrpc='2.0', id=None, error=dict(code=-32700, message='Parse error'))
        else:
            response = server.handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
            stdout.flush()


if __name__ == '__main__':
    main()
