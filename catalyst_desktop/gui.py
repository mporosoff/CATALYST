"""Native Tk desktop UI. All research state stays in memory until SciSure transfer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import webbrowser
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .design import apply_theme, Card, page_heading, section_navigation, icon, PAPER, INK, MUTED, GREEN, SIDEBAR, LINE
from .model import (Source, Revision, InputError, MODALITIES, COMMON_CONTEXT, MODALITY_CONTEXT,
    FIELDS, build_preview, make_profile, table, check_sources, load_sources)
from .scisure import SciSureClient, SciSureError, SANDBOX, remote_id, tenant_origin
from .credentials import load_token, save_token, forget_token, CredentialError
from .publication import Publisher, history, read_review, download
from .exports import suggested_filename, save_bytes, review_archive
from catalyst_ingest.jsonio import strict_loads
from .traceability import (LAB_CHOICES, MATERIAL_KINDS, MODEL_RELATIONS, PHYSICAL_CONTEXT,
    COMPUTATIONAL_CONTEXT, lab_id, lab_name, new_id, trace_context)
from .catalog import load_catalog, search_catalog
from .integration import inspect_setup, setup_summary
from .configuration import plan_configuration, configuration_summary, configuration_document, SchemaInstaller

RELEASES_URL = 'https://github.com/mporosoff/CATALYST/releases'


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = self.canvas = tk.Canvas(self, highlightthickness=0, background=PAPER, yscrollincrement=16)
        self.wheel_remainder = 0.0
        bar = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        self.body = ttk.Frame(canvas, padding=(30, 26), style='Page.TFrame')
        window = canvas.create_window((0, 0), window=self.body, anchor='nw')
        canvas.configure(yscrollcommand=bar.set)
        self.body.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')


class Disclosure(ttk.Frame):
    def __init__(self, parent, title, opened=False):
        super().__init__(parent, style='Page.TFrame')
        self.title, self.opened = title, opened
        self.toggle_button = ttk.Button(self, style='Disclosure.TButton', command=lambda: self.set_open(not self.opened))
        self.toggle_button.pack(fill='x')
        self.body = ttk.Frame(self, padding=(12, 12, 12, 4))
        self.set_open(opened)

    def set_open(self, opened):
        self.opened = bool(opened)
        self.toggle_button.configure(text=('▾  ' if self.opened else '▸  ') + self.title)
        if self.opened: self.body.pack(fill='x')
        else: self.body.pack_forget()


class Application:
    def __init__(self, root, smoke=False):
        self.root = root
        self.smoke = smoke
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.messages = queue.Queue()
        self.busy = False
        self.sources = []
        self.revision = None
        self.approval = None
        self.parent = None
        self.publisher = None
        self.transfer_operations = {}  # Keep uncertain-write guards across reconnects for this app session.
        self.schema_operations = {}
        self.client = None
        self.destination = None
        self.group_id = None
        self.experiments = []
        self.history_rows = []
        self.remote_files = []
        self.rules = []
        self.dirty = True
        self.pending_profile = None
        self.context_vars = {}
        self.catalog = None
        self.catalog_rows = []
        self.root.title(f'CATALYST · {__version__}')
        self.root.geometry(f'{max(900, min(1280, root.winfo_screenwidth() - 80))}x{max(680, min(860, root.winfo_screenheight() - 120))}')
        self.root.minsize(900, 680)
        self.root.configure(background=PAPER)
        apply_theme(root)
        self.status = tk.StringVar(value='Ready when you are. Nothing is sent until you approve a review.')
        self.sidebar = ttk.Frame(root, width=230, style='Sidebar.TFrame', padding=(18, 28))
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)
        brand = tk.Canvas(self.sidebar, width=52, height=46, background=SIDEBAR, highlightthickness=0)
        brand.pack(anchor='w', pady=(0, 10))
        points = [(8, 23), (20, 5), (42, 10), (44, 34), (23, 42)]
        for a, b in zip(points, points[1:] + points[:1]):
            brand.create_line(*a, *b, fill='#82B48F', width=2)
        for x, y in points:
            brand.create_oval(x-4, y-4, x+4, y+4, fill='#CBE1B8', outline=SIDEBAR, width=2)
        brand.create_oval(21, 19, 31, 29, fill='#F2D396', outline='')
        ttk.Label(self.sidebar, text='CATALYST', style='Brand.TLabel').pack(anchor='w')
        ttk.Label(self.sidebar, text='CATALYSIS DATA WORKSPACE', style='SideCaption.TLabel').pack(anchor='w', pady=(6, 32))
        ttk.Label(self.sidebar, text='WORKSPACE', style='SideCaption.TLabel').pack(anchor='w', padx=12, pady=(0, 10))
        self.main = ttk.Frame(root, style='Page.TFrame')
        self.main.pack(side='left', fill='both', expand=True)
        toolbar = ttk.Frame(self.main, padding=(30, 18), style='Page.TFrame')
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='SIX LABORATORIES  /  ONE SHARED WORKSPACE', style='Eyebrow.TLabel').pack(side='left')
        self.connection_badge = tk.StringVar(value='○  SciSure · Offline')
        ttk.Label(toolbar, textvariable=self.connection_badge, style='Badge.TLabel').pack(side='right')
        ttk.Separator(self.main).pack(fill='x')
        self.tabs = ttk.Notebook(self.main, style='Workspace.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        self.connection_tab = ScrollFrame(self.tabs)
        self.import_tab = ScrollFrame(self.tabs)
        self.review_tab = ttk.Frame(self.tabs, padding=(30, 26), style='Page.TFrame')
        self.history_tab = ttk.Frame(self.tabs, padding=(30, 26), style='Page.TFrame')
        self.catalog_tab = ttk.Frame(self.tabs, padding=(30, 26), style='Page.TFrame')
        self.navigation_buttons = {}
        self.nav_images = []
        for frame, label, graphic in [(self.import_tab, 'New submission', 'upload'), (self.review_tab, 'Review', 'review'),
                (self.history_tab, 'Saved records', 'library'), (self.catalog_tab, 'Samples & models', 'samples'),
                (self.connection_tab, 'SciSure connection', 'connection')]:
            self.tabs.add(frame, text=label)
            photo = icon(root, graphic, '#BCD1BF', 20)
            self.nav_images.append(photo)
            button = ttk.Button(self.sidebar, text='  ' + label, image=photo, compound='left', style='Nav.TButton',
                command=lambda frame=frame: self.tabs.select(frame))
            button.pack(fill='x', pady=3)
            self.navigation_buttons[str(frame)] = button
        def highlight_navigation(_=None):
            for frame, button in self.navigation_buttons.items():
                button.configure(style='SelectedNav.TButton' if frame == self.tabs.select() else 'Nav.TButton')
        self.tabs.bind('<<NotebookTabChanged>>', highlight_navigation)
        highlight_navigation()
        bottom = ttk.Frame(self.sidebar, style='Sidebar.TFrame')
        bottom.pack(side='bottom', fill='x')
        ttk.Label(bottom, text='TRACEABLE BY DESIGN', style='SideCaption.TLabel').pack(anchor='w', pady=(0, 8))
        ttk.Label(bottom, text='Every sample.\nEvery lab. Every revision.', style='Side.TLabel', justify='left').pack(anchor='w')
        ttk.Button(bottom, text='Get latest version  ↗', style='Nav.TButton', command=self.open_downloads).pack(fill='x', pady=(18, 4))
        ttk.Label(bottom, text='DESKTOP  /  ' + __version__, style='SideCaption.TLabel').pack(anchor='w', padx=12)
        self.build_connection()
        self.build_import()
        self.build_review()
        self.build_history()
        self.build_catalog()
        self.connection_status.trace_add('write', lambda *_: self.connection_badge.set('●  SciSure · Connected' if self.connection_status.get().startswith('Connected') else '○  SciSure · Offline'))
        self._scroll_tag = 'CatalystWheel' + str(id(self))
        for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            self.root.bind_class(self._scroll_tag, event, self.route_wheel)
        self.root.bind_all('<Map>', lambda event: self.install_wheel_handlers(event.widget), add='+')
        self.install_wheel_handlers()
        self.status_label = ttk.Label(self.main, textvariable=self.status, padding=(30, 12), wraplength=610, style='Page.TLabel')
        self.status_label.pack(side='bottom', fill='x', before=self.tabs)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.report_callback_exception = self.callback_error
        self.root.after(100, self.poll)

    def callback_error(self, *_):
        messagebox.showerror('CATALYST', 'This action could not be completed. Your in-memory review is retained.', parent=self.root)

    def install_wheel_handlers(self, widget=None):
        if not hasattr(self, '_scroll_tag'): return
        widget = widget or self.root
        try:
            tags = widget.bindtags()
            if self._scroll_tag not in tags: widget.bindtags((self._scroll_tag,) + tags)
            for child in widget.winfo_children(): self.install_wheel_handlers(child)
        except (AttributeError, tk.TclError):
            pass

    def route_wheel(self, event):
        widget = event.widget
        # Text, result tables, and open drop-down lists keep their own native scrolling.
        if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)) or not isinstance(widget, tk.Misc): return
        parent = widget
        while parent is not None and not isinstance(parent, ScrollFrame): parent = getattr(parent, 'master', None)
        if parent is None: return
        if getattr(event, 'num', None) in (4, 5): movement = -3 if event.num == 4 else 3
        else:
            delta = getattr(event, 'delta', 0)
            movement = -delta if self.root.tk.call('tk', 'windowingsystem') == 'aqua' else -delta / 120 * 3
        parent.wheel_remainder += movement
        steps = int(parent.wheel_remainder)
        parent.wheel_remainder -= steps
        if steps and parent.canvas.yview() != (0.0, 1.0): parent.canvas.yview_scroll(steps, 'units')
        return 'break'  # Scrolling over a lab/unit selector must never change its selected value.

    def open_downloads(self):
        webbrowser.open(RELEASES_URL)
        self.status.set('Opened the GitHub downloads page. Close CATALYST before replacing its application file; your saved OS credential remains available.')

    def invalidate(self, *_):
        self.dirty = True
        self.approval = None
        if hasattr(self, 'approval_status'):
            self.approval_status.set('Changes require a new preview and approval.')

    def watched(self, value=''):
        var = tk.StringVar(value=value)
        var.trace_add('write', self.invalidate)
        return var

    def label_entry(self, parent, row, label, variable, width=30, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
        widget = ttk.Entry(parent, textvariable=variable, width=width, show=show or '')
        widget.grid(row=row, column=1, sticky='ew', pady=5)
        parent.columnconfigure(1, weight=1)
        return widget

    def run(self, description, work, done, failed=None):
        if self.busy:
            messagebox.showinfo('CATALYST', 'Wait for the current operation to finish.', parent=self.root)
            return
        self.busy = True
        self.locked_widgets = []
        def lock(parent):
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button, ttk.Menubutton)):
                    self.locked_widgets.append((child, child.state()))
                    child.state(['disabled'])
                lock(child)
        lock(self.root)
        self.status.set(description)
        self.root.configure(cursor='watch')
        future = self.executor.submit(work)
        future.add_done_callback(lambda f: self.messages.put(('result', f, done, failed)))

    def poll(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == 'progress':
                    self.status.set(message[1])
                    continue
                _, future, done, failed = message
                self.busy = False
                for widget, state in self.locked_widgets:
                    if widget.winfo_exists():
                        widget.state(['!disabled', '!readonly'])
                        widget.state(state)
                self.locked_widgets = []
                self.root.configure(cursor='')
                try:
                    result = future.result()
                    done(result)
                except (InputError, SciSureError, CredentialError) as e:
                    if failed: failed()
                    self.status.set(str(e))
                    messagebox.showerror('CATALYST — action paused', str(e), parent=self.root)
                except Exception:
                    if failed: failed()
                    self.status.set('The operation could not be completed. Your review remains in memory.')
                    messagebox.showerror('CATALYST', 'The operation could not be completed. No raw error or credential was logged.', parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def build_connection(self):
        p = self.connection_tab.body
        page_heading(p, 'SciSure connection', 'Your laboratory records, connected directly to this workspace.', 'CONNECT  /  SCISURE')
        card = Card(p)
        card.pack(fill='x')
        p = card.body
        ttk.Label(p, text='Account & access', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Enter your SciSure server URL and an API token for that server.', style='Muted.TLabel').pack(anchor='w', pady=(5, 8))
        fields = ttk.Frame(p)
        fields.pack(fill='x', pady=12)
        self.tenant = tk.StringVar(value=SANDBOX)
        self.token = tk.StringVar()
        self.label_entry(fields, 0, 'SciSure server URL', self.tenant)
        self.tenant.trace_add('write', self.server_changed)
        self.label_entry(fields, 1, 'API token', self.token, show='•')
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text='Remember in this computer’s operating system credential store', variable=self.remember).pack(anchor='w')
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=12)
        ttk.Button(actions, text='Connect', style='Primary.TButton', command=self.connect).pack(side='left')
        ttk.Button(actions, text='Use saved token', command=self.use_saved_token).pack(side='left', padx=8)
        ttk.Button(actions, text='Forget & disconnect', command=self.forget).pack(side='left')
        self.connection_status = tk.StringVar(value='Not connected. You can prepare a review offline.')
        ttk.Label(p, textvariable=self.connection_status, wraplength=530, style='Muted.TLabel').pack(anchor='w', pady=12)
        ttk.Separator(p).pack(fill='x', pady=12)
        ttk.Label(p, text='Publication destination', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Choose the experiment that will receive approved files.', style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        self.experiment = ttk.Combobox(p, state='readonly', width=90)
        self.experiment.pack(fill='x', pady=10)
        self.experiment.bind('<<ComboboxSelected>>', lambda _: self.clear_destination())
        ttk.Button(p, text='Verify selected destination', command=self.verify_destination).pack(anchor='w')
        self.destination_status = tk.StringVar(value='No destination verified.')
        ttk.Label(p, textvariable=self.destination_status, wraplength=530, style='Muted.TLabel').pack(anchor='w', pady=10)
        ttk.Label(p, text='Shared tokens use the token account’s permissions and SciSure identity. Reviewer names in CATALYST are self-reported.', wraplength=530, style='Small.TLabel').pack(anchor='w', pady=12)
        advanced = Disclosure(p, 'Administrator tools · SciSure configuration')
        advanced.pack(fill='x', pady=(12, 0))
        p = advanced.body
        native_fields = ttk.Frame(p)
        native_fields.pack(fill='x')
        self.inspect_sample = tk.StringVar()
        self.inspect_protocol = tk.StringVar()
        self.label_entry(native_fields, 0, 'Existing SciSure sample ID (optional check)', self.inspect_sample)
        self.label_entry(native_fields, 1, 'SciSure protocol version ID (optional check)', self.inspect_protocol)
        ttk.Button(p, text='Inspect native SciSure setup (read only)', command=self.inspect_integration).pack(anchor='w', pady=5)
        ttk.Button(p, text='Prepare CATALYST material configuration', command=self.prepare_configuration).pack(anchor='w', pady=5)

    def prepare_configuration(self):
        if self.busy or not self.client or self.group_id is None:
            self.status.set('Connect to the CATALYST group before preparing its material schema.')
            return
        client, group = self.client, self.group_id
        def done(plan):
            window = tk.Toplevel(self.root)
            window.title('CATALYST material configuration')
            window.geometry('1000x740')
            tabs = ttk.Notebook(window)
            tabs.pack(fill='both', expand=True, padx=12, pady=12)
            for label, contents in [('Changes to apply', configuration_summary(plan)),
                    ('Configuration definition', json.dumps(configuration_document(), ensure_ascii=False, indent=2))]:
                frame, text = self.text_panel(tabs, height=30)
                tabs.add(frame, text=label)
                self.show_text(text, contents)
            def apply():
                if self.busy or self.client is not client or self.group_id != group:
                    self.status.set('Reconnect or wait for the current action, then prepare configuration again.')
                    return
                key = (client.origin, group)
                installer = SchemaInstaller(client, group, self.schema_operations.setdefault(key, {}))
                def installed(result):
                    if window.winfo_exists(): window.destroy()
                    self.status.set(f'Native material schema verified: sample type {result["sample_type_id"]}, {len(result["field_bindings"])} fields. No research samples or permissions were changed.')
                self.run('Applying the reviewed material schema…', lambda: installer.apply(plan,
                    lambda message: self.messages.put(('progress', message))), installed)
            actions = ttk.Frame(window, padding=12)
            actions.pack(fill='x')
            button = ttk.Button(actions, text='Apply listed schema additions', command=apply)
            button.pack(side='left')
            if plan['ready'] or plan['conflicts']: button.state(['disabled'])
            ttk.Button(actions, text='Close', command=window.destroy).pack(side='right')
            self.status.set('Configuration prepared for review. SciSure records have not been changed.')
        self.run('Discovering the CATALYST material type and field bindings…', lambda: plan_configuration(client, group), done)

    def inspect_integration(self):
        if not self.client or self.group_id is None:
            self.status.set('Connect first to inspect native sample types and account permissions.')
            return
        client, group = self.client, self.group_id
        try:
            sid = remote_id(self.inspect_sample.get()) if self.inspect_sample.get().strip() else None
            pid = remote_id(self.inspect_protocol.get()) if self.inspect_protocol.get().strip() else None
        except SciSureError as e:
            messagebox.showerror('CATALYST', str(e), parent=self.root)
            return
        index = self.experiment.current()
        eid = self.experiments[index]['experimentID'] if 0 <= index < len(self.experiments) else None
        def done(report):
            window = tk.Toplevel(self.root)
            window.title('SciSure integration setup — read-only findings')
            window.geometry('1000x700')
            tabs = ttk.Notebook(window)
            tabs.pack(fill='both', expand=True, padx=12, pady=12)
            for label, contents in [('Setup overview', setup_summary(report)), ('Field details', json.dumps(report, ensure_ascii=False, indent=2))]:
                frame, text = self.text_panel(tabs, height=30)
                tabs.add(frame, text=label)
                self.show_text(text, contents)
            self.status.set('Setup inspection finished. No sample, field, protocol, or permission was changed.')
        self.run('Inspecting native SciSure sample fields and account scope…', lambda: inspect_setup(client, group,
            lambda message: self.messages.put(('progress', message)), experiment_id=eid, sample_id=sid, protocol_version_id=pid), done)

    def server_changed(self, *_):
        # Never carry a loaded credential or a verified destination to another host.
        self.token.set('')
        self.client = None
        self.group_id = None
        self.experiments = []
        if hasattr(self, 'experiment'):
            self.experiment.configure(values=[])
            self.experiment.set('')
            self.clear_destination()
            self.connection_status.set('Server changed. Enter a token for this server and connect.')

    def connect(self):
        if self.busy:
            return
        token = self.token.get()
        origin = self.tenant.get()
        remember = self.remember.get()
        self.clear_destination()
        self.connection_status.set('Connecting to the selected SciSure server…')
        self.client = None
        self.group_id = None
        self.experiments = []
        self.experiment.configure(values=[])
        self.experiment.set('')
        def work():
            client = SciSureClient(token, origin=origin)
            connection = client.check_connection()
            warning = None
            if remember:
                try: save_token(client.origin, token.strip())
                except CredentialError as e: warning = str(e)
            return client, connection, warning
        def done(result):
            self.client, connection, warning = result
            self.group_id = connection['group_id']
            self.experiments = connection['experiments']
            self.experiment.configure(values=[f'{e["experimentID"]} · {e.get("name", "Unnamed")} · {e.get("signatureStatus", "Unknown status")}' for e in self.experiments])
            self.token.set('')
            self.connection_status.set(f'Connected to {self.client.origin} · {connection["group_name"]} (group {self.group_id}). {len(self.experiments)} experiments available.')
            self.status.set(warning or 'Connection verified. Select an experiment and verify it before sending data.')
        self.run('Checking the SciSure connection…', work, done,
            lambda: self.connection_status.set('Connection not verified. Check the token and connection, then try again.'))

    def use_saved_token(self):
        origin = self.tenant.get()
        def done(token):
            if not token:
                self.status.set('No saved token was found. Enter a token to connect.')
                return
            self.token.set(token)
            self.status.set('Saved token loaded. Click Connect to check SciSure.')
        self.run('Opening the system credential store…', lambda: load_token(tenant_origin(origin)), done)

    def forget(self):
        if self.busy:
            return
        origin = self.tenant.get()
        self.token.set('')
        self.client = None
        self.group_id = None
        self.experiments = []
        self.experiment.configure(values=[])
        self.experiment.set('')
        self.clear_destination()
        self.connection_status.set('Disconnected.')
        self.run('Removing the saved token…', lambda: forget_token(tenant_origin(origin)),
            lambda _: self.status.set('Saved token removed. Research files were not changed.'))

    def clear_destination(self):
        self.destination = None
        self.publisher = None
        self.history_rows = []
        self.remote_files = []
        self.loaded_review = None
        self.history_destination = self.files_destination = None
        self.catalog = None
        self.catalog_rows = []
        if hasattr(self, 'catalog_table'):
            self.catalog_table.delete(*self.catalog_table.get_children())
        if hasattr(self, 'history_table'):
            self.history_table.delete(*self.history_table.get_children())
            self.file_table.delete(*self.file_table.get_children())
        if hasattr(self, 'destination_status'):
            self.destination_status.set('No destination verified.')
        if hasattr(self, 'remote_text'):
            self.show_text(self.remote_text, 'Select and verify a SciSure destination to read its records.')
        if hasattr(self, 'catalog_status'):
            self.catalog_status.set('Refresh the catalog for the current connection. No catalog is cached on disk.')

    def selected_destination(self, writable=True):
        index = self.experiment.current()
        if not self.client or index < 0 or index >= len(self.experiments):
            raise InputError('Connect to SciSure and select an experiment first.')
        return self.client.destination(self.experiments[index]['experimentID'], self.group_id, writable=writable)

    def verify_destination(self):
        if self.busy:
            return
        index = self.experiment.current()
        if not self.client or index < 0:
            messagebox.showinfo('CATALYST', 'Connect and choose an experiment first.', parent=self.root)
            return
        client, eid, gid = self.client, self.experiments[index]['experimentID'], self.group_id
        def done(destination):
            self.destination = destination
            key = (destination['tenant'], gid, eid)
            self.publisher = Publisher(client, destination, self.transfer_operations.setdefault(key, {}))
            self.destination_status.set(f'Verified: {destination["experiment_name"]} · experiment {eid} · group {gid}')
            self.status.set('Destination verified. Sending still requires an approved revision and a transfer confirmation.')
        self.run('Verifying the destination…', lambda: client.destination(eid, gid), done)

    def build_import(self):
        p = self.import_tab.body
        page_heading(p, 'New submission', 'Bring your files together. Keep the full story of your sample.', 'PREPARE  /  REVIEW  /  SHARE')
        originals = Card(p)
        originals.pack(fill='x', pady=(0, 18))
        body = originals.body
        top = ttk.Frame(body)
        top.pack(fill='x')
        self.upload_icon = icon(self.root, 'upload', GREEN, 32)
        ttk.Label(top, image=self.upload_icon).pack(side='left', padx=(0, 18))
        copy = ttk.Frame(top)
        copy.pack(side='left', fill='x', expand=True)
        ttk.Label(copy, text='Start with your original files', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(copy, text='Tables, instrument exports, images and more.', style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        ttk.Button(top, text='Choose files', style='Primary.TButton', command=self.choose_files).pack(side='right', padx=(12, 0))
        ttk.Label(body, text='CSV  ·  XLSX  ·  JSON  ·  IMAGES  ·  NATIVE FILES', style='Small.TLabel').pack(anchor='w', pady=(18, 0))
        self.file_list = ttk.Frame(body)
        self.file_list.pack(fill='x')
        self.file_summary = tk.StringVar(value='Up to 6 files · 20 MiB each · 40 MiB total')
        options = ttk.Frame(body)
        options.pack(fill='x', pady=(14, 0))
        ttk.Label(options, textvariable=self.file_summary, style='Small.TLabel').pack(side='left')
        ttk.Button(options, text='+ Supporting files', command=self.add_supporting_files).pack(side='right')
        details = Card(p)
        details.pack(fill='x', pady=(0, 18))
        body = details.body
        ttk.Label(body, text='Submission details', style='Sub.TLabel').pack(anchor='w', pady=(0, 16))
        self.title = self.watched()
        self.entity = self.watched('Rochester')
        self.modality = self.watched('reactor')
        ttk.Label(body, text='Submission title', style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        ttk.Entry(body, textvariable=self.title).pack(fill='x', pady=(0, 16))
        selectors = ttk.Frame(body)
        selectors.pack(fill='x')
        selectors.columnconfigure(0, weight=1, uniform='selector')
        selectors.columnconfigure(1, weight=1, uniform='selector')
        ttk.Label(selectors, text='Source laboratory', style='Muted.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 6))
        ttk.Combobox(selectors, textvariable=self.entity, values=LAB_CHOICES, state='readonly', width=20).grid(row=1, column=0, sticky='ew', padx=(0, 16))
        ttk.Label(selectors, text='Data type', style='Muted.TLabel').grid(row=0, column=1, sticky='w', pady=(0, 6))
        combo = ttk.Combobox(selectors, textvariable=self.modality, values=MODALITIES, state='readonly', width=20)
        combo.grid(row=1, column=1, sticky='ew')
        combo.bind('<<ComboboxSelected>>', lambda _: self.rebuild_context())
        self.toolkit = tk.BooleanVar(value=False)
        self.toolkit.trace_add('write', self.invalidate)
        self.raw_only = tk.BooleanVar(value=False)
        self.raw_only.trace_add('write', self.invalidate)
        ttk.Checkbutton(body, text='Use the Rochester GC toolkit mapping', variable=self.toolkit).pack(anchor='w', pady=(12, 0))
        ttk.Checkbutton(body, text='Keep originals with context only · no table conversion', variable=self.raw_only).pack(anchor='w')
        self.context_section = Disclosure(p, '01   Sample & scientific context')
        self.context_section.pack(fill='x', pady=(0, 12))
        body = self.context_section.body
        ttk.Label(body, text='Identify the material, method and lab behind this submission. Reuse an existing identity from Samples & models.', style='Muted.TLabel', wraplength=570).pack(anchor='w', pady=(0, 16))
        identity_actions = ttk.Frame(body)
        identity_actions.pack(fill='x', pady=(0, 12))
        ttk.Button(identity_actions, text='New batch + sample IDs', command=lambda: self.generate_identity('batch')).pack(side='left')
        ttk.Button(identity_actions, text='New model ID', command=lambda: self.generate_identity('model')).pack(side='left', padx=6)
        ttk.Button(identity_actions, text='New dataset ID', command=self.generate_dataset_id).pack(side='left')
        self.context_frame = ttk.Frame(body)
        self.context_frame.pack(fill='x')
        self.rebuild_context()
        self.mapping_section = Disclosure(p, '02   Column mapping & versions')
        self.mapping_section.pack(fill='x', pady=(0, 12))
        p = self.mapping_section.body
        ttk.Label(p, text='For ordinary tables, choose a header row and explicit units. Toolkit bundles use their dedicated versioned mapping.', wraplength=950).pack(anchor='w', pady=5)
        mapping_form = ttk.Frame(p)
        mapping_form.pack(fill='x')
        self.source_choice = ttk.Combobox(mapping_form, state='readonly')
        self.source_choice.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Label(mapping_form, text='Table source file').grid(row=0, column=0, sticky='w')
        self.source_choice.bind('<<ComboboxSelected>>', lambda _: self.source_changed())
        self.sheet_choice = ttk.Combobox(mapping_form, state='readonly')
        self.sheet_choice.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(mapping_form, text='Worksheet').grid(row=1, column=0, sticky='w')
        self.sheet_choice.bind('<<ComboboxSelected>>', lambda _: self.invalidate())
        self.header_row = self.watched('1')
        self.profile_name = self.watched('Partner table mapping')
        self.profile_version = self.watched('1')
        self.source_version = self.watched()
        self.label_entry(mapping_form, 2, 'Header row', self.header_row)
        self.label_entry(mapping_form, 3, 'Source format / export version', self.source_version)
        self.label_entry(mapping_form, 4, 'Mapping profile name', self.profile_name)
        self.label_entry(mapping_form, 5, 'Mapping profile version', self.profile_version)
        ttk.Button(p, text='Read columns', command=self.read_columns).pack(anchor='w', pady=8)
        self.mapping_frame = ttk.Frame(p)
        self.mapping_frame.pack(fill='x')
        footer = ttk.Frame(self.import_tab, padding=(30, 16))
        footer.pack(side='bottom', fill='x', before=self.import_tab.canvas)
        ttk.Label(footer, text='DRAFT  ·  Not yet sent to SciSure', style='Small.TLabel').pack(side='left')
        ttk.Button(footer, text='Start over', command=self.new_submission).pack(side='right', padx=(10, 0))
        self.review_button = ttk.Button(footer, text='Review submission  →', style='Primary.TButton', command=self.preview)
        self.review_button.pack(side='right')
        self.review_button.state(['disabled'])

    def rebuild_context(self):
        old = {k: v.get() for k, v in self.context_vars.items()}
        for child in self.context_frame.winfo_children():
            child.destroy()
        self.context_vars = {}
        self.context_sections = ttk.Notebook(self.context_frame, style='Workspace.TNotebook')
        self.context_sections.pack(fill='x', pady=(10, 0))
        groups = {}
        for name in ('Sample & run', 'Measurement', 'Model & links' if self.modality.get() == 'computational' else 'Synthesis & lineage', 'Labs & handoff', 'Notes'):
            frame = ttk.Frame(self.context_sections, padding=(12, 14))
            self.context_sections.add(frame, text=name)
            frame.columnconfigure(0, weight=1, uniform='field')
            frame.columnconfigure(1, weight=1, uniform='field')
            groups[name] = [frame, 0]
        section_navigation(self.context_frame, self.context_sections, [(frame, name) for name, (frame, _) in groups.items()]).pack(fill='x', before=self.context_sections)
        labs = {'submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab', 'modelCreatedLab', 'custodyFromLab'}
        choices = {**{key: LAB_CHOICES for key in labs}, 'materialKind': MATERIAL_KINDS, 'modelRelation': MODEL_RELATIONS}
        for row, (key, label) in enumerate((COMMON_CONTEXT | trace_context(self.modality.get()) | MODALITY_CONTEXT[self.modality.get()]).items()):
            default = self.entity.get() if key in labs - {'custodyFromLab'} else ''
            var = self.watched(old.get(key, default))
            self.context_vars[key] = var
            if key in ('processingVersion', 'identityNote'): group = 'Notes'
            elif key in ('submittingLab', 'acquisitionLab', 'processingLab', 'custodyFromLab', 'custodySampleId', 'custodyRecord', 'receivedAt'): group = 'Labs & handoff'
            elif key in ('methodId', 'methodVersion') or key in MODALITY_CONTEXT[self.modality.get()]: group = 'Measurement'
            elif key in ('batchId', 'localBatchId', 'originLab', 'sampleCreatedLab', 'materialKind', 'parentSampleId', 'materialState'): group = 'Sample & run'
            elif key in PHYSICAL_CONTEXT: group = 'Synthesis & lineage'
            elif key in COMPUTATIONAL_CONTEXT: group = 'Model & links'
            else: group = 'Sample & run'
            parent, index = groups[group]
            field = ttk.Frame(parent, padding=(0, 0, 12, 10))
            field.grid(row=index // 2, column=index % 2, sticky='nsew')
            groups[group][1] += 1
            ttk.Label(field, text=label, wraplength=250, style='Muted.TLabel').pack(anchor='w', pady=(0, 5))
            if key in choices:
                ttk.Combobox(field, textvariable=var, values=choices[key], state='readonly', width=22).pack(fill='x')
            else:
                ttk.Entry(field, textvariable=var, width=22).pack(fill='x')
        self.install_wheel_handlers(self.context_frame)

    def generate_dataset_id(self):
        self.context_vars['datasetId'].set(new_id(self.context_vars['acquisitionLab'].get(), 'DS'))

    def generate_identity(self, kind):
        c = self.context_vars
        if kind == 'model':
            if self.modality.get() != 'computational':
                messagebox.showinfo('CATALYST', 'Choose computational as the modality before creating a model.', parent=self.root)
                return
            c['specimenId'].set(new_id(c['modelCreatedLab'].get(), 'MDL'))
            c['modelRelation'].set('no physical link')
        else:
            if self.modality.get() == 'computational':
                messagebox.showinfo('CATALYST', 'Physical batch IDs belong to synthesis or measurement submissions.', parent=self.root)
                return
            origin = c['originLab'].get()
            c['batchId'].set(new_id(origin, 'BAT'))
            c['synthesisExecutionId'].set(new_id(origin, 'SYN'))
            c['sampleCreatedLab'].set(origin)
            c['specimenId'].set(new_id(origin, 'SMP'))
            c['materialKind'].set('batch material')
            c['parentSampleId'].set('')
        self.generate_dataset_id()
        self.status.set('New IDs assigned to this draft. Keep these IDs on container labels and in SciSure; publication registers them.')

    def choose_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title='Select original and companion files',
            filetypes=[('All data and images', '*.*'), ('Tables', '*.csv *.xlsx *.json'),
                ('Images', '*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.svg')])
        if not paths:
            return
        if len(paths) > 6:
            messagebox.showerror('CATALYST', 'Select no more than six files.', parent=self.root)
            return
        def work():
            return load_sources(paths)
        def done(sources):
            self.raw_only.set(all(s.artifact['format'] == 'binary' for s in sources))
            if self.raw_only.get():
                self.toolkit.set(False)
            self.set_sources(sources)
        self.run('Reading source files into memory…', work, done)

    def set_sources(self, sources):
        self.sources = sources
        self.review_button.state(['!disabled'] if sources else ['disabled'])
        self.file_summary.set(f'{len(sources)} file{"s" if len(sources) != 1 else ""} · {sum(len(s.content) for s in sources) / 1024 / 1024:.1f} / 40 MiB' if sources else 'Up to 6 files · 20 MiB each · 40 MiB total')
        self.render_file_list()
        self.source_choice.configure(values=[s.name for s in sources])
        if sources:
            self.source_choice.current(next((i for i, s in enumerate(sources) if s.artifact['format'] != 'binary'), 0))
            if not self.title.get():
                self.title.set(Path(sources[0].name).stem)
        else:
            self.source_choice.set('')
        self.source_changed()
        if sources and self.pending_profile:
            profile = self.pending_profile
            source = sources[self.source_choice.current()]
            if (lab_id(profile['entity']), profile['modality'], profile['source_format']) == (lab_id(self.entity.get()), self.modality.get(), source.artifact['format']):
                self.sheet_choice.set(profile['sheet'])
                self.read_columns(profile)
        self.invalidate()
        if self.raw_only.get() or self.toolkit.get(): self.mapping_section.set_open(False)
        self.status.set('Source bytes are in memory. No extra research files were written to disk.')

    def render_file_list(self):
        for child in self.file_list.winfo_children(): child.destroy()
        for source in self.sources:
            ttk.Separator(self.file_list).pack(fill='x', pady=(14, 10))
            row = ttk.Frame(self.file_list)
            row.pack(fill='x')
            extension = Path(source.name).suffix.lstrip('.').upper()[:7] or 'FILE'
            ttk.Label(row, text=extension, style='Badge.TLabel').pack(side='left', padx=(0, 12))
            ttk.Label(row, text=f'{len(source.content) / 1024:,.1f} KB', style='Small.TLabel').pack(side='right')
            ttk.Label(row, text=source.name, style='Muted.TLabel', wraplength=420).pack(side='left', fill='x', expand=True)
        self.install_wheel_handlers(self.file_list)

    def add_supporting_files(self):
        if self.busy: return
        paths = filedialog.askopenfilenames(parent=self.root, title='Preserve images, native instrument files, structures, logs or method records',
            filetypes=[('All originals — preserve bytes without parsing', '*.*'),
                ('Images', '*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.svg')])
        if not paths: return
        current = tuple(self.sources)
        if len(current) + len(paths) > 6:
            messagebox.showerror('CATALYST', 'Select no more than six files in total.', parent=self.root)
            return
        selected = self.source_choice.get()
        sheet = self.sheet_choice.get()
        draft = {name: (target.get(), unit.get(), aliases.get()) for name, target, unit, aliases in self.rules}
        def work():
            return load_sources(paths, current, parse_tables=False)
        def done(sources):
            if all(s.artifact['format'] == 'binary' for s in sources):
                self.toolkit.set(False)
                self.raw_only.set(True)
            self.set_sources(sources)
            if draft and selected in [s.name for s in sources]:
                self.source_choice.set(selected)
                self.source_changed()
                self.sheet_choice.set(sheet)
                self.read_columns({'rules': [dict(source=name, target=values[0], unit=values[1]) for name, values in draft.items()]})
                for name, _, _, aliases in self.rules:
                    if name in draft: aliases.set(draft[name][2])
        self.run('Reading supporting files into memory without parsing or execution…', work, done)

    def source_changed(self):
        self.invalidate()
        index = self.source_choice.current()
        names = list(self.sources[index].artifact['sheets']) if 0 <= index < len(self.sources) else []
        self.sheet_choice.configure(values=names)
        if names:
            self.sheet_choice.current(0)
        else:
            self.sheet_choice.set('')
        self.rules = []
        for child in self.mapping_frame.winfo_children():
            child.destroy()

    def read_columns(self, profile=None):
        try:
            self.mapping_section.set_open(True)
            index = self.source_choice.current()
            if index < 0:
                raise InputError('Select source files first.')
            headers, _ = table(self.sources[index], self.sheet_choice.get(), int(self.header_row.get()))
            for child in self.mapping_frame.winfo_children():
                child.destroy()
            self.rules = []
            known = {r['source']: r for r in profile['rules']} if profile else {}
            for row, name in enumerate(headers, 1):
                saved = known.get(name, {})
                target = self.watched(saved.get('target', 'Ignore'))
                unit = self.watched(saved.get('unit', ''))
                aliases = self.watched(json.dumps(saved.get('aliases', {})) if saved.get('aliases') else '')
                field = ttk.Frame(self.mapping_frame, padding=(0, 12))
                field.pack(fill='x')
                ttk.Label(field, text=name, style='Sub.TLabel', wraplength=500).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
                ttk.Label(field, text='Canonical field', style='Small.TLabel').grid(row=1, column=0, sticky='w', pady=(0, 5))
                ttk.Label(field, text='Source unit', style='Small.TLabel').grid(row=1, column=1, sticky='w', pady=(0, 5))
                target_combo = ttk.Combobox(field, textvariable=target, values=['Ignore'] + list(FIELDS), state='readonly', width=22)
                target_combo.grid(row=2, column=0, sticky='ew', padx=(0, 12))
                units = ttk.Combobox(field, textvariable=unit, values=FIELDS.get(target.get(), ('', []))[1], state='readonly', width=18)
                units.grid(row=2, column=1, sticky='ew')
                def changed(_, target=target, unit=unit, units=units):
                    values = FIELDS.get(target.get(), ('', []))[1]
                    units.configure(values=values)
                    unit.set(values[0] if len(values) == 1 else '')
                target_combo.bind('<<ComboboxSelected>>', changed)
                ttk.Label(field, text='Exact name aliases · JSON, optional', style='Small.TLabel').grid(row=3, column=0, columnspan=2, sticky='w', pady=(10, 5))
                ttk.Entry(field, textvariable=aliases, width=25).grid(row=4, column=0, columnspan=2, sticky='ew')
                field.columnconfigure(0, weight=1, uniform='mapping')
                field.columnconfigure(1, weight=1, uniform='mapping')
                ttk.Separator(self.mapping_frame).pack(fill='x')
                self.rules.append((name, target, unit, aliases))
            self.invalidate()
            self.install_wheel_handlers(self.mapping_frame)
        except (InputError, ValueError):
            messagebox.showerror('CATALYST', 'Choose a valid source worksheet and header row with unique text column names.', parent=self.root)

    def current_profile(self):
        if self.toolkit.get() or self.raw_only.get():
            return None
        index = self.source_choice.current()
        if index < 0:
            raise InputError('Select source files first.')
        rules = []
        for source, target, unit, aliases in self.rules:
            if target.get() == 'Ignore':
                continue
            try: names = strict_loads(aliases.get(), max_bytes=1024 * 1024) if aliases.get().strip() else {}
            except ValueError: raise InputError('Name aliases must be JSON such as {"CO2": "carbon dioxide"}.') from None
            rules.append(dict(source=source, target=target.get(), unit=unit.get(), aliases=names))
        try:
            return make_profile(self.entity.get(), self.modality.get(), self.sources[index].artifact['format'],
                self.source_version.get(), self.profile_name.get(), int(self.profile_version.get()),
                self.sheet_choice.get(), int(self.header_row.get()), rules)
        except ValueError as e:
            raise InputError(str(e)) from None

    def preview(self):
        if self.busy:
            return
        try:
            profile = self.current_profile()
            sources = tuple(self.sources)
            context = {k: v.get() for k, v in self.context_vars.items()}
            entity, modality, title = self.entity.get(), self.modality.get(), self.title.get()
            index, toolkit, raw_only = self.source_choice.current(), self.toolkit.get(), self.raw_only.get()
            parent = self.revision.value()['id'] if self.revision else self.parent
        except InputError as e:
            messagebox.showerror('CATALYST', str(e), parent=self.root)
            return
        def work():
            preview = build_preview(sources, entity, modality, context, profile, index, toolkit, raw_only)
            return Revision.create(preview, title, parent)
        def done(revision):
            self.revision = revision
            self.approval = None
            self.dirty = False
            self.acknowledge.set(False)
            self.render_review(revision)
            self.tabs.select(self.review_tab)
        self.run('Building the standardized review…', work, done)

    def text_panel(self, parent, height=10):
        frame = ttk.Frame(parent)
        widget = tk.Text(frame, height=height, wrap='none', font=('Menlo' if sys.platform == 'darwin' else 'Consolas', 10),
            background='white', foreground='#183047', relief='flat', borderwidth=0, padx=14, pady=12)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=widget.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=widget.xview)
        widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set, state='disabled')
        widget.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, widget

    def show_text(self, widget, value):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', value)
        widget.configure(state='disabled')

    def build_review(self):
        p = self.review_tab
        page_heading(p, 'Review submission', 'Inspect the evidence, resolve warnings, then approve the exact revision.', 'CHECK  /  APPROVE  /  PUBLISH')
        self.review_empty = Card(p, padding=36)
        self.review_empty.pack(fill='both', expand=True)
        empty = ttk.Frame(self.review_empty.body)
        empty.place(relx=.5, rely=.44, anchor='center')
        self.review_icon = icon(self.root, 'review', GREEN, 52)
        ttk.Label(empty, image=self.review_icon).pack(pady=(0, 20))
        ttk.Label(empty, text='A clear view of your data', style='Sub.TLabel').pack()
        ttk.Label(empty, text='Create a preview to inspect standardized values,\nscientific context and sample lineage together.', style='Muted.TLabel', justify='center').pack(pady=(12, 22))
        ttk.Button(empty, text='Prepare a submission  →', style='Primary.TButton', command=lambda: self.tabs.select(self.import_tab)).pack()
        self.review_content = Card(p, padding=18)
        p = self.review_content.body
        self.review_heading = tk.StringVar(value='Build a preview to review the standardized data.')
        ttk.Label(p, textvariable=self.review_heading, style='Sub.TLabel', wraplength=1000).pack(anchor='w')
        detail_tabs = ttk.Notebook(p)
        self.review_details = detail_tabs
        detail_tabs.pack(fill='both', expand=True, pady=8)
        self.data_table = ttk.Treeview(detail_tabs, show='headings', height=8)
        data_frame = ttk.Frame(detail_tabs)
        self.data_table.destroy()
        self.data_table = ttk.Treeview(data_frame, show='headings')
        self.data_table.grid(row=0, column=0, sticky='nsew')
        sy = ttk.Scrollbar(data_frame, orient='vertical', command=self.data_table.yview)
        sx = ttk.Scrollbar(data_frame, orient='horizontal', command=self.data_table.xview)
        self.data_table.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.grid(row=0, column=1, sticky='ns'); sx.grid(row=1, column=0, sticky='ew')
        data_frame.columnconfigure(0, weight=1); data_frame.rowconfigure(0, weight=1)
        detail_tabs.add(data_frame, text='Standardized values')
        issues_frame, self.issue_text = self.text_panel(detail_tabs)
        detail_tabs.add(issues_frame, text='Validation')
        trace_frame, self.trace_text = self.text_panel(detail_tabs)
        detail_tabs.add(trace_frame, text='Sample lineage')
        json_frame, self.preview_text = self.text_panel(detail_tabs)
        detail_tabs.add(json_frame, text='Provenance')
        footer = ttk.Frame(p)
        footer.pack(side='bottom', fill='x', before=detail_tabs)
        p = footer
        form = ttk.Frame(p)
        form.pack(fill='x', pady=8)
        self.reviewer = tk.StringVar()
        self.review_note = tk.StringVar()
        self.label_entry(form, 0, 'Reviewer name (self-reported)', self.reviewer)
        self.label_entry(form, 1, 'Review note / warnings', self.review_note)
        self.acknowledge = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text='I reviewed the data, context, mappings and all warnings.', variable=self.acknowledge).pack(anchor='w')
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Approve this revision', style='Primary.TButton', command=self.approve).pack(side='left')
        ttk.Button(actions, text='Send / check transfer to SciSure', command=self.publish).pack(side='left', padx=8)
        self.approval_status = tk.StringVar(value='Not approved.')
        ttk.Label(p, textvariable=self.approval_status, wraplength=1000).pack(anchor='w')

    def render_review(self, revision):
        self.review_empty.pack_forget()
        self.review_content.pack(fill='both', expand=True)
        payload = revision.value()
        preview = payload['preview']
        self.show_text(self.trace_text, json.dumps(preview.get('traceability', {
            'legacy_review': 'This older review has no structured consortium identity. Supply identity context before republishing.'}), ensure_ascii=False, indent=2))
        issues = preview['validation']['issues']
        errors = sum(i['severity'] == 'error' for i in issues)
        self.review_details.select(1 if errors else 0)
        rows = preview['standardized'].get('rows', [])
        mode = 'FILES + CONTEXT ONLY' if preview.get('data_status') == 'original_files_only' else f'{len(rows)} rows'
        self.review_heading.set(f'{payload["title"]} · {mode} · {errors} blocking errors · revision {payload["id"][:8]}')
        lines = [f'{i["severity"].upper()} · {i["code"]}\n{i["message"]}\n' for i in issues]
        self.show_text(self.issue_text, '\n'.join(lines) or 'No validation issues.')
        # Full payload remains immutable in memory; cap rendering to keep the UI responsive.
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        self.show_text(self.preview_text, text[:500000] + ('\n[Display truncated; the full revision is retained.]' if len(text) > 500000 else ''))
        self.data_table.delete(*self.data_table.get_children())
        flat = []
        for row in rows[:1000]:
            values = {k: v for k, v in row.items() if k != 'quantities'}
            for q in row.get('quantities', []):
                values[q['field'] + ' [' + q['unit'] + ']'] = q['value_decimal']
            flat.append(values)
        if preview.get('data_status') == 'original_files_only':
            flat = [dict(original_file=a['filename'], bytes=a['size_bytes'], sha256=a['sha256'],
                handling='Original bytes; no scientific interpretation') for a in preview['artifacts']]
        self.review_details.tab(0, text='Original files' if preview.get('data_status') == 'original_files_only' else 'Standardized values')
        columns = list(dict.fromkeys(k for row in flat for k in row))
        self.data_table.configure(columns=[str(i) for i in range(len(columns))])
        for i, col in enumerate(columns):
            self.data_table.heading(str(i), text=col)
            self.data_table.column(str(i), width=155, minwidth=80, stretch=False)
        for row in flat:
            self.data_table.insert('', 'end', values=[str(row.get(c, '')) if row.get(c) is not None else '—' for c in columns])
        self.approval_status.set('Resolve blocking errors before approval.' if errors else 'Ready for review. Approval applies only to this immutable revision.')
        self.status.set(f'Preview ready. {len(flat)} original files with context; no standardized data rows.'
            if preview.get('data_status') == 'original_files_only'
            else f'Preview ready. Showing up to 1,000 rows; all {len(rows)} rows remain in the revision.')

    def approve(self):
        try:
            if self.busy or not self.revision or self.dirty:
                raise InputError('Build a fresh preview after changing files, context, or mapping.')
            self.approval = self.revision.approve(self.reviewer.get(), self.review_note.get(), self.acknowledge.get())
            self.approval_status.set(f'Approved by {self.approval["reviewer"]} · SHA-256 {self.revision.sha256[:16]}…')
            self.status.set('Revision approved locally. It has not been sent to SciSure.')
        except InputError as e:
            messagebox.showerror('CATALYST', str(e), parent=self.root)

    def publish(self):
        if self.busy:
            return
        if not self.revision or not self.approval or self.dirty:
            messagebox.showinfo('CATALYST', 'Approve the current revision first.', parent=self.root)
            return
        if not self.publisher or not self.destination:
            self.tabs.select(self.connection_tab)
            messagebox.showinfo('CATALYST', 'Connect and verify the destination first.', parent=self.root)
            return
        if not messagebox.askyesno('Send approved revision to SciSure',
            f'Send {len(self.sources)} original files and the approved review to:\n\n'
            f'{self.destination["experiment_name"]}\nExperiment {self.destination["experiment_id"]} · Group {self.destination["group_id"]}\n'
            f'{self.destination["tenant"]}\n\nExisting transfer steps are checked before new writes.', parent=self.root):
            return
        publisher, revision, approval, sources = self.publisher, self.revision, dict(self.approval), tuple(self.sources)
        def done(receipt):
            self.status.set(f'Transfer verified. SciSure experiment {receipt["destination"]["experiment_id"]}, section {receipt["section_id"]}.')
            self.approval_status.set('Published: original file checksums and completion receipt verified in SciSure.')
        self.run('Starting direct SciSure transfer…', lambda: publisher.publish(revision, approval, sources,
            lambda message: self.messages.put(('progress', message))), done)

    def build_catalog(self):
        p = self.catalog_tab
        page_heading(p, 'Samples & models', 'Follow a material across laboratories, measurements and revisions.', 'CONSORTIUM  /  SAMPLE LINEAGE')
        card = Card(p)
        card.pack(fill='both', expand=True)
        p = card.body
        ttk.Label(p, text='The shared identity catalog', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Search by sample ID, lab or local label. Use an existing material or create a linked derivative.', style='Muted.TLabel', wraplength=550).pack(anchor='w', pady=(8, 18))
        actions = ttk.Frame(p)
        actions.pack(fill='x')
        ttk.Button(actions, text='Refresh catalog', style='Primary.TButton', command=self.refresh_catalog).pack(side='left')
        self.catalog_query = tk.StringVar()
        ttk.Entry(actions, textvariable=self.catalog_query, width=24).pack(side='left', padx=8)
        ttk.Button(actions, text='Search', command=self.filter_catalog).pack(side='left')
        frame = ttk.Frame(p)
        frame.pack(fill='both', expand=True, pady=10)
        columns = ('id', 'origin', 'creator', 'batch', 'aliases', 'datasets')
        self.catalog_table = ttk.Treeview(frame, columns=columns, show='headings', height=10)
        for key, label in zip(columns, ('Canonical sample / model ID', 'Synthesis / model origin', 'Sample creator', 'Batch ID', 'Lab-specific labels', 'Datasets')):
            self.catalog_table.heading(key, text=label)
            self.catalog_table.column(key, width=240 if key in ('id', 'batch', 'aliases') else 140, stretch=False)
        self.catalog_table.grid(row=0, column=0, sticky='nsew')
        sy = ttk.Scrollbar(frame, orient='vertical', command=self.catalog_table.yview)
        sx = ttk.Scrollbar(frame, orient='horizontal', command=self.catalog_table.xview)
        self.catalog_table.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.grid(row=0, column=1, sticky='ns')
        sx.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        footer = ttk.Frame(p)
        footer.pack(side='bottom', fill='x', before=frame)
        p = footer
        buttons = ttk.Frame(p)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Use sample / model', command=lambda: self.use_catalog_subject(False)).pack(side='left')
        ttk.Button(buttons, text='Create derivative', command=lambda: self.use_catalog_subject(True)).pack(side='left', padx=8)
        ttk.Button(buttons, text='Repeat synthesis', command=self.repeat_catalog_procedure).pack(side='left')
        self.catalog_status = tk.StringVar(value='Connect first, then refresh. No catalog is cached on disk.')
        ttk.Label(p, textvariable=self.catalog_status, wraplength=550, style='Muted.TLabel').pack(anchor='w', pady=10)

    def refresh_catalog(self):
        if not self.client or self.group_id is None:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect to SciSure before reading the shared catalog.')
            return
        client, group = self.client, self.group_id
        def done(catalog):
            self.catalog = catalog
            self.filter_catalog()
            self.catalog_status.set(f'{len(catalog["entries"])} completed reviews scanned; '
                f'{len(catalog["pending"])} incomplete transfers excluded from selection; '
                f'{len(catalog.get("orphan_sections", []))} sections have no review manifest; '
                f'{catalog["legacy_reviews"]} older reviews need identity registration. Scope: active group {group}.')
        self.run('Reading the shared identity catalog from SciSure…', lambda: load_catalog(client, group,
            lambda message: self.messages.put(('progress', message))), done)

    def filter_catalog(self):
        self.catalog_rows = search_catalog(self.catalog, self.catalog_query.get()) if self.catalog else []
        self.catalog_table.delete(*self.catalog_table.get_children())
        for index, row in enumerate(self.catalog_rows):
            s = row['subject']
            self.catalog_table.insert('', 'end', iid=str(index), values=(s['id'], lab_name(row['origin_lab']),
                lab_name(s['creator_lab']), s.get('batch_id', 'Computational model'), ', '.join(sorted(row['aliases'])), len(row['datasets'])))

    def use_catalog_subject(self, derived=False):
        selection = self.catalog_table.selection()
        if self.busy or not selection:
            return
        row = self.catalog_rows[int(selection[0])]
        entry, subject = row['entry'], row['subject']
        computational = 'model' in entry['trace']
        if derived and computational:
            self.status.set('A computational model is not a physical parent sample.')
            return
        if computational:
            self.modality.set('computational')
        elif self.modality.get() in ('computational', 'synthesis'):
            self.modality.set('XRD')
        self.rebuild_context()
        fields = COMPUTATIONAL_CONTEXT if computational else PHYSICAL_CONTEXT
        for key in fields:
            if not key.startswith('custody') and key != 'receivedAt':
                self.context_vars[key].set(entry['context'].get(key, ''))
        c = self.context_vars
        c['specimenId'].set(subject['id'])
        for key in ('localSampleId', 'runId', 'acquiredAt', 'acquiredBy', 'methodId', 'methodVersion', 'processingVersion'):
            c[key].set('')
        for key in MODALITY_CONTEXT[self.modality.get()]:
            c[key].set('')
        for key in ('custodyFromLab', 'custodySampleId', 'custodyRecord', 'receivedAt'):
            if key in c: c[key].set('')
        c['acquisitionLab'].set(lab_name(self.entity.get()))
        c['processingLab'].set(lab_name(self.entity.get()))
        if derived:
            c['sampleCreatedLab'].set(lab_name(self.entity.get()))
            c['specimenId'].set(new_id(self.entity.get(), 'SMP'))
            c['parentSampleId'].set(subject['id'])
            c['materialKind'].set('aliquot')
            c['materialState'].set('')
            if lab_id(self.entity.get()) != subject['creator_lab']:
                c['custodySampleId'].set(subject['id'])
        self.generate_dataset_id()
        self.revision = self.approval = self.parent = None
        self.invalidate()
        self.tabs.select(self.import_tab)
        self.status.set('Identity reused for a new dataset. Enter the actual acquisition lab, method, local labels, measurement context, and handoff evidence.')

    def repeat_catalog_procedure(self):
        selection = self.catalog_table.selection()
        if self.busy or not selection:
            return
        entry = self.catalog_rows[int(selection[0])]['entry']
        if 'batch' not in entry['trace']:
            self.status.set('Choose a physical sample to repeat its synthesis procedure.')
            return
        self.modality.set('synthesis')
        self.rebuild_context()
        c = self.context_vars
        for variable in c.values(): variable.set('')
        for key in ('submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab'):
            c[key].set(lab_name(self.entity.get()))
        procedure = entry['trace']['batch']['procedure']
        for key, value in (('procedureId', procedure['id']), ('procedureVersion', procedure['version']),
                ('procedureReference', procedure['reference'])):
            c[key].set(value)
        self.generate_identity('batch')
        self.revision = self.approval = self.parent = None
        self.invalidate()
        self.context_section.set_open(True)
        self.tabs.select(self.import_tab)
        self.status.set('Shared procedure copied into a new lab-specific synthesis draft. '
            'Enter this execution’s actual record, date, batch label, deviations, state, and scale.')

    def build_history(self):
        p = self.history_tab
        page_heading(p, 'Saved records', 'Return to approved reviews and original files stored in SciSure.', 'LIBRARY  /  SCISURE')
        card = Card(p, padding=18)
        card.pack(fill='both', expand=True)
        self.library_sections = ttk.Notebook(card.body)
        self.library_sections.pack(fill='both', expand=True)
        self.library_reviews = ttk.Frame(self.library_sections, padding=(12, 18))
        self.library_files = ttk.Frame(self.library_sections, padding=(12, 18))
        self.library_detail, self.remote_text = self.text_panel(self.library_sections)
        self.library_sections.add(self.library_reviews, text='Saved reviews')
        self.library_sections.add(self.library_files, text='Original files')
        self.library_sections.add(self.library_detail, text='Record details')
        p = self.library_reviews
        ttk.Label(p, text='Reviews from the selected experiment', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Connect to SciSure and choose an experiment to explore its records.', style='Muted.TLabel', wraplength=520).pack(anchor='w', pady=(8, 16))
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=(0, 12))
        ttk.Button(actions, text='List saved reviews', style='Primary.TButton', command=self.load_history).pack(side='left')
        ttk.Button(actions, text='Open review', command=lambda: self.open_history(False)).pack(side='left', padx=8)
        more = ttk.Menubutton(actions, text='More actions ▾')
        menu = tk.Menu(more, tearoff=False)
        menu.add_command(label='Load review + originals', command=lambda: self.open_history(True))
        menu.add_command(label='Download review package…', command=self.export_review)
        menu.add_command(label='Reuse mapping / revise', command=self.revise_loaded)
        more.configure(menu=menu)
        more.pack(side='left')
        self.history_table = self.scrolling_table(p, ('id', 'section', 'status'))
        for key, name, width in [('id', 'Revision', 250), ('section', 'SciSure section', 140), ('status', 'Transfer status', 170)]:
            self.history_table.heading(key, text=name)
            self.history_table.column(key, width=width, minwidth=80, stretch=False)
        p = self.library_files
        ttk.Label(p, text='Original experiment attachments', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Preview files or download a copy to a folder you choose.', style='Muted.TLabel').pack(anchor='w', pady=(8, 16))
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=(0, 16))
        ttk.Button(actions, text='Browse attachments', style='Primary.TButton', command=self.browse_files).pack(side='left')
        ttk.Button(actions, text='Read selected file', command=self.read_file).pack(side='left', padx=8)
        ttk.Button(actions, text='Download…', command=self.export_file).pack(side='left')
        self.file_table = self.scrolling_table(p, ('name', 'section', 'id', 'parent', 'stored', 'size'))
        for key, name, width in [('name', 'File', 240), ('section', 'Section', 180), ('id', 'File ID', 85),
                ('parent', 'Previous file ID', 115), ('stored', 'Stored', 165), ('size', 'Bytes', 85)]:
            self.file_table.heading(key, text=name)
            self.file_table.column(key, width=width, minwidth=60, stretch=False)
        self.show_text(self.remote_text, 'Open a saved review or read a file to see its details here.')
        self.loaded_review = None

    def scrolling_table(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        table = ttk.Treeview(frame, columns=columns, show='headings', height=6)
        table.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return table

    def get_read_target(self):
        index = self.experiment.current()
        if not self.client or index < 0 or index >= len(self.experiments):
            messagebox.showinfo('CATALYST', 'Connect and select an experiment first.', parent=self.root)
            self.tabs.select(self.connection_tab)
            return None
        return self.client, self.experiments[index]['experimentID'], self.group_id

    def load_history(self):
        target = self.get_read_target()
        if not target: return
        client, eid, gid = target
        def work():
            destination = client.destination(eid, gid, writable=False)
            return destination, history(client, destination)
        def done(result):
            self.history_destination, self.history_rows = result
            self.history_table.delete(*self.history_table.get_children())
            for i, row in enumerate(self.history_rows):
                self.history_table.insert('', 'end', iid=str(i), values=(row['revision_id'], row['section_id'], row['status']))
            self.status.set(f'{len(self.history_rows)} CATALYST review records found. Select one to read.')
        self.run('Reading CATALYST history from SciSure…', work, done)

    def open_history(self, with_sources):
        selection = self.history_table.selection()
        if not selection or not self.client:
            return
        row = self.history_rows[int(selection[0])]
        client, destination = self.client, dict(self.history_destination)
        def done(loaded):
            self.loaded_review = loaded
            self.library_sections.select(self.library_detail)
            self.show_text(self.remote_text, json.dumps(loaded['revision'].value(), indent=2, ensure_ascii=False)[:500000])
            self.status.set('Review loaded into memory. ' + loaded['integrity'] + '.')
        self.run('Reading the saved review' + (' and original files…' if with_sources else '…'),
            lambda: read_review(client, destination, row['section_id'], with_sources), done)

    def revise_loaded(self):
        if not self.loaded_review or self.busy:
            return
        loaded = self.loaded_review
        payload = loaded['revision'].value()
        preview = payload['preview']
        if self.sources and not messagebox.askyesno('Replace current working review', 'Replace the current in-memory working files and context with this saved review? Unsaved changes will be discarded.', parent=self.root):
            return
        self.new_submission(ask=False)
        self.parent = payload['id']
        self.title.set(payload['title'])
        self.entity.set(lab_name(preview['entity']))
        self.modality.set(preview['modality'])
        self.rebuild_context()
        for key, value in preview['context'].items():
            if key in self.context_vars:
                self.context_vars[key].set(value)
        self.toolkit.set('toolkit_source_review' in preview)
        self.raw_only.set(preview.get('data_status') == 'original_files_only')
        if loaded['sources']:
            self.set_sources(loaded['sources'])
        else:
            self.status.set('Context loaded. Select source files or load the review with originals before creating a revision.')
        profile = preview['normalization'].get('profile', {})
        if profile.get('format') == 'catalyst-mapping/1':
            self.pending_profile = profile
            self.profile_name.set(profile['name'])
            self.profile_version.set(str(profile['version']))
            self.source_version.set(profile['source_version'])
            self.header_row.set(str(profile['header_row']))
            sha = preview['normalization']['source_artifact_sha256']
            for index, source in enumerate(self.sources):
                if source.artifact['sha256'] == sha:
                    self.source_choice.current(index)
                    self.source_changed()
                    self.sheet_choice.set(profile['sheet'])
                    self.read_columns(profile)
        self.tabs.select(self.import_tab)
        self.invalidate()

    def browse_files(self):
        target = self.get_read_target()
        if not target: return
        client, eid, gid = target
        def work():
            destination = client.destination(eid, gid, writable=False)
            result = []
            for section in client.list(f'/api/v1/experiments/{eid}/sections'):
                if section.get('deleted') or section.get('sectionType') not in ('FILES', 'FILE', 'CUSTOM'):
                    continue
                sid = remote_id(section['expJournalID'])
                for file in client.list(f'/api/v1/experiments/sections/{sid}/files'):
                    result.append(dict(file, section_id=sid, section_name=section.get('sectionHeader') or str(sid)))
                    if len(result) > 1000:
                        raise SciSureError('More than 1,000 attachments are visible. Choose a narrower experiment; no partial file list will be shown.')
            client.verify_destination(destination, writable=False)
            return destination, sorted(result, key=lambda f: remote_id(f['experimentFileID']), reverse=True)
        def done(result):
            self.files_destination, self.remote_files = result
            self.file_table.delete(*self.file_table.get_children())
            for i, f in enumerate(self.remote_files):
                self.file_table.insert('', 'end', iid=str(i), values=(f.get('realName'), f['section_name'],
                    f['experimentFileID'], f.get('parentExperimentFileID') or '—', f.get('stored') or '—', f.get('fileSize')))
            self.status.set(f'{len(self.remote_files)} attachments listed, newest file IDs first. Same-name Office revisions retain separate IDs. Embedded notebook images use separate SciSure endpoints.')
        self.run('Listing experiment files…', work, done)

    def attachment_bytes(self, client, destination, file):
        client.verify_destination(destination, writable=False)
        sid = remote_id(file['section_id'])
        sections = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
        if len([s for s in sections if remote_id(s.get('expJournalID')) == sid and not s.get('deleted')
                and s.get('sectionType') in ('FILE', 'FILES', 'CUSTOM')]) != 1:
            raise InputError('The selected attachment section changed or is missing. Refresh the file list.')
        base = f'/api/v1/experiments/sections/{sid}/files'
        fid = remote_id(file['experimentFileID'])
        current = [item for item in client.list(base) if remote_id(item.get('experimentFileID')) == fid]
        if len(current) != 1 or current[0].get('realName') != file.get('realName'):
            raise InputError('The selected attachment changed or is missing. Refresh the file list.')
        content = download(client, base, current[0], size=file.get('fileSize'), experiment=destination['experiment_id'])
        after = [item for item in client.list(base) if remote_id(item.get('experimentFileID')) == fid]
        if after != current:
            raise InputError('The attachment changed while downloading. Refresh the file list and try again.')
        sections_after = client.list(f'/api/v1/experiments/{destination["experiment_id"]}/sections')
        if len([s for s in sections_after if remote_id(s.get('expJournalID')) == sid and not s.get('deleted')
                and s.get('sectionType') in ('FILE', 'FILES', 'CUSTOM')]) != 1:
            raise InputError('The attachment section changed while downloading. Refresh the file list.')
        client.verify_destination(destination, writable=False)
        return content

    def export_file(self):
        selection = self.file_table.selection()
        if self.busy or not selection or not self.client: return
        file = dict(self.remote_files[int(selection[0])])
        client, destination = self.client, dict(self.files_destination)
        path = filedialog.asksaveasfilename(parent=self.root, title='Download SciSure attachment',
            initialfile=suggested_filename(file.get('realName')), filetypes=[('All files', '*.*')])
        if not path: return
        def work():
            content = self.attachment_bytes(client, destination, file)
            save_bytes(path, content)
            return len(content)
        self.run('Downloading the selected attachment…', work,
            lambda size: self.status.set(f'Downloaded {size:,} bytes to {path}'))

    def export_review(self):
        selection = self.history_table.selection()
        if self.busy or not selection or not self.client: return
        row = dict(self.history_rows[int(selection[0])])
        client, destination = self.client, dict(self.history_destination)
        path = filedialog.asksaveasfilename(parent=self.root, title='Download verified review package',
            initialfile=suggested_filename('CATALYST-' + row['revision_id'] + '.zip', 'CATALYST-review.zip'),
            defaultextension='.zip', filetypes=[('Review package', '*.zip')])
        if not path: return
        def work():
            loaded = read_review(client, destination, row['section_id'], True)
            save_bytes(path, review_archive(loaded))
        self.run('Downloading and verifying the review and all original files…', work,
            lambda _: self.status.set(f'Verified review, standardized data, and originals downloaded to {path}'))

    def read_file(self):
        selection = self.file_table.selection()
        if not selection or not self.client: return
        f = dict(self.remote_files[int(selection[0])])
        client, destination = self.client, dict(self.files_destination)
        def work():
            content = self.attachment_bytes(client, destination, f)
            name = str(f.get('realName', 'file'))
            if name.lower().endswith(('.csv', '.xlsx', '.json')):
                try:
                    source = Source.from_bytes(name, content)
                    return json.dumps(source.artifact, indent=2, ensure_ascii=False)[:500000]
                except InputError:
                    if name.lower().endswith('.json'):
                        try: return json.dumps(strict_loads(content), indent=2, ensure_ascii=False)[:500000]
                        except ValueError: raise InputError('This JSON file is malformed or exceeds the supported limits.') from None
                    raise
            return f'{name}\n{len(content):,} bytes received into memory. This file type has no desktop preview.'
        self.run('Reading the selected file from SciSure…', work,
            lambda text: (self.library_sections.select(self.library_detail), self.show_text(self.remote_text, text), self.status.set('File read into memory. No local copy was saved.')))

    def new_submission(self, ask=True):
        if self.busy: return
        if ask and self.sources and not messagebox.askyesno('New submission', 'Discard the current in-memory review and start a new submission? SciSure records and source files will remain unchanged.', parent=self.root):
            return
        self.revision = self.approval = self.parent = None
        self.pending_profile = None
        self.raw_only.set(False)
        self.toolkit.set(False)
        self.title.set('')
        for var in self.context_vars.values(): var.set('')
        for key in ('submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab', 'modelCreatedLab'):
            if key in self.context_vars: self.context_vars[key].set(lab_name(self.entity.get()))
        self.sources = []
        self.set_sources([])
        self.review_heading.set('Build a preview to review the standardized data.')
        self.review_content.pack_forget()
        self.review_empty.pack(fill='both', expand=True)
        self.context_section.set_open(False)
        self.mapping_section.set_open(False)
        self.data_table.delete(*self.data_table.get_children())
        self.show_text(self.issue_text, '')
        self.show_text(self.preview_text, '')
        self.show_text(self.trace_text, '')
        self.acknowledge.set(False)

    def close(self):
        if self.busy:
            messagebox.showinfo('CATALYST', 'Wait for the current operation to finish before closing. An interrupted transfer may need reconciliation.', parent=self.root)
            return
        if not self.smoke and self.sources and not messagebox.askyesno('Close CATALYST',
            'Close and discard the in-memory working review? Original source files and anything already sent to SciSure will remain unchanged.', parent=self.root):
            return
        self.client = None
        self.token.set('')
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main():
    import sys
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        app = Application(root, smoke=True)
        from catalyst_ingest.preview import PROFILE_ROOT
        from .credentials import system_store
        assert (PROFILE_ROOT / 'university-of-rochester/reactor/toolkit-gc-bundle-v1.json').is_file()
        assert system_store() is not None
        source = Source.from_bytes('synthetic.csv', b'mass,name\n72,001\n')
        app.set_sources([source])
        root.update_idletasks()
        assert app.source_choice.current() == 0
        app.close()
        return
    Application(root)
    root.mainloop()
