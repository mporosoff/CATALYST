"""Guidance agrees with review requirements for realistic form selections."""
from copy import deepcopy
import unittest

from catalyst_desktop.guidance import field_help, required_context_keys
from catalyst_desktop.model import COMMON_CONTEXT, FIELDS, MODALITIES, MODALITY_CONTEXT, context_issues
from catalyst_desktop.traceability import (MODEL_RELATIONS, build_traceability,
    trace_context)
from desktop_fixtures import computational_context, physical_context, uid


def complete_context(modality):
    context = computational_context() if modality == 'computational' else physical_context()
    for key in MODALITY_CONTEXT[modality]:
        context.setdefault(key, 'Synthetic record')
    for key, value in {'temperatureC': '100', 'pressureKpaAbs': '101.325',
            'catalystMassMg': '72', 'intervalMin': '1'}.items():
        if key in context:
            context[key] = value
    return context


def blocking_issues(modality, context, toolkit=False):
    context = {key: value.strip() for key, value in context.items()}
    entity = 'VT' if modality == 'computational' else 'UR'
    _, issues = build_traceability(entity, modality, context)
    return [item for item in issues + context_issues(context, modality, toolkit)
        if item['severity'] == 'error']


class GuidanceTests(unittest.TestCase):
    def test_adaptive_requirements_do_not_reintroduce_legacy_synthesis_fields(self):
        from catalyst_desktop.workflow import workflow_required
        examples = (
            ('sample', 'sample', 'metadata'),
            ('procedure', 'procedure', 'metadata'),
            ('measurement', 'XRD', 'originals'),
            ('measurement', 'XRD', 'mapped'),
            ('synthesis', 'synthesis', 'metadata'),
            ('computation', 'computational', 'originals'),
        )
        for kind, modality, mode in examples:
            context = dict(workflowVersion='2', recordType=kind, uploadMode=mode)
            with self.subTest(kind=kind, modality=modality, mode=mode):
                needed = required_context_keys(modality, context)
                self.assertEqual(needed, frozenset(workflow_required(context, modality)))
                self.assertNotIn('batchId', needed)
                self.assertNotIn('synthesizedAt', needed)
                self.assertNotIn('custodyRecord', needed)
        originals = dict(workflowVersion='2', recordType='measurement', uploadMode='originals')
        self.assertNotIn('signalUnit', required_context_keys('XRD', originals))
        self.assertIn('signalUnit', required_context_keys('XRD', dict(originals, uploadMode='mapped')))
        self.assertIn('processingVersion', required_context_keys('reactor', originals, toolkit=True))

    def assert_requirements_match_review(self, modality, context, toolkit=False):
        self.assertEqual(blocking_issues(modality, context, toolkit), [])
        expected = required_context_keys(modality, context, toolkit)
        keys = set(COMMON_CONTEXT) | set(trace_context(modality)) | set(MODALITY_CONTEXT[modality])
        self.assertLessEqual(expected, keys)
        for key in keys:
            missing = dict(context, **{key: ''})
            with self.subTest(modality=modality, missing=key, toolkit=toolkit):
                self.assertEqual(bool(blocking_issues(modality, missing, toolkit)), key in expected)

    def test_every_visible_context_and_mapped_field_has_specific_help(self):
        for modality in MODALITIES:
            for key in set(COMMON_CONTEXT) | set(trace_context(modality)) | set(MODALITY_CONTEXT[modality]) | set(FIELDS):
                with self.subTest(modality=modality, key=key):
                    self.assertTrue(field_help(key, modality))
        self.assertIn('model ID', field_help('specimenId', 'computational'))
        self.assertIn('sample ID', field_help('specimenId', 'XRD'))
        self.assertIn('image', field_help('technique', 'imaging'))
        self.assertNotEqual(field_help('technique', 'imaging'), field_help('technique', 'spectroscopy'))
        self.assertEqual(field_help('unknown_field'), '')

    def test_upload_mapping_connection_and_review_controls_have_help(self):
        for key in ('title', 'entity', 'modality', 'files', 'supporting_files',
                'toolkit', 'raw_only', 'source_choice', 'sheet_choice', 'header_row',
                'source_version', 'profile_name', 'profile_version', 'mapping_target',
                'mapping_unit', 'mapping_aliases', 'tenant', 'token', 'remember',
                'experiment', 'inspect_sample', 'inspect_protocol', 'reviewer',
                'review_note', 'acknowledge', 'catalog_query'):
            with self.subTest(key=key):
                self.assertTrue(field_help(key))

    def test_required_presence_matches_actual_review_for_every_modality(self):
        for modality in MODALITIES:
            self.assert_requirements_match_review(modality, complete_context(modality))

    def test_date_is_required_by_traceability_even_though_base_check_warns(self):
        context = physical_context(acquiredAt='')
        self.assertIn('acquiredAt', required_context_keys('synthesis', context))
        self.assertTrue(any(item['code'] == 'TRACE_acquiredAt'
            for item in blocking_issues('synthesis', context)))

    def test_toolkit_adds_processing_evidence_only(self):
        context = complete_context('reactor')
        before = deepcopy(context)
        ordinary = required_context_keys('reactor', context)
        toolkit = required_context_keys('reactor', context, toolkit=True)
        self.assertEqual(toolkit - ordinary, {'processingVersion'})
        self.assertNotIn('identityNote', toolkit)
        self.assertEqual(context, before)
        context['processingVersion'] = 'Synthetic toolkit processing v1'
        self.assert_requirements_match_review('reactor', context, toolkit=True)

    def test_parent_appears_after_a_valid_derivative_choice(self):
        for kind in ('', 'batch material', 'invalid choice'):
            with self.subTest(kind=kind):
                self.assertNotIn('parentSampleId', required_context_keys('synthesis', physical_context(materialKind=kind)))
        for kind in ('aliquot', 'treated material'):
            context = physical_context(materialKind=kind, specimenId=uid('UR', 'SMP', 2),
                parentSampleId=uid('UR', 'SMP'))
            self.assertIn('parentSampleId', required_context_keys('synthesis', context))
            self.assert_requirements_match_review('synthesis', context)

    def test_handoff_requirement_resolves_lab_aliases(self):
        context = physical_context(sampleCreatedLab='UR', acquisitionLab=' Rochester ')
        self.assertFalse({'custodyFromLab', 'custodyRecord', 'receivedAt', 'custodySampleId'}
            & required_context_keys('XRD', context))
        context['acquisitionLab'] = 'SLAC'
        needed = required_context_keys('XRD', context)
        self.assertLessEqual({'custodyFromLab', 'custodyRecord', 'receivedAt'}, needed)
        self.assertNotIn('custodySampleId', needed)

    def test_any_handoff_detail_activates_its_required_companions(self):
        for key in ('custodyFromLab', 'custodySampleId', 'custodyRecord', 'receivedAt'):
            context = physical_context(**{key: 'Synthetic transfer detail'})
            with self.subTest(key=key):
                needed = required_context_keys('synthesis', context)
                self.assertLessEqual({'custodyFromLab', 'custodyRecord', 'receivedAt'}, needed)
                self.assertNotIn('custodySampleId', needed)
                context[key] = '  '
                self.assertNotIn('custodyRecord', required_context_keys('synthesis', context))

    def test_cross_lab_handoff_matches_review_and_defaults_to_current_sample(self):
        context = complete_context('XRD')
        context.update(acquisitionLab='SLAC', datasetId=uid('SLAC', 'DS'),
            custodyFromLab='UR', custodyRecord='Synthetic handoff', receivedAt='2026-09-11')
        self.assert_requirements_match_review('XRD', context)
        trace, _ = build_traceability('UR', 'XRD', context)
        self.assertEqual(trace['custody_evidence']['sample_id'], context['specimenId'])

    def test_model_links_and_evidence_follow_relationship(self):
        context = computational_context()
        self.assertNotIn('relatedSampleIds', required_context_keys('computational', context))
        self.assertNotIn('modelLinkEvidence', required_context_keys('computational', context))
        for relation in MODEL_RELATIONS[1:]:
            context = computational_context(modelRelation=relation)
            needed = required_context_keys('computational', context)
            self.assertIn('relatedSampleIds', needed)
            self.assertNotIn('modelLinkEvidence', needed)
            context.update(relatedSampleIds=uid('UR', 'SMP'), modelLinkEvidence='Synthetic relationship evidence')
            self.assertIn('modelLinkEvidence', required_context_keys('computational', context))
            self.assert_requirements_match_review('computational', context)

    def test_link_evidence_remains_required_even_for_inconsistent_relationship(self):
        context = computational_context(relatedSampleIds=uid('UR', 'SMP'))
        self.assertIn('modelLinkEvidence', required_context_keys('computational', context))
        context['relatedSampleIds'] = ' , , '
        self.assertNotIn('modelLinkEvidence', required_context_keys('computational', context))


if __name__ == '__main__':
    unittest.main()
