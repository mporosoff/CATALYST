"""Synthetic cross-lab identity, lineage, technique, and SciSure catalog tests."""
from copy import deepcopy
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from catalyst_desktop.model import Source, Revision, InputError, build_preview, make_profile
from catalyst_desktop.traceability import (LABS, lab_id, new_id, identity_lab,
    build_traceability, validate_catalog)
from catalyst_desktop.catalog import load_catalog, search_catalog, check_publication
from catalyst_desktop.scisure import SciSureClient, SciSureError
from catalyst_desktop.publication import Publisher
from desktop_fixtures import physical_context, computational_context, uid
from test_desktop import FakeSciSure, review


def trace(context=None, lab='Rochester', modality='synthesis'):
    result, issues = build_traceability(lab, modality, context or physical_context())
    if issues:
        raise AssertionError(issues)
    return result


def make_review(modality, context, source, rules, lab='Rochester'):
    profile = make_profile(lab, modality, 'csv', 'synthetic-v1', modality + ' test', 1, 'Table', 1, rules)
    return Revision.create(build_preview([source], lab, modality, context, profile), 'Synthetic ' + modality)


class TraceabilityTests(unittest.TestCase):
    def test_six_labs_resolve_to_stable_namespaces(self):
        self.assertEqual(len(LABS), 6)
        for key, (code, label) in LABS.items():
            self.assertEqual(lab_id(code), key)
            self.assertEqual(lab_id(label), key)
            first, second = new_id(label, 'SMP'), new_id(key, 'SMP')
            self.assertNotEqual(first, second)
            self.assertEqual(identity_lab(first, 'SMP'), key)
        with self.assertRaises(InputError): lab_id('invented seventh lab')

    def test_shared_procedure_and_same_label_never_merge_labs_or_batches(self):
        first = trace()
        second = trace(physical_context('Northwestern'), 'Northwestern')
        third = trace(physical_context('Rochester', 2))
        self.assertEqual(first['batch']['procedure'], second['batch']['procedure'])
        self.assertNotEqual(first['batch']['id'], second['batch']['id'])
        self.assertNotEqual(first['batch']['synthesis_execution']['id'], third['batch']['synthesis_execution']['id'])
        self.assertEqual(validate_catalog(second, [first]), [])  # same label, different lab namespace
        ambiguous = validate_catalog(third, [first, second])
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(len(ambiguous[0]['canonical_ids']), 2)  # even reused labels in one lab stay distinct

    def test_missing_procedure_version_and_forged_lab_id_block(self):
        context = physical_context(procedureVersion='', originLab='SLAC')
        _, issues = build_traceability('Rochester', 'synthesis', context)
        codes = {i['code'] for i in issues}
        self.assertIn('TRACE_procedureVersion', codes)
        self.assertIn('ID_LAB_batchId', codes)
        self.assertIn('SYNTHESIS_LAB', codes)

    def test_sample_moves_without_changing_its_synthesis_origin(self):
        root = trace()
        moved = physical_context(acquisitionLab='SLAC', datasetId=uid('SLAC', 'DS'),
            custodyFromLab='Rochester', custodyRecord='Synthetic shipment 1', receivedAt='2026-09-11')
        measured = trace(moved, 'SLAC', 'XRD')
        self.assertEqual(measured['material'], root['material'])
        self.assertEqual(measured['batch'], root['batch'])
        self.assertEqual(measured['dataset']['acquisition_lab'], 'slac')
        self.assertEqual(measured['submitting_lab'], 'university-of-rochester')
        self.assertEqual(measured['source_lab'], 'slac')
        validate_catalog(measured, [root])
        moved.pop('custodyRecord')
        _, issues = build_traceability('SLAC', 'XRD', moved)
        self.assertIn('TRACE_custodyRecord', [i['code'] for i in issues])

    def test_cross_lab_aliquot_requires_parent_and_handoff(self):
        root = trace()
        c = physical_context(acquisitionLab='SLAC', sampleCreatedLab='SLAC', specimenId=uid('SLAC', 'SMP'),
            datasetId=uid('SLAC', 'DS'), materialKind='aliquot', parentSampleId=uid('UR', 'SMP'), materialState='split, untreated')
        child = trace(c, 'SLAC', 'XRD')
        with self.assertRaises(InputError): validate_catalog(child, [root])
        c.update(custodySampleId=uid('UR', 'SMP'), custodyFromLab='UR', custodyRecord='Synthetic handoff', receivedAt='2026-09-11')
        child = trace(c, 'SLAC', 'XRD')
        validate_catalog(child, [root])
        with self.assertRaises(InputError): validate_catalog(child, [])
        c['parentSampleId'] = c['specimenId']
        _, issues = build_traceability('SLAC', 'XRD', c)
        self.assertIn('SELF_PARENT', [i['code'] for i in issues])

    def test_identity_mutation_duplicate_root_and_changed_procedure_are_blocked(self):
        root = trace()
        changed = deepcopy(root)
        changed['material']['state'] = 'reduced'
        with self.assertRaises(InputError): validate_catalog(changed, [root])
        duplicate = trace(physical_context(specimenId=uid('UR', 'SMP', 2), datasetId=uid('UR', 'DS', 2)))
        with self.assertRaises(InputError): validate_catalog(duplicate, [root])
        conflict = trace(physical_context('SLAC', procedureReference='Different procedure document'), 'SLAC')
        with self.assertRaises(InputError): validate_catalog(conflict, [root])

    def test_theoretical_models_do_not_require_a_physical_batch(self):
        c = computational_context()
        model = trace(c, 'VT', 'computational')
        self.assertNotIn('batch', model)
        self.assertNotIn('material', model)
        validate_catalog(model, [])
        c.update(modelRelation='represents', relatedSampleIds=uid('UR', 'SMP'), modelLinkEvidence='Synthetic structural correspondence')
        linked = trace(c, 'VT', 'computational')
        with self.assertRaises(InputError): validate_catalog(linked, [])
        validate_catalog(linked, [trace()])
        c['modelRelation'] = 'no physical link'
        _, issues = build_traceability('VT', 'computational', c)
        self.assertIn('MODEL_RELATION', [i['code'] for i in issues])

    def test_rows_cannot_silently_merge_different_samples(self):
        source = Source.from_bytes('mixed.csv', b'mass,name\n72,001\n80,OTHER\n')
        _, _, revision = review(source)
        self.assertIn('SUBJECT_MISMATCH', [i['code'] for i in revision.value()['preview']['validation']['issues']])
        with self.assertRaises(InputError): revision.approve('Tester', 'Reviewed', True)

    def test_requested_techniques_have_explicit_context_and_row_subjects(self):
        cases = [
            ('XRD', 'two_theta_deg', 'degree (2theta)', '20', dict(axisUnit='degree (2theta)', signalUnit='counts', radiation='Synthetic radiation', geometry='Synthetic geometry', calibration='CAL1')),
            ('XAFS/XANES', 'energy_eV', 'eV', '8900', dict(axisUnit='eV', signalUnit='normalized absorption', absorberEdge='Synthetic edge', detectionMode='Synthetic mode', energyReference='Synthetic foil', calibration='CAL1')),
            *[(t, 'temperature_K', 'degC', '100', dict(pretreatment='Synthetic pretreatment', gasComposition='Synthetic gas', rampProgram='Synthetic ramp', flowBasis='Synthetic flow', catalystMassMg='72', signalUnit='counts', calibration='CAL1')) for t in ('TPR', 'TPD', 'TPO')],
        ]
        for modality, axis, unit, x, extra in cases:
            with self.subTest(modality=modality):
                source = Source.from_bytes('synthetic.csv', f'x,y\n{x},0.12\n'.encode())
                revision = make_review(modality, physical_context(**extra), source,
                    [dict(source='x', target=axis, unit=unit), dict(source='y', target='signal', unit='as recorded')])
                revision.approve('Tester', 'Reviewed', True)
                self.assertEqual(revision.value()['preview']['standardized']['rows'][0]['canonical_subject_id'], uid('UR', 'SMP'))
                extra.pop('calibration')
                invalid = make_review(modality, physical_context(**extra), source,
                    [dict(source='x', target=axis, unit=unit), dict(source='y', target='signal', unit='as recorded')])
                with self.assertRaises(InputError): invalid.approve('Tester', 'Reviewed', True)

    def test_co_uptake_units_and_signed_computational_energies(self):
        source = Source.from_bytes('uptake.csv', b'uptake\n125\n')
        revision = make_review('CO uptake', physical_context(pretreatment='Synthetic pretreatment',
            adsorptionTemperature='25 degC', uptakeBasis='per g catalyst, stated conditions',
            stoichiometry='not calculated', calibration='CAL1'), source,
            [dict(source='uptake', target='uptake_mol_g', unit='umol/g')])
        revision.approve('Tester', 'Reviewed', True)
        self.assertEqual(revision.value()['preview']['standardized']['rows'][0]['uptake_mol_g'], '0.000125')
        source = Source.from_bytes('calculation.csv', b'energy\n-123.456\n')
        revision = make_review('computational', computational_context(), source,
            [dict(source='energy', target='computed_energy_eV', unit='eV per configuration')], 'VT')
        revision.approve('Tester', 'Reviewed', True)
        self.assertEqual(revision.value()['preview']['standardized']['rows'][0]['computed_energy_eV'], '-123.456')


