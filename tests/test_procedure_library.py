"""Procedure reuse and sample references never re-declare research identities."""
from copy import deepcopy
import unittest

from catalyst_desktop.library import (search_procedures, procedure_context, search_samples,
    sample_context, validate_reference_sources, load_native_procedures, native_procedure_context)
from catalyst_desktop.catalog import search_catalog, load_catalog
from catalyst_desktop.model import InputError, Source, Revision, build_preview
from catalyst_desktop.scisure import SciSureError, SciSureClient
from catalyst_desktop.publication import Publisher
from desktop_fixtures import physical_context, uid
from catalyst_desktop.traceability import build_traceability
from test_desktop import FakeSciSure


def entry(trace, number=1, **context):
    return dict(trace=trace, context=context, revision_id=f'review-{number}',
        revision_sha256=f'{number:064x}', section_id=number,
        destination=dict(tenant='https://example.elabjournal.com', group_id=7, experiment_id=42))


def procedure(number=1, **changes):
    p = dict(id=uid('UR', 'PRC'), version='1', name='Shared XRD method', type='measurement',
        modality='XRD', instructions='Synthetic acquisition procedure', reference='',
        owner_lab='university-of-rochester', artifact_sha256s=[])
    p.update(changes)
    return entry(dict(schema_version='catalyst-traceability/2', procedure=p,
        source_lab='university-of-rochester', aliases=[],
        dataset=dict(id=uid('UR', 'DS', number), kind='procedure')), number)


def sample(number=2):
    return entry(dict(schema_version='catalyst-traceability/2',
        source_lab='university-of-rochester',
        material=dict(id=uid('UR', 'SMP'), creator_lab='university-of-rochester',
            kind='registered sample', description='Synthetic supported catalyst', state='as prepared', parent_sample_id=None),
        dataset=dict(id=uid('UR', 'DS', number), subject_id=uid('UR', 'SMP'), kind='sample'),
        aliases=[dict(lab='university-of-rochester', local_label='A001', canonical_id=uid('UR', 'SMP'))]),
        number, localSampleId='A001')


def measurement(method_source, sample_source, number=3):
    return entry(dict(schema_version='catalyst-traceability/2', source_lab='slac',
        sample_ref=dict(id=uid('UR', 'SMP'), source_revision_id=sample_source['revision_id'],
            source_revision_sha256=sample_source['revision_sha256']),
        dataset=dict(id=uid('SLAC', 'DS', number), subject_id=uid('UR', 'SMP'), kind='measurement',
            method=dict(id=method_source['trace']['procedure']['id'], version='1',
                source_revision_id=method_source['revision_id'], source_revision_sha256=method_source['revision_sha256'])),
        aliases=[dict(lab='slac', local_label='beamline-27', canonical_id=uid('UR', 'SMP'))]), number)


