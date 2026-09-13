"""Adversarial inputs, stale remote state and scientific edge cases; synthetic only."""
from copy import deepcopy
from decimal import localcontext
from http.client import IncompleteRead, RemoteDisconnected
import io
import json
import zipfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from catalyst_ingest.jsonio import strict_loads
from catalyst_ingest.readers import read_artifact
from catalyst_ingest.toolkit import preview_toolkit_bundle, _integer
from catalyst_desktop.model import (Source, Revision, InputError, build_preview, make_profile,
    check_sources, load_sources, encode, digest, MAX_FILE)
from catalyst_desktop.publication import Publisher, read_review, MANIFEST, RECEIPT
from catalyst_desktop.scisure import SciSureClient, SciSureError
from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.configuration import plan_configuration, configuration_document
from desktop_fixtures import physical_context, uid
from test_desktop import FakeSciSure, review
from test_ingestion import workbook, S
from test_toolkit_bundle import fixture
from test_configuration import SchemaAPI


class InputRobustnessTests(unittest.TestCase):
    def test_ambiguous_json_is_rejected_at_the_api_boundary(self):
        for content in (b'{"id":1,"id":2}', b'{"n":NaN}', b'{"n":1e400}',
                b'{"s":"\\ud800"}', b'[' * 70 + b'0' + b']' * 70):
            with self.subTest(content=content[:30]):
                client = SciSureClient('synthetic-token', transport=lambda *_: (200, content))
                with self.assertRaises(SciSureError): client.request('/api/v1/groups/active')

    def test_json_underflow_does_not_become_zero(self):
        with self.assertRaises(InputError): Source.from_bytes('tiny.json', b'[{"value":1e-400}]')
        with self.assertRaises(InputError): Source.from_bytes('tiny.xlsx', workbook({'Data': {'A1': ('1', '1e-400')}}))

    def test_filename_rules_apply_to_tables_and_supporting_files(self):
        for name in ('bad\x00.csv', 'x\n.csv', 'CON.csv', 'bad:name.csv', 'bad.csv.', 'x' * 241 + '.csv'):
            for parse in (True, False):
                with self.subTest(name=name, parse=parse):
                    with self.assertRaises(InputError): Source.from_bytes(name, b'mass\n72\n', parse=parse)
        self.assertEqual(Source.from_bytes('nested/path/μ-data.csv', b'mass\n72\n').name, 'μ-data.csv')

    def test_parsed_artifact_mutation_cannot_change_approved_science(self):
        source, profile, _ = review()
        source.artifact['sheets']['Table']['cells']['A2']['value'] = '999'
        with self.assertRaisesRegex(InputError, 'integrity'):
            build_preview([source], 'Rochester', 'synthesis', physical_context(), profile)

    def test_metadata_copy_does_not_mutate_the_source(self):
        source, _, _ = review()
        metadata = source.metadata()
        metadata['parser']['formulas_executed'] = True
        check_sources([source])
        self.assertFalse(source.metadata()['parser']['formulas_executed'])

    def test_combined_limits_stop_the_rest_of_a_bundle_from_loading(self):
        sources = [Source.from_bytes(f'{i}.csv', b'value\n1\n2\n3\n') for i in range(3)]
        with patch('catalyst_desktop.model.MAX_CELLS', 6), patch.object(Source, 'from_path', side_effect=sources) as opened:
            with self.assertRaises(InputError): load_sources(['0.csv', '1.csv', '2.csv'])
            self.assertEqual(opened.call_count, 2)

    def test_source_size_and_filename_are_bound_to_original(self):
        source, _, _ = review()
        for change in ({'size_bytes': 1}, {'filename': 'other.csv'}):
            artifact = deepcopy(source.artifact)
            artifact.update(change)
            with self.assertRaises(InputError): check_sources([Source(source.name, source.content, artifact)])

    def styled(self, format_id, custom=''):
        styles = f'<styleSheet xmlns="{S}">{custom}<cellXfs><xf numFmtId="{format_id}"/></cellXfs></styleSheet>'
        return Source.from_bytes('styled.xlsx', workbook({'Table': {'A1': 'value', 'B1': 'name', 'A2': 0.125, 'B2': '001'}}, {'xl/styles.xml': styles}))

    def mapped(self, source, target, unit):
        profile = make_profile('Rochester', 'synthesis', 'xlsx', 'synthetic', 'Synthetic', 1, 'Table', 1,
            [dict(source='value', target=target, unit=unit)])
        return build_preview([source], 'Rochester', 'synthesis', physical_context(), profile)

    def test_excel_date_and_elapsed_time_serials_are_not_measurements(self):
        for format_id, custom in [('14', ''), ('46', ''), ('164', '<numFmts><numFmt numFmtId="164" formatCode="[h]:mm:ss"/></numFmts>')]:
            with self.subTest(format_id=format_id):
                preview = self.mapped(self.styled(format_id, custom), 'time_s', 'h')
                self.assertIsNone(preview['standardized']['rows'][0]['time_s'])
                self.assertTrue(any('serial number' in i['message'] for i in preview['validation']['issues']))

    def test_excel_percentages_require_the_stored_fraction_unit(self):
        source = self.styled('10')
        invalid = self.mapped(source, 'loading_fraction', '%')
        self.assertIsNone(invalid['standardized']['rows'][0]['loading_fraction'])
        valid = self.mapped(source, 'loading_fraction', 'fraction')
        self.assertEqual(valid['standardized']['rows'][0]['loading_fraction'], '0.125')
        Revision.create(valid, 'Stored Excel fraction').approve('Synthetic', 'Checked display and stored value', True)

    def test_quoted_unit_text_is_not_mistaken_for_an_excel_date(self):
        custom = '<numFmts><numFmt numFmtId="164" formatCode="0.00 &quot;mg&quot;"/></numFmts>'
        preview = self.mapped(self.styled('164', custom), 'mass_g', 'mg')
        self.assertEqual(preview['standardized']['rows'][0]['mass_g'], '0.000125')

    def test_hidden_data_and_merged_ranges_are_disclosed(self):
        original = workbook({'Table': {'A1': 'value', 'B1': 'name', 'A2': 72, 'B2': '001'}})
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(buffer, 'w') as out:
            for name in source.namelist():
                content = source.read(name)
                if name == 'xl/worksheets/sheet1.xml':
                    content = content.replace(b'<sheetData>', b'<cols><col min="1" max="1" hidden="1"/></cols><sheetData>')
                    content = content.replace(b'</worksheet>', b'<mergeCells><mergeCell ref="A5:B5"/></mergeCells></worksheet>')
                out.writestr(name, content)
        preview = self.mapped(Source.from_bytes('hidden.xlsx', buffer.getvalue()), 'mass_g', 'mg')
        self.assertEqual(preview['standardized']['rows'][0]['mass_g'], '0.072')
        codes = {i['code'] for i in preview['validation']['issues']}
        self.assertTrue({'HIDDEN_SPREADSHEET_DATA', 'MERGED_SPREADSHEET_CELLS'} <= codes)

    def test_two_mapped_subject_columns_cannot_hide_a_conflicting_label(self):
        source = Source.from_bytes('labels.csv', b'sample,model\n001,DIFFERENT\n')
        _, _, revision = review(source, rules=[dict(source='sample', target='specimen_id', unit='text'),
            dict(source='model', target='model_id', unit='text')])
        with self.assertRaises(InputError): revision.approve('Synthetic', 'Review', True)

    def test_impossible_diffraction_and_uptake_values_block_approval(self):
        for target, unit, value in [('two_theta_deg', 'degree (2theta)', '181'),
                ('scattering_q_A_inverse', '1/angstrom (q)', '-1'), ('uptake_mol_g', 'mol/g', '-0.1')]:
            with self.subTest(target=target):
                source = Source.from_bytes('physical.csv', f'value\n{value}\n'.encode())
                _, _, revision = review(source, rules=[dict(source='value', target=target, unit=unit)])
                with self.assertRaises(InputError): revision.approve('Synthetic', 'Review', True)

    def test_dates_and_handoffs_have_consistent_chronology(self):
        for context in (physical_context(acquiredAt='2026-9-12'), physical_context(
                custodyFromLab='SLAC', custodyRecord='Synthetic shipment', receivedAt='2026-09-09')):
            _, _, revision = review(context=context)
            with self.assertRaises(InputError): revision.approve('Synthetic', 'Review', True)

    def test_gc_normalization_is_independent_of_global_decimal_precision(self):
        with localcontext() as context:
            context.prec = 6
            first = preview_toolkit_bundle(fixture(), 'university-of-rochester', 'reactor')
        with localcontext() as context:
            context.prec = 28
            second = preview_toolkit_bundle(fixture(), 'university-of-rochester', 'reactor')
        self.assertEqual(first, second)

    def test_gc_long_decimals_and_extreme_counts(self):
        artifacts = fixture()
        summary = next(a for a in artifacts if a['filename'].endswith('_gc_summary.csv'))
        cells = summary['sheets']['Table']['cells']
        address = next(a for a, c in cells.items() if c['value'] == 'catalyst_mass_mg')
        value = '125.12345678901234567890123456789'
        cells[address[:-1] + '2']['value'] = value
        result = preview_toolkit_bundle(artifacts, 'university-of-rochester', 'reactor')
        amount = next(q for q in result['data']['summary_quantities'] if q['source_field'] == 'catalyst_mass_mg')
        self.assertEqual(amount['value_decimal'], '0.12512345678901234567890123456789')
        for value in ('1e1000000', '50001', '-1'):
            with self.assertRaises(InputError): _integer(value)

    def test_gc_raw_comparison_uses_lexical_precision(self):
        artifacts = fixture()
        raw = next(a for a in artifacts if a['filename'] == 'source.xlsx')['sheets']['Page 1']['cells']
        embedded = next(a for a in artifacts if a['filename'].endswith('_gc_analysis.xlsx'))['sheets']['Raw Original']['cells']
        raw['H6'].update(value=1.0, lexical_value='1.00000000000000001')
        embedded['H6'].update(value=1.0, lexical_value='1.00000000000000002')
        result = preview_toolkit_bundle(artifacts, 'university-of-rochester', 'reactor')
        self.assertFalse(result['validation']['embedded_raw_values_match'])

    def test_gc_missing_or_impossible_mass_and_reference_conditions_fail(self):
        for value in ('', '-1', '1e1000000'):
            with self.assertRaises(InputError):
                preview_toolkit_bundle(fixture({'catalyst_mass_mg': value}), 'university-of-rochester', 'reactor')

    def test_gc_identity_correction_is_explicit_and_22_4_is_preserved(self):
        sources = []
        for i, artifact in enumerate(fixture()):
            content = ('Synthetic fixture ' + str(i)).encode()
            artifact.update(sha256=digest(content), size_bytes=len(content))
            sources.append(Source(artifact['filename'], content, artifact))
        context = physical_context(reactorType='packed bed', temperatureC='300', pressureKpaAbs='101.325',
            catalystMassMg='125', intervalMin='22.4', flowBasis='Synthetic reference', calibration='Synthetic calibration',
            processingVersion='Synthetic reviewed processing')
        invalid = build_preview(sources, 'Rochester', 'reactor', context, toolkit=True)
        self.assertIn('PARTNER_SUBJECT_UNCONFIRMED', {i['code'] for i in invalid['validation']['issues']})
        context['identityNote'] = 'Synthetic is the producer label for source label 001; reviewed correction.'
        valid = build_preview(sources, 'Rochester', 'reactor', context, toolkit=True)
        revision = Revision.create(valid, 'Synthetic corrected GC run')
        approval = revision.approve('Synthetic', 'Reviewed original and correction', True)
        from catalyst_desktop.publication import validate_approval
        validate_approval(revision, approval)
        self.assertEqual(valid['scientific_processing']['time_axis']['interval_min'], '22.4')


