"""Synthetic native-widget integration smoke test; no credentials or network."""
import sys
from pathlib import Path
import time
import tkinter as tk
import tempfile
import zipfile
from types import SimpleNamespace
from tkinter import ttk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from catalyst_desktop.gui import Application, RELEASES_URL
from catalyst_desktop.model import Source, InputError
from catalyst_desktop.model import COMMON_CONTEXT, MODALITY_CONTEXT
from catalyst_desktop.traceability import trace_context
from desktop_fixtures import physical_context
from test_api_contract import NativeSetupAPI, client_for
from test_configuration import SchemaAPI
from test_desktop import FakeSciSure, review
from catalyst_desktop.scisure import SciSureClient, SANDBOX
from catalyst_desktop.publication import Publisher
from catalyst_desktop.configuration import material_fields


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def check_navigation(app):
    """Exercise real Tk event bindings, including high-resolution wheels and dynamic fields."""
    root = app.root
    root.geometry('900x680')
    root.deiconify()
    app.tabs.select(app.import_tab)
    root.update()
    canvas = app.import_tab.canvas
    assert app.review_button.instate(['disabled']), 'Review must wait for a file selection'
    bar = next(w for w in app.import_tab.winfo_children() if isinstance(w, ttk.Scrollbar))
    assert bar.winfo_width() >= 8, 'Scrollbar thumb must remain visible and draggable'
    assert canvas.yview()[1] < 1, 'Fixture must overflow the form to exercise scrolling'
    combo = next(w for w in descendants(app.import_tab) if isinstance(w, ttk.Combobox) and w.winfo_ismapped())
    entry = next(w for w in descendants(app.import_tab) if isinstance(w, ttk.Entry) and w.winfo_ismapped())
    original = combo.get()
    delta = -2 if root.tk.call('tk', 'windowingsystem') == 'aqua' else -120
    canvas.yview_moveto(0)
    for widget in (entry, combo, app.import_tab.body):
        before = canvas.yview()[0]
        widget.event_generate('<MouseWheel>', delta=delta)
        root.update()
        assert canvas.yview()[0] > before, f'Mouse wheel did not scroll over {widget.winfo_class()}'
    assert combo.get() == original, 'Mouse wheel changed the chosen lab or modality'
    canvas.yview_moveto(0)
    if root.tk.call('tk', 'windowingsystem') != 'aqua':
        app.import_tab.wheel_remainder = 0
        for _ in range(4): entry.event_generate('<MouseWheel>', delta=-10)
        root.update()
        assert canvas.yview()[0] > 0, 'High-resolution wheel deltas were lost'
    for _ in range(60): app.import_tab.body.event_generate('<MouseWheel>', delta=-abs(delta) * 10)
    root.update()
    assert canvas.yview()[1] == 1, 'Cannot reach bottom of form'
    review = next(w for w in descendants(app.import_tab) if isinstance(w, ttk.Button) and w.cget('text') == 'Review submission  →')
    assert review.winfo_ismapped()
    assert 0 <= review.winfo_rooty() - root.winfo_rooty() < root.winfo_height() - review.winfo_height()
    assert app.status_label.winfo_ismapped(), 'Connection and transfer status must remain visible'
    assert app.status_label.winfo_rooty() + app.status_label.winfo_height() <= root.winfo_rooty() + root.winfo_height()
    for selected in ('computational', 'synthesis', 'reactor'):
        app.modality.set(selected)
        app.rebuild_context()
        expected = COMMON_CONTEXT | trace_context(selected) | MODALITY_CONTEXT[selected]
        assert set(app.context_vars) == set(expected), 'Grouping omitted scientific context fields'
        assert all(app._scroll_tag in w.bindtags() for w in descendants(app.context_frame))
    assert not any(isinstance(w, ttk.Notebook) for w in descendants(app.context_frame)), 'Context must be one form, not tabs'
    root.update()
    assert app.mapping_section.winfo_ismapped(), 'Required mapping must be visible without a disclosure'
    for key in app.required_keys:
        assert app.context_widgets[key].winfo_ismapped(), f'Required field {key} is hidden'
        assert app.context_labels[key].cget('text').startswith('* ')
        assert app.context_widgets[key]._tooltip is not None
    assert not app.context_widgets['identityNote'].winfo_ismapped(), 'Optional notes should start collapsed'
    app.optional_context.toggle_button.invoke()
    root.update()
    assert app.context_widgets['identityNote'].winfo_ismapped()
    app.optional_context.set_open(False)
    parent_widget = app.context_widgets['parentSampleId']
    parent_var = app.context_vars['parentSampleId']
    app.context_vars['materialKind'].set('aliquot')
    root.update()
    assert 'parentSampleId' in app.required_keys and parent_widget.winfo_ismapped()
    assert app.context_widgets['parentSampleId'] is parent_widget and app.context_vars['parentSampleId'] is parent_var
    app.context_vars['materialKind'].set('batch material')
    app.context_vars['acquisitionLab'].set('SLAC')
    root.update()
    for key in ('custodyFromLab', 'custodyRecord', 'receivedAt'):
        assert key in app.required_keys and app.context_widgets[key].winfo_ismapped()
    app.context_vars['acquisitionLab'].set('Rochester')
    app.toolkit.set(True)
    root.update()
    assert 'processingVersion' in app.required_keys
    assert app.context_widgets['processingVersion'].winfo_ismapped()
    assert not app.mapping_section.winfo_ismapped()
    app.raw_only.set(True)
    root.update()
    assert not app.toolkit.get(), 'Import modes must be mutually exclusive'
    assert 'processingVersion' not in app.required_keys
    app.raw_only.set(False)
    root.update()
    assert app.mapping_section.winfo_ismapped()
    app.focus_missing()
    root.update()
    first = app.missing_widgets[0]
    assert 0 <= first.winfo_rooty() - canvas.winfo_rooty() < canvas.winfo_height(), 'Missing field was not scrolled into view'
    app.show_help()
    root.update()
    help_window = app.help_window
    app.show_help()
    assert app.help_window is help_window, 'Help should reuse the open pane'
    app.help_pane.show_guide('access')
    app.help_navigate('records')
    root.update()
    assert app.tabs.select() == str(app.history_tab) and help_window.winfo_exists()
    help_window.destroy()
    # Native data/text views must retain their own wheel handling.
    assert app.route_wheel(SimpleNamespace(widget=app.data_table, delta=delta)) is None
    assert app.route_wheel(SimpleNamespace(widget=app.issue_text, delta=delta)) is None
    app.tabs.select(app.connection_tab)
    app.connection_tab.canvas.yview_moveto(0)
    root.update()
    # Verify the route selects the scroll container beneath the pointer, not the upload form.
    before = canvas.yview()
    app.connection_tab.body.event_generate('<MouseWheel>', delta=delta)
    root.update()
    assert canvas.yview() == before
    with patch('catalyst_desktop.gui.webbrowser.open') as opened:
        app.open_downloads()
        opened.assert_called_once_with(RELEASES_URL)
    for page, button in app.navigation_buttons.items():
        button.invoke()
        root.update()
        assert app.tabs.select() == page
        assert button.cget('style') == 'SelectedNav.TButton'
        assert button.winfo_width() >= button.winfo_reqwidth(), 'Sidebar label clipped'
    for page in (app.history_tab, app.catalog_tab):
        app.tabs.select(page)
        root.update()
        for button in descendants(page):
            if isinstance(button, (ttk.Button, ttk.Menubutton)) and button.winfo_ismapped():
                assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width(), 'Action clipped horizontally'
                assert button.winfo_rooty() + button.winfo_height() <= app.status_label.winfo_rooty(), 'Action hidden below status bar'
        if page is app.catalog_tab:
            assert app.catalog_table.winfo_height() >= 80, 'Catalog rows need visible space below the headings'
    app.tabs.select(app.history_tab)
    root.update()
    assert app.history_table.winfo_height() >= 75, 'Saved-record viewport is too short'
    # Both library tables need their own independently scrollable viewport.
    for table, page in ((app.history_table, app.library_reviews), (app.file_table, app.library_files)):
        app.library_sections.select(page)
        for i in range(100): table.insert('', 'end', values=(str(i),))
        root.update()
        table.yview_moveto(1)
        assert table.yview()[0] > 0 and table.yview()[1] == 1
        table.delete(*table.get_children())
    app.tabs.select(app.import_tab)
    canvas.yview_moveto(0)
    root.geometry('1180x860')
    root.update()
    root.withdraw()