class LibraryTests(unittest.TestCase):
    def test_live_catalog_round_trip_register_once_and_reference_later(self):
        api = FakeSciSure()
        client = SciSureClient('synthetic-token', transport=api)
        publisher = Publisher(client, client.destination(42))
        def publish_record(modality, context, sources=()):
            preview = build_preview(list(sources), 'Rochester', modality, context,
                raw_only=context['uploadMode'] == 'originals')
            revision = Revision.create(preview, 'Synthetic library integration')
            publisher.publish(revision, revision.approve('Synthetic reviewer', 'Checked procedure and sample links', True), list(sources))
            return revision
        method = publish_record('procedure', dict(workflowVersion='2', recordType='procedure',
            uploadMode='metadata', datasetId=uid('UR', 'DS', 21), procedureId=uid('UR', 'PRC'),
            procedureName='Synthetic shared XRD', procedureVersion='1', procedureType='measurement',
            procedureModality='XRD', procedureText='Synthetic acquisition procedure'))
        original = publish_record('sample', dict(workflowVersion='2', recordType='sample', uploadMode='metadata',
            datasetId=uid('UR', 'DS', 22), specimenId=uid('UR', 'SMP'), localSampleId='A001',
            sampleDescription='Synthetic registered catalyst'))
        catalog = load_catalog(client, 7)
        context = dict(workflowVersion='2', recordType='measurement', uploadMode='originals',
            datasetId=uid('UR', 'DS', 23), acquiredBy='Synthetic researcher', acquiredAt='2026-09-23')
        context.update(procedure_context(search_procedures(catalog)[0]))
        context.update(sample_context(search_samples(catalog)[0]))
        source = Source.from_bytes('synthetic-xrd.raw', b'Synthetic native instrument bytes', parse=False)
        publish_record('XRD', context, [source])
        catalog = load_catalog(client, 7)
        self.assertEqual(len(catalog['entries']), 3)
        self.assertEqual(len(search_procedures(catalog)), 1)
        row = search_samples(catalog)[0]
        self.assertEqual(row['entry']['revision_id'], original.value()['id'])
        self.assertEqual(len(row['datasets']), 2)
        self.assertEqual(procedure_context(search_procedures(catalog)[0])['procedureSourceRevisionId'], method.value()['id'])

    def test_standalone_procedure_is_searchable_without_a_sample(self):
        catalog = dict(entries=[procedure()], pending=[])
        self.assertEqual(search_catalog(catalog), [])
        for query in ('XRD', 'Shared', 'Rochester', 'measurement', uid('UR', 'PRC')):
            self.assertEqual(len(search_procedures(catalog, query)), 1)
        self.assertEqual(search_procedures(catalog, kind='synthesis'), [])
        self.assertEqual(search_procedures(catalog, modality='TPR'), [])
        self.assertEqual(search_procedures(catalog, lab='SLAC'), [])
        self.assertEqual(len(search_procedures(catalog, 'shared rochester', kind='measurement', modality='XRD')), 1)

    def test_exact_duplicate_definitions_keep_all_sources_and_versions(self):
        first = procedure()
        same = procedure(2)
        next_version = procedure(3, version='2', instructions='Updated synthetic procedure')
        catalog = dict(entries=[first, same, next_version], pending=[])
        rows = search_procedures(catalog)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]['sources']), 2)
        context = procedure_context(rows[0])
        self.assertEqual(context['procedureSourceRevisionId'], first['revision_id'])
        self.assertEqual(context['procedureSourceRevisionSha256'], first['revision_sha256'])
        self.assertNotIn('acquiredAt', context)
        same['trace']['procedure']['instructions'] = 'Unversioned change'
        with self.assertRaisesRegex(InputError, 'conflicting'):
            search_procedures(catalog)

    def test_pending_definitions_and_legacy_methods_without_documents_are_not_choices(self):
        legacy, issues = build_traceability('Rochester', 'synthesis', physical_context())
        self.assertEqual(issues, [])
        catalog = dict(entries=[entry(legacy)], pending=[procedure(4)])
        rows = search_procedures(catalog)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['procedure']['type'], 'synthesis')
        self.assertEqual(rows[0]['procedure']['reference'], legacy['batch']['procedure']['reference'])
        self.assertNotEqual(rows[0]['procedure']['id'], legacy['dataset']['method']['id'])

    def test_measurement_adds_dataset_and_alias_without_replacing_sample(self):
        original, method = sample(), procedure()
        linked = measurement(method, original)
        catalog = dict(entries=[method, linked, original], pending=[])
        before = deepcopy(original)
        for query in ('beamline-27', 'A001', 'supported catalyst', uid('UR', 'SMP')):
            rows = search_samples(catalog, query)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['entry'], original)
            self.assertEqual(len(rows[0]['datasets']), 2)
            self.assertEqual(len(rows[0]['aliases']), 2)
        context = sample_context(rows[0])
        self.assertEqual(context['sampleSourceRevisionId'], original['revision_id'])
        self.assertEqual(context['specimenId'], uid('UR', 'SMP'))
        self.assertNotIn('materialState', context)
        self.assertEqual(original, before)

    def test_each_pinned_reference_requires_exact_completed_defining_review(self):
        original, method = sample(), procedure()
        linked = measurement(method, original)
        catalog = dict(entries=[method, original], pending=[])
        validate_reference_sources(linked['trace'], catalog)
        for key in ('source_revision_id', 'source_revision_sha256'):
            broken = deepcopy(linked['trace'])
            broken['sample_ref'][key] = 'changed'
            with self.assertRaisesRegex(InputError, 'missing, changed, or incomplete'):
                validate_reference_sources(broken, catalog)
        incomplete = dict(entries=[original], pending=[method])
        with self.assertRaises(InputError):
            validate_reference_sources(linked['trace'], incomplete)
        wrong = deepcopy(linked['trace'])
        wrong['dataset']['method']['id'] = 'wrong-procedure'
        with self.assertRaisesRegex(InputError, 'does not match'):
            validate_reference_sources(wrong, catalog)


