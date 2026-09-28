"""Reusable Tk controls: calendar date picker, type-to-search dropdown, scrolling form."""
from __future__ import annotations

import calendar
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk

from .design import INK, MUTED, GREEN, LINE, PAPER, icon


class DateEntry(ttk.Frame):
    """A date field with a calendar button. Defaults to today; the value is YYYY-MM-DD."""

    def __init__(self, parent, value=None, width=12, on_change=None):
        super().__init__(parent)
        self.var = tk.StringVar(value=(value or date.today()).isoformat() if not isinstance(value, str) else value)
        self.on_change = on_change
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.pack(side='left', fill='x', expand=True)
        self._icon = icon(self, 'calendar', size=18)
        self.button = ttk.Button(self, image=self._icon, command=self.open, padding=(6, 4), takefocus=False)
        self.entry.bind('<Alt-Down>', lambda _e: self.open())
        self.button.pack(side='left', padx=(4, 0))
        self.var.trace_add('write', lambda *_: self.on_change and self.on_change())
        self._popup = None

    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(value.isoformat() if isinstance(value, date) else str(value or ''))

    def date(self):
        try:
            return datetime.strptime(self.get(), '%Y-%m-%d').date()
        except ValueError:
            return None

    def open(self):
        if self._popup is not None and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = CalendarPopup(self, self.date() or date.today(), self._picked)

    def _picked(self, value):
        self.set(value)
        self.entry.focus_set()


class CalendarPopup(tk.Toplevel):
    def __init__(self, anchor, current, callback):
        super().__init__(anchor)
        self.withdraw()
        self.overrideredirect(True)
        self.configure(background=LINE)
        self.callback, self.current = callback, current
        self.shown = current.replace(day=1)
        body = tk.Frame(self, background='white', padx=10, pady=8)
        body.pack(padx=1, pady=1)
        head = tk.Frame(body, background='white')
        head.pack(fill='x')
        ttk.Button(head, text='‹', width=3, command=lambda: self.move(-1)).pack(side='left')
        self.title = tk.Label(head, background='white', foreground=INK, font=('Segoe UI', 10, 'bold'))
        self.title.pack(side='left', expand=True)
        ttk.Button(head, text='›', width=3, command=lambda: self.move(1)).pack(side='right')
        self.grid_frame = tk.Frame(body, background='white')
        self.grid_frame.pack(pady=(6, 4))
        foot = tk.Frame(body, background='white')
        foot.pack(fill='x')
        ttk.Button(foot, text='Today', style='Quiet.TButton', command=lambda: self.pick(date.today())).pack(side='left')
        ttk.Button(foot, text='Close', style='Quiet.TButton', command=self.destroy).pack(side='right')
        self.draw()
        self.bind('<Escape>', lambda _: self.destroy())
        self.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        self.geometry(f'+{x}+{y}')
        self.deiconify()
        self.lift()
        self.focus_set()
        self.bind('<FocusOut>', lambda e: self.after(150, self._maybe_close))

    def _maybe_close(self):
        try:
            focus = self.focus_get()
        except (KeyError, tk.TclError):
            focus = None
        if self.winfo_exists() and (focus is None or not str(focus).startswith(str(self))):
            self.destroy()

    def move(self, months):
        year, month = self.shown.year + (self.shown.month - 1 + months) // 12, (self.shown.month - 1 + months) % 12 + 1
        self.shown = date(year, month, 1)
        self.draw()

    def draw(self):
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self.title.configure(text=self.shown.strftime('%B %Y'))
        for column, name in enumerate(('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')):
            tk.Label(self.grid_frame, text=name, background='white', foreground=MUTED, width=4,
                font=('Segoe UI', 8, 'bold')).grid(row=0, column=column)
        for row, week in enumerate(calendar.Calendar().monthdatescalendar(self.shown.year, self.shown.month), 1):
            for column, day in enumerate(week):
                selected, today = day == self.current, day == date.today()
                colour = 'white' if selected else INK if day.month == self.shown.month else '#A9B6AE'
                button = tk.Label(self.grid_frame, text=str(day.day), width=4, pady=3, cursor='hand2',
                    background=GREEN if selected else '#EAF3EA' if today else 'white', foreground=colour,
                    font=('Segoe UI', 9, 'bold' if today or selected else 'normal'))
                button.grid(row=row, column=column, padx=1, pady=1)
                button.bind('<Button-1>', lambda _e, d=day: self.pick(d))

    def pick(self, value):
        self.destroy()
        self.callback(value)


