"""Bounded, value-preserving input readers for the future processing service.

Spreadsheet styles are not required to extract scientific source cells. This
allows GC exports with malformed fills to be inspected without repairing them.
Formula text and saved results are separate. No formula is ever evaluated.
"""

from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
import math
import posixpath
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from .jsonio import strict_loads, JSONInputError

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


def source_filename(value):
    """Portable leaf names; never silently rename a saved scientific original."""
    if not isinstance(value, str):
        raise InputError('A source filename must be text.')
    name = value.replace('\\', '/').rsplit('/', 1)[-1]
    try:
        invalid = (not name or len(name.encode('utf-8')) > 240 or name.endswith(('.', ' '))
            or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"/\\|?*' for c in name)
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I))
    except UnicodeError:
        invalid = True
    if invalid:
        raise InputError('Use a portable filename of at most 240 UTF-8 bytes, without reserved names or control characters.')
    return name


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
    if not math.isfinite(result) or (result == 0 and Decimal(value) != 0):
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


def _number_formats(archive, names):
    if 'xl/styles.xml' not in names:
        return []
    styles = _xml(archive, 'xl/styles.xml')
    custom = {n.get('numFmtId'): _text(n.get('formatCode', '')) for n in styles.findall('s:numFmts/s:numFmt', NS)}
    result = []
    for xf in styles.findall('s:cellXfs/s:xf', NS):
        ident = xf.get('numFmtId', '0')
        if not re.fullmatch(r'[0-9]{1,10}', ident) or len(result) >= MAX_CELLS:
            raise InputError('Invalid workbook number format.')
        # Ignore literal strings, escapes, colours and conditions; retain elapsed-time [h]/[m]/[s].
        code = re.sub(r'"[^\"]*"|\\.|_.|\*.|\[(?![hms]+\])[^\]]*\]', '', custom.get(ident, ''), flags=re.I)
        date_format = int(ident) in {*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)}
        result.append(dict(date_time=date_format or bool(re.search(r'[dmyhs]', code, re.I)),
            percent=int(ident) in (9, 10) or '%' in code))
    return result


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
        properties = book.find('s:workbookPr', NS)
        epoch = '1904' if properties is not None and properties.get('date1904') in ('1', 'true') else '1900'
        formats = _number_formats(archive, names)
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
            hidden_rows = {int(r.get('r')) for r in root.findall('s:sheetData/s:row', NS)
                if r.get('hidden') in ('1', 'true') and (r.get('r') or '').isdigit()}
            hidden_columns = set()
            for column in root.findall('s:cols/s:col', NS):
                if column.get('hidden') in ('1', 'true'):
                    try:
                        start, end = int(column.get('min')), int(column.get('max'))
                        if not 1 <= start <= end <= 16384:
                            raise ValueError
                        hidden_columns.update(range(start, min(end, MAX_COLUMNS) + 1))
                    except (ValueError, TypeError):
                        raise InputError('Invalid hidden column range.') from None
            cells = {}
            for cell in root.findall('s:sheetData/s:row/s:c', NS):
                count += 1
                if count > MAX_CELLS:
                    raise InputError('Workbook cell limit exceeded.')
                address = cell.get('r')
                row_number, column_number = coordinate_parts(address)
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
                style = cell.get('s', '0')
                if not re.fullmatch(r'[0-9]{1,6}', style):
                    raise InputError('Invalid cell style reference.')
                if int(style) < len(formats):
                    entry.update(number_format=formats[int(style)], excel_date_system=epoch)
                elif cell.get('s') is not None:
                    entry['number_format_unavailable'] = True
                if row_number in hidden_rows or column_number in hidden_columns:
                    entry['hidden'] = True
                if formula is not None:
                    entry.update(value=None, formula=_text('=' + (formula.text or '')),
                                 formula_attributes=dict(formula.attrib), cached_value=value)
                cells[address] = entry
            sheets[name] = {'state': declaration.get('state', 'visible'), 'cells': cells,
                'merged_ranges': [_text(m.get('ref')) for m in root.findall('s:mergeCells/s:mergeCell', NS)],
                'excel_date_system': epoch}
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


def read_artifact(content: bytes, filename: str) -> dict:
    """Parse source bytes; source hash always identifies exactly those bytes."""
    if not content or len(content) > MAX_FILE_BYTES:
        raise InputError('File is empty or exceeds the 20 MiB input limit.')
    filename = source_filename(filename)
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
                data = strict_loads(text)
            except (ValueError, RecursionError) as error:
                if isinstance(error, InputError):
                    raise
                if isinstance(error, JSONInputError):
                    raise InputError(str(error)) from None
                raise InputError('Malformed or excessively nested JSON.') from error
            if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
                raise InputError('JSON must contain a nonempty array of flat records.')
            headers = list(data[0])
            if len(data) >= MAX_ROWS or len(headers) > MAX_COLUMNS or (len(data) + 1) * len(headers) > MAX_CELLS:
                raise InputError('JSON table exceeds the supported row, column, or cell limits.')
            if not headers or any(set(r) != set(headers) for r in data):
                raise InputError('JSON records must have the same field names.')
            for row in data:
                if any(isinstance(v, (list, dict)) or isinstance(v, float) and not math.isfinite(v) for v in row.values()):
                    raise InputError('Nested or non-finite JSON values are unsupported.')
            sheets = _table_sheet([headers] + [[r[k] for k in headers] for r in data])
            # As with XLSX, retain the exact numeric token alongside the display value.
            # Parsing floats alone would erase source precision in offline review exports.
            lexical = strict_loads(text, lexical_numbers=True)
            for row_num, record in enumerate(data, 2):
                for col, key in enumerate(headers, 1):
                    if type(record[key]) in (int, float):
                        sheets['Table']['cells'][f'{col_name(col)}{row_num}']['lexical_value'] = lexical[row_num - 2][key]
    else:
        raise InputError('Supported input types are CSV, XLSX, and flat-record JSON.')
    return {'filename': filename, 'sha256': hashlib.sha256(content).hexdigest(),
            'size_bytes': len(content), 'format': extension[1:], 'sheets': sheets,
            'parser': {'name': 'catalyst-ingest', 'version': '0.1.1', 'formulas_executed': False}}
