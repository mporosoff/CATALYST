"""Bounded, value-preserving input readers for the future processing service.

Spreadsheet styles are not required to extract scientific source cells. This
allows GC exports with malformed fills to be inspected without repairing them.
Formula text and saved results are separate. No formula is ever evaluated.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_SHEETS = 32
MAX_CELLS = 200_000
MAX_TEXT = 32768
MAX_ROWS = 50000
MAX_COLUMNS = 512
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
COORDINATE = re.compile(r'([A-Z]{1,3})([1-9][0-9]{0,6})\Z')


class InputError(ValueError):
    """A rejected source input; safe for a caller to show as a validation error."""


def col_name(index: int) -> str:
    result = ''
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def coordinate_parts(address: str) -> tuple[int, int]:
    match = COORDINATE.fullmatch(address or '')
    if not match:
        raise InputError('Invalid spreadsheet cell address.')
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    row = int(match[2])
    if row > MAX_ROWS or column > MAX_COLUMNS:
        raise InputError('Spreadsheet row or column limit exceeded.')
    return row, column


def _text(value):
    if value is not None and len(str(value)) > MAX_TEXT:
        raise InputError('Cell text exceeds the supported limit.')
    return value


def _number(value: str):
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
        raise InputError('Invalid numeric cell.')
    if len(value) > 128:
        raise InputError('Numeric cell exceeds the supported precision limit.')
    result = float(value)
    if not math.isfinite(result):
        raise InputError('Non-finite numeric cell.')
    # The lexical value is retained separately, including all source precision.
    return int(value) if re.fullmatch(r'[+-]?\d+', value) else result


def _xml(archive: zipfile.ZipFile, name: str):
    try:
        content = archive.read(name)
    except KeyError as error:
        raise InputError('Workbook is missing a required XML part.') from error
    # Reject declarations before parsing, including UTF-16/32 encodings.
    probe = content.replace(b'\x00', b'').lower()
    if b'<!doctype' in probe or b'<!entity' in probe:
        raise InputError('XML entity and document type declarations are unsupported.')
    try:
        return ET.fromstring(content)
    except ET.ParseError as error:
        raise InputError('Malformed workbook XML.') from error


def _read_xlsx(content: bytes):
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise InputError('The XLSX file is not a readable ZIP workbook.') from error
    with archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > MAX_MEMBERS or len(names) != len(set(names)):
            raise InputError('Too many or duplicate workbook archive members.')
        if sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
            raise InputError('Expanded workbook size limit exceeded.')
        for info in infos:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                raise InputError('Unsafe workbook member path.')
            if info.flag_bits & 1:
                raise InputError('Encrypted workbooks are unsupported.')
        if any('vbaproject' in n.lower() or n.startswith('xl/externalLinks/') for n in names):
            raise InputError('Macros and external workbook links are unsupported.')
        book = _xml(archive, 'xl/workbook.xml')
        declarations = book.find('s:sheets', NS)
        if declarations is None or not 1 <= len(declarations) <= MAX_SHEETS:
            raise InputError('Workbook sheet count is unsupported.')
        relationships = {}
        for rel in _xml(archive, 'xl/_rels/workbook.xml.rels'):
            ident = rel.get('Id')
            if ident in relationships:
                raise InputError('Duplicate workbook relationship.')
            if rel.get('TargetMode') == 'External':
                raise InputError('External workbook relationships are unsupported.')
            relationships[ident] = rel.get('Target', '')
        strings = []
        if 'xl/sharedStrings.xml' in names:
            for item in _xml(archive, 'xl/sharedStrings.xml').findall('s:si', NS):
                if len(strings) >= MAX_CELLS:
                    raise InputError('Shared string limit exceeded.')
                strings.append(_text(''.join(t.text or '' for t in item.findall('.//s:t', NS))))
        sheets = {}; count = 0
        for declaration in declarations:
            name = _text(declaration.get('name'))
            if not name or name in sheets:
                raise InputError('Missing or duplicate sheet name.')
            target = relationships.get(declaration.get(RID), '')
            resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if not resolved.startswith('xl/worksheets/') or not resolved.endswith('.xml'):
                raise InputError('Unsupported worksheet relationship.')
            root = _xml(archive, resolved)
            cells = {}
            for cell in root.findall('s:sheetData/s:row/s:c', NS):
                count += 1
                if count > MAX_CELLS:
                    raise InputError('Workbook cell limit exceeded.')
                address = cell.get('r')
                coordinate_parts(address)
                if address in cells:
                    raise InputError('Duplicate worksheet cell.')
                kind = cell.get('t', 'n')
                element = cell.find('s:v', NS)
                raw_value = _text(element.text) if element is not None else None
                value = raw_value
                if kind == 's':
                    try:
                        index = int(raw_value)
                        if index < 0:
                            raise ValueError
                        value = strings[index]
                    except (ValueError, TypeError, IndexError) as error:
                        raise InputError('Invalid shared string reference.') from error
                elif kind == 'inlineStr':
                    inline = cell.find('s:is', NS)
                    value = _text(''.join(t.text or '' for t in inline.findall('.//s:t', NS))) if inline is not None else None
                elif kind == 'n' and value is not None:
                    value = _number(value)
                elif kind == 'b' and value is not None:
                    if value not in ('0', '1'):
                        raise InputError('Invalid boolean cell.')
                    value = value == '1'
                elif kind not in ('n', 'b', 'str', 'e', 'd'):
                    raise InputError('Unsupported spreadsheet cell type.')
                formula = cell.find('s:f', NS)
                entry = {'value': value, 'source_type': kind, 'lexical_value': raw_value, 'style_index': cell.get('s')}
                if formula is not None:
                    entry.update(value=None, formula=_text('=' + (formula.text or '')),
                                 formula_attributes=dict(formula.attrib), cached_value=value)
                cells[address] = entry
            sheets[name] = {'state': declaration.get('state', 'visible'), 'cells': cells}
        return sheets


def _table_sheet(rows):
    cells = {}
    for row_num, row in enumerate(rows, 1):
        if row_num > MAX_ROWS or len(row) > MAX_COLUMNS:
            raise InputError('Table row or column limit exceeded.')
        for col, value in enumerate(row, 1):
            if len(cells) >= MAX_CELLS:
                raise InputError('Table cell limit exceeded.')
            cells[f'{col_name(col)}{row_num}'] = {'value': _text(value), 'source_type': type(value).__name__}
    return {'Table': {'state': 'visible', 'cells': cells}}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError('Duplicate JSON key.')
        result[key] = value
    return result


def read_artifact(content: bytes, filename: str) -> dict:
    """Parse source bytes; source hash always identifies exactly those bytes."""
    if not content or len(content) > MAX_FILE_BYTES:
        raise InputError('File is empty or exceeds the 20 MiB input limit.')
    filename = filename.replace('\\', '/').rsplit('/', 1)[-1]
    extension = PurePosixPath(filename).suffix.lower()
    if extension == '.xlsx':
        try:
            sheets = _read_xlsx(content)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError) as error:
            raise InputError('Workbook archive is corrupt or uses unsupported compression.') from error
    elif extension in ('.csv', '.json'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise InputError('CSV/JSON input must use UTF-8 encoding.') from error
        if extension == '.csv':
            try:
                sheets = _table_sheet(csv.reader(io.StringIO(text, newline=''), strict=True))
            except csv.Error as error:
                raise InputError('Malformed CSV input.') from error
        else:
            try:
                data = json.loads(text, object_pairs_hook=_unique_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(InputError('Non-finite JSON number.')))
            except (ValueError, RecursionError) as error:
                if isinstance(error, InputError):
                    raise
                raise InputError('Malformed or excessively nested JSON.') from error
            if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
                raise InputError('JSON must contain a nonempty array of flat records.')
            headers = list(data[0])
            if not headers or any(set(r) != set(headers) for r in data):
                raise InputError('JSON records must have the same field names.')
            for row in data:
                if any(isinstance(v, (list, dict)) or isinstance(v, float) and not math.isfinite(v) for v in row.values()):
                    raise InputError('Nested or non-finite JSON values are unsupported.')
            sheets = _table_sheet([headers] + [[r[k] for k in headers] for r in data])
    else:
        raise InputError('Supported input types are CSV, XLSX, and flat-record JSON.')
    return {'filename': filename, 'sha256': hashlib.sha256(content).hexdigest(),
            'size_bytes': len(content), 'format': extension[1:], 'sheets': sheets,
            'parser': {'name': 'catalyst-ingest', 'version': '0.1.0', 'formulas_executed': False}}
