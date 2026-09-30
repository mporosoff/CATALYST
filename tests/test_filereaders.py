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
        '# Lattice Spacing:\t1.920156', '# Encoder\tEnergy\tADC_01\tADC_02\tADC_03\tTime\tGate']
    for i in range(points):
        lines.append(f'{-600000 - i}\t{10400 + 10 * i}\t{100 + i}\t{50 + i}\t{25 + i}\t{i * 1000}\t1000')
    return '\n'.join(lines).encode()


def nu_workbook(injections=((90, 1, 0, 6), (88, 1.2, 0, 7), (88, 1, 0, 7), (86, 1, 0, 8))):
    cells = {'A1': 'Catalyst Name', 'B1': 'Synthetic CZA', 'C1': 'Mass of Catalyst Tested', 'D1': 250, 'E1': 'mg',
        'J1': 'Peak Area', 'L1': 'Injection No.', 'M1': 'Injection time(min)', 'O1': 'Concentration (umol)',
        'X1': 'CO2 Conversion (%)', 'Y1': 'H2 Conversion(%)', 'AA1': 'Product Selectivity(%)',
        'D3': 'Oven Temp. Program', 'J3': 'Hydrogen', 'O3': 'Carbon Dioxide', 'P3': 'Dimethyl Ether',
        'Q3': 'Formaldehyde', 'R3': 'Methanol', 'I5': 'RT Calibration', 'Q5': ('(3-1)/2', 1.0), 'R5': ('(5-1)/2', 2.0),
        'P8': ('AVERAGE(P5:P6)', 1.5), 'R8': ('AVERAGE(R5:R7)', 2.0), 'J4': 'H2', 'K4': 'CO2', 'O4': 'CO2', 'P4': 'CH3OCH3', 'Q4': 'CH2O', 'R4': 'CH3OH',
        'AA4': 'CO2', 'AB4': 'CH3OCH3', 'AC4': 'CH2O', 'AD4': 'CH3OH',
        'A5': 'Research CO2', 'B5': 25, 'C5': 'mL/min', 'D5': '50C for 5 min, 20C/min to 200C',
        'A6': 'UHP H2', 'B6': 75, 'C6': 'mL/min', 'D6': 'Split ratio= 10:1', 'A7': 'UHP He', 'B7': 0, 'C7': 'mL/min',
        'A8': 'Pressure', 'B8': 30, 'C8': 'bar'}
    for index, (co2, dme, ch2o, meoh) in enumerate(injections):
        row = 10 + index
        cells.update({f'L{row}': index + 1, f'M{row}': 20 * (index + 1), f'O{row}': co2, f'P{row}': dme,
            f'Q{row}': ch2o, f'R{row}': meoh, f'X{row}': 5.0, f'Y{row}': ('(#REF!-#REF!)/#REF!*100', None)})
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
        self.assertEqual(found['results'], {})
        self.assertIn('not been confirmed', found['warnings'][0])

    def test_other_text_files_are_ignored(self):
        self.assertIsNone(filereaders.read_file('notes.txt', b'just some notes'))
        self.assertEqual(filereaders.parse_trajectory('Mo_K_XANES'), ('Mo', 'K-edge', 'XANES'))


class GCWorkbookTests(unittest.TestCase):
    def test_setup_and_standard_results(self):
        found = filereaders.read_file('run.xlsx', nu_workbook())
        c = found['conditions']
        self.assertEqual((c['catalyst_mass_mg'], c['pressure_bar'], c['flow_mL_min']), ('250', '30', '100'))
        self.assertEqual(c['feed'], 'CO2 25 + H2 75 mL/min (H2:CO2 = 3:1)')
        self.assertNotIn('time_on_stream_h', c)  # the workbook only knows time since the first injection
        r = found['results']
        self.assertEqual((r['injections'], r['averaged_over_last']), (4, 3))
        # last three injections: carbon in products 9.4/98.4... computed by hand below
        expected = []
        for co2, dme, meoh in ((88, 1.2, 7), (88, 1, 7), (86, 1, 8)):
            products = 2 * dme + meoh
            expected.append(100 * products / (co2 + products))
        self.assertAlmostEqual(r['co2_conversion_pct'], sum(expected) / 3, places=2)
        self.assertAlmostEqual(r['selectivity_CH3OH_pct'] + r['selectivity_CH3OCH3_pct'] + r['selectivity_CH2O_pct'], 100, places=2)
        self.assertIn('Y', found['warnings'][0])
        self.assertEqual(r['last_injection_time_min'], 80)
        self.assertIn('provisional', r['status'])
        self.assertTrue(any('differs from the workbook' in w for w in found['warnings']))  # lab says 5 %
        table = list(csv.DictReader(io.StringIO(found['derived'][0]['content'].decode())))
        self.assertEqual(len(table), 4)
        self.assertAlmostEqual(float(table[0]['catalyst_CO2_conversion_pct']), 100 * 8 / 98, places=3)
        self.assertEqual(float(table[0]['lab_CO2_conversion_pct']), 5)

    def test_empty_template_reads_setup_only(self):
        found = filereaders.read_file('template.xlsx', nu_workbook(injections=()))
        self.assertEqual(found['results'], {})
        self.assertEqual(found['derived'], [])
        self.assertIn('empty template', found['warnings'][-1])

    def test_negative_amounts_are_left_out(self):
        found = filereaders.read_file('run.xlsx', nu_workbook(injections=((90, 1, 0, 6), (-5, 1, 0, 6))))
        self.assertTrue(any('negative' in w for w in found['warnings']))
        self.assertAlmostEqual(found['results']['co2_conversion_pct'], 100 * 8 / 98, places=3)

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
