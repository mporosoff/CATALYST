"""Synthetic adaptive records, reusable dependencies, and stored-envelope boundary checks."""
from copy import deepcopy
import unittest

from catalyst_desktop.model import Source, Revision, InputError, build_preview, make_profile
from catalyst_desktop.contracts import approved_payload
from catalyst_desktop.traceability import build_traceability, validate_catalog, identity_records, new_id, identity_lab
from catalyst_desktop.workflow import workflow_required, workflow_fields
from desktop_fixtures import uid, physical_context


def context(kind, number=1, **extra):
    result = dict(workflowVersion='2', recordType=kind, datasetId=uid('UR', 'DS', number),
        uploadMode='metadata' if kind in ('procedure', 'sample', 'synthesis') else 'originals')
    if kind == 'procedure':
        result.update(procedureId=uid('UR', 'PRC'), procedureName='Synthetic diffraction method',
            procedureVersion='1', procedureType='measurement', procedureModality='XRD',
            procedureText='Synthetic instructions; use the documented instrument program.')
    if kind == 'sample':
        result.update(specimenId=uid('UR', 'SMP'), localSampleId='SYNTHETIC sample', sampleDescription='Synthetic supported material')
    if kind in ('measurement', 'synthesis', 'computation'):
        result.update(specimenId=uid('UR', 'SMP'), acquiredBy='Synthetic operator', acquiredAt='2026-09-22',
            methodId=uid('UR', 'PRC'), methodVersion='1')
    if kind == 'synthesis':
        result.update(procedureId=uid('UR', 'PRC'), procedureVersion='1', synthesisExecutionId=uid('UR', 'SYN', number))
    if kind == 'computation':
        result.update(specimenId=uid('UR', 'MDL'), modelDescription='Synthetic theoretical cluster', inputStructure='Synthetic geometry document')
    result.update(extra)
    return result


def preview(kind, number=1, sources=None, modality=None, **extra):
    fields = context(kind, number, **extra)
    modality = modality or {'procedure': 'procedure', 'sample': 'sample', 'synthesis': 'synthesis',
        'measurement': 'XRD', 'computation': 'computational'}[kind]
    if sources is None:
        sources = [] if fields['uploadMode'] == 'metadata' else [Source.from_bytes('synthetic.dat', b'SYNTHETIC native data', parse=False)]
    return build_preview(sources, 'UR', modality, fields, raw_only=fields['uploadMode'] == 'originals')


def approved(value):
    revision = Revision.create(value, 'Synthetic workflow test')
    approval = revision.approve('Synthetic reviewer', 'Reviewed synthetic record and source links.', True)
    return approved_payload(revision, approval)


