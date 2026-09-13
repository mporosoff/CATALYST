"""Validate stored review envelopes before trusting or downloading their contents."""
from datetime import datetime
import re
import uuid

from .model import (InputError, MAX_FILE, MAX_TOTAL, MODALITIES, encode, digest,
    make_profile, context_issues)
from .traceability import lab_id, build_traceability
from catalyst_ingest.readers import source_filename, MAX_ROWS


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid text field.')
    return value


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid revision identifier.')


def _timestamp(value):
    result = datetime.fromisoformat(_text(value, 50))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('A provenance timestamp needs its time zone.')
    return result


def artifact_manifest(artifacts):
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 6:
        raise ValueError('Invalid source count.')
    names, total = set(), 0
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError('Invalid source metadata.')
        name = source_filename(item['filename'])
        if name != item['filename'] or name.casefold() in names:
            raise ValueError('Duplicate or nonportable source filename.')
        names.add(name.casefold())
        if type(item['size_bytes']) is not int or not 1 <= item['size_bytes'] <= MAX_FILE:
            raise ValueError('Invalid source size.')
        total += item['size_bytes']
        if not isinstance(item['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
            raise ValueError('Invalid source checksum.')
        if item['format'] not in ('csv', 'xlsx', 'json', 'binary'):
            raise ValueError('Unsupported source format.')
        if item['format'] != 'binary' and not name.lower().endswith('.' + item['format']):
            raise ValueError('Source format and extension disagree.')
        parser = item['parser']
        if not isinstance(parser, dict) or parser.get('formulas_executed') is not False:
            raise ValueError('Unsupported parser declaration.')
        _text(parser['name'], 100)
        _text(parser['version'], 100)
    if total > MAX_TOTAL:
        raise ValueError('Combined source size exceeds the supported limit.')


def approved_payload(revision, approval):
    """Checks structure and bindings; a checksum is not an electronic signature."""
    try:
        payload = revision.value()
        _uuid(payload['id'])
        if payload.get('parent') is not None:
            _uuid(payload['parent'])
            if payload['parent'] == payload['id']:
                raise ValueError('A revision cannot be its own parent.')
        created = _timestamp(payload['created_at'])
        _text(payload['title'], 200)
        if (not isinstance(approval, dict) or approval.get('revision_id') != payload['id']
                or approval.get('revision_sha256') != revision.sha256 or approval.get('acknowledged') is not True):
            raise ValueError('Approval does not bind this revision.')
        _text(approval['reviewer'], 200)
        _text(approval['note'], 4000)
        if _timestamp(approval['approved_at']) < created:
            raise ValueError('Approval precedes revision creation.')
        preview = payload['preview']
        if not isinstance(preview, dict) or preview.get('schema_version') not in (
                'catalyst-desktop-review/1', 'catalyst-desktop-review/2'):
            raise ValueError('Unsupported review schema.')
        if preview['modality'] not in MODALITIES or lab_id(preview['entity']) != preview['entity']:
            raise ValueError('Invalid modality or lab.')
        context = preview['context']
        if not isinstance(context, dict) or len(context) > 100 or any(not isinstance(k, str) or len(k) > 100
                or not isinstance(v, str) or len(v) > 4000 for k, v in context.items()):
            raise ValueError('Invalid context.')
        artifact_manifest(preview['artifacts'])
        validation = preview['validation']
        if (not isinstance(validation, dict) or validation.get('version') != 'desktop/1'
                or not isinstance(validation.get('issues'), list) or len(validation['issues']) > 1000):
            raise ValueError('Invalid validation record.')
        for issue in validation['issues']:
            if not isinstance(issue, dict) or issue.get('severity') not in ('warning', 'info'):
                raise ValueError('Unresolved or unsupported validation issue.')
            _text(issue['code'], 100)
            _text(issue['message'], MAX_FILE)
        for key in ('normalization', 'scientific_processing', 'standardized'):
            if not isinstance(preview[key], dict):
                raise ValueError('Invalid review stage.')
        if preview['scientific_processing'].get('executed') is not False:
            raise ValueError('Unsupported scientific processing execution.')
        rows = preview['standardized']['rows']
        if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(not isinstance(r, dict) for r in rows):
            raise ValueError('Invalid standardized rows.')
        trace = preview.get('traceability')
        if preview['schema_version'] == 'catalyst-desktop-review/2':
            rebuilt, issues = build_traceability(preview['entity'], preview['modality'], context)
            issues += context_issues(context, preview['modality'], 'toolkit_source_review' in preview)
            if trace != rebuilt or any(i['severity'] == 'error' for i in issues):
                raise ValueError('The lineage or scientific context is incomplete or inconsistent.')
            if any(r.get('canonical_subject_id') != trace['dataset']['subject_id']
                    or r.get('canonical_dataset_id') != trace['dataset']['id'] for r in rows):
                raise ValueError('Rows are not bound to the declared subject and dataset.')
        profile = preview['normalization'].get('profile', {})
        if not isinstance(profile, dict):
            raise ValueError('Invalid mapping profile.')
        if profile.get('format') == 'catalyst-mapping/1':
            checked = make_profile(**{k: profile[k] for k in ('entity', 'modality', 'source_format',
                'source_version', 'name', 'version', 'sheet', 'header_row', 'rules')})
            source_hash = preview['normalization']['source_artifact_sha256']
            if (checked != profile or profile['entity'] != preview['entity'] or profile['modality'] != preview['modality']
                    or digest(encode(profile)) != preview['normalization']['profile_sha256']
                    or not any(a['sha256'] == source_hash and a['format'] == profile['source_format'] for a in preview['artifacts'])):
                raise ValueError('Mapping profile and source bindings do not match.')
            targets = [r['target'] for r in profile['rules']]
            if not rows or preview['standardized']['columns'] != targets:
                raise ValueError('Missing standardized columns or rows.')
            seen = set()
            for row in rows:
                pos = row.get('source_row')
                if type(pos) is not int or not profile['header_row'] < pos <= MAX_ROWS or pos in seen:
                    raise ValueError('Invalid or repeated source row.')
                seen.add(pos)
                if row.get('source_artifact_sha256') != source_hash or any(
                        not isinstance(row.get(t), str) or not row[t] for t in targets):
                    raise ValueError('Invalid standardized values or source binding.')
        elif preview['normalization'].get('method') == 'preserve-files-with-context/1':
            if rows or preview.get('data_status') != 'original_files_only' or preview['normalization'].get('executed') is not False:
                raise ValueError('Files-only mode cannot claim standardized results.')
        elif profile.get('id') == 'ur-reactor-toolkit-gc-bundle-v1' and profile.get('version') == '0.1.0':
            original = preview['toolkit_source_review']
            if (not isinstance(original, dict) or original.get('profile') != profile
                    or original.get('profile_content_sha256') != preview['normalization'].get('profile_sha256')
                    or preview['entity'] != 'university-of-rochester' or preview['modality'] != 'reactor'
                    or preview['standardized'].get('kind') != 'toolkit_gc_processing_revision'):
                raise ValueError('Toolkit processing provenance does not match.')
        else:
            raise ValueError('Unsupported normalization method. Use a compatible application version.')
        return payload
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise InputError('This exact revision must have valid provenance, source metadata, mapping, lineage, and approval before transfer.') from None
