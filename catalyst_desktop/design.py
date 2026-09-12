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
    style.configure('Vertical.TScrollbar', arrowsize=10, width=10, borderwidth=0, troughcolor=PAPER, background='#BBCBBB', bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [('Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
    style.configure('Horizontal.TScrollbar', arrowsize=10, borderwidth=0, troughcolor=PAPER, background='#BBCBBB', bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.layout('Horizontal.TScrollbar', [('Horizontal.Scrollbar.trough', {'sticky': 'we', 'children': [('Horizontal.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
    return style


class Card(tk.Frame):
    def __init__(self, parent, padding=22):
        super().__init__(parent, background='white', highlightbackground=LINE, highlightthickness=1, borderwidth=0)
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
