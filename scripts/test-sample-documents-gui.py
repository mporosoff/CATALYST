"""Review, send and retrieve sample documents through real Tk with synthetic storage."""
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT), str(PROJECT / 'tests')]
from catalyst_desktop.gui import Application
from catalyst_desktop.model import Source
from test_api_contract import client_for
from test_sample_documents import DocumentsAPI


def widgets(parent):
    for child in parent.winfo_children():
        yield child
        yield from widgets(child)


def button(parent, label):
    return next(w for w in widgets(parent) if isinstance(w, ttk.Button) and w.cget('text') == label)


def main():
    root = tk.Tk(); app = Application(root, smoke=True)
    errors = []; root.report_callback_exception = lambda *exc: errors.append(exc)
    api = DocumentsAPI(); client = client_for(api)
    app.client, app.group_id = client, 7
    source = Source.from_bytes('synthetic-certificate.pdf', b'Synthetic sample certificate', parse=False)
    try:
        with patch.object(app, 'run', side_effect=lambda label, work, done, **kwargs: done(work())):
            app._review_sample_documents(client, 7, 8, api.samples[0], (source,))
            root.update()
            window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            assert button(window, 'Send documents').instate(['disabled'])
            assert api.uploads == api.document_links == 0
            button(window, 'Review documents').invoke(); root.update()
            assert not button(window, 'Send documents').instate(['disabled'])
            assert api.uploads == api.document_links == 0
            button(window, 'Send documents').invoke(); root.update()
            assert api.uploads == api.document_links == 1
            assert api.native_posts == 0
            assert all(method != 'PUT' for method, _ in api.document_requests)
            assert not window.winfo_exists()
            browser = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
            listing = next(w for w in widgets(browser) if isinstance(w, ttk.Treeview))
            selected = next(i for i in listing.get_children() if listing.item(i, 'values')[0] == source.name)
            listing.selection_set(selected)
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / source.name
                with patch('catalyst_desktop.sample_documents_gui.filedialog.asksaveasfilename', return_value=str(target)):
                    button(browser, 'Download selected document…').invoke(); root.update()
                assert target.read_bytes() == source.content
            assert not errors, errors
        print('Sample documents GUI passed: review before writes, direct sample attachment, preserved links and exact download.')
    finally:
        app.executor.shutdown(wait=False, cancel_futures=True); root.destroy()


if __name__ == '__main__': main()
