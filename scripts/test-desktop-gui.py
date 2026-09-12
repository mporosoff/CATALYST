"""Synthetic native-widget integration smoke test; no credentials or network."""
import sys
from pathlib import Path
import time
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalyst_desktop.gui import Application
from catalyst_desktop.model import Source

root = tk.Tk()
root.withdraw()
app = Application(root, smoke=True)
app.modality.set('synthesis')
app.rebuild_context()
app.title.set('Synthetic GUI check')
app.set_sources([Source.from_bytes('synthetic.csv', b'mass,name\n72,001\n')])
for key, value in dict(specimenId='001',runId='SYNTHETIC',acquiredBy='Test laboratory',acquiredAt='2026-09-12',synthesisMethod='Synthetic protocol').items():
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
    app.close()
print('Native GUI smoke passed: select, map, preview, approve, and invalidate on edit. No network calls.')
