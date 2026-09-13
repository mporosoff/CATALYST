"""Read consortium identities from review packages in the active SciSure group."""
from .model import InputError
from .scisure import SciSureError
from .traceability import build_traceability, validate_catalog, identity_records, lab_name

MAX_REVIEWS = 250


def load_catalog(client, group_id, progress=lambda _: None):
    from .publication import history, read_review
    connection = client.check_connection()
    if connection['group_id'] != group_id:
        raise SciSureError('The active group changed. Reconnect before reading the sample catalog.')
    entries, pending, profiles, legacy, count = [], [], [], 0, 0
    revisions, orphan_sections = {}, []
    for experiment in connection['experiments']:
        progress('Reading sample identities from SciSure experiment ' + str(experiment['experimentID']) + '…')
        destination = client.destination(experiment['experimentID'], group_id, writable=False)
        for item in history(client, destination):
            count += 1
            if count > MAX_REVIEWS:
                raise SciSureError('The active group exceeds this release’s 250-review catalog limit. No partial catalog will be used for publication.')
            if not item['manifest_id']:
                orphan_sections.append(dict(item, destination=destination))
                continue
            loaded = read_review(client, destination, item['section_id'])
            payload = loaded['revision'].value()
            if payload['id'] in revisions:
                raise SciSureError('A revision appears in more than one SciSure section. Reconcile the duplicate before catalog reuse.')
            revisions[payload['id']] = loaded['revision'].sha256
            preview = payload['preview']
            profiles.append(preview['normalization'].get('profile', {}))
            trace = preview.get('traceability')
            if not trace:
                legacy += 1
                continue
            rebuilt, issues = build_traceability(preview['entity'], preview['modality'], preview['context'])
            if trace != rebuilt or any(i['severity'] == 'error' for i in issues):
                raise SciSureError('A saved traceability declaration is inconsistent with its approved context.')
            entry = dict(trace=trace, context=preview['context'], destination=destination,
                profile=preview['normalization'].get('profile', {}),
                revision_id=payload['id'], revision_sha256=loaded['revision'].sha256, section_id=item['section_id'])
            (entries if loaded['state'] == 'complete' else pending).append(entry)
    # Fail on conflicting definitions already in the catalog, before offering them for reuse.
    saved = []
    for entry in entries + pending:
        records = identity_records(entry['trace'])
        for existing in saved:
            previous = identity_records(existing)
            if any(key in previous and previous[key] != value for key, value in records.items()):
                raise SciSureError('Conflicting saved identities require review in SciSure before catalog reuse.')
        saved.append(entry['trace'])
    complete = [e['trace'] for e in entries]
    try:
        for entry in entries + pending:
            validate_catalog(entry['trace'], complete)
            validate_catalog(entry['trace'], saved)
    except InputError:
        raise SciSureError('Saved sample lineage, root identities, or shared procedure versions conflict or have missing completed references. Reconcile them in SciSure.') from None
    client.assert_group(group_id)
    return dict(entries=entries, pending=pending, profiles=profiles, legacy_reviews=legacy, orphan_sections=orphan_sections)


def check_publication(trace, catalog):
    complete = [e['trace'] for e in catalog['entries']]
    # References must resolve to completed records; pending records still reserve their IDs.
    validate_catalog(trace, complete)
    return validate_catalog(trace, complete + [e['trace'] for e in catalog['pending']])


def search_catalog(catalog, query=''):
    """Every match retains its canonical ID; duplicate local labels never collapse records."""
    query = query.strip().casefold()
    by_id = {}
    for entry in catalog['entries']:
        trace = entry['trace']
        subject = trace.get('material') or trace.get('model')
        sid = subject['id']
        row = by_id.setdefault(sid, dict(entry=entry, subject=subject, aliases=set(), datasets=set(),
            origin_lab=trace.get('batch', {}).get('origin_lab') or subject['creator_lab']))
        row['aliases'].update(a['lab'] + ': ' + a['local_label'] for a in trace['aliases'])
        row['datasets'].add(trace['dataset']['id'])
    return [row for sid, row in sorted(by_id.items()) if not query or query in ' '.join(
        [sid, row['origin_lab'], lab_name(row['origin_lab']), row['subject'].get('batch_id', ''), *row['aliases'], *row['datasets']]).casefold()]