class TransportRobustnessTests(unittest.TestCase):
    def test_interrupted_http_response_is_redacted_and_write_stays_uncertain(self):
        for error in (IncompleteRead(b'synthetic-token', 100), RemoteDisconnected('synthetic-token')):
            for method in ('GET', 'POST'):
                client = SciSureClient('synthetic-token', transport=lambda *_: (_ for _ in ()).throw(error))
                with self.assertRaises(SciSureError) as caught: client.request('/api/v1/experiments', method)
                self.assertEqual(caught.exception.uncertain, method == 'POST')
                self.assertNotIn('synthetic-token', str(caught.exception))

    def test_short_http_body_never_looks_successful(self):
        for method in ('GET', 'POST'):
            client = SciSureClient('synthetic-token', transport=lambda *_: (200, b'42', {'Content-Length': '200'}))
            with self.assertRaises(SciSureError) as caught: client.request('/api/v1/experiments', method)
            self.assertEqual(caught.exception.uncertain, method == 'POST')

    def test_encoded_controls_and_nested_escaping_do_not_reach_transport(self):
        calls = []
        client = SciSureClient('synthetic-token', transport=lambda *_: calls.append(True))
        for path in ('/api/v1/%0d%0a', '/api/v1/%252e%252e/x', '/api/v1/x%7f'):
            with self.assertRaises(SciSureError): client.request(path)
        self.assertEqual(calls, [])

    def test_foreign_keys_do_not_make_distinct_list_records_duplicates(self):
        for path, key, foreign in [('/api/v1/projects', 'projectID', 'userID'),
                ('/api/v1/studies', 'studyID', 'userID'), ('/api/v1/samples/42/meta', 'sampleMetaID', 'sampleID')]:
            data = [{key: 10, foreign: 7}, {key: 11, foreign: 7}]
            api = FakeSciSure()
            client = SciSureClient('synthetic-token', transport=lambda *_: api.response({'data': data}))
            self.assertEqual(client.list(path), data)

    def test_page_parameters_cannot_be_duplicated(self):
        client = SciSureClient('synthetic-token', transport=lambda *_: self.fail('Transport must not run'))
        with self.assertRaises(SciSureError): client.list('/api/v1/experiments?%24page=1')

    def test_same_record_with_integer_or_string_id_is_still_a_duplicate(self):
        api = FakeSciSure()
        client = SciSureClient('synthetic-token', transport=lambda *_: api.response({'data': [{'projectID': 10}, {'projectID': '10'}]}))
        with self.assertRaisesRegex(SciSureError, 'repeated'): client.list('/api/v1/projects')

    def test_wrong_response_shapes_are_actionable_errors(self):
        for content in (b'null', b'[]', b'"unexpected"'):
            client = SciSureClient('synthetic-token', transport=lambda *_: (200, content))
            with self.assertRaises(SciSureError): client.check_connection()

    def test_group_change_during_experiment_read_is_rejected(self):
        api, changed = FakeSciSure(), []
        def transport(url, method, headers, body):
            if urlsplit(url).path == '/api/v1/experiments/42': changed.append(True)
            if changed and urlsplit(url).path == '/api/v1/groups/active': return api.response({'groupID': 8})
            return api(url, method, headers, body)
        client = SciSureClient('synthetic-token', transport=transport)
        with self.assertRaisesRegex(SciSureError, 'group changed'): client.destination(42)

    def test_empty_catalog_checks_group_at_the_end(self):
        api, reads = FakeSciSure(), []
        api.experiment['deleted'] = True
        def transport(url, method, headers, body):
            if urlsplit(url).path == '/api/v1/groups/active':
                reads.append(True)
                return api.response({'groupID': 8 if len(reads) >= 3 else 7})
            return api(url, method, headers, body)
        with self.assertRaises(SciSureError): load_catalog(SciSureClient('synthetic-token', transport=transport), 7)


class RemoteReviewRobustnessTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeSciSure()
        self.client = SciSureClient('synthetic-token', transport=self.api)
        self.destination = self.client.destination(42)
        self.source, _, self.revision = review()
        self.approval = self.revision.approve('Synthetic reviewer', 'Reviewed original', True)
        self.publisher = Publisher(self.client, self.destination)
        self.receipt = self.publisher.publish(self.revision, self.approval, [self.source])

    def write_packet(self, mutate):
        packet = json.loads(self.api.content[self.receipt['manifest_id']])
        mutate(packet)
        packet['revision_sha256'] = digest(encode(packet['revision']))
        if isinstance(packet['approval'], dict): packet['approval']['revision_sha256'] = packet['revision_sha256']
        self.replace_file(self.receipt['manifest_id'], encode(packet))

    def replace_file(self, fid, content):
        self.api.content[fid] = content
        next(f for f in self.api.files[self.receipt['section_id']] if f['experimentFileID'] == fid)['fileSize'] = len(content)

    def load(self, originals=False):
        return read_review(self.client, self.destination, self.receipt['section_id'], originals)

    def test_non_object_manifest_and_receipt_fail_without_raw_exceptions(self):
        original_manifest = self.api.content[self.receipt['manifest_id']]
        for content in (b'[]', b'null', b'1'):
            self.replace_file(self.receipt['manifest_id'], content)
            with self.assertRaises(SciSureError): self.load()
        self.replace_file(self.receipt['manifest_id'], original_manifest)
        for content in (b'[]', b'null', b'1'):
            self.replace_file(self.receipt['receipt_id'], content)
            with self.assertRaises(SciSureError): self.load()

    def test_artifact_limits_are_checked_before_original_downloads(self):
        original = self.api.content[self.receipt['manifest_id']]
        changes = [lambda a: a.extend(deepcopy(a) * 6), lambda a: a[0].update(size_bytes=MAX_FILE + 1),
            lambda a: a[0].update(filename='../moved.csv'), lambda a: a[0].update(size_bytes=True),
            lambda a: a[0].update(format='future-format'), lambda a: a[0].update(sha256='not-a-hash')]
        for mutate in changes:
            self.replace_file(self.receipt['manifest_id'], original)
            self.write_packet(lambda p: mutate(p['revision']['preview']['artifacts']))
            source_path = '/' + str(self.receipt['files'][0]['file_id'])
            def transport(url, *args):
                self.assertFalse(urlsplit(url).path.endswith(source_path), 'An original was fetched before manifest validation')
                return self.api(url, *args)
            self.client._transport = transport
            with self.assertRaises(SciSureError): self.load(True)

    def test_approval_schema_and_required_context_cannot_be_bypassed(self):
        original = self.api.content[self.receipt['manifest_id']]
        mutations = [lambda p: p['approval'].update(reviewer=['Not a name']),
            lambda p: p['approval'].update(approved_at='2026-01-01'),
            lambda p: p['revision']['preview']['context'].update(scale=''),
            lambda p: p['revision']['preview'].update(schema_version='catalyst-desktop-review/999'),
            lambda p: p['revision']['preview']['normalization'].update(profile_sha256='0' * 64),
            lambda p: p['revision']['preview']['normalization'].update(profile={}),
            lambda p: p['revision']['preview']['standardized']['rows'][0].update(canonical_subject_id='wrong'),
            lambda p: p['revision']['preview']['standardized']['rows'].append(deepcopy(p['revision']['preview']['standardized']['rows'][0]))]
        for mutate in mutations:
            self.replace_file(self.receipt['manifest_id'], original)
            self.write_packet(mutate)
            with self.assertRaises(SciSureError): self.load()

    def test_duplicate_original_name_invalidates_even_metadata_only_receipt(self):
        files = self.api.files[self.receipt['section_id']]
        original = next(f for f in files if f['experimentFileID'] == self.receipt['files'][0]['file_id'])
        files.append(dict(original, experimentFileID=999))
        with self.assertRaises(SciSureError): self.load()

    def test_file_metadata_checksum_and_experiment_owner_are_checked(self):
        metadata = next(f for f in self.api.files[self.receipt['section_id']] if f['experimentFileID'] == self.receipt['files'][0]['file_id'])
        metadata['SHA256Hash'] = '0' * 64
        with self.assertRaises(SciSureError): self.load()
        metadata['SHA256Hash'] = self.receipt['files'][0]['sha256'].upper()
        self.assertEqual(self.load()['state'], 'complete')
        metadata['experimentID'] = 999
        with self.assertRaises(SciSureError): self.load()

    def test_readback_reports_its_integrity_scope(self):
        self.assertIn('not downloaded', self.load()['integrity'])
        self.assertEqual(self.load(True)['integrity'], 'original checksums verified')

    def test_untitled_non_catalyst_sections_do_not_break_the_catalog(self):
        self.api.sections.append(dict(expJournalID=999, sectionHeader=None, sectionType='FILE', deleted=False))
        self.assertEqual(len(load_catalog(self.client, 7)['entries']), 1)
        with self.assertRaises(SciSureError): read_review(self.client, self.destination, 999)

    def test_oversized_original_metadata_stops_before_download(self):
        metadata = next(f for f in self.api.files[self.receipt['section_id']] if f['experimentFileID'] == self.receipt['files'][0]['file_id'])
        metadata['fileSize'] = MAX_FILE + 1
        with self.assertRaises(SciSureError): self.load(True)

    def test_unknown_http_write_is_reconciled_without_duplicate(self):
        source, _, revision = review(context=physical_context(number=2))
        approval = revision.approve('Synthetic', 'Reviewed', True)
        failures = []
        def transport(url, method, headers, body):
            result = self.api(url, method, headers, body)
            if method == 'POST' and not failures:
                failures.append(True)
                raise IncompleteRead(b'synthetic-token', 100)
            return result
        self.client._transport = transport
        publisher = Publisher(self.client, self.destination)
        with self.assertRaises(SciSureError): publisher.publish(revision, approval, [source])
        self.assertIn('unknown', publisher.operations.values())
        self.assertEqual(publisher.publish(revision, approval, [source])['state'], 'complete')
        self.assertEqual(len(self.api.sections), 2)

    def test_archived_revision_is_not_recreated_after_restart(self):
        self.api.sections[0]['deleted'] = True
        posts = self.api.posts
        with self.assertRaises(SciSureError): Publisher(self.client, self.destination).publish(self.revision, self.approval, [self.source])
        self.assertEqual(self.api.posts, posts)

    def test_catalog_detects_conflicting_preexisting_root_samples(self):
        source, _, other = review(context=physical_context(specimenId=uid('UR', 'SMP', 2), datasetId=uid('UR', 'DS', 2)))
        with patch('catalyst_desktop.catalog.load_catalog', return_value=dict(entries=[], pending=[], profiles=[])):
            Publisher(self.client, self.destination).publish(other, other.approve('Synthetic', 'Reviewed', True), [source])
        with self.assertRaisesRegex(SciSureError, 'root identities'): load_catalog(self.client, 7)

    def test_duplicate_revision_in_two_sections_blocks_catalog_reuse(self):
        section = dict(self.api.sections[0], expJournalID=999)
        self.api.sections.append(section)
        self.api.files[999] = [f for f in self.api.files[self.receipt['section_id']] if f['realName'] != RECEIPT]
        with self.assertRaisesRegex(SciSureError, 'more than one'): load_catalog(self.client, 7)


class ConfigurationRobustnessTests(unittest.TestCase):
    def test_wrong_sample_type_details_never_produce_a_setup_plan(self):
        api = SchemaAPI()
        record = dict(configuration_document()['sample_type'], sampleTypeID=20, groupID=7, deleted=False)
        api.types = [record]
        def transport(url, method, headers, body):
            if urlsplit(url).path == '/api/v1/sampleTypes/20': return api.response(dict(record, sampleTypeID=99))
            return api(url, method, headers, body)
        with self.assertRaisesRegex(SciSureError, 'different sample type'):
            plan_configuration(SciSureClient('synthetic-token', transport=transport), 7)
        self.assertEqual(api.native_posts, 0)
