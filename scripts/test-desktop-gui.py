"""Synthetic native-widget integration smoke test; no credentials or network."""
import sys
from pathlib import Path
import time
import tkinter as tk
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from catalyst_desktop.gui import Application
from catalyst_desktop.model import Source
from desktop_fixtures import physical_context
from test_api_contract import NativeSetupAPI, client_for

def finish(app):
    deadline = time.monotonic() + 20
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.02)
    assert not app.busy, 'GUI background action did not finish'

root = tk.Tk()
root.withdraw()
app = Application(root, smoke=True)
app.modality.set('synthesis')
app.rebuild_context()
app.title.set('Synthetic GUI check')
app.set_sources([Source.from_bytes('synthetic.csv', b'mass,name\n72,001\n')])
app.generate_identity('batch')
assert app.context_vars['specimenId'].get().startswith('CAT-UR-SMP-')
for key, value in physical_context().items():
    app.context_vars[key].set(value)
app.source_version.set('synthetic v1')
app.read_columns()
assert len(app.rules) == 2
app.rules[0][1].set('mass_g')
app.rules[0][2].set('mg')
app.rules[1][1].set('specimen_id')
app.rules[1][2].set('text')
errors = []
with patch('tkinter.messagebox.showerror', side_effect=lambda *a, **kw: errors.append(a)):
    app.preview()
    deadline = time.monotonic() + 20
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert not app.busy and not errors, str(errors)
    assert app.revision is not None
    assert app.revision.value()['preview']['standardized']['rows'][0]['mass_g'] == '0.072'
    app.reviewer.set('Synthetic reviewer')
    app.review_note.set('Checked source units and context.')
    app.acknowledge.set(True)
    app.approve()
    assert app.approval is not None
    app.context_vars['specimenId'].set('changed')
    assert app.approval is None and app.dirty
    app.approve()
    assert errors and app.approval is None
    payload = app.revision.value()['preview']
    app.catalog = dict(entries=[dict(trace=payload['traceability'], context=payload['context'])], pending=[], legacy_reviews=0)
    app.filter_catalog()
    assert len(app.catalog_rows) == 1
    app.catalog_table.selection_set('0')
    app.entity.set('SLAC')
    app.use_catalog_subject(derived=True)
    assert app.context_vars['originLab'].get() == 'Rochester'
    assert app.context_vars['sampleCreatedLab'].get() == 'SLAC'
    assert app.context_vars['parentSampleId'].get() == physical_context()['specimenId']
    assert app.context_vars['specimenId'].get().startswith('CAT-SLAC-SMP-')
    assert app.context_vars['datasetId'].get().startswith('CAT-SLAC-DS-')
    assert app.approval is None and app.dirty
    assert app.context_vars['methodId'].get() == ''
    app.entity.set('Northwestern')
    app.repeat_catalog_procedure()
    assert app.modality.get() == 'synthesis'
    assert app.context_vars['procedureId'].get() == physical_context()['procedureId']
    assert app.context_vars['batchId'].get().startswith('CAT-NU-BAT-')
    assert app.context_vars['synthesisExecutionId'].get().startswith('CAT-NU-SYN-')
    assert app.context_vars['synthesisRecord'].get() == ''
    assert app.context_vars['synthesisDeviations'].get() == ''
    errors.clear()
    app.entity.set('Rochester')
    app.modality.set('imaging')
    app.rebuild_context()
    for key, value in physical_context(technique='Synthetic photo', imageContext='Synthetic sample image', scaleReference='Not quantitative').items():
        if key in app.context_vars: app.context_vars[key].set(value)
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / 'synthetic.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + bytes(range(256)))
        with patch('tkinter.filedialog.askopenfilenames', return_value=[str(image)]):
            app.choose_files()
            finish(app)
        assert app.raw_only.get() and not app.toolkit.get()
        assert app.sources[0].artifact['format'] == 'binary'
        app.preview()
        finish(app)
        assert app.revision.value()['preview']['data_status'] == 'original_files_only'
        assert 'FILES + CONTEXT ONLY' in app.review_heading.get()
        assert app.review_details.tab(0, 'text') == 'Original files'
        assert app.data_table.item(app.data_table.get_children()[0], 'values')[0] == 'synthetic.png'
        app.acknowledge.set(True)
        app.approve()
        assert app.approval is not None
        table_file = Path(folder) / 'synthetic.csv'
        table_file.write_bytes(b'mass,name\n72,001\n')
        with patch('tkinter.filedialog.askopenfilenames', return_value=[str(table_file)]):
            app.choose_files()
            finish(app)
        assert not app.raw_only.get()
        app.read_columns()
        app.rules[0][1].set('mass_g')
        app.rules[0][2].set('mg')
        app.rules[1][1].set('species')
        app.rules[1][2].set('text')
        app.rules[1][3].set('{unfinished alias draft')
        app.source_version.set('')
        with patch('tkinter.filedialog.askopenfilenames', return_value=[str(image)]):
            app.add_supporting_files()
            finish(app)
        assert len(app.sources) == 2 and app.sources[1].artifact['format'] == 'binary'
        assert app.rules[0][1].get() == 'mass_g' and app.rules[0][2].get() == 'mg'
        assert app.rules[1][3].get() == '{unfinished alias draft'
        assert app.approval is None
    api = NativeSetupAPI()
    app.client = client_for(api)
    app.group_id = 7
    app.experiments = [api.experiment]
    app.experiment.configure(values=['42 · Synthetic experiment'])
    app.experiment.current(0)
    app.inspect_sample.set('20')
    app.inspect_protocol.set('30')
    app.inspect_integration()
    finish(app)
    assert api.calls and {method for _, method in api.calls} == {'GET'}
    assert 'Setup inspection finished' in app.status.get()
    assert any(isinstance(child, tk.Toplevel) for child in root.winfo_children())
    app.transfer_operations[(app.client.origin, 7, 42)] = {'section/synthetic': 'unknown'}
    app.clear_destination()
    app.verify_destination()
    finish(app)
    assert app.publisher.operations['section/synthetic'] == 'unknown'
    assert not errors, str(errors)
    app.close()
print('Native GUI smoke passed: tables, image-only review, approval, lineage, read-only setup inspection, and reconnect recovery guards. No network calls.')
