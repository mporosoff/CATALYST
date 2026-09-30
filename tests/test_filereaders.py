"""Partner file readers, tested on synthetic look-alike files (no real research data in the repository)."""
import csv
import io
import unittest
import zipfile
from xml.sax.saxutils import escape

from catalyst_desktop import filereaders
from test_ingestion import workbook


def ssrl_scan(trajectory='Re_L-3_EXAFS', points=5):
    lines = ['# Scan Name:\tSYNTH-01', '# Run Number:\t3', '# Session Id:\t1', f'# Trajectory Name:\t{trajectory}',
        '# First Scan Create Date:\t7/19/2025 11:17:15 PM', '# This Scan Create Date:\t7/19/2025 11:45:03 PM',
        '# Lattice Spacing:\t1.920156', '# Encoder\tEnergy\tADC_01\tADC_02\tADC_03\tADC_04\tTime\tGate']
    import math
    for i in range(points):
        step = 1 / (1 + math.exp(-(i - points / 2)))  # an absorption edge in the middle of the scan
        i0 = 1000.0
        i1 = i0 * math.exp(-(0.5 + step))
        i2 = i1 * math.exp(-(0.2 + 0.5 * step))
        lines.append(f'{-600000 - i}\t{10400 + 10 * i}\t{i0}\t{i1}\t{i2}\t{100 + 50 * step}\t{i * 1000}\t1000')
    lines.append(lines[-1])  # the scan dwells at its last energy
    return '\n'.join(lines).encode()


INJECTIONS = ((2.0, 0.5, 0.0, 5.0, 91.0, 4500), (2.2, 0.6, 0.0, 5.5, 90.0, 4450), (2.0, 0.5, 0.0, 6.0, 90.0, 4400),
    (2.5, 0.5, 0.0, 6.0, 89.0, 4400))


def nu_workbook(injections=INJECTIONS, lab_conversion=None):
    """Same layout as Northwestern's template: products O–R (O labelled CO2 but is CO), unreacted CO2 in the unlabeled
    column V, reference injections in rows 5–7 averaged in row 8, and the template's own conversion formula."""
    cells = {'A1': 'Catalyst Name', 'B1': 'Synthetic CZA', 'C1': 'Mass of Catalyst Tested', 'D1': 250, 'E1': 'mg',
        'J1': 'Peak Area', 'L1': 'Injection No.', 'M1': 'Injection time(min)', 'O1': 'Concentration (umol)',
        'X1': 'CO2 Conversion (%)', 'Y1': 'H2 Conversion(%)', 'AA1': 'Product Selectivity(%)',
        'D3': 'Oven Temp. Program', 'J3': 'Hydrogen', 'K3': 'CO2', 'O3': 'Carbon Dioxide', 'P3': 'Dimethyl Ether',
        'Q3': 'Formaldehyde', 'R3': 'Methanol', 'W3': 'Carbon Balance', 'AA3': 'Carbon Dioxide',
        'J4': 'H2', 'K4': 'CO2', 'O4': 'CO2', 'P4': 'CH3OCH3', 'Q4': 'CH2O', 'R4': 'CH3OH',
        'AA4': 'CO2', 'AB4': 'CH3OCH3', 'AC4': 'CH2O', 'AD4': 'CH3OH',
        'A5': 'Research CO2', 'B5': 25, 'C5': 'mL/min', 'D5': '50C for 5 min, 20C/min to 200C', 'I5': 'RT Calibration',
        'A6': 'UHP H2', 'B6': 75, 'C6': 'mL/min', 'D6': 'Split ratio= 10:1', 'A7': 'UHP He', 'B7': 0, 'C7': 'mL/min',
        'A8': 'Pressure', 'B8': 30, 'C8': 'bar', 'Q8': ('AVERAGE(Q5:Q7)', None), 'R8': ('AVERAGE(R5:R7)', None)}
    for row in (5, 6, 7):
        cells.update({f'V{row}': 100.0, f'J{row}': 5000.0, f'Q{row}': ('(#REF!-349.32)/25.399', None)})
    for index, (co, dme, ch2o, meoh, co2, h2) in enumerate(injections):
        row = 10 + index
        lab = lab_conversion if lab_conversion is not None else 100 - co2
        cells.update({f'L{row}': index + 1, f'M{row}': 15 * index, f'J{row}': h2, f'O{row}': co, f'P{row}': dme,
            f'Q{row}': ch2o, f'R{row}': meoh, f'V{row}': co2, f'X{row}': (f'($V$8-V{row})/$V$8*100', lab),
            f'Y{row}': ('(#REF!-#REF!)/#REF!*100', None), f'AA{row}': (f'O{row}/(O{row}+P{row}/2+Q{row}+R{row})*100', 1.0)})
    return workbook({'052025': cells})


