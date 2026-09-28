"""Regression checks for actionable sample selection and honest historical methods."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from catalyst_desktop.model import Revision, Source, build_preview
from catalyst_desktop.workflow import workflow_required
from catalyst_desktop.workflow_gui import WorkflowUI
from catalyst_desktop.batch import BatchQueue
from catalyst_desktop.library import search_samples
from catalyst_desktop.inventory import sample_snapshot
from test_adaptive_workflow import context, preview


class Value:
    def __init__(self, value=''): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class InlineHarness(WorkflowUI):
    """Exercise the real inline staging without a display or API writes."""
    def __init__(self, kind='synthesis'):
        self.busy = False
        self.client = self.destination = self.catalog = None
        self.group_id = None
        self.entity = Value('UR')
        self.status = Value()
        self.record_type = Value(kind)
        self.context_vars = {key: Value(value) for key, value in context(kind, 51).items()}
        self.context_vars.update(inventoryMode=Value('records'), methodStatus=Value('recorded'))
        self.batch_queue = BatchQueue()
        self.native_sample_choices = []
        self.native_sample_scope = None
        self.sources = [Source.from_bytes('synthetic.dat', b'Synthetic run evidence', parse=False)]
        self.title = Value('Unfinished synthesis')
    def watched(self, value=''): return Value(value)
    def run(self, _, work, done, failed=None): done(work())
    def refresh_batch(self): self.batch_queue.validate(self.catalog or dict(entries=[], pending=[]))
    def refresh_link_options(self, *_): pass
    def refresh_workflow_requirements(self): pass


class WorkflowUsabilityTests(unittest.TestCase):
    def test_missing_synthesis_product_gives_one_actionable_error(self):
        value = preview('synthesis', specimenId='')
        issues = value['validation']['issues']
        sample_errors = [issue for issue in issues if 'specimenId' in issue['code']]
        self.assertEqual(len(sample_errors), 1)
        self.assertEqual(sample_errors[0]['code'], 'CONTEXT_specimenId')
        self.assertIn('+ Add new sample', sample_errors[0]['message'])
        self.assertNotIn('SMP', sample_errors[0]['message'])

    def test_nonblank_invalid_identity_still_cannot_be_approved(self):
        value = preview('synthesis', specimenId='local-label-is-not-an-internal-id')
        self.assertIn('ID_specimenId', {issue['code'] for issue in value['validation']['issues']})
        revision = Revision.create(value, 'Invalid sample')
        with self.assertRaises(ValueError):
            revision.approve('Researcher', 'Checked', True)

    def test_historical_method_is_explicit_without_dummy_procedure(self):
        value = preview('measurement', methodStatus='not-recorded', methodId='', methodVersion='')
        self.assertFalse([issue for issue in value['validation']['issues'] if issue['severity'] == 'error'])
        self.assertIsNone(value['traceability']['dataset']['method'])
        self.assertEqual(value['traceability']['dataset']['method_status'], 'not-recorded')
        self.assertNotIn('methodId', workflow_required(value['context'], 'XRD'))
        self.assertIn('specimenId', workflow_required(value['context'], 'XRD'))
        self.assertIn('acquiredAt', workflow_required(value['context'], 'XRD'))

    def test_unknown_method_does_not_hide_a_selected_or_required_procedure(self):
        for kind, changes in (
                ('measurement', {}), ('measurement', dict(methodId='', methodVersion='', procedureSourceRevisionId='old')),
                ('synthesis', dict(methodId='', methodVersion='')), ('computation', dict(methodId='', methodVersion=''))):
            with self.subTest(kind=kind, changes=changes):
                value = preview(kind, methodStatus='not-recorded', **changes)
                self.assertIn('METHOD_STATUS', {issue['code'] for issue in value['validation']['issues']})

    def test_inline_product_stages_unapproved_definition_and_preserves_run(self):
        app = InlineHarness()
        before = {key: value.get() for key, value in app.context_vars.items()}
        sources = tuple(app.sources)
        app.stage_linked_sample(dict(localSampleId='SYNTHETIC product', sampleDescription='Synthetic supported material', inventoryMode='records'))
        self.assertEqual(app.record_type.get(), 'synthesis')
        self.assertEqual(tuple(app.sources), sources)
        self.assertEqual(app.title.get(), 'Unfinished synthesis')
        for key in ('datasetId', 'acquiredBy', 'acquiredAt', 'procedureId', 'procedureVersion', 'synthesisExecutionId'):
            self.assertEqual(app.context_vars[key].get(), before[key], key)
        self.assertEqual(len(app.batch_queue.items), 1)
        sample = app.batch_queue.items[0]
        self.assertIsNone(sample.approval)
        self.assertEqual(sample.status, 'needs_review')
        self.assertNotEqual(app.context_vars['specimenId'].get(), before['specimenId'])
        self.assertEqual(app.context_vars['sampleSourceRevisionId'].get(), sample.revision.value()['id'])
        self.assertEqual(app.context_vars['sampleSourceRevisionSha256'].get(), sample.revision.sha256)
        self.assertEqual(sample.revision.value()['preview']['traceability']['material']['id'], app.context_vars['specimenId'].get())

    def test_saved_sample_selection_inherits_native_link_and_clears_it_for_records_only(self):
        app = InlineHarness('measurement')
        definition = preview('sample')
        revision = Revision.create(definition, 'Sample')
        entry = dict(trace=definition['traceability'], context=definition['context'],
            revision_id=revision.value()['id'], revision_sha256=revision.sha256)
        row = search_samples(dict(entries=[entry]))[0]
        row['entry']['context']['inventoryMode'] = 'native'
        app.apply_sample_choice(row)
        self.assertEqual(app.context_vars['inventoryMode'].get(), 'native')
        row = deepcopy(row)
        row['entry']['context']['inventoryMode'] = 'records'
        app.apply_sample_choice(row)
        self.assertEqual(app.context_vars['inventoryMode'].get(), 'records')

    def test_inline_procedure_document_preserves_measurement_and_stages_exact_source(self):
        app = InlineHarness('measurement')
        app.context_vars['methodStatus'].set('not-recorded')
        before = {key: value.get() for key, value in app.context_vars.items()}
        original_sources = tuple(app.sources)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic-procedure.pdf'
            path.write_bytes(b'SYNTHETIC procedure document')
            app.stage_linked_procedure(dict(procedureName='Synthetic XRD method', procedureVersion='2',
                procedureType='measurement', procedureModality='XRD', procedureText='', procedureReference=''), [path])
        self.assertEqual(app.record_type.get(), 'measurement')
        self.assertEqual(tuple(app.sources), original_sources)
        for key in ('datasetId', 'specimenId', 'acquiredBy', 'acquiredAt'):
            self.assertEqual(app.context_vars[key].get(), before[key])
        procedure = app.batch_queue.items[0]
        self.assertIsNone(procedure.approval)
        self.assertEqual(procedure.sources[0].content, b'SYNTHETIC procedure document')
        self.assertEqual(procedure.revision.value()['preview']['context']['uploadMode'], 'originals')
        self.assertEqual(app.context_vars['procedureSourceRevisionId'].get(), procedure.revision.value()['id'])
        self.assertEqual(app.context_vars['methodVersion'].get(), '2')
        self.assertEqual(app.context_vars['methodStatus'].get(), 'recorded')

    def test_native_protocol_reference_can_be_staged_without_switching_record_type(self):
        app = InlineHarness('synthesis')
        before = {key: value.get() for key, value in app.context_vars.items()}
        procedure_id = context('procedure')['procedureId']
        app.stage_linked_procedure(dict(procedureId=procedure_id, procedureName='Native synthesis protocol',
            procedureVersion='3', procedureType='synthesis', procedureModality='synthesis',
            procedureReference='https://sandbox.elabjournal.com/api/v1/protocols/version/72', procedureText=''))
        self.assertEqual(app.record_type.get(), 'synthesis')
        self.assertEqual(app.context_vars['procedureId'].get(), procedure_id)
        self.assertEqual(app.context_vars['procedureVersion'].get(), '3')
        for key in ('datasetId', 'specimenId', 'synthesisExecutionId', 'acquiredBy', 'acquiredAt'):
            self.assertEqual(app.context_vars[key].get(), before[key])
        self.assertFalse(app.batch_queue.items[0].sources)
        self.assertIsNone(app.batch_queue.items[0].approval)

    def test_native_id_search_returns_existing_registration_once(self):
        app = InlineHarness('measurement')
        definition = preview('sample')
        revision = Revision.create(definition, 'Sample')
        entry = dict(trace=definition['traceability'], context=definition['context'],
            revision_id=revision.value()['id'], revision_sha256=revision.sha256)
        sample = dict(sampleID=700, sampleTypeID=600, name='External sample', archived=False,
            altID=definition['traceability']['material']['id'], description='External material', meta=[])
        app.client = SimpleNamespace(origin='https://sandbox.elabjournal.com')
        app.group_id = 7
        app.native_sample_scope = (app.client.origin, app.group_id)
        app.native_sample_choices = [dict(source='native', sample=sample,
            native=dict(tenant=app.client.origin, group_id=7, sample_id=700, sample_type_id=600, snapshot_sha256=sample_snapshot(sample)),
            label='External sample · SciSure 700')]
        catalog = dict(entries=[entry])
        rows = app.sample_choices(catalog, '700')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['subject']['id'], sample['altID'])
        self.assertEqual(len(app.sample_choices(catalog)), 1)
        app.group_id = 8
        self.assertEqual(app.sample_choices(catalog, '700'), [], 'Never reuse another group’s cached native choices.')

    def test_every_record_captures_its_chosen_run_before_review(self):
        app = InlineHarness()
        destination = dict(tenant='https://sandbox.elabjournal.com', group_id=7,
            experiment_id=42, experiment_name='Selected synthesis run', study_id=11, project_id=12)
        value = preview('procedure')
        app.prepare_inventory_preview(value, {}, None, destination)
        self.assertEqual(value['publication_destination'], destination)
        destination['experiment_id'] = 99
        self.assertEqual(value['publication_destination']['experiment_id'], 42)


if __name__ == '__main__': unittest.main()