class NativeClient:
    origin = 'https://example.elabjournal.com'

    def __init__(self):
        self.calls = []
        self.protocol = dict(protID=24, protVersionID=73, version=2, groupID=7, name='Synthetic native SOP',
            draft=False, deleted=False, description='Synthetic description', steps=[], vars=[])
        self.listed = [deepcopy(self.protocol)]

    def assert_group(self, group):
        self.calls.append(('group', group))
        if group != 7:
            raise SciSureError('Group changed')

    def list(self, path):
        self.calls.append(('list', path))
        return deepcopy(self.listed)

    def object(self, path):
        self.calls.append(('object', path))
        return deepcopy(self.protocol)


class NativeProcedureTests(unittest.TestCase):
    def test_native_versions_are_read_only_scoped_and_require_classification(self):
        client = NativeClient()
        rows = load_native_procedures(client, 7)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['procedure']['type'], '')
        self.assertEqual(row['source'], 'native')
        self.assertIn(('list', '/api/v1/protocols?scope=group&groupIDs=7'), client.calls)
        self.assertIn(('object', '/api/v1/protocols/version/73?%24expand=signingStatus'), client.calls)
        with self.assertRaises(InputError):
            procedure_context(row)
        with self.assertRaises(InputError):
            native_procedure_context(row, '', 'XRD', 'Rochester')
        context = native_procedure_context(row, 'measurement', 'XRD', 'Rochester')
        self.assertEqual(context['procedureReference'], 'https://example.elabjournal.com/api/v1/protocols/version/73')
        self.assertEqual(context['procedureVersion'], '2')
        self.assertTrue(context['procedureId'].startswith('CAT-UR-PRC-'))
        self.assertEqual(context['procedureText'], '')
        newer = deepcopy(row)
        newer['procedure']['version'] = '3'
        self.assertEqual(native_procedure_context(newer, 'measurement', 'XRD', 'Rochester')['procedureId'], context['procedureId'])

    def test_shared_group_visible_procedures_can_be_owned_by_another_group(self):
        client = NativeClient()
        client.protocol['groupID'] = 8
        client.listed[0]['groupID'] = 8
        self.assertEqual(len(load_native_procedures(client, 7)), 1)

    def test_drafts_deleted_changed_and_duplicate_versions_are_not_offered(self):
        for flag in ('draft', 'deleted'):
            client = NativeClient()
            client.listed[0][flag] = True
            self.assertEqual(load_native_procedures(client, 7), [])
        client = NativeClient()
        client.protocol['name'] = 'Changed name'
        with self.assertRaisesRegex(SciSureError, 'changed during retrieval'):
            load_native_procedures(client, 7)
        client = NativeClient()
        client.listed.append(deepcopy(client.listed[0]))
        with self.assertRaisesRegex(SciSureError, 'more than once'):
            load_native_procedures(client, 7)


if __name__ == '__main__':
    unittest.main()
