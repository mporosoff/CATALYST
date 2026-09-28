"""Real Tk native-inventory review and transfer checks using synthetic in-memory data."""
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import ttk
import traceback
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'tests')]

from catalyst_desktop.gui import Application
from catalyst_desktop.configuration import SchemaInstaller, plan_configuration
from catalyst_desktop.model import Source
from catalyst_desktop.publication import read_review
from test_inventory import InventoryAPI
from test_api_contract import client_for

ERRORS, CALLBACKS = [], []


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(parent, label):
    return next(widget for widget in descendants(parent)
        if isinstance(widget, ttk.Button) and widget.cget('text') == label)


def finish(app):
    deadline = time.monotonic() + 30
    app.root.update()
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.02)
    app.root.update()
    assert not app.busy, 'Native inventory GUI action timed out.'
    assert not CALLBACKS, '\n'.join(CALLBACKS)
    assert not ERRORS, str(ERRORS)


def enter(app, **values):
    for key, value in values.items():
        app.context_vars[key].set(value)
    app.root.update()
    app.refresh_workflow_requirements()


def select_link(app, kind, identifier=None):
    app.refresh_link_options(kind)
    rows = app.link_rows[kind]['rows']
    index = next((i for i, row in enumerate(rows) if identifier is None or
        (row['subject']['id'] if kind == 'sample' else row['procedure']['id']) == identifier), None)
    assert index is not None, f'Missing saved {kind}: {identifier}'
    app.link_widgets[kind].current(index)
    app.link_widgets[kind].event_generate('<<ComboboxSelected>>')
    app.root.update()


def stage(app):
    previous = {item.id for item in app.batch_queue.items}
    button(app.workflow_card, 'Stage this record').invoke()
    finish(app)
    items = [item for item in app.batch_queue.items if item.id not in previous]
    assert len(items) == 1, 'Stage must create one reviewable queue item.'
    assert not items[0].errors, items[0].errors
    return items[0]


def approve(app, item, expected_inventory=None):
    app.batch_panel.select(item.id)
    app.batch_panel._buttons['inspect'].invoke()
    app.root.update()
    assert app.review_batch_id == item.id and not app.acknowledge.get()
    if expected_inventory:
        summary = app.record_summary_text.get('1.0', 'end')
        assert 'LIMS inventory actions' in summary, summary
        assert expected_inventory in summary, summary
        assert item.revision.value()['preview']['native_inventory_plan']
    app.reviewer.set('Synthetic inventory GUI reviewer')
    app.review_note.set('Checked sample identity, exact native inventory action and original files.')
    app.acknowledge.set(True)
    button(app.review_content, 'Approve this revision').invoke()
    finish(app)
    assert app.batch_queue.get(item.id).status == 'approved'


def assert_layout(app, page):
    app.tabs.select(page)
    app.root.update()
    left, right = app.root.winfo_rootx(), app.root.winfo_rootx() + app.root.winfo_width()
    for widget in descendants(page):
        if widget.winfo_ismapped() and isinstance(widget, (ttk.Button, ttk.Combobox, ttk.Entry, ttk.Checkbutton)):
            assert widget.winfo_rootx() >= left and widget.winfo_rootx() + widget.winfo_width() <= right, (
                widget.winfo_class(), widget.cget('text') if 'text' in widget.keys() else '',
                widget.winfo_rootx() - left, widget.winfo_width(), right - left)