def docx(tables):
    W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    body = ''.join('<w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>'
        for c in row) + '</w:tr>' for row in table) + '</w:tbl>' for table in tables)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


class SSRLTests(unittest.TestCase):
    def test_header_and_energy_are_read(self):
        found = filereaders.read_file('scan_0003.txt', ssrl_scan())
        self.assertEqual((found['technique'], found['date']), ('XAS', '2025-07-19'))
        self.assertEqual(found['conditions']['edge'], 'Re L3-edge')
        self.assertIn('Si(220)', found['conditions']['instrument'])
        self.assertEqual(found['metadata']['Energy range (eV)'], '10400.0–10440.0')
        self.assertEqual(found['warnings'], [])

    def test_mu_is_calculated_from_the_confirmed_channels(self):
        import math
        found = filereaders.read_file('scan.txt', ssrl_scan(points=40))
        table = list(csv.DictReader(io.StringIO(found['derived'][0]['content'].decode())))
        self.assertEqual(len(table), 40)  # the repeated last energy is averaged into one point
        first = table[0]
        self.assertAlmostEqual(float(first['mu_transmission']), 0.5 + 1 / (1 + math.exp(20)), places=4)
        self.assertAlmostEqual(float(first['mu_reference']), 0.2 + 0.5 / (1 + math.exp(20)), places=4)
        self.assertAlmostEqual(float(first['mu_fluorescence']), 0.1, places=4)
        r = found['results']
        self.assertAlmostEqual(r['edge_transmission_eV'], 10595, delta=10)
        self.assertAlmostEqual(r['edge_reference_eV'], 10595, delta=10)
        self.assertIn('ADC_01 = I0', r['channel_map'])

    def test_other_text_files_are_ignored(self):
        self.assertIsNone(filereaders.read_file('notes.txt', b'just some notes'))
        self.assertEqual(filereaders.parse_trajectory('Mo_K_XANES'), ('Mo', 'K-edge', 'XANES'))


class GCWorkbookTests(unittest.TestCase):
    def test_setup_and_results_follow_the_template(self):
        found = filereaders.read_file('run.xlsx', nu_workbook())
        c = found['conditions']
        self.assertEqual((c['catalyst_mass_mg'], c['pressure_bar'], c['flow_mL_min']), ('250', '30', '100'))
        self.assertEqual(c['feed'], 'CO2 25 + H2 75 mL/min (H2:CO2 = 3:1)')
        self.assertNotIn('time_on_stream_h', c)  # the workbook only knows time since the first injection
        self.assertIn('read as CO', found['metadata']['Column labels'])
        r = found['results']
        self.assertEqual((r['injections'], r['averaged_over_last']), (4, 3))
        last = INJECTIONS[1:]
        mean = lambda values: sum(values) / len(values)
        self.assertAlmostEqual(r['co2_conversion_pct'], mean([100 - i[4] for i in last]), places=3)  # (100 - out)/100
        carbon = [i[0] + 2 * i[1] + i[2] + i[3] for i in last]
        self.assertAlmostEqual(r['carbon_balance_pct'], mean([i[4] + c for i, c in zip(last, carbon)]), places=3)
        self.assertAlmostEqual(r['selectivity_CO_pct'], mean([100 * i[0] / c for i, c in zip(last, carbon)]), places=3)
        self.assertAlmostEqual(r['selectivity_CH3OCH3_pct'], mean([100 * 2 * i[1] / c for i, c in zip(last, carbon)]), places=3)
        self.assertAlmostEqual(r['h2_conversion_pct'], mean([100 * (5000 - i[5]) / 5000 for i in last]), places=3)
        self.assertNotIn('selectivity_CO2_pct', r)
        self.assertIn('Y', found['warnings'][0])
        self.assertFalse(any('differs from the workbook' in w for w in found['warnings']))  # same formula as the lab
        self.assertEqual(r['last_injection_time_min'], 45)
        table = list(csv.DictReader(io.StringIO(found['derived'][0]['content'].decode())))
        self.assertEqual(len(table), 4)  # the reference rows 5–8 are not injections
        self.assertEqual((float(table[0]['catalyst_CO2_conversion_pct']), float(table[0]['lab_CO2_conversion_pct'])), (9, 9))
        self.assertAlmostEqual(float(table[0]['catalyst_carbon_balance_pct']), 99, places=6)

    def test_lab_value_that_disagrees_is_flagged(self):
        found = filereaders.read_file('run.xlsx', nu_workbook(lab_conversion=20.0))
        self.assertTrue(any('differs from the workbook' in w for w in found['warnings']))

    def test_empty_template_reads_setup_only(self):
        found = filereaders.read_file('template.xlsx', nu_workbook(injections=()))
        self.assertEqual(found['results'], {})
        self.assertEqual(found['derived'], [])
        self.assertIn('empty template', found['warnings'][-1])

    def test_negative_amounts_are_left_out(self):
        found = filereaders.read_file('run.xlsx', nu_workbook(injections=(INJECTIONS[0], (-1, 0.5, 0, 5, 91, 4500))))
        self.assertTrue(any('negative' in w for w in found['warnings']))
        self.assertAlmostEqual(found['results']['co2_conversion_pct'], 9, places=3)

    def test_bad_input_never_raises(self):
        for content in (b'PK\x03\x04garbage', b'<?xml version="1.0" encoding="nonsense"?><a/>', b'\x00' * 100):
            self.assertIsNone(filereaders.read_file('x.xlsx', content))
            self.assertIsNone(filereaders.read_test_protocol('x.docx', content))
        self.assertEqual(filereaders.read_test_protocol('x.docx', docx([[['Temperature Ramp', ''], ['', 'x']]])), {'gc_method': 'x'})

    def test_carbon_atoms(self):
        self.assertEqual([filereaders.carbon_atoms(f) for f in ('CO2', 'CH3OCH3', 'C2H5OH', 'CH4', 'Cl2', 'C3H8')], [1, 2, 2, 1, 0, 3])


