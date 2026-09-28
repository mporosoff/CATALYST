"""Real Tk adaptive-flow checks with synthetic files and an in-memory SciSure API."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import traceback
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'tests')]

from catalyst_desktop.gui import Application
from catalyst_desktop.model import Source
from catalyst_desktop.publication import Publisher
from catalyst_desktop.catalog import load_catalog
from catalyst_desktop.library import search_samples, search_procedures
from catalyst_desktop.scisure import SciSureClient
from test_desktop import FakeSciSure


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(parent, text):
    return next(widget for widget in descendants(parent)
        if isinstance(widget, ttk.Button) and widget.cget('text') == text)


def finish(app):
    deadline = time.monotonic() + 20
    app.root.update()
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.02)
    app.root.update()
    assert not app.busy, 'Background action did not finish.'
    assert not CALLBACKS, '\n'.join(CALLBACKS)
    assert not ERRORS, str(ERRORS)


def enter(app, **values):
    for key, value in values.items():
        app.context_vars[key].set(value)
    app.root.update()
    app.refresh_workflow_requirements()
    app.root.update()


def select_link(app, kind):
    app.refresh_link_options(kind)
    assert len(app.link_rows[kind]['rows']) == 1, f'Expected one reusable {kind} choice.'
    widget = app.link_widgets[kind]
    widget.current(0)
    widget.event_generate('<<ComboboxSelected>>')
    app.root.update()


def stage(app):
    app.tabs.select(app.import_tab)
    button(app.workflow_card, 'Stage this record').invoke()
    finish(app)


def approve(app, identifier):
    app.batch_panel.select(identifier)
    app.batch_panel._buttons['inspect'].invoke()
    app.root.update()
    assert app.tabs.select() == str(app.review_tab)
    assert app.review_batch_id == identifier and not app.acknowledge.get()
    app.reviewer.set('Synthetic GUI reviewer')
    app.review_note.set('Reviewed original evidence and selected saved sample / procedure references.')
    app.acknowledge.set(True)
    button(app.review_content, 'Approve this revision').invoke()
    finish(app)
    item = app.batch_queue.get(identifier)
    assert item.approval and item.status == 'approved', (item.status, item.errors)


def assert_layout(app, page):
    root = app.root
    app.tabs.select(page)
    root.update()
    left, right = root.winfo_rootx(), root.winfo_rootx() + root.winfo_width()
    clipped = []
    clipped_text = []
    for widget in descendants(page):
        if not widget.winfo_ismapped():
            continue
        if isinstance(widget, (ttk.Button, ttk.Combobox, ttk.Entry, ttk.Checkbutton)):
            if widget.winfo_rootx() < left or widget.winfo_rootx() + widget.winfo_width() > right:
                clipped.append((widget.winfo_class(), str(widget.cget('text')) if 'text' in widget.keys() else '',
                    widget.winfo_rootx() - left, widget.winfo_width(), right - left))
        if isinstance(widget, (ttk.Label, ttk.Button, ttk.Checkbutton)):
            if widget.winfo_reqwidth() > widget.winfo_width() + 4:
                label = str(widget.cget('text'))
                if not label and widget.cget('textvariable'):
                    label = str(widget.getvar(widget.cget('textvariable')))
                if label:
                    clipped_text.append((label[:90], widget.winfo_reqwidth(), widget.winfo_width()))
    assert not clipped, f'Controls clipped at 900 × 680: {clipped}'
    assert not clipped_text, f'Labels / actions truncated instead of wrapping at 900 × 680: {clipped_text}'


def check_fields(app):
    expected = {
        'measurement': ({'acquiredBy', 'acquiredAt'}, {'sample', 'procedure'}),
        'sample': ({'localSampleId', 'sampleDescription'}, set()),
        'procedure': ({'procedureName', 'procedureVersion', 'procedureType', 'procedureModality'}, set()),
        'synthesis': ({'acquiredBy', 'acquiredAt'}, {'sample', 'procedure'}),
        'computation': ({'acquiredBy', 'acquiredAt', 'modelDescription', 'inputStructure'}, {'procedure'}),
    }
    for kind, (required_fields, pickers) in expected.items():
        app.change_record_type(kind, confirm=False)
        app.root.update()
        required_controls = set(app.context_widgets) & set(app.required_keys)
        assert required_controls == required_fields, (kind, required_controls)
        assert set(app.link_widgets) == pickers
        assert not any(key in app.context_widgets for key in ('batchId', 'localBatchId', 'synthesisRecord',
            'originLab', 'synthesizedAt', 'calibration', 'radiation', 'geometry'))
        assert 'sampleDescription' in app.context_widgets if kind == 'sample' else 'sampleDescription' not in app.context_widgets
        assert app.mapping_section.winfo_manager() == ''
        assert_layout(app, app.import_tab)


ERRORS, CALLBACKS = [], []
root = tk.Tk()
root.withdraw()
app = None
with patch('tkinter.messagebox.showerror', side_effect=lambda *a, **kw: ERRORS.append(a)), \
        patch('tkinter.messagebox.showinfo'), patch('tkinter.messagebox.askyesno', return_value=True):
    try:
        app = Application(root, smoke=True)
        root.report_callback_exception = lambda cls, value, tb: CALLBACKS.append(''.join(traceback.format_exception(cls, value, tb)))
        root.geometry('900x680')
        root.deiconify()
        root.update()
        assert app.adaptive and app.record_type.get() == 'measurement'
        check_fields(app)

        app.change_record_type('measurement', confirm=False)
        app.modality.set('XRD')
        app.rebuild_context()
        original = Source.from_bytes('synthetic-first.xrd', b'SYNTHETIC diffraction original 1', parse=False)
        app.set_sources([original])
        app.title.set('Synthetic linked measurement')
        enter(app, acquiredBy='Synthetic researcher', acquiredAt='2026-09-22')
        preserved_id = app.context_vars['datasetId'].get()

        button(app.link_frame, '+ Add new sample').invoke()
        root.update()
        assert app.record_type.get() == 'measurement' and not app.link_return_drafts
        sample_dialog = next(widget for widget in root.winfo_children()
            if isinstance(widget, tk.Toplevel) and widget.title() == 'Add new sample')
        sample_entries = [widget for widget in descendants(sample_dialog) if isinstance(widget, ttk.Entry)]
        sample_entries[0].insert(0, 'Synthetic material 101')
        sample_entries[1].insert(0, 'Synthetic supported metal sample')
        button(sample_dialog, 'Add sample to draft').invoke()
        finish(app)
        sample_id = app.context_vars['specimenId'].get()
        assert app.record_type.get() == 'measurement' and not app.link_return_drafts
        assert app.context_vars['specimenId'].get() == sample_id
        assert app.context_vars['acquiredBy'].get() == 'Synthetic researcher'
        assert app.context_vars['acquiredAt'].get() == '2026-09-22'
        assert app.context_vars['datasetId'].get() == preserved_id
        assert app.sources[0].content == original.content
        sample = app.batch_queue.items[0]
        assert sample.revision.value()['preview']['data_status'] == 'metadata_only' and not sample.sources

        button(app.link_frame, '+ Add new procedure').invoke()
        root.update()
        assert app.record_type.get() == 'measurement' and not app.link_return_drafts
        procedure_dialog = next(widget for widget in root.winfo_children()
            if isinstance(widget, tk.Toplevel) and widget.title() == 'Add new procedure')
        procedure_entries = [widget for widget in descendants(procedure_dialog) if isinstance(widget, ttk.Entry)]
        procedure_entries[0].insert(0, 'Synthetic XRD procedure')
        instructions = next(widget for widget in descendants(procedure_dialog) if isinstance(widget, tk.Text))
        instructions.insert('1.0', 'Synthetic instrument setup and acquisition instructions.')
        button(procedure_dialog, 'Add procedure to draft').invoke()
        finish(app)
        procedure_id = app.context_vars['methodId'].get()
        assert app.record_type.get() == 'measurement'
        assert app.context_vars['methodId'].get() == procedure_id
        assert app.context_vars['specimenId'].get() == sample_id
        assert app.context_vars['datasetId'].get() == preserved_id
        assert app.context_vars['acquiredAt'].get() == '2026-09-22'
        assert app.title.get() == 'Synthetic linked measurement'
        assert app.sources[0].content == original.content
        stage(app)
        assert [item.revision.value()['preview']['context']['recordType'] for item in app.batch_queue.items] == ['sample', 'procedure', 'measurement']
        assert all(not item.errors for item in app.batch_queue.items), [(item.title, item.errors) for item in app.batch_queue.items]
        assert all(item.revision.value()['parent'] is None for item in app.batch_queue.items)
        assert all(not item.approval for item in app.batch_queue.items), 'Staging must never imply approval.'
        initial = tuple(app.batch_queue.items)
        for item in initial:
            approve(app, item.id)
        assert_layout(app, app.review_tab)
        assert_layout(app, app.batch_tab)

        # Editing an unsent definition replaces its draft and revokes dependent approvals.
        procedure = app.batch_queue.items[1]
        app.batch_panel.select(procedure.id)
        app.batch_panel._buttons['edit'].invoke()
        root.update()
        enter(app, procedureText='Synthetic revised instructions before any transfer.')
        stage(app)
        replacement = app.batch_queue.get(procedure.id)
        assert replacement.revision.value()['parent'] is None, 'Replacing an unsent draft must not require its removed previous revision.'
        dependent = app.batch_queue.items[2]
        assert not dependent.approval and dependent.errors, 'Changing a procedure must require reviewing the linked measurement again.'
        app.batch_panel.select(dependent.id)
        app.batch_panel._buttons['edit'].invoke()
        root.update()
        select_link(app, 'procedure')
        stage(app)
        assert not app.batch_queue.get(dependent.id).errors
        assert app.batch_queue.get(dependent.id).revision.value()['parent'] is None

        # One explicitly confirmed set of links/date/operator is copied to separate file drafts.
        before = len(app.batch_queue.items)
        context = app.workflow_values()
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / 'synthetic-second.xrd', Path(directory) / 'synthetic-third.xrd']
            for index, path in enumerate(paths, 2): path.write_bytes(f'SYNTHETIC native record {index}'.encode())
            with patch('tkinter.filedialog.askopenfilenames', return_value=tuple(str(path) for path in paths)):
                app.batch_files_button.invoke()
                finish(app)
        assert len(app.batch_queue.items) == before + 2
        for item in app.batch_queue.items[-2:]:
            fields = item.revision.value()['preview']['context']
            for key in ('specimenId', 'methodId', 'methodVersion', 'acquiredAt', 'acquiredBy',
                    'sampleSourceRevisionSha256', 'procedureSourceRevisionSha256'):
                assert fields[key] == context[key], (key, fields[key], context[key])
            assert len(item.sources) == 1 and not item.approval and not item.errors
        assert len({item.revision.value()['preview']['context']['datasetId'] for item in app.batch_queue.items}) == 5
        assert app.publisher is None, 'Preparing batches must not require a live destination.'

        # Mapped characterization reveals only its required signal unit and source mapping units.
        app.change_record_type('measurement', confirm=False)
        select_link(app, 'sample')
        select_link(app, 'procedure')
        enter(app, acquiredBy='Synthetic mapper', acquiredAt='2026-09-22')
        table_bytes = b'angle,intensity\n20,120\n25,135\n'
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / 'synthetic-native.json'
            malformed_bytes = b'{"instrument_native_payload": [NaN,,}'
            malformed.write_bytes(malformed_bytes)
            with patch('tkinter.filedialog.askopenfilenames', return_value=(str(malformed),)):
                button(app.originals_card, 'Choose files').invoke()
                finish(app)
            assert app.raw_only.get() and app.sources[0].artifact['format'] == 'binary'
            assert app.sources[0].content == malformed_bytes, 'Original preservation must not reject or rewrite non-table JSON.'
            path = Path(directory) / 'synthetic-mapped.csv'
            path.write_bytes(table_bytes)
            with patch('tkinter.filedialog.askopenfilenames', return_value=(str(path),)):
                button(app.originals_card, 'Choose files').invoke()
                finish(app)
            assert app.raw_only.get() and app.sources[0].artifact['format'] == 'binary'
            app.raw_only_widget.invoke()
            finish(app)
            assert not app.raw_only.get() and app.sources[0].artifact['format'] == 'csv'
            assert app.sources[0].content == table_bytes, 'Opting into table conversion must retain the original bytes.'
        app.title.set('Synthetic mapped characterization')
        app.source_version.set('synthetic export 1')
        enter(app, signalUnit='counts')
        app.read_columns_button.invoke()
        root.update()
        assert len(app.rules) == 2 and 'signalUnit' in app.required_keys
        app.rules[0][1].set('two_theta_deg')
        app.rules[0][2].set('')
        app.rules[1][1].set('signal')
        app.rules[1][2].set('as recorded')
        root.update()
        axis_unit = app.rule_widgets['angle'][1]
        assert axis_unit in app.missing_widgets, 'An explicitly mapped numerical column needs its source unit.'
        app.focus_missing()
        root.update()
        assert root.focus_get() is axis_unit
        app.rules[0][2].set('degree (2theta)')
        root.update()
        assert not app.missing_widgets, app.required_summary.get()
        app.optional_context.set_open(True)
        root.update()
        assert app.context_widgets['notes'].winfo_ismapped()
        assert 'notes' not in app.required_keys
        assert_layout(app, app.import_tab)
        app.review_button.invoke()
        finish(app)
        assert app.revision.value()['preview']['standardized']['rows'][0]['two_theta_deg'] == '20'
        assert not any(issue['severity'] == 'error' for issue in app.revision.value()['preview']['validation']['issues'])
        assert len(app.batch_queue.items) == 5, 'Preview alone must not append an upload.'

        # The real Send callback may write only to this in-memory synthetic API.
        api = FakeSciSure()
        app.client = SciSureClient('synthetic-token', transport=api)
        app.group_id = 7
        app.destination = app.client.destination(42, 7)
        app.publisher = Publisher(app.client, app.destination)
        app.catalog = load_catalog(app.client, 7)
        assert api.posts == 0
        app.refresh_batch()
        for item in app.batch_queue.items:
            approve(app, item.id)
        assert api.posts == 0, 'Review and approval must not send records.'
        app.tabs.select(app.batch_tab)
        app.batch_panel._buttons['send'].invoke()
        finish(app)
        assert all(item.status == 'complete' for item in app.batch_queue.items), [(item.status, item.errors) for item in app.batch_queue.items]
        assert len(api.sections) == 5
        catalog = load_catalog(app.client, 7)
        assert len(catalog['entries']) == 5 and len(search_samples(catalog)) == len(search_procedures(catalog)) == 1
        assert_layout(app, app.batch_tab)
        assert not CALLBACKS and not ERRORS
    finally:
        if app is not None:
            app.close()
        elif root.winfo_exists():
            root.destroy()

print('Adaptive native GUI passed: record-specific fields, linked add-new drafts, staged metadata, per-record approval, dependency edits, batch file review, mapped units, minimum layout, and ordered synthetic transfer. No network calls.')