class AdaptiveWorkflowTests(unittest.TestCase):
    def test_measurement_requires_links_and_execution_facts_not_synthesis_reentry(self):
        required = workflow_required(context('measurement'), 'XRD')
        self.assertEqual(required, {'recordType', 'datasetId', 'uploadMode', 'specimenId', 'acquiredBy',
            'acquiredAt', 'methodId', 'methodVersion'})
        self.assertNotIn('batchId', workflow_fields('measurement', 'XRD'))
        self.assertNotIn('radiation', required)
        result = preview('measurement', 3)
        approved(result)
        self.assertEqual(result['schema_version'], 'catalyst-desktop-review/3')
        self.assertNotIn('material', result['traceability'])
        self.assertNotIn('batch', result['traceability'])
        self.assertEqual(result['traceability']['sample_ref']['id'], uid('UR', 'SMP'))

    def test_sample_and_procedure_register_without_dummy_data_files(self):
        for kind in ('sample', 'procedure'):
            with self.subTest(kind=kind):
                result = preview(kind)
                self.assertEqual(result['artifacts'], [])
                self.assertEqual(result['data_status'], 'metadata_only')
                approved(result)
        self.assertEqual(identity_lab(new_id('UR', 'PRC'), 'PRC'), 'university-of-rochester')

    def test_procedure_requires_real_content_or_attached_document(self):
        with self.assertRaises(InputError): approved(preview('procedure', procedureText=''))
        source = Source.from_bytes('synthetic-procedure.pdf', b'SYNTHETIC protocol document', parse=False)
        value = preview('procedure', sources=[source], uploadMode='originals', procedureText='')
        approved(value)
        self.assertEqual(value['traceability']['procedure']['artifact_sha256s'], [source.artifact['sha256']])
        value['traceability']['procedure']['artifact_sha256s'] = []
        with self.assertRaises(InputError): approved(value)

    def test_measurement_rejects_missing_or_mismatched_completed_links(self):
        sample, method, measured = preview('sample', 1)['traceability'], preview('procedure', 2)['traceability'], preview('measurement', 3)['traceability']
        validate_catalog(measured, [sample, method])
        for saved in ([], [sample], [method]):
            with self.subTest(saved=len(saved)), self.assertRaises(InputError): validate_catalog(measured, saved)
        for change in ({'type': 'synthesis'}, {'modality': 'spectroscopy'}, {'instructions': '', 'reference': '', 'artifact_sha256s': []}):
            wrong = deepcopy(method)
            wrong['procedure'].update(change)
            with self.assertRaises(InputError): validate_catalog(measured, [sample, wrong])
        forged = deepcopy(measured)
        forged['sample_ref']['id'] = uid('UR', 'SMP', 42)
        with self.assertRaises(InputError): validate_catalog(forged, [sample, method])

    def test_source_less_measurement_and_forged_review_schema_are_rejected(self):
        with self.assertRaises(InputError): preview('measurement', sources=[], uploadMode='metadata')
        value = preview('measurement')
        value['artifacts'] = []
        with self.assertRaises(InputError): approved(value)
        for key in ('specimenId', 'methodId', 'methodVersion', 'acquiredBy', 'acquiredAt'):
            value = preview('measurement')
            value['context'][key] = ''
            value['traceability'], _ = build_traceability('UR', 'XRD', value['context'], value['artifacts'])
            with self.subTest(key=key), self.assertRaises(InputError): approved(value)
        value = preview('sample')
        value['schema_version'] = 'catalyst-desktop-review/2'
        with self.assertRaises(InputError): approved(value)

    def test_saved_identity_is_immutable_but_procedures_can_have_new_versions(self):
        original = preview('procedure', 1)['traceability']
        changed = preview('procedure', 2, procedureText='Changed instructions')['traceability']
        with self.assertRaises(InputError): validate_catalog(changed, [original])
        revised = preview('procedure', 2, procedureText='Changed instructions', procedureVersion='2')['traceability']
        validate_catalog(revised, [original])
        self.assertIn(uid('UR', 'PRC') + '@1', identity_records(original))
        sample = preview('sample', 3)['traceability']
        changed = preview('sample', 4, sampleDescription='Different material')['traceability']
        with self.assertRaises(InputError): validate_catalog(changed, [sample])

    def test_synthesis_links_registered_sample_and_reuses_saved_procedure(self):
        sample = preview('sample', 1)['traceability']
        procedure = preview('procedure', 2, procedureType='synthesis', procedureModality='synthesis')['traceability']
        execution = preview('synthesis', 3)
        approved(execution)
        validate_catalog(execution['traceability'], [sample, procedure])
        self.assertNotIn('batch', execution['traceability'])
        self.assertEqual(execution['traceability']['synthesis_execution']['deviations'], '')

    def test_models_do_not_require_physical_samples_unless_linked(self):
        procedure = preview('procedure', 1, procedureType='computation', procedureModality='computational')['traceability']
        calculation = preview('computation', 2)
        approved(calculation)
        validate_catalog(calculation['traceability'], [procedure])
        linked = preview('computation', 3, relatedSampleIds=uid('UR', 'SMP'), modelRelation='represents', modelLinkEvidence='Synthetic correspondence')
        with self.assertRaises(InputError): validate_catalog(linked['traceability'], [procedure])
        validate_catalog(linked['traceability'], [procedure, preview('sample', 4)['traceability']])

    def test_legacy_complete_samples_and_synthesis_procedures_are_reusable(self):
        legacy, issues = build_traceability('UR', 'synthesis', physical_context())
        self.assertEqual(issues, [])
        execution = preview('synthesis', 2, procedureId='SYNTHETIC-SOP')
        validate_catalog(execution['traceability'], [legacy])
        measurement = preview('measurement', 3, methodId='SYNTHETIC-METHOD')
        with self.assertRaises(InputError): validate_catalog(measurement['traceability'], [legacy])

    def test_old_reviews_retain_all_required_context(self):
        source = Source.from_bytes('synthetic.dat', b'SYNTHETIC data', parse=False)
        value = build_preview([source], 'UR', 'XRD', physical_context(), raw_only=True)
        self.assertEqual(value['schema_version'], 'catalyst-desktop-review/2')
        codes = {i['code'] for i in value['validation']['issues']}
        self.assertIn('CONTEXT_radiation', codes)
        self.assertIn('CONTEXT_geometry', codes)
        self.assertIn('CONTEXT_calibration', codes)
        with self.assertRaises(InputError): approved(value)

    def test_mapped_characterization_still_requires_axis_signal_units_and_valid_values(self):
        source = Source.from_bytes('synthetic.csv', b'angle,intensity\n20,100\n')
        rules = [dict(source='angle', target='two_theta_deg', unit='degree (2theta)'),
            dict(source='intensity', target='signal', unit='as recorded')]
        profile = make_profile('UR', 'XRD', 'csv', 'synthetic', 'Synthetic XRD', 1, 'Table', 1, rules)
        fields = context('measurement', uploadMode='mapped', signalUnit='counts')
        value = build_preview([source], 'UR', 'XRD', fields, profile)
        approved(value)
        self.assertNotIn('calibration', workflow_required(fields, 'XRD'))
        no_units = dict(fields, signalUnit='')
        with self.assertRaises(InputError): approved(build_preview([source], 'UR', 'XRD', no_units, profile))
        source = Source.from_bytes('synthetic.csv', b'angle,intensity\n200,100\n')
        invalid = build_preview([source], 'UR', 'XRD', fields, profile)
        self.assertIn('VALUE_INVALID', {i['code'] for i in invalid['validation']['issues']})
        invalid['validation']['issues'] = []
        with self.assertRaises(InputError): approved(invalid)


if __name__ == '__main__':
    unittest.main()