class MultiExperimentAPI(FakeSciSure):
    def __init__(self):
        super().__init__()
        self.second = dict(self.experiment, experimentID=43, name='Synthetic second lab experiment')
        self.buckets = {42: self.sections, 43: []}

    def __call__(self, url, method, headers, body):
        path = urlsplit(url).path
        if path == '/api/v1/experiments':
            return self.response(dict(data=[self.experiment, self.second], hasNextPage=False))
        if path == '/api/v1/experiments/43':
            return self.response(self.second)
        if path.endswith('/sections'):
            eid = int(path.split('/')[4])
            previous = self.sections
            self.sections = self.buckets[eid]
            try:
                return super().__call__(url.replace('/experiments/43/', '/experiments/42/'), method, headers, body)
            finally:
                self.sections = previous
        return super().__call__(url, method, headers, body)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.api = MultiExperimentAPI()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.source, _, self.revision = review()
        self.publisher = Publisher(self.client, self.client.destination(42))
        self.publisher.publish(self.revision, self.revision.approve('Tester', 'Reviewed', True), [self.source])

    def test_two_labs_two_experiments_one_sample_can_be_found_by_either_alias(self):
        context = physical_context(acquisitionLab='SLAC', datasetId=uid('SLAC', 'DS'), localSampleId='SLAC-local-7',
            custodyFromLab='UR', custodyRecord='Synthetic transfer', receivedAt='2026-09-11',
            axisUnit='degree (2theta)', signalUnit='counts', radiation='Synthetic radiation', geometry='Synthetic geometry', calibration='CAL1')
        source = Source.from_bytes('xrd.csv', b'x,y\n20,10\n')
        revision = make_review('XRD', context, source,
            [dict(source='x', target='two_theta_deg', unit='degree (2theta)'), dict(source='y', target='signal', unit='as recorded')], 'SLAC')
        Publisher(self.client, self.client.destination(43)).publish(revision, revision.approve('Tester', 'Reviewed', True), [source])
        catalog = load_catalog(self.client, 7)
        self.assertEqual(len(catalog['entries']), 2)
        for query in ('001', 'SLAC-local-7', uid('UR', 'SMP'), 'Rochester'):
            matches = search_catalog(catalog, query)
            self.assertEqual(len(matches), 1)
            self.assertEqual(len(matches[0]['datasets']), 2)

    def test_conflict_in_other_experiment_blocks_before_any_write(self):
        _, _, revision = review(context=physical_context(materialState='different state with same ID'))
        count = self.api.posts
        with self.assertRaises(InputError):
            Publisher(self.client, self.client.destination(43)).publish(revision, revision.approve('Tester', 'Reviewed', True), [self.source])
        self.assertEqual(self.api.posts, count)

    def test_incomplete_catalog_never_silently_approves_references(self):
        catalog = load_catalog(self.client, 7)
        parent = catalog['entries'].pop()
        catalog['pending'].append(parent)
        child = trace(physical_context(specimenId=uid('UR', 'SMP', 2), datasetId=uid('UR', 'DS', 2),
            materialKind='aliquot', parentSampleId=uid('UR', 'SMP')))
        with self.assertRaises(InputError): check_publication(child, catalog)
        self.assertEqual(search_catalog(catalog), [])
        with patch('catalyst_desktop.catalog.MAX_REVIEWS', 0):
            with self.assertRaises(SciSureError): load_catalog(self.client, 7)

    def test_mapping_version_conflict_in_another_experiment_is_blocked(self):
        _, _, changed = review(rules=[dict(source='mass', target='mass_g', unit='g')])
        count = self.api.posts
        with self.assertRaises(InputError):
            Publisher(self.client, self.client.destination(43)).publish(changed, changed.approve('Tester', 'Reviewed', True), [self.source])
        self.assertEqual(self.api.posts, count)


if __name__ == '__main__':
    unittest.main()
