"""Local, dependency-free visual components for the desktop workspace."""
import math
import sys
import tkinter as tk
from tkinter import ttk

PAPER = '#F3F5F1'
INK = '#203B32'
MUTED = '#64776E'
GREEN = '#2C6852'
LINE = '#DEE6DF'
SIDEBAR = '#173A30'


class Tooltip:
    """Delayed field help, available on hover and keyboard focus without taking focus."""

    def __init__(self, widget, text):
        previous = getattr(widget, '_tooltip', None)
        if previous is not None:
            previous.destroy()
        self.widget = widget
        self.text = text
        self._window = None
        self._after_id = None
        self._destroyed = False
        self._bindings = []
        widget._tooltip = self
        for sequence, callback in (
            ('<Enter>', self._schedule), ('<Leave>', self.hide),
            ('<ButtonPress>', self.hide), ('<Unmap>', self.hide),
            ('<Destroy>', self._on_destroy),
        ):
            self._bind(widget, sequence, callback)
        # Tk treats Focus bindings as evidence that a widget belongs in tab traversal.
        # Static captions retain hover help without introducing extra keyboard stops.
        if not isinstance(widget, (tk.Label, ttk.Label, tk.Message, tk.Frame,
                                   ttk.Frame, tk.LabelFrame, ttk.LabelFrame, ttk.Separator)):
            self._bind(widget, '<FocusIn>', self._schedule)
            self._bind(widget, '<FocusOut>', self.hide)
        # A hovered label may not own focus; Escape and clicking elsewhere still dismiss it.
        top = widget.winfo_toplevel()
        self._bind(top, '<Escape>', self.hide)
        self._bind(top, '<ButtonPress>', self.hide)

    def _bind(self, widget, sequence, callback):
        binding = widget.bind(sequence, callback, add='+')
        self._bindings.append((widget, sequence, binding))

    def _schedule(self, _event=None):
        self.hide()
        if not self._destroyed:
            try:
                self._after_id = self.widget.after(400, self._show)
            except tk.TclError:
                pass

    def _show(self):
        self._after_id = None
        if self._destroyed:
            return
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
                return
            text = self.text() if callable(self.text) else self.text
            if not text:
                return
            self.hide()
            window = self._window = tk.Toplevel(self.widget, takefocus=False)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes('-topmost', True)
            # Window managers can recognize this as passive help, not a new application window.
            if window.tk.call('tk', 'windowingsystem') == 'win32':
                window.attributes('-disabled', True)
            elif window.tk.call('tk', 'windowingsystem') == 'x11':
                window.attributes('-type', 'tooltip')
            left, top = self.widget.winfo_vrootx(), self.widget.winfo_vrooty()
            screen_width = self.widget.winfo_vrootwidth()
            screen_height = self.widget.winfo_vrootheight()
            tk.Label(
                window, text=str(text), justify='left', anchor='w',
                background=INK, foreground='white', padx=12, pady=9,
                font=('Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI', 10),
                wraplength=max(80, min(360, screen_width - 48)),
            ).pack()
            window.update_idletasks()
            width = min(window.winfo_reqwidth(), screen_width - 16)
            height = min(window.winfo_reqheight(), screen_height - 16)
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            if y + height > top + screen_height - 8:
                y = self.widget.winfo_rooty() - height - 6
            x = max(left + 8, min(x, left + screen_width - width - 8))
            y = max(top + 8, min(y, top + screen_height - height - 8))
            window.geometry(f'{width}x{height}+{x}+{y}')
            window.deiconify()
        except tk.TclError:
            # A field can disappear when changing data types or navigating during the delay.
            self.hide()

    def hide(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        window, self._window = self._window, None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _on_destroy(self, event):
        if event.widget is self.widget:
            self.destroy()

    def destroy(self):
        """Remove the window, pending callback, and only this tooltip's event bindings."""
        if self._destroyed:
            return
        self._destroyed = True
        self.hide()
        for widget, sequence, binding in self._bindings:
            try:
                widget.unbind(sequence, binding)
            except tk.TclError:
                pass
        self._bindings.clear()
        if getattr(self.widget, '_tooltip', None) is self:
            self.widget._tooltip = None


def rounded_image(root, fill, edge, size=28, radius=8):
    """A small scalable nine-slice button surface, drawn locally."""
    image = tk.PhotoImage(master=root, width=size, height=size)
    def inside(x, y, inset):
        low, high, r = inset, size - inset, radius - inset
        if not (low <= x <= high and low <= y <= high): return False
        cx, cy = min(max(x, low + r), high - r), min(max(y, low + r), high - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2
    for y in range(size):
        for x in range(size):
            if inside(x + .5, y + .5, 0):
                image.put(fill if inside(x + .5, y + .5, 1) else edge, (x, y))
    return image


def icon(root, name, color=GREEN, size=22):
    """Simple original line icons. No network assets, fonts, or image libraries."""
    shapes = {
        'upload': [[(4, 15), (4, 19), (20, 19), (20, 15)], [(12, 15), (12, 4)], [(7, 9), (12, 4), (17, 9)]],
        'review': [[(5, 3), (16, 3), (20, 7), (20, 21), (5, 21), (5, 3)], [(9, 13), (12, 16), (17, 10)]],
        'library': [[(4, 5), (20, 5), (20, 10), (4, 10), (4, 5)], [(4, 14), (20, 14), (20, 19), (4, 19), (4, 14)], [(8, 7), (8, 8)], [(8, 16), (8, 17)]],
        'samples': [[(12, 2), (21, 7), (21, 17), (12, 22), (3, 17), (3, 7), (12, 2)], [(3, 7), (12, 12), (21, 7)], [(12, 12), (12, 22)]],
        'connection': [[(8, 3), (8, 8)], [(16, 3), (16, 8)], [(5, 8), (19, 8), (19, 11), (16, 15), (8, 15), (5, 11), (5, 8)], [(12, 15), (12, 21)]],
        'file': [[(5, 2), (14, 2), (20, 8), (20, 22), (5, 22), (5, 2)], [(14, 2), (14, 8), (20, 8)], [(9, 12), (16, 12)], [(9, 16), (16, 16)]],
        'calendar': [[(3, 5), (21, 5), (21, 21), (3, 21), (3, 5)], [(3, 10), (21, 10)], [(8, 2), (8, 7)], [(16, 2), (16, 7)],
            [(7, 14), (8, 14)], [(12, 14), (13, 14)], [(16, 14), (17, 14)], [(7, 18), (8, 18)], [(12, 18), (13, 18)]],
        'procedure': [[(6, 3), (18, 3), (18, 21), (6, 21), (6, 3)], [(9, 8), (15, 8)], [(9, 12), (15, 12)], [(9, 16), (13, 16)]],
        'export': [[(4, 15), (4, 19), (20, 19), (20, 15)], [(12, 4), (12, 15)], [(7, 10), (12, 15), (17, 10)]],
        'help': [[(12, 3), (19, 6), (21, 12), (19, 18), (12, 21), (5, 18), (3, 12), (5, 6), (12, 3)], [(9, 9), (12, 7), (15, 9), (12, 12), (12, 14)], [(12, 17), (12, 18)]],
    }
    paths = shapes[name]
    segments = [(a, b) for path in paths for a, b in zip(path, path[1:])]
    image = tk.PhotoImage(master=root, width=size, height=size)
    for y in range(size):
        for x in range(size):
            px, py = (x + .5) * 24 / size, (y + .5) * 24 / size
            for (ax, ay), (bx, by) in segments:
                dx, dy = bx - ax, by - ay
                t = min(1, max(0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
                if math.hypot(px - ax - t * dx, py - ay - t * dy) <= .8:
                    image.put(color, (x, y)); break
    return image


def apply_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    family = 'Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI'
    root.option_add('*Listbox.background', 'white')
    root.option_add('*Listbox.foreground', INK)
    root.option_add('*Listbox.selectBackground', '#DCECE1')
    root.option_add('*Listbox.selectForeground', INK)
    style.configure('.', font=(family, 10), background='white', foreground=INK)
    for name, bg in [('TFrame', 'white'), ('Page.TFrame', PAPER), ('Sidebar.TFrame', SIDEBAR)]:
        style.configure(name, background=bg)
    for name, bg, fg, font in [
        ('TLabel', 'white', INK, (family, 10)),
        ('Muted.TLabel', 'white', MUTED, (family, 10)),
        ('Heading.TLabel', PAPER, INK, (family, 27, 'bold')),
        ('Page.TLabel', PAPER, MUTED, (family, 10)),
        ('Eyebrow.TLabel', PAPER, GREEN, (family, 9, 'bold')),
        ('Sub.TLabel', 'white', INK, (family, 12, 'bold')),
        ('Small.TLabel', 'white', MUTED, (family, 9)),
        ('Badge.TLabel', '#E7EFE8', GREEN, (family, 9, 'bold')),
        ('Brand.TLabel', SIDEBAR, '#F5F5E9', (family, 19, 'bold')),
        ('Side.TLabel', SIDEBAR, '#AEC5B6', (family, 9)),
        ('SideCaption.TLabel', SIDEBAR, '#86AC96', (family, 8, 'bold')),
    ]:
        style.configure(name, background=bg, foreground=fg, font=font)
    style.configure('Badge.TLabel', padding=(10, 5))
    root._design_images = []
    for name, fill, edge, active, foreground in [
        ('TButton', '#FFFFFF', LINE, '#F0F5EF', INK),
        ('Primary.TButton', GREEN, GREEN, '#20523F', 'white'),
        ('Quiet.TButton', PAPER, PAPER, '#E5EDE4', GREEN),
        ('Disclosure.TButton', 'white', LINE, '#F5F8F3', INK),
        ('Nav.TButton', SIDEBAR, SIDEBAR, '#244E3D', '#C4D7C9'),
        ('SelectedNav.TButton', '#315B43', '#315B43', '#3B674D', '#FFFFFF'),
        ('Segment.TButton', '#F1F5EF', '#F1F5EF', '#E4EDE2', MUTED),
        ('SelectedSegment.TButton', '#E0EDDF', '#E0EDDF', '#D0E4CF', '#23543B'),
    ]:
        normal = rounded_image(root, fill, edge)
        hover = rounded_image(root, active, active)
        focus = rounded_image(root, fill, '#B7D6AF' if name in ('Primary.TButton', 'Nav.TButton', 'SelectedNav.TButton') else '#638C6C')
        disabled = rounded_image(root, '#E9EDE8', '#E9EDE8')
        root._design_images.extend((normal, hover, focus, disabled))
        element = 'Catalyst.' + name
        style.element_create(element, 'image', normal, ('disabled', disabled), ('pressed', hover), ('focus', focus), ('active', hover), border=9, sticky='nsew')
        style.layout(name, [(element, {'sticky': 'nsew', 'children': [('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, padding=(12, 5), background=SIDEBAR if name in ('Nav.TButton', 'SelectedNav.TButton') else PAPER if name == 'Quiet.TButton' else 'white', foreground=foreground, font=(family, 10, 'bold' if name in ('Primary.TButton', 'SelectedNav.TButton') else 'normal'))
        style.map(name, foreground=[('disabled', '#87958A')])
    style.configure('Nav.TButton', anchor='w', padding=(8, 7))
    style.configure('SelectedNav.TButton', anchor='w', padding=(8, 7))
    style.configure('Disclosure.TButton', anchor='w', padding=(14, 8), font=(family, 11, 'bold'))
    style.configure('Segment.TButton', padding=(6, 5), font=(family, 9))
    style.configure('SelectedSegment.TButton', padding=(6, 5), font=(family, 9, 'bold'))
    style.layout('TMenubutton', style.layout('TButton'))
    style.configure('TMenubutton', padding=(12, 5), font=(family, 10), foreground=INK, background='white')
    style.configure('TEntry', padding=(11, 9), fieldbackground='#FBFCFA', bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map('TEntry', bordercolor=[('focus', '#638C6C')])
    style.configure('TCombobox', padding=(10, 9), arrowsize=13, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED)
    style.map('TCombobox', fieldbackground=[('readonly', '#FBFCFA')], selectbackground=[('readonly', '#E3EFE4')], selectforeground=[('readonly', INK)])
    style.configure('TCheckbutton', padding=(0, 6), background='white', foreground=MUTED)
    style.configure('TSeparator', background=LINE)
    style.configure('Treeview', rowheight=36, fieldbackground='white', background='white', foreground=INK, borderwidth=0)
    style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
    style.configure('Treeview.Heading', padding=(12, 11), background='#EDF3EC', foreground=MUTED, relief='flat', font=(family, 9, 'bold'))
    style.map('Treeview', background=[('selected', '#E0EDDF')], foreground=[('selected', INK)])
    style.configure('TNotebook', background=PAPER, borderwidth=0, bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.configure('TNotebook.Tab', padding=(14, 10), background='#E6EDE3', foreground=MUTED)
    style.map('TNotebook.Tab', background=[('selected', 'white')], foreground=[('selected', GREEN)])
    tab = rounded_image(root, '#F1F5EF', '#F1F5EF')
    selected = rounded_image(root, '#DFEBDB', '#DFEBDB')
    tab_focus = rounded_image(root, '#DFEBDB', '#638C6C')
    root._design_images.extend((tab, selected, tab_focus))
    style.element_create('Catalyst.tab', 'image', tab, ('focus', tab_focus), ('selected', selected), border=9, sticky='nsew')
    style.layout('TNotebook.Tab', [('Catalyst.tab', {'sticky': 'nsew', 'children': [('Notebook.padding', {'sticky': 'nsew', 'children': [('Notebook.label', {'sticky': 'nsew'})]})]})])
    style.configure('TNotebook.Tab', padding=(8, 4), font=(family, 9))
    style.layout('Workspace.TNotebook.Tab', [])
    style.configure('Vertical.TScrollbar', arrowsize=10, width=10, gripcount=0, borderwidth=0, troughcolor=PAPER, background='#BBCBBB', bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
    style.configure('Horizontal.TScrollbar', arrowsize=10, gripcount=0, borderwidth=0, troughcolor=PAPER, background='#BBCBBB', bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.layout('Horizontal.TScrollbar', [('Horizontal.Scrollbar.trough', {'sticky': 'we', 'children': [('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
    return style


class Card(tk.Frame):
    def __init__(self, parent, padding=22):
        super().__init__(parent, background='white', highlightbackground=LINE, highlightcolor=LINE, highlightthickness=1, borderwidth=0, takefocus=0)
        self.body = ttk.Frame(self, padding=padding)
        self.body.pack(fill='both', expand=True)


def page_heading(parent, title, description, eyebrow='CATALYST WORKSPACE'):
    frame = ttk.Frame(parent, style='Page.TFrame')
    frame.pack(fill='x', pady=(0, 24))
    ttk.Label(frame, text=eyebrow, style='Eyebrow.TLabel').pack(anchor='w', pady=(0, 7))
    ttk.Label(frame, text=title, style='Heading.TLabel').pack(anchor='w')
    ttk.Label(frame, text=description, style='Page.TLabel', wraplength=680).pack(anchor='w', pady=(8, 0))
    return frame


def section_navigation(parent, notebook, labels):
    """Keyboard-accessible, wrapping controls for the context notebook."""
    bar = ttk.Frame(parent)
    buttons = {}
    for i, (frame, label) in enumerate(labels):
        button = ttk.Button(bar, text=label, style='Segment.TButton', command=lambda frame=frame: notebook.select(frame))
        button.grid(row=i // 3, column=i % 3, sticky='ew', padx=(0, 5), pady=(0, 5))
        bar.columnconfigure(i % 3, weight=1, uniform='section')
        buttons[str(frame)] = button
    def update(_=None):
        for frame, button in buttons.items():
            button.configure(style='SelectedSegment.TButton' if frame == notebook.select() else 'Segment.TButton')
    notebook.bind('<<NotebookTabChanged>>', update)
    update()
    return bar