class ProtocolDocumentTests(unittest.TestCase):
    def test_standard_conditions_map_to_the_template(self):
        content = docx([
            [['Catalyst Mass- Based on Bed Height'] * 4, ['GHSV', '11,000', 'h-1', ''], ['Catalyst Mass', '250', 'mg', ''],
             ['Diluent Identity', 'Quartz Sand', '', ''], ['Total Flow Rate', '40', 'cm3/min', '']],
            [['Alternative mass'] * 3, ['Catalyst Mass', '0.24', 'g']],
            [['GC Method', 'GC Method'], ['Temperature Ramp', '50 ℃ hold 5 min'], ['', '20 ℃/min to 200 ℃'],
             ['Split Ratio', '10:1'], ['Stage 1', 'Catalyst Reduction'], ['RT to Reduction Temperature', '10 ℃/min in He'],
             ['Reduction Temperature', '250 ℃'], ['Reduction Time', '1 hour'], ['UHP H2', '60 cm3/min'],
             ['Stage 2', 'Pressurize the Reactor'], ['UHP He', '40 SCCM'], ['Stage 3', 'CO2 to Methanol'],
             ['Reaction Temperature', '250℃'], ['CO2', '10 SCCM'], ['UHP H2', '30 SCCM'], ['Stabilization time', '30 min'],
             ['Operator', 'anyone']]])
        values = filereaders.read_test_protocol('conditions.docx', content)
        self.assertEqual((values['GHSV_h'], values['catalyst_mass_g'], values['diluent']), (11000, 0.25, 'Quartz Sand'))
        self.assertEqual(values['gc_method'], '50 ℃ hold 5 min; 20 ℃/min to 200 ℃')
        self.assertEqual((values['pretreatment_T_C'], values['pretreatment_gas'], values['pretreatment_flow_mL_min']), (250, 'H2', 60))
        self.assertEqual((values['reaction_T_C'], values['stabilization_h']), (250, 0.5))
        self.assertEqual(values['feed'], 'CO2 10 + H2 30 mL/min (H2:CO2 = 3:1)')
        self.assertIn('UHP He: 40 SCCM', values['pressurization'])
        self.assertIn('Alternative', values['steps'])
        self.assertIn('Operator: anyone', values['steps'])
        self.assertIsNone(filereaders.read_test_protocol('x.docx', b'not a zip'))

    def test_units_are_converted(self):
        values = filereaders.read_test_protocol('c.docx', docx([
            [['Catalyst Mass', '250 mg'], ['Diluent Mass', '0,5', 'g']],
            [['Stage 1', 'Catalyst Reduction'], ['Reduction Time', '30', 'min']],
            [['Oven time', '5 min'], ['Stage 3', 'CO2 to Methanol'], ['Reaction Pressure', '3 MPa']]]))
        self.assertEqual((values['catalyst_mass_g'], values['diluent_mass_g'], values['pretreatment_time_h']), (0.25, 0.5, 0.5))
        self.assertEqual(values['pressure_bar'], 30)
        self.assertIn('Oven time: 5 min', values['steps'])  # a new table starts outside any stage
        self.assertEqual(filereaders.number('11,000'), 11000)
        self.assertEqual(filereaders.number('0,252 g'), 0.252)


if __name__ == '__main__':
    unittest.main()
