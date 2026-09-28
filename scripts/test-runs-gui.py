"""Exercise existing/new runs and parent creation through real Tk controls; no network."""
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
from catalyst_desktop.scisure import SciSureClient
from test_runs import RunsAPI

ERRORS, CALLBACKS = [], []


def children(parent):
    for widget in parent.winfo_children():
        yield widget
        yield from children(widget)


def button(parent, text):
    return next(w for w in children(parent) if isinstance(w, ttk.Button) and w.cget('text') == text)


def finish(app):
    deadline = time.monotonic() + 20
    app.root.update()
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(.02)
    app.root.update()
    assert not app.busy, 'Run GUI timed out.'
    assert not ERRORS, str(ERRORS)
    assert not CALLBACKS, str(CALLBACKS)


def run():
    root = tk.Tk()
    root.report_callback_exception = lambda *exc: CALLBACKS.append(''.join(traceback.format_exception(*exc)))
    app = Application(root, smoke=True)
    api = RunsAPI()
    app.client = SciSureClient('synthetic-run-token', transport=api)
    app.group_id = 7
    selected = []
    try:
        app.open_run_picker('new', callback=lambda: selected.append(app.destination))
        finish(app)
        state = app.run_picker_state
        assert app._run_selection('project')['projectID'] == 10
        assert app._run_selection('study')['studyID'] == 20
        state['name'].set('Synthetic characterization run')
        button(app.run_window, 'Review new run').invoke()
        finish(app)
        assert not api.writes, 'Opening a creation review must not write.'
        button(app.run_window, 'Create run').invoke()
        finish(app)
        assert app.destination['experiment_name'] == 'Synthetic characterization run'
        assert len(selected) == 1 and len(api.writes) == 1 and app.publisher
        assert app.experiment.current() == 0

        # Existing-run selection must not create anything.
        app.open_run_picker('existing', callback=lambda: selected.append(app.destination))
        finish(app)
        state = app.run_picker_state
        assert len(state['tree'].get_children()) == 1
        button(app.run_window, 'Use selected run').invoke()
        finish(app)
        assert len(selected) == 2 and len(api.writes) == 1

        # A new project requires explicit notes and its own creation review.
        app.open_run_picker('new', callback=lambda: selected.append(app.destination))
        finish(app)
        button(app.run_window, 'New project…').invoke()
        root.update()
        editor = next(w for w in children(app.run_window) if isinstance(w, tk.Toplevel) and w.title() == 'New project')
        entries = [w for w in children(editor) if isinstance(w, ttk.Entry)]
        entries[0].insert(0, 'Synthetic project')
        entries[1].insert(0, 'A synthetic purpose for this test')
        button(editor, 'Review new project').invoke()
        finish(app)
        assert len(api.writes) == 1
        button(app.run_window, 'Create project').invoke()
        finish(app)
        assert app._run_selection('project')['name'] == 'Synthetic project'
        assert not app.run_picker_state['studies']

        button(app.run_window, 'New study…').invoke()
        root.update()
        editor = next(w for w in children(app.run_window) if isinstance(w, tk.Toplevel) and w.title() == 'New study')
        next(w for w in children(editor) if isinstance(w, ttk.Entry)).insert(0, 'Synthetic study')
        button(editor, 'Review new study').invoke()
        finish(app)
        button(app.run_window, 'Create study').invoke()
        finish(app)
        assert app._run_selection('study')['name'] == 'Synthetic study'
        assert len(api.writes) == 3

        # Server changes close the browser so old tenant rows cannot be selected.
        window = app.run_window
        app.tenant.set('https://synthetic.example.org')
        root.update()
        assert not window.winfo_exists() and app.run_window is None
        assert app.client is None and app.destination is None
        print('Run GUI passed: reviewed run/project/study creation, existing reuse, auto-selection and stale-connection cleanup.')
    finally:
        app.executor.shutdown(wait=True)
        root.destroy()


if __name__ == '__main__':
    with patch('tkinter.messagebox.showerror', side_effect=lambda *args, **kwargs: ERRORS.append(args)):
        run()
