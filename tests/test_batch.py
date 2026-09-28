"""Synthetic memory-only queues: review boundaries, dependencies, and stop-on-error."""
from copy import deepcopy
import unittest

from catalyst_desktop.batch import BatchQueue
from catalyst_desktop.model import Source, Revision, InputError, build_preview
from desktop_fixtures import physical_context
from test_adaptive_workflow import context as adaptive_context


EMPTY = dict(entries=[], pending=[])


def record(number=1, *, parent=None, content=None, context=None):
    source = Source.from_bytes(f'synthetic-{number}.txt', content or f'Synthetic original {number}'.encode(), parse=False)
    preview = build_preview([source], 'Rochester', 'synthesis', context or physical_context(number=number), raw_only=True)
    revision = Revision.create(preview, f'Synthetic record {number}', parent=parent)
    return revision, (source,)


def approve(revision):
    return revision.approve('Synthetic reviewer', 'Checked this exact source and all declared links.', True)


class Receiver:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def publish(self, revision, approval, sources, progress):
        self.calls.append((revision, approval, sources))
        if len(self.calls) == self.fail_at:
            raise RuntimeError('Synthetic uncertain transfer failure')
        return dict(state='complete', revision_id=revision.value()['id'], revision_sha256=revision.sha256)


class BatchTests(unittest.TestCase):
    def test_staging_never_approves_or_sends_implicitly(self):
        queue = BatchQueue()
        revision, sources = record()
        item = queue.add(revision, sources)
        self.assertEqual(item.status, 'needs_review')
        self.assertIsNone(item.approval)
        receiver = Receiver()
        with self.assertRaisesRegex(InputError, 'explicitly approve'):
            queue.publish(receiver, EMPTY)
        self.assertFalse(receiver.calls)

    def test_staging_keeps_original_bytes_and_isolated_artifacts(self):
        queue = BatchQueue()
        revision, sources = record(content=b'\x00Synthetic raw bytes\r\n')
        item = queue.add(revision, sources)
        sources[0].artifact['filename'] = 'outside-mutation.txt'
        item.sources[0].artifact['filename'] = 'snapshot-mutation.txt'
        retained = queue.get(item.id)
        self.assertEqual(retained.sources[0].name, 'synthetic-1.txt')
        self.assertEqual(retained.sources[0].artifact['filename'], 'synthetic-1.txt')
        self.assertEqual(retained.sources[0].content, b'\x00Synthetic raw bytes\r\n')

    def test_duplicate_revision_and_renamed_original_group_rejected(self):
        queue = BatchQueue()
        revision, sources = record()
        queue.add(revision, sources)
        with self.assertRaisesRegex(InputError, 'revision is already'):
            queue.add(revision, sources)
        second, renamed = record(2, content=sources[0].content)
        with self.assertRaisesRegex(InputError, 'exact original files'):
            queue.add(second, renamed)
        self.assertEqual(len(queue), 1)

    def test_count_and_retained_data_limits_are_atomic(self):
        queue = BatchQueue(max_items=1)
        queue.add(*record())
        with self.assertRaisesRegex(InputError, 'batch is full'):
            queue.add(*record(2))
        self.assertEqual(len(queue), 1)
        tiny = BatchQueue(max_bytes=1)
        with self.assertRaisesRegex(InputError, 'retained-data limit'):
            tiny.add(*record())
        self.assertEqual(len(tiny), 0)
        self.assertEqual(tiny.total_bytes, 0)

    def test_mismatched_originals_are_rejected(self):
        revision, _ = record()
        _, other = record(2)
        with self.assertRaisesRegex(InputError, 'do not match'):
            BatchQueue().add(revision, other)

    def test_approval_binds_exact_revision_and_cannot_be_mutated(self):
        queue = BatchQueue()
        revision, sources = record()
        item = queue.add(revision, sources)
        other, _ = record(2)
        with self.assertRaises(InputError):
            queue.approve(item.id, approve(other))
        approval = approve(revision)
        snapshot = queue.approve(item.id, approval)
        approval['reviewer'] = 'Changed caller'
        snapshot.approval['reviewer'] = 'Changed snapshot'
        self.assertEqual(queue.get(item.id).approval['reviewer'], 'Synthetic reviewer')

    def test_editing_clears_approval_but_retains_queue_position(self):
        queue = BatchQueue()
        revision, sources = record()
        item = queue.add(revision, sources, approve(revision))
        updated = Revision.create(revision.value()['preview'], 'Corrected title')
        replacement = queue.replace(item.id, updated, sources)
        self.assertEqual(replacement.id, item.id)
        self.assertEqual(replacement.status, 'needs_review')
        self.assertIsNone(replacement.approval)
        self.assertEqual(queue.items[0].title, 'Corrected title')

    def test_revision_parent_must_come_first_and_removal_revokes_dependent(self):
        queue = BatchQueue()
        parent, parent_sources = record()
        child, child_sources = record(2, parent=parent.value()['id'])
        first = queue.add(parent, parent_sources, approve(parent))
        second = queue.add(child, child_sources, approve(child))
        self.assertEqual(second.status, 'approved')
        queue.move(second.id, 0)
        self.assertEqual(queue.get(second.id).status, 'blocked')
        self.assertIsNone(queue.get(second.id).approval)
        queue.move(second.id, 1)
        queue.approve(second.id, approve(child))
        queue.remove(first.id)
        self.assertEqual(queue.get(second.id).status, 'blocked')
        self.assertIsNone(queue.get(second.id).approval)

    def test_unapproved_parent_cannot_authorize_approved_child(self):
        queue = BatchQueue()
        parent, parent_sources = record()
        child, child_sources = record(2, parent=parent.value()['id'])
        queue.add(parent, parent_sources)
        queue.add(child, child_sources, approve(child))
        receiver = Receiver()
        with self.assertRaisesRegex(InputError, 'referenced samples/procedures first'):
            queue.publish(receiver, EMPTY)
        self.assertFalse(receiver.calls)

    def test_only_approved_records_send_and_completed_are_never_resent(self):
        queue = BatchQueue()
        one, source_one = record()
        two, source_two = record(2)
        staged = queue.add(one, source_one)
        queued = queue.add(two, source_two, approve(two))
        receiver = Receiver()
        completed = queue.publish(receiver, EMPTY)
        self.assertEqual([item.id for item in completed], [queued.id])
        self.assertEqual(receiver.calls[0][0].sha256, two.sha256)
        self.assertEqual(queue.get(staged.id).status, 'needs_review')
        queue.approve(staged.id, approve(one))
        queue.publish(receiver, EMPTY)
        self.assertEqual(len(receiver.calls), 2)
        self.assertEqual(receiver.calls[1][0].sha256, one.sha256)
        self.assertTrue(all(item.status == 'complete' for item in queue.items))

    def test_first_failure_preserves_completed_and_blocks_blind_retries(self):
        queue = BatchQueue()
        for number in range(1, 4):
            revision, sources = record(number)
            queue.add(revision, sources, approve(revision))
        receiver = Receiver(fail_at=2)
        with self.assertRaisesRegex(RuntimeError, 'Synthetic uncertain'):
            queue.publish(receiver, EMPTY)
        self.assertEqual([item.status for item in queue.items], ['complete', 'needs_attention', 'approved'])
        self.assertIsNotNone(queue.items[0].receipt)
        with self.assertRaisesRegex(InputError, 'will not retry'):
            queue.publish(receiver, EMPTY)
        with self.assertRaisesRegex(InputError, 'already attempted'):
            queue.remove(queue.items[1].id)
        self.assertEqual(len(receiver.calls), 2)

    def test_mismatched_receipt_is_not_success(self):
        class WrongReceipt(Receiver):
            def publish(self, *args):
                return dict(state='complete', revision_id='another-revision', revision_sha256='wrong')
        queue = BatchQueue()
        revision, sources = record()
        item = queue.add(revision, sources, approve(revision))
        with self.assertRaisesRegex(InputError, 'verified receipt'):
            queue.publish(WrongReceipt(), EMPTY)
        self.assertEqual(queue.get(item.id).status, 'needs_attention')

    def test_queue_cannot_be_edited_during_transfer(self):
        queue = BatchQueue()
        revision, sources = record()
        item = queue.add(revision, sources, approve(revision))
        observed = []
        def changed(snapshot):
            if snapshot.status == 'sending':
                try:
                    queue.remove(item.id)
                except InputError as exc:
                    observed.append(str(exc))
        queue.publish(Receiver(), EMPTY, on_change=changed)
        self.assertTrue(observed)
        self.assertIn('current batch transfer', observed[0])

    def test_catalog_selector_entries_are_isolated_and_show_staging(self):
        queue = BatchQueue()
        revision, sources = record()
        queue.add(revision, sources)
        entries = queue.catalog_entries()
        self.assertTrue(entries[0]['staged'])
        entries[0]['context']['specimenId'] = 'mutated'
        self.assertNotEqual(queue.catalog_entries()[0]['context']['specimenId'], 'mutated')
        self.assertEqual(queue.catalog_entries(completed_only=True), [])

    def adaptive_batch(self):
        queue = BatchQueue()
        definitions = []
        for number, kind in ((11, 'procedure'), (12, 'sample')):
            fields = adaptive_context(kind, number)
            value = build_preview([], 'UR', kind, fields)
            revision = Revision.create(value, 'Synthetic ' + kind)
            definitions.append(queue.add(revision, (), approve(revision)))
        procedure, sample = definitions
        fields = adaptive_context('measurement', 13,
            sampleSourceRevisionId=sample.revision.value()['id'], sampleSourceRevisionSha256=sample.revision.sha256,
            procedureSourceRevisionId=procedure.revision.value()['id'], procedureSourceRevisionSha256=procedure.revision.sha256)
        source = Source.from_bytes('synthetic-xrd.raw', b'Synthetic raw diffraction bytes', parse=False)
        revision = Revision.create(build_preview([source], 'UR', 'XRD', fields, raw_only=True), 'Synthetic XRD')
        measured = queue.add(revision, (source,), approve(revision))
        return queue, procedure, sample, measured

    def test_adaptive_metadata_definitions_and_pinned_data_send_in_order(self):
        queue, procedure, sample, measured = self.adaptive_batch()
        self.assertEqual([item.status for item in queue.items], ['approved'] * 3)
        self.assertEqual(queue.get(procedure.id).sources, ())
        self.assertEqual(queue.get(sample.id).sources, ())
        receiver = Receiver()
        queue.publish(receiver, EMPTY)
        self.assertEqual([call[0].sha256 for call in receiver.calls],
            [procedure.revision.sha256, sample.revision.sha256, measured.revision.sha256])

    def test_replacing_queued_method_requires_picking_its_new_source_revision(self):
        queue, procedure, _, measured = self.adaptive_batch()
        replacement = Revision.create(procedure.revision.value()['preview'], 'New reviewed title, same method')
        queue.replace(procedure.id, replacement, ())
        item = queue.get(measured.id)
        self.assertEqual(item.status, 'blocked')
        self.assertIsNone(item.approval)
        self.assertTrue(any('source review is missing' in message for message in item.errors))

    def test_unapproved_staged_method_cannot_supply_source_pin_at_send_time(self):
        queue, procedure, _, _ = self.adaptive_batch()
        queue.revoke(procedure.id)
        receiver = Receiver()
        with self.assertRaisesRegex(InputError, 'referenced samples/procedures first'):
            queue.publish(receiver, EMPTY)
        self.assertFalse(receiver.calls)

    def test_invalid_definitions_are_excluded_from_selectors(self):
        fields = adaptive_context('procedure', 17, procedureText='')
        revision = Revision.create(build_preview([], 'UR', 'procedure', fields), 'Unfinished synthetic method')
        queue = BatchQueue()
        item = queue.add(revision, ())
        self.assertEqual(item.status, 'blocked')
        self.assertEqual(queue.catalog_entries(), [])

    def test_completed_queue_sources_are_not_duplicated_after_catalog_refresh(self):
        queue, procedure, sample, measured = self.adaptive_batch()
        queue.revoke(measured.id)
        queue.publish(Receiver(), EMPTY)
        refreshed = dict(entries=deepcopy(queue.catalog_entries(completed_only=True)), pending=[])
        queue.validate(refreshed)
        self.assertEqual(queue.get(measured.id).errors, ())
        self.assertEqual(queue.approve(measured.id, approve(measured.revision)).status, 'approved')

    def test_batch_round_trip_uses_existing_verified_publisher(self):
        from catalyst_desktop.catalog import load_catalog
        from catalyst_desktop.publication import Publisher
        from catalyst_desktop.scisure import SciSureClient
        from test_desktop import FakeSciSure
        queue, _, _, _ = self.adaptive_batch()
        api = FakeSciSure()
        client = SciSureClient('synthetic-token', transport=api)
        publisher = Publisher(client, client.destination(42))
        completed = queue.publish(publisher, EMPTY)
        self.assertEqual(len(completed), 3)
        self.assertEqual(len(load_catalog(client, 7)['entries']), 3)
        self.assertTrue(all(item.receipt.get('verified_at') for item in completed))


if __name__ == '__main__':
    unittest.main()