class SearchCombo(ttk.Combobox):
    """Type to narrow the list. Items are (value, label) pairs; get_value() returns the value."""

    def __init__(self, parent, items=(), width=40, on_select=None, allow_blank=False, placeholder=''):
        super().__init__(parent, width=width)
        self.on_select, self.allow_blank = on_select, allow_blank
        self._items, self._value = [], None
        self.placeholder = placeholder
        self.set_items(items)
        self.bind('<KeyRelease>', self._filter)
        self.bind('<<ComboboxSelected>>', self._selected)
        self.bind('<FocusOut>', self._resolve)
        self.bind('<Return>', self._resolve)

    def set_items(self, items):
        self._items = [(str(v), str(l)) for v, l in items]
        self.configure(values=[label for _, label in self._items])
        if self._value is not None and not any(v == self._value for v, _ in self._items):
            self._value = None

    def _filter(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape', 'Tab'):
            return
        words = self.get().casefold().split()
        matches = [label for _, label in self._items if all(w in label.casefold() for w in words)]
        self.configure(values=matches if words else [label for _, label in self._items])
        self._value = None

    def _selected(self, _event=None):
        label = self.get()
        self._value = next((v for v, l in self._items if l == label), None)
        if self.on_select:
            self.on_select(self._value)

    def _resolve(self, _event=None):
        text = self.get().strip()
        if not text:
            if self._value is not None:
                self._value = None
                if self.on_select: self.on_select(None)
            return
        match = next((v for v, l in self._items if l == text or v == text), None)
        if match is None:
            words = text.casefold().split()
            found = [v for v, l in self._items if all(w in l.casefold() for w in words)]
            match = found[0] if len(found) == 1 else None
        if match is not None and match != self._value:
            self.set_value(match)
            if self.on_select: self.on_select(match)

    def get_value(self):
        if self._value is None:
            self._resolve()
        return self._value

    def set_value(self, value):
        value = None if value in (None, '') else str(value)
        if value is not None and not any(v == value for v, _ in self._items):
            value = None  # e.g. a draft that refers to a sample that no longer exists
        self._value = value
        self.set(next((l for v, l in self._items if v == value), '') if value else '')


class ScrollFrame(ttk.Frame):
    """A vertically scrolling container; put children in ``.body``."""

    def __init__(self, parent, style='TFrame', padding=0):
        super().__init__(parent, style=style)
        background = PAPER if style == 'Page.TFrame' else 'white'
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=background)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style=style, padding=padding)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor='nw')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.body.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.bind_all('<FocusIn>', self._follow_focus, add='+')
        self.bind_all('<MouseWheel>', self._wheel, add='+')
        self.bind_all('<Button-4>', lambda e: self._scroll(e, -1), add='+')
        self.bind_all('<Button-5>', lambda e: self._scroll(e, 1), add='+')

    def _follow_focus(self, event):
        """Scroll so the field reached with Tab is visible."""
        widget = event.widget
        if not isinstance(widget, tk.Misc) or not self.winfo_ismapped():
            return
        try:
            if not str(widget).startswith(str(self.body) + '.'):
                return
            top = widget.winfo_rooty() - self.body.winfo_rooty()
            bottom = top + widget.winfo_height()
            height = max(1, self.body.winfo_height())
            view_top = self.canvas.canvasy(0)
            view_bottom = view_top + self.canvas.winfo_height()
            if top < view_top + 10:
                self.canvas.yview_moveto(max(0, top - 60) / height)
            elif bottom > view_bottom - 10:
                self.canvas.yview_moveto(max(0, bottom - self.canvas.winfo_height() + 60) / height)
        except (tk.TclError, KeyError):
            pass

    def _inside(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return False
        while widget is not None:
            if widget is self:
                return True
            if isinstance(widget, (tk.Text, ttk.Treeview, tk.Listbox)):
                return False
            widget = widget.master
        return False

    def _wheel(self, event):
        if self._inside(event):
            self.canvas.yview_scroll(int(-event.delta / (120 if abs(event.delta) >= 120 else 1)), 'units')

    def _scroll(self, event, amount):
        if self._inside(event):
            self.canvas.yview_scroll(amount, 'units')

    def top(self):
        self.canvas.yview_moveto(0)


def text_box(parent, height=4, width=60):
    box = tk.Text(parent, height=height, width=width, wrap='word', relief='flat', borderwidth=0,
        highlightthickness=1, highlightbackground=LINE, highlightcolor='#638C6C', background='#FBFCFA',
        foreground=INK, font=('Segoe UI', 10), padx=10, pady=8, undo=True)
    # Tab moves to the next field (as in every other box); Ctrl+Tab inserts a real tab character.
    box.bind('<Tab>', lambda e: (e.widget.tk_focusNext().focus_set(), 'break')[1])
    box.bind('<Shift-Tab>', lambda e: (e.widget.tk_focusPrev().focus_set(), 'break')[1])
    box.bind('<ISO_Left_Tab>', lambda e: (e.widget.tk_focusPrev().focus_set(), 'break')[1])
    box.bind('<Control-Tab>', lambda e: (e.widget.insert('insert', '\t'), 'break')[1])
    return box


def text_value(box):
    return box.get('1.0', 'end').strip()


def set_text(box, value):
    box.delete('1.0', 'end')
    box.insert('1.0', value or '')


class ComponentsEditor(ttk.Frame):
    """Rows of  [metal / phase] [loading] [unit]  with + Add and × remove. Tab moves along each row."""

    def __init__(self, parent, units, on_change=None, rows=2):
        super().__init__(parent)
        self.units, self.on_change, self.rows = units, on_change, []
        head = ttk.Frame(self)
        head.pack(fill='x')
        for text, width in (('Metal, promoter or phase', 26), ('Loading', 9), ('Unit', 12)):
            ttk.Label(head, text=text, style='Hint.TLabel', width=width).pack(side='left', padx=(0, 8))
        self.body = ttk.Frame(self)
        self.body.pack(fill='x')
        self.add_button = ttk.Button(self, text='+ Add another metal / phase', style='Quiet.TButton', command=self.add)
        self.add_button.pack(anchor='w', pady=(4, 0))
        for _ in range(rows):
            self.add(notify=False)

    def add(self, values=None, notify=True):
        row = ttk.Frame(self.body)
        row.pack(fill='x', pady=2)
        name = ttk.Entry(row, width=26)
        loading = ttk.Entry(row, width=9)
        unit = ttk.Combobox(row, values=self.units, state='readonly', width=11)
        unit.set((values or {}).get('unit') or self.units[0])
        remove = ttk.Button(row, text='×', width=3, takefocus=False, style='Quiet.TButton', command=lambda: self.remove(row))
        for widget in (name, loading, unit):
            widget.pack(side='left', padx=(0, 8))
            widget.bind('<KeyRelease>', lambda _e: self._changed())
        unit.bind('<<ComboboxSelected>>', lambda _e: self._changed())
        remove.pack(side='left')
        if values:
            name.insert(0, values.get('component') or '')
            if values.get('loading') is not None:
                loading.insert(0, f"{values['loading']:g}" if isinstance(values['loading'], float) else str(values['loading']))
        self.rows.append((row, name, loading, unit))
        if notify:
            name.focus_set()
            self._changed()

    def remove(self, frame):
        self.rows = [r for r in self.rows if r[0] is not frame]
        frame.destroy()
        if not self.rows:
            self.add(notify=False)
        self._changed()

    def get(self):
        return [dict(component=n.get(), loading=l.get(), unit=u.get()) for _, n, l, u in self.rows
            if n.get().strip() or l.get().strip()]

    def set(self, values):
        for frame, *_ in self.rows:
            frame.destroy()
        self.rows = []
        for value in values or []:
            self.add(value, notify=False)
        while len(self.rows) < 2:
            self.add(notify=False)

    def _changed(self):
        if self.on_change:
            self.on_change()