def run():
    root = tk.Tk()
    root.report_callback_exception = lambda *exc: CALLBACKS.append(''.join(traceback.format_exception(*exc)))
    root.geometry('900x680')
    app = Application(root, smoke=True)
    try:
        api = InventoryAPI()
        app.client = client_for(api)
        app.group_id = 7
        SchemaInstaller(app.client, 7).apply(plan_configuration(app.client, 7))
        app.experiments = [api.experiment]
        app.experiment.configure(values=['42 · Synthetic experiment'])
        app.experiment.current(0)
        app.verify_destination()
        finish(app)
        assert app.destination and app.publisher and app.catalog is not None

        # New registration includes four populated native metadata values and no synthesis form.
        app.change_record_type('sample', confirm=False)
        enter(app, localSampleId='SYNTHETIC native GUI sample', sampleDescription='Synthetic supported material')
        inventory = next(widget for widget in descendants(app.link_frame)
            if isinstance(widget, ttk.Checkbutton) and 'LIMS inventory' in widget.cget('text'))
        assert app.context_vars['inventoryMode'].get() == 'native', 'Connected new physical samples should default to native inventory.'
        assert_layout(app, app.import_tab)
        sample_id = app.context_vars['specimenId'].get()
        sample = stage(app)
        sample_plan = sample.revision.value()['preview']['native_inventory_plan']
        assert sample_plan['sample']['action'] == 'create' and sample_plan['link'] is None
        assert len(sample_plan['sample']['body']['sampleMetas']) == 4
        assert api.sample_writes == api.link_writes == 0, 'Staging must never write to inventory.'

        app.change_record_type('procedure', confirm=False)
        enter(app, procedureName='SYNTHETIC XRD method', procedureType='measurement',
            procedureModality='XRD', procedureVersion='1', procedureText='Synthetic instructions for instrument acquisition.')
        procedure_id = app.context_vars['procedureId'].get()
        procedure = stage(app)

        app.change_record_type('measurement', confirm=False)
        select_link(app, 'sample', sample_id)
        select_link(app, 'procedure', procedure_id)
        enter(app, acquiredBy='Synthetic researcher', acquiredAt='2026-09-23')
        app.set_sources([Source.from_bytes('synthetic-native-xrd.dat', b'SYNTHETIC native diffraction data', parse=False)])
        assert app.context_vars['inventoryMode'].get() == 'native'
        assert_layout(app, app.import_tab)
        measurement = stage(app)
        assert measurement.revision.value()['preview']['native_inventory_plan']['link']['section_type'] == 'SAMPLESIN'
        approve(app, sample, 'create sample if absent')
        approve(app, procedure)
        approve(app, measurement, 'Used sample')
        assert_layout(app, app.review_tab)
        app.send_batch()
        finish(app)
        assert all(item.status == 'complete' for item in app.batch_queue.items)
        assert api.sample_writes == 1 and api.link_writes == 1
        assert api.samples[0]['altID'] == sample_id
        assert any(section['sectionType'] == 'SAMPLESIN' for section in api.sections)
        completed_measurement = app.batch_queue.get(measurement.id)
        loaded = read_review(app.client, app.destination, completed_measurement.receipt['section_id'], True)
        assert loaded['state'] == 'complete' and loaded['sources'][0].content == measurement.sources[0].content

        # One sample picker searches saved records and native inventory, then
        # stages an existing native reference without interrupting this draft.
        api.types.append(dict(sampleTypeID=600, groupID=7, deleted=False, name='External sample type'))
        external = dict(sampleID=700, sampleTypeID=600, name='SYNTHETIC external original', archived=False,
            altID='EXT-SYNTHETIC-10', description='Synthetic external material', meta=[])
        api.samples.append(external)
        app.change_record_type('measurement', confirm=False)
        enter(app, acquiredBy='Synthetic second researcher', acquiredAt='2026-09-22')
        app.set_sources([Source.from_bytes('synthetic-external-xrd.dat', b'SYNTHETIC external diffraction data', parse=False)])
        preserved_dataset = app.context_vars['datasetId'].get()
        search = app.link_rows['sample']['search']
        search.insert(0, 'SYNTHETIC external original')
        button(app.link_frame, 'Search LIMS').invoke()
        finish(app)
        choices = app.link_rows['sample']['rows']
        assert len(choices) == 1 and choices[0]['native']['sample_id'] == 700
        assert app.link_widgets['sample'].cget('height') == 12
        app.link_widgets['sample'].current(0)
        app.link_widgets['sample'].event_generate('<<ComboboxSelected>>')
        finish(app)
        external_registration = app.batch_queue.items[-1]
        assert external_registration.revision.value()['preview']['context']['nativeSampleId'] == '700'
        assert external_registration.revision.value()['preview']['context']['sampleDescription'] == external['description']
        assert external_registration.revision.value()['preview']['native_inventory_plan']['sample']['action'] == 'reuse'
        assert app.record_type.get() == 'measurement'
        assert app.context_vars['acquiredBy'].get() == 'Synthetic second researcher'
        assert app.context_vars['acquiredAt'].get() == '2026-09-22'
        assert app.context_vars['datasetId'].get() == preserved_dataset
        assert app.sources[0].name == 'synthetic-external-xrd.dat'
        assert app.context_vars['inventoryMode'].get() == 'native'
        assert not any(row.get('source') == 'native' for row in app.sample_choices(app.available_library(), 'SYNTHETIC external original')), 'Registered and native choices must not show the same sample twice.'
        select_link(app, 'procedure', procedure_id)
        external_measurement = stage(app)
        approve(app, external_registration, 'reuse existing sample')
        approve(app, external_measurement, 'Used sample')
        app.send_batch()
        finish(app)
        assert all(item.status == 'complete' for item in app.batch_queue.items)
        assert api.sample_writes == 1, 'Reusing existing inventory must not create a duplicate sample.'
        assert api.link_writes == 2
        assert api.samples[-1] == external, 'An external sample reference must preserve the native sample.'
        assert any(700 in identifiers for identifiers in api.sample_links.values())
        writes = api.sample_writes, api.link_writes
        app.send_batch()
        finish(app)
        assert (api.sample_writes, api.link_writes) == writes, 'Completed queue records must not be sent twice.'
    finally:
        root.destroy()


if __name__ == '__main__':
    with patch('tkinter.messagebox.showerror', side_effect=lambda *a, **k: ERRORS.append(a)), \
            patch('tkinter.messagebox.showwarning', side_effect=lambda *a, **k: ERRORS.append(a)), \
            patch('tkinter.messagebox.showinfo'), patch('tkinter.messagebox.askyesno', return_value=True):
        run()
    print('Native inventory GUI passed: minimal registration, exact action review, staged dependencies, verified Used-sample links, searchable existing inventory, preserved draft and no duplicate sample creation. Synthetic API only.')
