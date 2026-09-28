"""Real Tk smoke for sample-first discovery; synthetic records, no live credentials."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'tests')]
from catalyst_desktop.gui import Application
from catalyst_desktop.model import Source, Revision, build_preview, make_profile
from test_sample_workspace import entry, native, HistoryClient
from test_adaptive_workflow import context
from desktop_fixtures import uid


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def run():
    root = tk.Tk()
    app = Application(root, smoke=True)
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    try:
        root.geometry('900x680'); root.update()
        assert app.tabs.select() == str(app.catalog_tab)
        app.client = HistoryClient(); app.group_id = 7
        app.catalog = dict(entries=[entry('sample', 1), entry('measurement', 2, experiment=88)], pending=[])
        app.native_workspace_rows = [native(canonical=uid('UR', 'SMP')), native(9, 'Other sample')]
        app.sample_workspace_scope = (app.client.origin, 7)
        app.filter_catalog(); root.update()
        assert len(app.catalog_rows) == 2
        index = next(i for i, row in enumerate(app.catalog_rows) if row.get('entry'))
        app.catalog_table.selection_set(str(index)); root.update()
        assert '2 linked CATALYST records' in app.sample_selection_summary.get()
        with patch.object(app, 'run', side_effect=lambda label, work, done, **kw: done(work())):
            app.open_sample_workspace(); root.update()
        window = app.sample_detail_window
        assert window and window.winfo_exists()
        labels = [str(widget.cget('text')) for widget in widgets(window) if isinstance(widget, ttk.Button)]
        assert 'Download record + originals…' in labels
        assert 'Sample documents…' in labels
        assert 'Attach sample documents…' in labels
        assert 'Browse experiment attachments…' in labels
        record_table = next(widget for widget in widgets(window) if isinstance(widget, ttk.Treeview)
            and tuple(widget.cget('columns')) == ('type', 'technique', 'date', 'run', 'relation'))
        measurement_key = next(key for key in record_table.get_children()
            if record_table.item(key, 'values')[0] == 'measurement')
        record_table.selection_set(measurement_key)
        source = Source.from_bytes('synthetic-values.csv', b'angle,intensity\n20.0,123.40\n')
        profile = make_profile('UR', 'XRD', 'csv', 'synthetic', 'Synthetic XRD', 1, 'Table', 1,
            [dict(source='angle', target='two_theta_deg', unit='degree (2theta)'),
             dict(source='intensity', target='signal', unit='as recorded')])
        preview = build_preview([source], 'UR', 'XRD', context('measurement', uploadMode='mapped', signalUnit='counts'), profile)
        loaded = dict(revision=Revision.create(preview, 'Synthetic saved XRD data'), sources=[source], state='complete',
            destination=dict(experiment_name='Experiment 88'))
        with patch('catalyst_desktop.sample_workspace.read_review', return_value=loaded), \
                patch.object(app, 'run', side_effect=lambda label, work, done, **kw: done(work())):
            next(widget for widget in widgets(window) if isinstance(widget, ttk.Button)
                and widget.cget('text') == 'Open record details').invoke()
            root.update()
            standardized = next(widget for widget in widgets(window) if isinstance(widget, ttk.Treeview)
                and any(widget.heading(column, 'text') == 'signal [counts]' for column in widget.cget('columns')))
            assert len(standardized.get_children()) == 1
            actual_values = dict(zip((standardized.heading(column, 'text') for column in standardized.cget('columns')),
                standardized.item(standardized.get_children()[0], 'values')))
            assert actual_values == {'two_theta_deg': '20', 'signal [counts]': '123.4', 'Source row': '2'}, actual_values
            assert standardized.winfo_ismapped() and standardized.winfo_height() >= 100, ('standardized table height', standardized.winfo_height())
            next(widget for widget in widgets(window) if isinstance(widget, ttk.Button)
                and widget.cget('text') == 'Preview original table').invoke()
            root.update()
            original = next(widget for widget in widgets(window) if isinstance(widget, ttk.Treeview)
                and [widget.heading(column, 'text') for column in widget.cget('columns')] == ['Source row', 'A', 'B'])
            original_page = original.master.master
            original_page.master.select(original_page)
            root.update()
            assert len(original.get_children()) == 2
            actual_original = original.item(original.get_children()[1], 'values')
            assert actual_original == ('2', '20.0', '123.40'), actual_original
            assert original.winfo_ismapped() and original.winfo_height() >= 78, ('original table height', original.winfo_height())
        window.destroy()
        with patch('catalyst_desktop.workflow_gui.messagebox.askyesno', return_value=True), patch.object(app, 'apply_sample_choice') as apply:
            app.start_selected_sample('measurement')
            apply.assert_called_once()
        assert app.record_type.get() == 'measurement'
        app.tabs.select(app.catalog_tab); root.update()
        left, right = root.winfo_rootx(), root.winfo_rootx() + root.winfo_width()
        for widget in widgets(app.catalog_tab):
            if widget.winfo_ismapped() and isinstance(widget, (ttk.Button, ttk.Entry, ttk.Menubutton)):
                assert left <= widget.winfo_rootx() and widget.winfo_rootx() + widget.winfo_width() <= right, widget
        assert not errors, errors
        print('PASS sample workspace: exact identity merge, cross-experiment data, standardized values, verified original table preview, native links, download actions, 900px layout')
    finally:
        app.executor.shutdown(wait=False, cancel_futures=True)
        root.destroy()


if __name__ == '__main__': run()
