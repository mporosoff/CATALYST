"""Exercise corrective buttons against real adaptive Tk controls and staged drafts."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'tests')]
from catalyst_desktop.gui import Application
from catalyst_desktop.model import Revision, Source, build_preview
from catalyst_desktop.issues_gui import issue_action
from test_adaptive_workflow import context


def main():
    root = tk.Tk()
    errors = []
    app = Application(root, smoke=True)
    root.report_callback_exception = lambda *args: errors.append(args)
    root.geometry('1000x780'); root.update(); root.focus_force()
    source = Source.from_bytes('synthetic-notes.txt', b'Synthetic synthesis notes', parse=False)
    values = context('synthesis', specimenId='', uploadMode='originals')
    app.change_record_type('synthesis', confirm=False)
    for key, value in values.items():
        if key in app.context_vars: app.context_vars[key].set(value)
    app.set_sources([source])
    preview = build_preview([source], 'UR', 'synthesis', values, raw_only=True)
    revision = Revision.create(preview, 'Synthetic synthesis with missing sample')
    app.revision = revision
    app.render_review(revision); app.tabs.select(app.review_tab); root.update()
    blocking = [i for i in preview['validation']['issues'] if i['severity'] == 'error']
    assert len(blocking) == 1, blocking
    assert 'SMP identifier' not in app.issue_text.get('1.0', 'end')
    assert app.fix_issue_button.cget('text') == 'Choose or add sample'
    app.fix_issue_button.invoke(); root.update()
    assert app.tabs.select() == str(app.import_tab)
    assert root.focus_get() == app.link_widgets['sample']
    assert app.context_vars['acquiredAt'].get() == values['acquiredAt']
    assert app.sources[0].content == source.content

    staged = app.batch_queue.add(revision, (source,))
    app.inspect_batch_item(staged.id); root.update()
    app.fix_issue_button.invoke(); root.update()
    assert app.editing_batch_id == staged.id
    assert app.review_batch_id is None
    assert app.batch_queue.get(staged.id).approval is None
    assert app.sources[0].content == source.content

    app.render_issues([dict(code='DATE_INVALID', message='Use YYYY-MM-DD for the date performed.', severity='error')])
    app.tabs.select(app.review_tab); root.focus_force(); app.fix_issue_button.invoke(); root.update()
    assert root.focus_get() == app.context_widgets['acquiredAt'], (str(root.focus_get()),
        str(app.context_widgets['acquiredAt']), app.issue_list.selection(), app.validation_issues, errors)
    assert app.tabs.select() == str(app.import_tab)

    app.render_issues([dict(code='INVENTORY_DESTINATION', message='Choose a run before reviewing inventory actions.', severity='error')])
    app.fix_issue_button.invoke(); root.update()
    assert app.tabs.select() == str(app.connection_tab)
    assert issue_action(dict(code='SPECTRAL_MAPPING'))[0] == 'mapping'
    assert issue_action(dict(code='PROCEDURE_CONTENT'))[0] == 'procedureText'
    app.tabs.select(app.import_tab)
    app.show_action_error('SciSure HTTP 401: check your token.')
    root.update()
    popup = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == 'CATALYST · action needs attention')
    actions = popup.winfo_children()[0].winfo_children()[-1]
    button = next(w for w in actions.winfo_children() if w.cget('text') == 'Go to connection')
    assert button.winfo_ismapped()
    button.invoke(); root.update()
    assert app.tabs.select() == str(app.connection_tab) and not popup.winfo_exists()
    assert app.sources[0].content == source.content
    for target, label, page in (('review', 'Return to review', app.review_tab),
            ('mapping', 'Review column mapping', app.import_tab), ('files', 'Review original files', app.import_tab)):
        app.show_action_error('Synthetic correction request.', target=target); root.update()
        popup = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == 'CATALYST · action needs attention')
        actions = popup.winfo_children()[0].winfo_children()[-1]
        matches = [w for w in actions.winfo_children() if w.cget('text') == label]
        assert matches, (target, [w.cget('text') for w in actions.winfo_children()], errors)
        matches[0].invoke(); root.update()
        assert app.tabs.select() == str(page) and not popup.winfo_exists()
    assert not errors, errors
    app.close()
    print('Issue correction buttons, field focus, draft preservation and batch editing passed.')


if __name__ == '__main__': main()
