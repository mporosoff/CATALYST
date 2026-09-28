"""Read consortium identities from review packages in the active SciSure group."""
from .model import InputError
from .scisure import SciSureError
from .traceability import build_traceability, validate_catalog, identity_records
from .library import search_samples, search_procedures, validate_reference_sources

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
            # Even a section whose manifest upload was interrupted reserves its
            # revision ID; otherwise reconnecting could create it elsewhere.
            if item['revision_id'] in revisions:
                raise SciSureError('A revision appears in more than one SciSure section. Reconcile the duplicate before catalog reuse.')
            revisions[item['revision_id']] = item['section_id']
            if not item['manifest_id']:
                orphan_sections.append(dict(item, destination=destination))
                continue
            loaded = read_review(client, destination, item['section_id'])
            payload = loaded['revision'].value()
            preview = payload['preview']
            profiles.append(preview['normalization'].get('profile', {}))
            trace = preview.get('traceability')
            if not trace:
                legacy += 1
                continue
            rebuilt, issues = build_traceability(preview['entity'], preview['modality'], preview['context'],
                artifacts=preview.get('artifacts', []))
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
        library = dict(entries=entries, pending=pending)
        # Index construction also checks standalone procedure definitions. The
        # pending entries reserve identities, but are never selectable sources.
        search_procedures(dict(entries=entries + pending))
        search_samples(dict(entries=entries + pending))
        for entry in entries + pending:
            validate_catalog(entry['trace'], complete)
            validate_catalog(entry['trace'], saved)
            validate_reference_sources(entry['trace'], library)
    except InputError:
        raise SciSureError('Saved sample lineage, root identities, or shared procedure versions conflict or have missing completed references. Reconcile them in SciSure.') from None
    client.assert_group(group_id)
    return dict(entries=entries, pending=pending, profiles=profiles, legacy_reviews=legacy,
        orphan_sections=orphan_sections, group_id=group_id, tenant=client.origin)


def check_publication(trace, catalog):
    complete = [e['trace'] for e in catalog['entries']]
    # References must resolve to completed records; pending records still reserve their IDs.
    validate_catalog(trace, complete)
    result = validate_catalog(trace, complete + [e['trace'] for e in catalog['pending']])
    validate_reference_sources(trace, catalog)
    search_procedures(dict(entries=catalog['entries'] + catalog['pending'] + [dict(trace=trace)]))
    return result


def search_catalog(catalog, query=''):
    """Every match retains its canonical ID; duplicate local labels never collapse records."""
    return search_samples(catalog, query)
