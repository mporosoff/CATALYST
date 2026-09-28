"""Explicit user-requested exports; no automatic local research-data cache."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import re
import tempfile
import zipfile

from .model import FIELDS, InputError, check_sources, digest, encode
from .contracts import approved_payload
from .scisure import SciSureError, remote_id
from catalyst_ingest.readers import source_filename


def suggested_filename(name, fallback='SciSure-file'):
    """Remote names are suggestions, never paths or Windows device names."""
    try:
        return source_filename(str(name))
    except InputError:
        return fallback


def save_bytes(path, content):
    """Replace only after the complete download has been written successfully."""
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.catalyst-download-', suffix='.tmp',
                dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError:
        raise InputError('The download could not be saved. Check the selected folder, free space, and whether the file is open, then try again.') from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the actionable save error if the OS also locks cleanup.
                pass


def csv_cell(value, numeric=False):
    """Spreadsheet-safe view; exact source and standardized JSON stay unchanged."""
    if not isinstance(value, str):
        return value
    if numeric and re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
        return value
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def standardized_csv(standardized):
    """Flatten the documented toolkit quantities for a useful, non-lossy CSV view."""
    rows = standardized.get('rows', [])
    numeric_columns = {key for key, field in FIELDS.items() if field[0] == 'number'}
    if standardized.get('kind') == 'toolkit_gc_processing_revision':
        metadata_keys = {key for row in rows for key in row if key != 'quantities'}
        quantity_columns, flattened = {}, []
        for row in rows:
            values = {key: value for key, value in row.items() if key != 'quantities'}
            quantities = row.get('quantities')
            if not isinstance(quantities, list):
                raise InputError('Toolkit quantities cannot be exported as a table because their structure is unsupported.')
            for quantity in quantities:
                if (not isinstance(quantity, dict) or any(not isinstance(quantity.get(key), str)
                        or not quantity[key].strip() for key in ('field', 'unit'))
                        or 'value_decimal' not in quantity
                        or quantity['value_decimal'] is not None and not isinstance(quantity['value_decimal'], str)):
                    raise InputError('Toolkit quantities cannot be exported as a table because a field, unit, or value is invalid.')
                identity = (quantity['field'], quantity['unit'])
                column = quantity['field'] + ' [' + quantity['unit'] + ']'
                if (column in metadata_keys or column in values
                        or column in quantity_columns and quantity_columns[column] != identity):
                    raise InputError('Toolkit CSV columns conflict or repeat. The quantities cannot be flattened without losing information.')
                quantity_columns[column] = identity
                values[column] = quantity['value_decimal']
            flattened.append(values)
        rows = flattened
        numeric_columns.update(quantity_columns)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        return None
    headers = [csv_cell(key) for key in columns]
    if len(set(headers)) != len(headers):
        raise InputError('CSV headers conflict after spreadsheet-safety formatting. Download the original files or use the structured JSON values.')
    table = io.StringIO(newline='')
    writer = csv.writer(table)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([csv_cell(row.get(key, ''), key in numeric_columns) for key in columns])
    return table.getvalue().encode('utf-8-sig')


def review_archive(loaded):
    """Export a validated complete package, original bytes, and convenient data views."""
    revision = loaded['revision']
    payload = approved_payload(revision, loaded['approval'])
    sources = loaded['sources']
    if sources or payload['preview'].get('data_status') != 'metadata_only':
        check_sources(sources)
    expected = [(a['filename'], a['sha256'], a['size_bytes']) for a in payload['preview']['artifacts']]
    actual = [(s.name, digest(s.content), len(s.content)) for s in sources]
    if loaded.get('state') != 'complete' or not loaded.get('receipt'):
        raise InputError('This transfer is incomplete. Finish or reconcile it in SciSure before downloading a complete review package.')
    if expected != actual:
        raise InputError('All original files must be downloaded and verified before exporting this review.')
    receipt = loaded['receipt']
    try:
        if (not isinstance(receipt, dict) or receipt.get('format') != 'catalyst-desktop-receipt/1'
                or receipt.get('state') != 'complete' or receipt.get('revision_id') != payload['id']
                or receipt.get('revision_sha256') != revision.sha256
                or receipt.get('destination') != loaded['destination']
                or not isinstance(receipt.get('files'), list) or len(receipt['files']) != len(sources)):
            raise ValueError
        remote_id(receipt.get('section_id'))
        remote_id(receipt.get('manifest_id'))
        inventory_plan = payload['preview'].get('native_inventory_plan')
        if inventory_plan:
            from .inventory import validate_inventory_receipt
            validate_inventory_receipt(receipt.get('inventory'), inventory_plan)
        elif receipt.get('inventory') is not None:
            raise ValueError('Unapproved inventory actions in receipt.')
        file_ids = set()
        for index, (source, file) in enumerate(zip(sources, receipt['files']), 1):
            if (not isinstance(file, dict) or file.get('source_name') != source.name
                    or file.get('remote_name') != f'{index:02d}-' + source.name
                    or file.get('size_bytes') != len(source.content) or file.get('sha256') != digest(source.content)):
                raise ValueError
            identifier = remote_id(file.get('file_id'))
            if identifier in file_ids:
                raise ValueError
            file_ids.add(identifier)
    except (ValueError, KeyError, TypeError, SciSureError):
        raise InputError('The transfer receipt does not match this review and its verified original files. Download the review again.') from None
    standardized = payload['preview']['standardized']
    table = standardized_csv(standardized)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        packet = dict(format='catalyst-desktop-transfer/1', state='prepared', revision=payload,
            revision_sha256=revision.sha256, approval=loaded['approval'], destination=loaded['destination'])
        archive.writestr('CATALYST-review.json', encode(packet))
        archive.writestr('CATALYST-complete.json', encode(loaded['receipt']))
        archive.writestr('standardized.json', encode(standardized))
        if table is not None:
            archive.writestr('standardized.csv', table)
        for index, source in enumerate(sources, 1):
            archive.writestr(f'originals/{index:02d}-' + source_filename(source.name), source.content)
        archive.writestr('README.txt',
            'CATALYST verified review download\n\n'
            'CATALYST-review.json contains the approved revision, units, mapping, context and provenance.\n'
            'CATALYST-complete.json is the SciSure transfer receipt.\n'
            'Reviewed native inventory actions, when selected, are in the review; verified sample IDs and experiment links are in the receipt.\n'
            'standardized.json preserves exact standardized values and metadata.\n'
            'standardized.csv is a convenience table; spreadsheet software may infer types.\n'
            'Toolkit CSV quantities use one column per field and unit; all source evidence remains in standardized.json.\n'
            'Text beginning with spreadsheet formula characters is prefixed with an apostrophe in CSV only.\n'
            'originals/ contains byte-for-byte verified source files when supplied.\n'
            'Metadata-only procedure, sample, or synthesis records keep their full details in CATALYST-review.json.\n'
            'No scientific recalculation is performed.\n')
    return output.getvalue()