def check_required_field_navigation(app):
    """Exercise missing mappings and conditional fields with real focus and typing."""
    root = app.root
    app.new_submission(ask=False)
    app.modality.set('synthesis')
    app.rebuild_context()
    app.title.set('Synthetic required-field navigation')
    for key, value in physical_context().items():
        app.context_vars[key].set(value)
    app.set_sources([Source.from_bytes('synthetic-navigation.csv', b'mass\n72\n')])
    app.source_version.set('synthetic navigation v1')
    app.read_columns()
    app.tabs.select(app.import_tab)
    root.geometry('900x680')
    root.deiconify()
    root.focus_force()
    root.update()
    canvas = app.import_tab.canvas

    def assert_visible(widget):
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        assert widget.winfo_ismapped() and 0 <= top, f'Focused field is above the visible form: top={top}, canvas height={canvas.winfo_height()}'
        assert top + widget.winfo_height() <= canvas.winfo_height(), f'Focused field is below the visible form: bottom={top + widget.winfo_height()}, canvas height={canvas.winfo_height()}'

    # Reading columns must lead to the first target when every column is ignored.
    name, target, unit, _ = app.rules[0]
    target_widget, unit_widget = app.rule_widgets[name]
    assert target.get() == 'Ignore' and not app.missing_widgets
    app.focus_missing()
    root.update()
    assert root.focus_get() is target_widget, 'All-Ignore mapping must focus the first column meaning'
    assert_visible(target_widget)

    # A selected meaning with no source unit is a missing required field.
    target.set('mass_g')
    unit.set('')
    root.update()
    assert app.missing_widgets == [unit_widget], 'A mapped column without a source unit must be counted'
    assert '1 remaining.' in app.required_summary.get()
    app.focus_missing()
    root.update()
    assert root.focus_get() is unit_widget, 'Next missing field must focus the blank source unit'
    assert_visible(unit_widget)
    unit.set('mg')
    root.update()
    assert not app.missing_widgets and '0 remaining.' in app.required_summary.get()

    # The first typed handoff character promotes this optional field. The same
    # entry and variable must survive, retain focus, and remain fully visible.
    key = 'custodyRecord'
    widget, variable = app.context_widgets[key], app.context_vars[key]
    assert key not in app.required_keys and not variable.get()
    app.optional_context.set_open(True)
    root.update()
    widget.focus_force()
    root.update()
    assert_visible(widget)
    widget.insert('end', 'S')
    root.update()
    assert key in app.required_keys
    assert app.context_widgets[key] is widget and app.context_vars[key] is variable
    assert root.focus_get() is widget and variable.get() == 'S'
    assert_visible(widget)
    widget.insert('end', 'ynthetic handoff')
    root.update()
    assert variable.get() == widget.get() == 'Synthetic handoff'
    assert root.focus_get() is widget
    assert_visible(widget)

    # Hover help on captions must not introduce an extra stop for every field.
    for widget in app.context_widgets.values():
        if widget.winfo_ismapped():
            assert not isinstance(widget.tk_focusNext(), (tk.Label, ttk.Label)), 'Tab must skip field captions'
    entry = app.context_widgets['acquiredAt']
    entry.focus_force()
    root.update()
    following = entry.tk_focusNext()
    entry.event_generate('<Tab>')
    root.update()
    assert root.focus_get() is following, 'Native Tab traversal must reach the next control'
    assert not isinstance(root.focus_get(), (tk.Label, ttk.Label))

    app.new_submission(ask=False)
    app.source_version.set('')
    root.geometry('1180x860')
    root.update()
    root.withdraw()


