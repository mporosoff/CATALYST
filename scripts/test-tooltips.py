"""Native Tk checks for field-help timing, accessibility, and lifecycle cleanup."""
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalyst_desktop.design import Tooltip


def pump(root, duration=0):
    end = time.monotonic() + duration
    while True:
        root.update()
        if time.monotonic() >= end:
            return
        time.sleep(.01)


def main():
    root = tk.Tk()
    root.geometry('360x160+40+40')
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    try:
        field = ttk.Entry(root)
        field.pack(padx=20, pady=20)
        caption = ttk.Label(root, text='Field caption')
        caption.pack()
        caption_tip = Tooltip(caption, 'Help remains available by hovering over the caption.')
        other = ttk.Entry(root)
        other.pack()
        text = tk.StringVar(root, 'Required: identifies the person responsible for the data.')
        tip = Tooltip(field, text.get)
        pump(root)
        # Caption help must not add a tab stop between the editable fields.
        field.focus_force()
        pump(root)
        field.event_generate('<Tab>')
        pump(root)
        assert root.focus_get() is other, 'Tab navigation stopped on a static caption'
        caption.event_generate('<Enter>')
        pump(root, .5)
        assert caption_tip._window is not None and root.focus_get() is other
        caption.event_generate('<Leave>')
        assert caption_tip._window is None
        # Hover must wait, then show the latest text without taking keyboard focus.
        other.focus_force()
        pump(root)
        field.event_generate('<Enter>')
        assert tip._after_id and tip._window is None
        text.set('Updated guidance for this field.')
        pump(root, .5)
        assert tip._window is not None
        assert tip._window.winfo_children()[0].cget('text') == text.get()
        assert root.focus_get() is other
        window = tip._window
        assert 0 <= window.winfo_rootx() <= root.winfo_screenwidth() - window.winfo_width()
        assert 0 <= window.winfo_rooty() <= root.winfo_screenheight() - window.winfo_height()
        other.event_generate('<Escape>')
        pump(root)
        assert tip._window is None
        # Keyboard focus gives the same help; leaving or clicking removes it.
        field.focus_force()
        pump(root, .5)
        assert tip._window is not None and root.focus_get() is field
        field.event_generate('<Leave>')
        assert tip._window is None
        field.event_generate('<Enter>')
        pump(root, .5)
        other.event_generate('<ButtonPress-1>')
        assert tip._window is None
        # Leaving during the delay must cancel help rather than open it later.
        field.event_generate('<Enter>')
        field.event_generate('<Leave>')
        pump(root, .5)
        assert tip._window is None and tip._after_id is None
        # Replacing help removes old bindings, and destruction cancels pending callbacks.
        replacement = Tooltip(field, 'Replacement guidance.')
        assert tip._destroyed and not tip._bindings and field._tooltip is replacement
        field.event_generate('<Enter>')
        assert replacement._after_id is not None
        field.destroy()
        pump(root, .5)
        assert replacement._destroyed and replacement._after_id is None
        assert not replacement._bindings
        caption.destroy()
        assert not root.bind('<Escape>') and not root.bind('<ButtonPress>')
        assert not errors, errors
    finally:
        root.destroy()
    print('Tooltip native-widget checks passed.')


if __name__ == '__main__':
    main()
