"""Bounded, memory-only staging with exact-review approvals and ordered transfer."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import uuid

from .model import InputError, Revision, Source, check_sources, digest, encode
from .publication import validate_approval
from .traceability import identity_records, validate_catalog


MAX_BATCH_ITEMS = 50
MAX_BATCH_BYTES = 200 * 1024 * 1024
_LOCKED = ('sending', 'complete', 'needs_attention')


@dataclass(frozen=True)
class BatchItem:
    id: str
    title: str
    status: str
    revision: Revision = field(repr=False)
    sources: tuple[Source, ...] = field(repr=False)
    approval: dict | None = field(default=None, repr=False)
    errors: tuple[str, ...] = ()
    receipt: dict | None = field(default=None, repr=False)

    @property
    def source(self):
        """The full source group, retained in its original order."""
        return self.sources


@dataclass
class _Entry:
    id: str
    revision: Revision
    sources: tuple[Source, ...]
    retained_bytes: int
    source_group: tuple[str, ...]
    approval: dict | None = None
    state: str = 'queued'
    errors: tuple[str, ...] = ()
    receipt: dict | None = None


def _snapshot(entry):
    status = entry.state
    if status == 'queued':
        status = 'blocked' if entry.errors else ('approved' if entry.approval else 'needs_review')
    return BatchItem(entry.id, entry.revision.value()['title'], status, entry.revision,
        tuple(Source(s.name, s.content, s.artifact) for s in entry.sources),
        deepcopy(entry.approval), entry.errors, deepcopy(entry.receipt))


def _keys(trace):
    result = {('identity', key) for key in identity_records(trace)}
    procedure = trace.get('procedure') or trace.get('batch', {}).get('procedure')
    if procedure:
        result.add(('procedure', procedure['id'], procedure['version']))
    return result


def _references(payload):
    trace = payload['preview'].get('traceability', {})
    result = set()
    if trace.get('sample_ref'):
        result.add(('identity', trace['sample_ref']['id']))
    if trace.get('material', {}).get('parent_sample_id'):
        result.add(('identity', trace['material']['parent_sample_id']))
    for identifier in trace.get('model', {}).get('related_sample_ids', []):
        result.add(('identity', identifier))
    for procedure in (trace.get('dataset', {}).get('method'),
            trace.get('synthesis_execution', {}).get('procedure')):
        if procedure and procedure.get('id') and procedure.get('version'):
            result.add(('procedure', procedure['id'], procedure['version']))
    for ref in (trace.get('sample_ref'), trace.get('dataset', {}).get('method')):
        if ref and ref.get('source_revision_id'):
            result.add(('revision', ref['source_revision_id']))
    if payload.get('parent'):
        result.add(('revision', payload['parent']))
    return result


class BatchQueue:
    """No file writes, implicit approvals, parallel writes, or automatic retries.

    ``validate`` checks staging order against a freshly loaded catalog. ``publish``
    checks only approved records, so an unapproved definition cannot authorize a
    dependent upload. Publisher remains responsible for fresh server checks and
    read-back verification on every transfer.
    """

    def __init__(self, max_items=MAX_BATCH_ITEMS, max_bytes=MAX_BATCH_BYTES):
        if type(max_items) is not int or not 1 <= max_items <= MAX_BATCH_ITEMS:
            raise InputError('A batch supports at most 50 records.')
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_BATCH_BYTES:
            raise InputError('A batch supports at most 200 MiB of retained data.')
        self.max_items, self.max_bytes = max_items, max_bytes
        self._entries = []
        self._catalog = dict(entries=[], pending=[])
        self._publishing = False

    @property
    def items(self):
        return tuple(_snapshot(entry) for entry in self._entries)

    @property
    def total_bytes(self):
        return sum(entry.retained_bytes for entry in self._entries)

    def __len__(self):
        return len(self._entries)

    def _entry(self, identifier):
        identifier = identifier.id if isinstance(identifier, BatchItem) else identifier
        for entry in self._entries:
            if entry.id == identifier:
                return entry
        raise InputError('Choose a record that is still in the batch.')

    def get(self, identifier):
        return _snapshot(self._entry(identifier))

    def _mutable(self, entry=None):
        if self._publishing:
            raise InputError('Wait for the current batch transfer before changing the queue.')
        if entry and entry.state in _LOCKED:
            raise InputError('This record was already attempted. Check its saved state in SciSure before making another submission.')

    def _prepare(self, revision, sources, exclude=None):
        if not isinstance(revision, Revision):
            raise InputError('Create a reviewed revision before adding it to the batch.')
        payload = revision.value()
        sources = tuple(sources)
        context = payload['preview'].get('context', {})
        metadata_only = (payload['preview'].get('schema_version') == 'catalyst-desktop-review/3'
            and context.get('uploadMode') == 'metadata' and context.get('recordType') in ('procedure', 'sample', 'synthesis'))
        if sources or not metadata_only:
            check_sources(sources)
        expected = [(a['filename'], a['sha256'], a['size_bytes']) for a in payload['preview']['artifacts']]
        actual = [(s.name, digest(s.content), len(s.content)) for s in sources]
        if expected != actual:
            raise InputError('The original files do not match this revision. Build a new review before staging.')
        source_group = tuple(sorted(digest(s.content) for s in sources))
        other = [entry for entry in self._entries if entry is not exclude]
        if any(entry.revision.value()['id'] == payload['id'] or entry.revision.sha256 == revision.sha256 for entry in other):
            raise InputError('This revision is already in the batch.')
        if source_group and any(entry.source_group == source_group for entry in other):
            raise InputError('These exact original files are already in the batch, including under other filenames.')
        # Include parsed source representations and review content in the bound,
        # rather than accounting only for the smaller on-disk original size.
        retained = len(revision.content) + sum(len(s.content) + len(encode(s.artifact)) for s in sources)
        if sum(entry.retained_bytes for entry in other) + retained > self.max_bytes:
            raise InputError('The batch exceeds its retained-data limit (at most 200 MiB). Send or reduce this batch before adding more.')
        return _Entry(str(uuid.uuid4()), Revision(bytes(revision.content), revision.sha256),
            tuple(Source(s.name, s.content, s.artifact) for s in sources), retained, source_group)

    def add(self, revision, sources, approval=None):
        self._mutable()
        if len(self._entries) >= self.max_items:
            raise InputError('The batch is full (at most 50 records).')
        entry = self._prepare(revision, sources)
        if approval is not None:
            validate_approval(entry.revision, approval)
            entry.approval = deepcopy(approval)
        self._entries.append(entry)
        self.validate(self._catalog)
        return _snapshot(entry)

    def _invalidate_dependents(self, entry):
        payload = entry.revision.value()
        changed = _keys(payload['preview'].get('traceability', {})) | {('revision', payload['id'])}
        # Iterate to propagate through registered parent/child sample chains.
        changed_entries = {entry.id}
        to_revoke = []
        while True:
            affected = [other for other in self._entries if other.id not in changed_entries
                and _references(other.revision.value()) & changed]
            if not affected:
                break
            if any(other.state in _LOCKED for other in affected):
                raise InputError('A transferred or attempted record depends on this definition. Keep the definition unchanged.')
            for other in affected:
                to_revoke.append(other)
                changed_entries.add(other.id)
                value = other.revision.value()
                changed |= _keys(value['preview']['traceability']) | {('revision', value['id'])}
        for other in to_revoke:
            other.approval = None

    def replace(self, identifier, revision, sources):
        entry = self._entry(identifier)
        self._mutable(entry)
        replacement = self._prepare(revision, sources, exclude=entry)
        self._invalidate_dependents(entry)
        replacement.id = entry.id
        self._entries[self._entries.index(entry)] = replacement
        self.validate(self._catalog)
        return _snapshot(replacement)

    def remove(self, identifier):
        entry = self._entry(identifier)
        self._mutable(entry)
        self._invalidate_dependents(entry)
        self._entries.remove(entry)
        self.validate(self._catalog)

    def move(self, identifier, new_index):
        entry = self._entry(identifier)
        self._mutable(entry)
        if type(new_index) is not int or not 0 <= new_index < len(self._entries):
            raise InputError('Choose a position within the current batch.')
        self._entries.remove(entry)
        self._entries.insert(new_index, entry)
        self.validate(self._catalog)
        return _snapshot(entry)

    def approve(self, identifier, approval):
        entry = self._entry(identifier)
        self._mutable(entry)
        self.validate(self._catalog)
        if entry.errors:
            raise InputError('Resolve this record’s review and dependency errors before approval: ' + entry.errors[0])
        validate_approval(entry.revision, approval)
        entry.approval = deepcopy(approval)
        return _snapshot(entry)

    def revoke(self, identifier):
        entry = self._entry(identifier)
        self._mutable(entry)
        entry.approval = None
        return _snapshot(entry)

    def catalog_entries(self, *, completed_only=False):
        """Selector records; callers should visibly label staged definitions."""
        result = []
        for entry in self._entries:
            if completed_only and entry.state != 'complete':
                continue
            value = entry.revision.value()
            preview = value['preview']
            if (not preview.get('traceability') or entry.state == 'needs_attention'
                    or any(issue['severity'] == 'error' for issue in preview['validation']['issues'])):
                continue
            result.append(dict(trace=deepcopy(preview['traceability']), context=deepcopy(preview['context']),
                profile=deepcopy(preview['normalization'].get('profile', {})), revision_id=value['id'],
                revision_sha256=entry.revision.sha256, queue_id=entry.id, staged=entry.state != 'complete'))
        return result

    def _validate(self, catalog, *, approved_only=False):
        from .library import validate_reference_sources
        saved = [entry['trace'] for entry in catalog.get('entries', [])]
        pending = [entry['trace'] for entry in catalog.get('pending', [])]
        source_entries = list(catalog.get('entries', []))
        known_sources = {(entry.get('revision_id'), entry.get('revision_sha256')) for entry in source_entries}
        queued_sources = {entry['queue_id']: entry for entry in self.catalog_entries()}

        def add_source(entry):
            source = queued_sources[entry.id]
            identity = source['revision_id'], source['revision_sha256']
            if identity not in known_sources:
                source_entries.append(source)
                known_sources.add(identity)

        revisions = {entry['revision_id'] for entry in catalog.get('entries', []) if entry.get('revision_id')}
        # Completed queue records remain usable even before a selector refresh.
        for entry in self._entries:
            if entry.state == 'complete':
                saved.append(entry.revision.value()['preview']['traceability'])
                revisions.add(entry.revision.value()['id'])
                add_source(entry)
        result = {}
        for entry in self._entries:
            if entry.state in _LOCKED or (approved_only and entry.approval is None):
                continue
            value = entry.revision.value()
            preview = value['preview']
            errors = [issue['message'] for issue in preview['validation']['issues'] if issue['severity'] == 'error']
            if value.get('parent') and value['parent'] not in revisions:
                errors.append('The previous revision must be saved or earlier in this batch.')
            trace = preview.get('traceability')
            try:
                if not trace:
                    raise InputError('Create a review with sample and procedure links before sending.')
                validate_catalog(trace, saved)
                validate_catalog(trace, saved + pending)
                validate_reference_sources(trace, dict(entries=source_entries))
            except InputError as exc:
                errors.append(str(exc))
            result[entry.id] = tuple(dict.fromkeys(errors))
            if not errors:
                saved.append(trace)
                revisions.add(value['id'])
                add_source(entry)
        return result

    def validate(self, catalog):
        self._catalog = deepcopy(catalog)
        results = self._validate(self._catalog)
        for entry in self._entries:
            if entry.id in results:
                entry.errors = results[entry.id]
                if entry.errors:
                    entry.approval = None
        return results

    def publish(self, publisher, catalog, progress=lambda _: None, on_change=lambda _: None, publisher_factory=None):
        """Send approved records once, in order; retain all state on first failure."""
        self._mutable()
        if any(entry.state == 'needs_attention' for entry in self._entries):
            raise InputError('A previous transfer needs attention. Check its saved state in SciSure; the batch will not retry it automatically.')
        self.validate(catalog)
        selected = [entry for entry in self._entries if entry.state == 'queued' and entry.approval is not None]
        if not selected:
            raise InputError('Review and explicitly approve at least one queued record before sending.')
        errors = self._validate(catalog, approved_only=True)
        for entry in selected:
            if errors.get(entry.id):
                raise InputError('Approve and send referenced samples/procedures first, or move their approved records earlier in the batch: '
                    + errors[entry.id][0])
            validate_approval(entry.revision, entry.approval)
            if entry.sources:
                check_sources(entry.sources)
        completed = []
        # Resolve all run destinations before any write. Each immutable review
        # keeps its own run even after the user switches the active upload form.
        publishers = {entry.id: publisher_factory(entry.revision) if publisher_factory else publisher for entry in selected}
        self._publishing = True
        try:
            for number, entry in enumerate(selected, 1):
                entry.state = 'sending'
                try:
                    on_change(_snapshot(entry))
                    receipt = publishers[entry.id].publish(entry.revision, deepcopy(entry.approval), entry.sources,
                        lambda message, n=number: progress(f'Record {n} of {len(selected)} · {message}'))
                    if (not isinstance(receipt, dict) or receipt.get('state') != 'complete'
                            or receipt.get('revision_id') != entry.revision.value()['id']
                            or receipt.get('revision_sha256') != entry.revision.sha256):
                        raise InputError('The transfer did not return a verified receipt for this revision.')
                except Exception:
                    entry.state = 'needs_attention'
                    entry.errors = ('Transfer stopped. Check this record in SciSure before any further upload; completed records remain saved.',)
                    on_change(_snapshot(entry))
                    raise
                entry.receipt = deepcopy(receipt)
                entry.state = 'complete'
                entry.errors = ()
                completed.append(_snapshot(entry))
                on_change(_snapshot(entry))
        finally:
            self._publishing = False
        return tuple(completed)