def finish(app):
    deadline = time.monotonic() + 20
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.02)
    assert not app.busy, 'GUI background action did not finish'

root = tk.Tk()
root.withdraw()
app = Application(root, smoke=True, legacy=True)
check_navigation(app)
check_required_field_navigation(app)
app.modality.set('synthesis')
app.rebuild_context()
app.title.set('Synthetic GUI check')
app.set_sources([Source.from_bytes('synthetic.csv', b'mass,name\n72,001\n')])
assert not app.review_button.instate(['disabled'])
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
with patch('tkinter.messagebox.showerror', side_effect=lambda *a, **kw: errors.append(a)), \
        patch.object(app, 'show_action_error', side_effect=lambda message, **kwargs: errors.append(('CATALYST', message))):
    app.preview()
    deadline = time.monotonic() + 20
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)
    assert not app.busy and not errors, str(errors)
    assert app.revision is not None
    assert app.revision.value()['preview']['standardized']['rows'][0]['mass_g'] == '0.072'
    assert app.review_empty.winfo_manager() == '' and app.review_content.winfo_manager() == 'pack'
    root.geometry('900x680')
    root.deiconify()
    root.update()
    for button in descendants(app.review_content):
        if isinstance(button, ttk.Button) and button is not app.fix_issue_button:
            assert button.winfo_ismapped(), 'Review action disappeared at the minimum window size'
            assert button.winfo_rooty() + button.winfo_height() <= app.status_label.winfo_rooty()
    app.review_details.select(1)
    root.update()
    assert app.fix_issue_button.winfo_ismapped(), ('Validation correction must remain available in its selected tab',
        [(str(w), w.winfo_geometry(), w.winfo_ismapped()) for w in
         (app.review_details, app.fix_issue_button.master, app.issue_heading, app.fix_issue_button)])
    assert app.fix_issue_button.winfo_rooty() + app.fix_issue_button.winfo_height() <= app.status_label.winfo_rooty()
    root.withdraw()
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
    schema_api = SchemaAPI()
    app.client = client_for(schema_api)
    app.group_id = 7
    app.prepare_configuration()
    finish(app)
    assert schema_api.native_posts == 0, 'Preparing configuration must not mutate SciSure'
    apply_schema = next(w for w in descendants(root) if isinstance(w, ttk.Button) and w.cget('text') == 'Apply listed schema additions')
    apply_schema.invoke()
    finish(app)
    assert schema_api.native_posts == 1 + len(material_fields())
    assert 'Native material schema verified' in app.status.get()
    # A selected custom server drives connection and origin-bound credential storage.
    api = FakeSciSure()
    custom_origin = 'https://scisure.example.edu'
    def custom_transport(url, method, headers, body):
        assert url.startswith(custom_origin + '/api/v1/')
        return api(SANDBOX + url[len(custom_origin):], method, headers, body)
    custom_client = SciSureClient('synthetic-token', origin=custom_origin, transport=custom_transport)
    app.tenant.set(custom_origin)
    app.token.set('synthetic-token')
    app.remember.set(True)
    with patch('catalyst_desktop.gui.SciSureClient', return_value=custom_client) as constructor, \
            patch('catalyst_desktop.gui.save_token') as saved:
        app.connect()
        finish(app)
        constructor.assert_called_once_with('synthetic-token', origin=custom_origin)
        saved.assert_called_once_with(custom_origin, 'synthetic-token')
    assert app.client is custom_client and app.token.get() == ''
    app.experiment.current(0)
    destination = custom_client.destination(42, 7)
    download_source, _, download_revision = review()
    Publisher(custom_client, destination).publish(download_revision,
        download_revision.approve('GUI tester', 'Synthetic export', True), [download_source])
    app.load_history()
    finish(app)
    app.history_table.selection_set('0')
    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder) / 'review.zip'
        with patch('tkinter.filedialog.asksaveasfilename', return_value=str(package)):
            app.export_review()
            finish(app)
        with zipfile.ZipFile(package) as archive:
            assert archive.read('originals/01-synthetic.csv') == download_source.content
            assert 'standardized.csv' in archive.namelist()
        app.browse_files()
        finish(app)
        index = next(i for i, item in enumerate(app.remote_files) if item['realName'] == '01-synthetic.csv')
        app.file_table.selection_set(str(index))
        attachment = Path(folder) / 'original.csv'
        with patch('tkinter.filedialog.asksaveasfilename', return_value=str(attachment)):
            app.export_file()
            finish(app)
        assert attachment.read_bytes() == download_source.content
        with patch('tkinter.filedialog.asksaveasfilename', return_value=''), \
                patch.object(app, 'attachment_bytes') as cancelled:
            app.export_file()
            cancelled.assert_not_called()
        with patch('tkinter.filedialog.asksaveasfilename', return_value=str(attachment)), \
                patch.object(app, 'attachment_bytes', side_effect=InputError('Synthetic corrupt download')):
            app.export_file()
            finish(app)
        assert attachment.read_bytes() == download_source.content
        assert len(errors) == 1 and 'Synthetic corrupt download' in errors.pop()[1]
    app.token.set('synthetic-token')
    app.tenant.set(SANDBOX)
    assert app.client is None and app.token.get() == '' and not app.remote_files
    assert not app.history_rows and app.group_id is None and app.destination is None
    app.remember.set(False)
    # Failed sign-in cannot leave a stale connected indicator, experiment, or remote preview.
    app.show_text(app.remote_text, 'Synthetic previous account record')
    app.token.set('synthetic-token')
    with patch('catalyst_desktop.gui.SciSureClient', return_value=client_for(lambda *_: (401, b''))):
        app.connect()
        finish(app)
    assert app.client is None and app.group_id is None and app.experiments == []
    assert 'not verified' in app.connection_status.get()
    assert 'previous account record' not in app.remote_text.get('1.0', 'end')
    assert len(errors) == 1 and 'HTTP 401' in errors[0][1], str(errors)
    errors.pop()  # The deliberately rejected sign-in is the only expected error.
    with patch('catalyst_desktop.gui.forget_token') as removed:
        app.forget()
        finish(app)
        removed.assert_called_once()
    assert app.experiment.get() == '' and app.group_id is None
    assert app.connection_status.get() == 'Disconnected.'
    assert app.transfer_operations, 'Disconnecting must retain uncertain-write guards'
    app.new_submission(ask=False)
    assert app.review_empty.winfo_manager() == 'pack' and app.review_content.winfo_manager() == ''
    assert not app.optional_context.opened
    assert app.mapping_section.winfo_manager() == 'pack'
    assert app.review_button.instate(['disabled'])
    assert not errors, str(errors)
    app.close()
print('Native GUI smoke passed: layout, required-field navigation, mapping, approval, schema setup, custom-server credentials, verified review/file downloads, cancelled/failed saves, reconnect guards, and disconnected state. No network calls.')
