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
from .design import apply_theme, Card, Tooltip, page_heading, section_navigation, icon, PAPER, INK, MUTED, GREEN, SIDEBAR, LINE
from .guidance import field_help, required_context_keys
from .help import HelpPane
from .workflow_gui import WorkflowUI
from .runs_gui import RunUI
from .sample_workspace import SampleWorkspace
from .issues_gui import IssueUI
from .sample_documents_gui import SampleDocumentsUI
from .library import search_procedures, search_samples
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


class Application(IssueUI, SampleDocumentsUI, WorkflowUI, SampleWorkspace, RunUI):
    def __init__(self, root, smoke=False, legacy=False):
        self.root = root
        self.adaptive = not legacy
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
        toolbar = ttk.Frame(self.main, padding=(30, 12), style='Page.TFrame')
        toolbar.pack(fill='x')
        self.help_window = None
        ttk.Button(toolbar, text='Help · upload & access', command=self.show_help).pack(side='left')
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
        self.batch_tab = ttk.Frame(self.tabs, padding=(20, 18), style='Page.TFrame')
        self.navigation_buttons = {}
        self.nav_images = []
        for frame, label, graphic in [(self.catalog_tab, 'Samples & data', 'samples'), (self.import_tab, 'Add data', 'upload'), (self.review_tab, 'Review', 'review'),
                (self.history_tab, 'Saved records', 'library'),
                (self.batch_tab, 'Batch review', 'review'), (self.connection_tab, 'SciSure connection', 'connection')]:
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
        self.build_batch_page()
        if self.adaptive: self.tabs.select(self.catalog_tab)
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
        self.show_action_error('This action could not be completed. Your in-memory review is retained.')

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

    def show_help(self):
        if self.help_window is not None and self.help_window.winfo_exists():
            self.help_window.deiconify()
            self.help_window.lift()
            return
        window = self.help_window = tk.Toplevel(self.root)
        window.title('CATALYST · Upload & access guide')
        window.geometry('660x350')
        window.minsize(600, 340)
        window.protocol('WM_DELETE_WINDOW', window.withdraw)
        self.help_pane = HelpPane(window, self.help_navigate)
        self.help_pane.pack(fill='both', expand=True)
        self.help_pane.show_guide('access' if self.tabs.select() == str(self.history_tab) else 'upload')

    def help_navigate(self, page):
        pages = {'submission': self.import_tab, 'review': self.review_tab,
            'connection': self.connection_tab, 'records': self.history_tab, 'catalog': self.catalog_tab, 'batch': self.batch_tab}
        self.tabs.select(pages[page])

    def invalidate(self, *_):
        self.dirty = True
        self.approval = None
        if hasattr(self, 'approval_status'):
            self.approval_status.set('Changes require a new preview and approval.')

    def watched(self, value=''):
        var = tk.StringVar(value=value)
        var.trace_add('write', self.invalidate)
        return var

    def explain(self, widget, key):
        Tooltip(widget, lambda: field_help(key, self.modality.get() if hasattr(self, 'modality') else 'reactor'))
        return widget

    def label_entry(self, parent, row, label, variable, width=30, show=None, help_key=None):
        caption = ttk.Label(parent, text=label, wraplength=220)
        caption.grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
        widget = ttk.Entry(parent, textvariable=variable, width=width, show=show or '')
        widget.grid(row=row, column=1, sticky='ew', pady=5)
        parent.columnconfigure(1, weight=1)
        if help_key:
            self.explain(caption, help_key)
            self.explain(widget, help_key)
        return widget

    def run(self, description, work, done, failed=None):
        if self.busy:
            messagebox.showinfo('CATALYST', 'Wait for the current operation to finish.', parent=self.root)
            return
        self.busy = True
        self.locked_widgets = []
        self.locked_text_widgets = []
        def lock(parent):
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button, ttk.Menubutton)):
                    self.locked_widgets.append((child, child.state()))
                    child.state(['disabled'])
                elif isinstance(child, tk.Text):
                    self.locked_text_widgets.append((child, child.cget('state')))
                    child.configure(state='disabled')
                lock(child)
        lock(self.root)
        if hasattr(self, 'batch_panel'): self.batch_panel.set_busy(True)
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
                for widget, state in self.locked_text_widgets:
                    if widget.winfo_exists(): widget.configure(state=state)
                self.locked_text_widgets = []
                if hasattr(self, 'batch_panel'): self.batch_panel.set_busy(False)
                self.root.configure(cursor='')
                try:
                    result = future.result()
                    done(result)
                except (InputError, SciSureError, CredentialError) as e:
                    if failed: failed()
                    self.status.set(str(e))
                    self.show_action_error(str(e))
                except Exception:
                    if failed: failed()
                    self.status.set('The operation could not be completed. Your review remains in memory.')
                    self.show_action_error('The operation could not be completed. Your review remains in memory.')
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
        self.label_entry(fields, 0, '* SciSure server URL', self.tenant, help_key='tenant')
        self.tenant.trace_add('write', self.server_changed)
        self.label_entry(fields, 1, '* API token', self.token, show='•', help_key='token')
        self.remember = tk.BooleanVar(value=False)
        self.explain(ttk.Checkbutton(p, text='Remember in this computer’s operating system credential store', variable=self.remember), 'remember').pack(anchor='w')
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=12)
        ttk.Button(actions, text='Connect', style='Primary.TButton', command=self.connect).pack(side='left')
        ttk.Button(actions, text='Use saved token', command=self.use_saved_token).pack(side='left', padx=8)
        ttk.Button(actions, text='Forget & disconnect', command=self.forget).pack(side='left')
        self.connection_status = tk.StringVar(value='Not connected. You can prepare a review offline.')
        ttk.Label(p, textvariable=self.connection_status, wraplength=530, style='Muted.TLabel').pack(anchor='w', pady=12)
        ttk.Separator(p).pack(fill='x', pady=12)
        ttk.Label(p, text='Run / experiment', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Choose an existing run or create one for this work.', style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        run_actions = ttk.Frame(p)
        run_actions.pack(fill='x', pady=(10, 0))
        ttk.Button(run_actions, text='Find existing run…', command=lambda: self.open_run_picker('existing')).pack(side='left')
        ttk.Button(run_actions, text='New run…', command=lambda: self.open_run_picker('new')).pack(side='left', padx=10)
        self.experiment = self.explain(ttk.Combobox(p, state='readonly', width=90), 'experiment')
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
        self.label_entry(native_fields, 0, 'Existing SciSure sample ID (optional check)', self.inspect_sample, help_key='inspect_sample')
        self.label_entry(native_fields, 1, 'SciSure protocol version ID (optional check)', self.inspect_protocol, help_key='inspect_protocol')
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
            self.show_action_error(str(e), target='connection')
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
        self.reset_run_browser()
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
        self.reset_run_browser()
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
            if self.adaptive: self.refresh_catalog()
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
        self.reset_run_browser()
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
        if self.adaptive and hasattr(self, 'link_rows'):
            self.native_procedures = []
            self.refresh_link_options()
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
            self.accept_run_destination(destination)
        self.run('Verifying the destination…', lambda: client.destination(eid, gid), done)

    def build_import(self):
        p = self.import_tab.body
        page_heading(p, 'Add data', 'Choose a record type, link your samples and methods, then review and send.', '1  PREPARE  /  2  REVIEW  /  3  SEND')
        self.build_workflow_start(p)
        originals = self.originals_card = Card(p)
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
        self.explain(ttk.Button(top, text='Choose files', style='Primary.TButton', command=self.choose_files), 'files').pack(side='right', padx=(12, 0))
        ttk.Label(body, text='CSV  ·  XLSX  ·  JSON  ·  IMAGES  ·  NATIVE FILES', style='Small.TLabel').pack(anchor='w', pady=(18, 0))
        self.file_list = ttk.Frame(body)
        self.file_list.pack(fill='x')
        self.file_summary = tk.StringVar(value='Up to 6 files · 20 MiB each · 40 MiB total')
        options = ttk.Frame(body)
        options.pack(fill='x', pady=(14, 0))
        ttk.Label(options, textvariable=self.file_summary, style='Small.TLabel').pack(side='left')
        self.explain(ttk.Button(options, text='+ Supporting files', command=self.add_supporting_files), 'supporting_files').pack(side='right')
        details = Card(p)
        details.pack(fill='x', pady=(0, 18))
        body = details.body
        ttk.Label(body, text='Required information', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(body, text='* Required for this submission. Hover over a label or field for its meaning and purpose.\nKeyboard: focus a field to see its explanation; Escape dismisses it.',
            style='Muted.TLabel', wraplength=540).pack(anchor='w', pady=(6, 12))
        self.required_summary = tk.StringVar()
        ttk.Label(body, textvariable=self.required_summary, style='Badge.TLabel', wraplength=520).pack(fill='x')
        ttk.Button(body, text='Go to next missing field', command=self.focus_missing).pack(anchor='w', pady=(8, 16))
        self.submission_widgets = {}
        self.title = self.watched()
        self.entity = self.watched('Rochester')
        self.modality = self.watched('XRD' if self.adaptive else 'reactor')
        self.title_frame = ttk.Frame(body)
        self.title_frame.pack(fill='x')
        self.explain(ttk.Label(self.title_frame, text='Submission title · filled from file name', style='Muted.TLabel'), 'title').pack(anchor='w', pady=(0, 6))
        self.submission_widgets['title'] = self.explain(ttk.Entry(self.title_frame, textvariable=self.title), 'title')
        self.submission_widgets['title'].pack(fill='x', pady=(0, 16))
        selectors = self.selectors_frame = ttk.Frame(body)
        selectors.pack(fill='x')
        selectors.columnconfigure(0, weight=1, uniform='selector')
        selectors.columnconfigure(1, weight=1, uniform='selector')
        self.explain(ttk.Label(selectors, text='* Source laboratory', style='Muted.TLabel'), 'entity').grid(row=0, column=0, sticky='w', pady=(0, 6))
        self.submission_widgets['entity'] = self.explain(ttk.Combobox(selectors, textvariable=self.entity, values=LAB_CHOICES, state='readonly', width=20), 'entity')
        self.submission_widgets['entity'].grid(row=1, column=0, sticky='ew', padx=(0, 16))
        self.submission_widgets['entity'].bind('<<ComboboxSelected>>', lambda _: self.workflow_lab_changed())
        self.modality_label = self.explain(ttk.Label(selectors, text='* Data type', style='Muted.TLabel'), 'modality')
        self.modality_label.grid(row=0, column=1, sticky='w', pady=(0, 6))
        combo = self.submission_widgets['modality'] = self.explain(ttk.Combobox(selectors, textvariable=self.modality, values=[m for m in MODALITIES if m not in ('sample', 'procedure')], state='readonly', width=20), 'modality')
        combo.grid(row=1, column=1, sticky='ew')
        combo.bind('<<ComboboxSelected>>', lambda _: self.workflow_modality_changed() if self.adaptive else self.rebuild_context())
        self.toolkit = tk.BooleanVar(value=False)
        self.toolkit.trace_add('write', self.invalidate)
        self.raw_only = tk.BooleanVar(value=self.adaptive)
        self.raw_only.trace_add('write', self.invalidate)
        self.toolkit_widget = self.explain(ttk.Checkbutton(body, text='Use the Rochester GC toolkit mapping', variable=self.toolkit, command=self.prepare_table_sources), 'toolkit')
        self.toolkit_widget.pack(anchor='w', pady=(12, 0))
        self.raw_only_widget = self.explain(ttk.Checkbutton(body, text='Preserve original files · uncheck to convert a table', variable=self.raw_only, command=self.prepare_table_sources), 'raw_only')
        self.raw_only_widget.pack(anchor='w')
        self.context_separator = ttk.Separator(body)
        self.context_separator.pack(fill='x', pady=16)
        self.context_heading = ttk.Label(body, text='Sample & scientific context', style='Sub.TLabel')
        self.context_heading.pack(anchor='w')
        self.context_intro = ttk.Label(body, text='Use an existing sample to reuse its synthesis details, or create IDs for new material.', style='Muted.TLabel', wraplength=530)
        self.context_intro.pack(anchor='w', pady=(5, 10))
        identity_actions = ttk.Frame(body)
        identity_actions.pack(fill='x', pady=(0, 12))
        self.new_identity_button = ttk.Button(identity_actions, text='New batch + sample IDs', command=lambda: self.generate_identity('model' if self.modality.get() == 'computational' else 'batch'))
        self.new_identity_button.grid(row=0, column=0, sticky='w', padx=(0, 6), pady=3)
        ttk.Button(identity_actions, text='Use existing sample / model', command=lambda: self.tabs.select(self.catalog_tab)).grid(row=0, column=1, sticky='w', pady=3)
        self.explain(ttk.Button(identity_actions, text='New dataset ID', command=self.generate_dataset_id), 'datasetId').grid(row=1, column=0, sticky='w', pady=3)
        self.context_frame = ttk.Frame(body)
        self.context_frame.pack(fill='x')
        self.rebuild_context()
        self.mapping_section = ttk.Frame(body)
        self.mapping_section.pack(fill='x', pady=(0, 12))
        p = self.mapping_section
        ttk.Separator(p).pack(fill='x', pady=16)
        ttk.Label(p, text='Column mapping & versions', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(p, text='Required for table conversion. Read columns, then choose the meaning and original unit of each column you want to convert. Leave other columns as Ignore.', style='Muted.TLabel', wraplength=530).pack(anchor='w', pady=(5, 12))
        mapping_form = ttk.Frame(p)
        mapping_form.pack(fill='x')
        self.source_choice = self.explain(ttk.Combobox(mapping_form, state='readonly'), 'source_choice')
        self.source_choice.grid(row=0, column=1, sticky='ew', pady=4)
        self.explain(ttk.Label(mapping_form, text='* Table source file'), 'source_choice').grid(row=0, column=0, sticky='w')
        self.source_choice.bind('<<ComboboxSelected>>', lambda _: self.source_changed())
        self.sheet_choice = self.explain(ttk.Combobox(mapping_form, state='readonly'), 'sheet_choice')
        self.sheet_choice.grid(row=1, column=1, sticky='ew', pady=4)
        self.explain(ttk.Label(mapping_form, text='* Worksheet'), 'sheet_choice').grid(row=1, column=0, sticky='w')
        self.sheet_choice.bind('<<ComboboxSelected>>', lambda _: self.invalidate())
        self.header_row = self.watched('1')
        self.profile_name = self.watched('Partner table mapping')
        self.profile_version = self.watched('1')
        self.source_version = self.watched()
        self.mapping_widgets = {'source_choice': self.source_choice, 'sheet_choice': self.sheet_choice}
        for row, key, label in [(2, 'header_row', 'Header row'), (3, 'source_version', 'Source format / export version'),
                (4, 'profile_name', 'Mapping profile name'), (5, 'profile_version', 'Mapping profile version')]:
            self.mapping_widgets[key] = self.label_entry(mapping_form, row, '* ' + label, getattr(self, key), help_key=key)
        self.read_columns_button = ttk.Button(p, text='Read columns', command=self.read_columns)
        self.read_columns_button.pack(anchor='w', pady=8)
        self.mapping_frame = ttk.Frame(p)
        self.mapping_frame.pack(fill='x')
        self.mapping_note = ttk.Label(body, style='Muted.TLabel', wraplength=530)
        for variable in (self.title, self.entity, self.header_row, self.source_version, self.profile_name, self.profile_version):
            variable.trace_add('write', self.schedule_requirements)
        self.toolkit.trace_add('write', lambda *_: self.mode_changed('toolkit'))
        self.raw_only.trace_add('write', lambda *_: self.mode_changed('raw_only'))
        footer = ttk.Frame(self.import_tab, padding=(30, 16))
        footer.pack(side='bottom', fill='x', before=self.import_tab.canvas)
        ttk.Label(footer, text='DRAFT  ·  Not yet sent to SciSure', style='Small.TLabel').pack(side='left')
        ttk.Button(footer, text='Start over', command=self.new_submission).pack(side='right', padx=(10, 0))
        self.review_button = ttk.Button(footer, text='Review submission  →', style='Primary.TButton', command=self.preview)
        self.review_button.pack(side='right')
        self.review_button.state(['disabled'])
        self.refresh_requirements()

    def rebuild_context(self):
        if self.adaptive:
            self._workflow_layout_ready = False
            return self.rebuild_workflow_context()
        old = {k: v.get() for k, v in self.context_vars.items()}
        for child in self.context_frame.winfo_children():
            child.destroy()
        self.context_vars = {}
        self.context_widgets = {}
        self.context_fields = {}
        self.context_labels = {}
        self.context_groups = {}
        self.required_keys = None
        self.context_group_frames = {}
        for name in ('Sample & run', 'Measurement', 'Model & links' if self.modality.get() == 'computational' else 'Synthesis & lineage', 'Labs & handoff', 'Notes'):
            frame = ttk.Frame(self.context_frame)
            frame.columnconfigure(0, weight=1, uniform='field')
            frame.columnconfigure(1, weight=1, uniform='field')
            ttk.Label(frame, text=name, style='Sub.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(14, 12))
            self.context_group_frames[name] = frame
        self.optional_context = Disclosure(self.context_frame, 'Optional details · notes, links & handoff')
        self.optional_context.body.columnconfigure(0, weight=1, uniform='optional')
        self.optional_context.body.columnconfigure(1, weight=1, uniform='optional')
        labs = {'submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab', 'modelCreatedLab', 'custodyFromLab'}
        choices = {**{key: LAB_CHOICES for key in labs}, 'custodyFromLab': ('',) + LAB_CHOICES,
            'materialKind': MATERIAL_KINDS, 'modelRelation': MODEL_RELATIONS}
        for row, (key, label) in enumerate((COMMON_CONTEXT | trace_context(self.modality.get()) | MODALITY_CONTEXT[self.modality.get()]).items()):
            default = self.entity.get() if key in labs - {'custodyFromLab'} else ''
            var = self.watched(old.get(key, default))
            var.trace_add('write', self.schedule_requirements)
            self.context_vars[key] = var
            if key in ('processingVersion', 'identityNote'): group = 'Notes'
            elif key in ('submittingLab', 'acquisitionLab', 'processingLab', 'custodyFromLab', 'custodySampleId', 'custodyRecord', 'receivedAt'): group = 'Labs & handoff'
            elif key in ('methodId', 'methodVersion') or key in MODALITY_CONTEXT[self.modality.get()]: group = 'Measurement'
            elif key in ('batchId', 'localBatchId', 'originLab', 'sampleCreatedLab', 'materialKind', 'parentSampleId', 'materialState'): group = 'Sample & run'
            elif key in PHYSICAL_CONTEXT: group = 'Synthesis & lineage'
            elif key in COMPUTATIONAL_CONTEXT: group = 'Model & links'
            else: group = 'Sample & run'
            self.context_groups[key] = group
            # Keep one widget per field when a condition makes it required. Moving its
            # grid placement preserves text, keyboard focus and approval invalidation.
            field = self.context_fields[key] = ttk.Frame(self.context_frame, padding=(0, 0, 12, 10))
            caption = self.context_labels[key] = ttk.Label(field, wraplength=235, style='Muted.TLabel')
            caption.pack(anchor='w', pady=(0, 5))
            if key in choices:
                widget = ttk.Combobox(field, textvariable=var, values=choices[key], state='readonly', width=18)
            else:
                widget = ttk.Entry(field, textvariable=var, width=18)
            widget.pack(side='bottom', fill='x')
            widget.bind('<FocusIn>', lambda event: self.root.after_idle(lambda widget=event.widget: self.ensure_field_visible(widget)), add='+')
            self.context_widgets[key] = widget
            for target in (caption, widget):
                Tooltip(target, lambda key=key: ('Required for this submission. ' if key in (self.required_keys or ()) else 'Optional unless applicable. ') + field_help(key, self.modality.get()))
        self.new_identity_button.configure(text='New model ID' if self.modality.get() == 'computational' else 'New batch + sample IDs')
        self.refresh_requirements()
        self.install_wheel_handlers(self.context_frame)

    def schedule_requirements(self, *_):
        if getattr(self, '_requirements_pending', None) is None:
            self._requirements_pending = self.root.after_idle(self.refresh_requirements)

    def mode_changed(self, selected):
        if getattr(self, selected).get():
            other = self.raw_only if selected == 'toolkit' else self.toolkit
            if other.get(): other.set(False)
        self.schedule_requirements()

    def refresh_requirements(self):
        if self.adaptive: return self.refresh_workflow_requirements()
        pending = getattr(self, '_requirements_pending', None)
        if pending is not None:
            self.root.after_cancel(pending)
            self._requirements_pending = None
        context = {key: var.get() for key, var in self.context_vars.items()}
        required = required_context_keys(self.modality.get(), context, toolkit=self.toolkit.get() and not self.raw_only.get())
        if required != self.required_keys:
            focused = self.root.focus_get()
            self.required_keys = required
            labels = COMMON_CONTEXT | trace_context(self.modality.get()) | MODALITY_CONTEXT[self.modality.get()]
            counts = {group: 0 for group in self.context_group_frames}
            for field in self.context_fields.values(): field.grid_forget()
            for frame in self.context_group_frames.values(): frame.pack_forget()
            self.optional_context.pack_forget()
            optional_count = 0
            for key, field in self.context_fields.items():
                if key in required:
                    group = self.context_groups[key]
                    index = counts[group]
                    counts[group] += 1
                    parent = self.context_group_frames[group]
                    row = 1 + index // 2
                    label = '* ' + labels[key]
                else:
                    index = optional_count
                    optional_count += 1
                    parent = self.optional_context.body
                    row = index // 2
                    label = labels[key] + ' · Optional'
                self.context_labels[key].configure(text=label)
                field.grid(in_=parent, row=row, column=index % 2, sticky='nsew')
            for group, frame in self.context_group_frames.items():
                if counts[group]: frame.pack(fill='x')
            if optional_count: self.optional_context.pack(fill='x', pady=(14, 6))
            if focused in self.context_widgets.values():
                self.root.after_idle(lambda widget=focused: self.ensure_field_visible(widget))
        if not hasattr(self, 'mapping_section'): return
        mapping_needed = not (self.raw_only.get() or self.toolkit.get())
        if mapping_needed:
            self.mapping_note.pack_forget()
            self.mapping_section.pack(fill='x', pady=(0, 12))
        else:
            self.mapping_section.pack_forget()
            self.mapping_note.configure(text='Original files only: column mapping is not required. Sample and scientific context are still required.'
                if self.raw_only.get() else 'Rochester toolkit: the dedicated mapping is supplied automatically. Processing method / version evidence is required above.')
            self.mapping_note.pack(fill='x', pady=12)
        values = [(self.title.get(), self.submission_widgets['title'])]
        values.extend((getattr(self, key).get(), self.submission_widgets[key]) for key in ('entity', 'modality'))
        values.extend((context[key], self.context_widgets[key]) for key in self.context_vars if key in required)
        if mapping_needed:
            values.extend((getattr(self, key).get(), widget) for key, widget in self.mapping_widgets.items())
            values.extend((unit.get(), self.rule_widgets[name][1]) for name, target, unit, _ in self.rules
                if target.get() != 'Ignore')
        self.missing_widgets = [widget for value, widget in values if not value.strip()]
        remaining = len(self.missing_widgets)
        suffix = ' Add original files to begin.' if not self.sources else ''
        if mapping_needed and not any(target.get() != 'Ignore' for _, target, _, _ in self.rules):
            suffix += ' Read columns and map at least one field.'
        self.required_summary.set(f'{len(values) - remaining} / {len(values)} required fields filled · {remaining} remaining.' + suffix + '\nValues and units are checked during review.')

    def focus_missing(self):
        self.refresh_requirements()
        if self.missing_widgets:
            widget = self.missing_widgets[0]
        elif not (self.toolkit.get() or self.raw_only.get()) and not any(target.get() != 'Ignore' for _, target, _, _ in self.rules):
            widget = next(iter(self.rule_widgets.values()))[0] if self.rules else self.read_columns_button
        else:
            widget = self.review_button
        self.root.update_idletasks()
        if widget is not self.review_button:
            canvas = self.import_tab.canvas
            y = widget.winfo_rooty() - self.import_tab.body.winfo_rooty()
            canvas.yview_moveto(max(0, y - 70) / max(1, self.import_tab.body.winfo_height()))
        widget.focus_set()

    def ensure_field_visible(self, widget):
        # Conditional fields can move between containers. Settle the resulting
        # geometry and scroll region before measuring their new position.
        self.root.update_idletasks()
        if not widget.winfo_exists() or not widget.winfo_ismapped() or self.tabs.select() != str(self.import_tab): return
        canvas = self.import_tab.canvas
        y = widget.winfo_rooty() - self.import_tab.body.winfo_rooty()
        top, height = canvas.canvasy(0), canvas.winfo_height()
        if y < top + 12:
            target = y - 70
        elif y + widget.winfo_height() > top + height - 12:
            target = y + widget.winfo_height() - height + 24
        else:
            return
        canvas.yview_moveto(max(0, target) / max(1, self.import_tab.body.winfo_height()))

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
            self.show_action_error('Select no more than six files.', target='files')
            return
        parse_tables = not self.adaptive or not self.raw_only.get()
        def work():
            return load_sources(paths, parse_tables=parse_tables)
        def done(sources):
            if not self.adaptive or all(s.artifact['format'] == 'binary' for s in sources):
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
        self.refresh_requirements()
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
            self.show_action_error('Select no more than six files in total.', target='files')
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
        self.rule_widgets = {}
        for child in self.mapping_frame.winfo_children():
            child.destroy()
        self.schedule_requirements()

    def read_columns(self, profile=None):
        try:
            index = self.source_choice.current()
            if index < 0:
                raise InputError('Select source files first.')
            headers, _ = table(self.sources[index], self.sheet_choice.get(), int(self.header_row.get()))
            for child in self.mapping_frame.winfo_children():
                child.destroy()
            self.rules = []
            self.rule_widgets = {}
            known = {r['source']: r for r in profile['rules']} if profile else {}
            for row, name in enumerate(headers, 1):
                saved = known.get(name, {})
                target = self.watched(saved.get('target', 'Ignore'))
                unit = self.watched(saved.get('unit', ''))
                aliases = self.watched(json.dumps(saved.get('aliases', {})) if saved.get('aliases') else '')
                field = ttk.Frame(self.mapping_frame, padding=(0, 12))
                field.pack(fill='x')
                ttk.Label(field, text=name, style='Sub.TLabel', wraplength=500).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
                self.explain(ttk.Label(field, text='Column meaning · or Ignore', style='Small.TLabel'), 'mapping_target').grid(row=1, column=0, sticky='w', pady=(0, 5))
                self.explain(ttk.Label(field, text='* Source unit if mapped', style='Small.TLabel'), 'mapping_unit').grid(row=1, column=1, sticky='w', pady=(0, 5))
                target_combo = ttk.Combobox(field, textvariable=target, values=['Ignore'] + list(FIELDS), state='readonly', width=22)
                Tooltip(target_combo, lambda target=target: field_help('mapping_target') + '\n\n' + field_help(target.get()))
                target_combo.grid(row=2, column=0, sticky='ew', padx=(0, 12))
                units = ttk.Combobox(field, textvariable=unit, values=FIELDS.get(target.get(), ('', []))[1], state='readonly', width=18)
                self.explain(units, 'mapping_unit')
                units.grid(row=2, column=1, sticky='ew')
                def changed(_, target=target, unit=unit, units=units):
                    values = FIELDS.get(target.get(), ('', []))[1]
                    units.configure(values=values)
                    unit.set(values[0] if len(values) == 1 else '')
                target_combo.bind('<<ComboboxSelected>>', changed)
                self.explain(ttk.Label(field, text='Exact name aliases · JSON, optional', style='Small.TLabel'), 'mapping_aliases').grid(row=3, column=0, columnspan=2, sticky='w', pady=(10, 5))
                self.explain(ttk.Entry(field, textvariable=aliases, width=25), 'mapping_aliases').grid(row=4, column=0, columnspan=2, sticky='ew')
                field.columnconfigure(0, weight=1, uniform='mapping')
                field.columnconfigure(1, weight=1, uniform='mapping')
                ttk.Separator(self.mapping_frame).pack(fill='x')
                self.rules.append((name, target, unit, aliases))
                self.rule_widgets[name] = (target_combo, units)
                target.trace_add('write', self.schedule_requirements)
                unit.trace_add('write', self.schedule_requirements)
            self.invalidate()
            self.schedule_requirements()
            self.install_wheel_handlers(self.mapping_frame)
        except (InputError, ValueError):
            self.show_action_error('Choose a valid source worksheet and header row with unique text column names.', target='mapping')

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
            context = self.workflow_values() if self.adaptive else {k: v.get() for k, v in self.context_vars.items()}
            entity, modality, title = self.entity.get(), self.modality.get(), self.title.get()
            index, toolkit, raw_only = self.source_choice.current(), self.toolkit.get(), self.raw_only.get()
            parent = self.parent if self.adaptive else self.revision.value()['id'] if self.revision else self.parent
            catalog = self.available_library() if self.adaptive else None
            client, destination = self.client, dict(self.destination) if self.destination else None
        except InputError as e:
            self.stage_requested = False
            self.show_action_error(str(e), target='mapping')
            return
        def work():
            preview = build_preview(sources, entity, modality, context, profile, index, toolkit, raw_only)
            if context.get('workflowVersion') == '2': preview = self.prepare_inventory_preview(preview, catalog, client, destination)
            return Revision.create(preview, title, parent)
        def done(revision):
            self.revision = revision
            self.approval = None
            self.dirty = False
            self.acknowledge.set(False)
            self.render_review(revision)
            self.tabs.select(self.review_tab)
            if self.adaptive: self.workflow_preview_done(revision)
        self.run('Building the standardized review…', work, done, failed=lambda: setattr(self, 'stage_requested', False))

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
        heading = ttk.Frame(p, style='Page.TFrame')
        heading.pack(fill='x', pady=(0, 12))
        ttk.Label(heading, text='Review submission', style='Heading.TLabel').pack(anchor='w')
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
        ttk.Label(p, textvariable=self.review_heading, style='Sub.TLabel', wraplength=535).pack(anchor='w')
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
        issues_frame = self.build_issue_panel(detail_tabs)
        detail_tabs.add(issues_frame, text='Validation')
        trace_frame, self.trace_text = self.text_panel(detail_tabs)
        detail_tabs.add(trace_frame, text='Sample lineage')
        json_frame, self.preview_text = self.text_panel(detail_tabs)
        detail_tabs.add(json_frame, text='Provenance')
        summary_frame, self.record_summary_text = self.text_panel(detail_tabs)
        self.record_summary_text.configure(wrap='word', font=('Segoe UI', 10))
        self.record_summary_frame = summary_frame
        detail_tabs.add(summary_frame, text='Record summary')
        footer = ttk.Frame(p)
        footer.pack(side='bottom', fill='x', before=detail_tabs)
        p = footer
        form = ttk.Frame(p)
        form.pack(fill='x', pady=8)
        self.reviewer = tk.StringVar()
        self.review_note = tk.StringVar()
        for column, (label, variable, help_key) in enumerate((
                ('* Reviewer name (self-reported)', self.reviewer, 'reviewer'),
                ('* Review note / warnings', self.review_note, 'review_note'))):
            field = ttk.Frame(form)
            field.grid(row=0, column=column, sticky='ew', padx=(0, 12 if column == 0 else 0))
            form.columnconfigure(column, weight=1, uniform='review')
            self.explain(ttk.Label(field, text=label, wraplength=245), help_key).pack(anchor='w', pady=(0, 4))
            self.explain(ttk.Entry(field, textvariable=variable), help_key).pack(fill='x')
        self.acknowledge = tk.BooleanVar(value=False)
        self.explain(ttk.Checkbutton(p, text='* I reviewed the data, context, mappings and all warnings.', variable=self.acknowledge), 'acknowledge').pack(anchor='w')
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
        adaptive = preview.get('context', {}).get('workflowVersion') == '2'
        self.review_details.select(1 if errors else self.record_summary_frame if adaptive else 0)
        if adaptive:
            context = preview['context']
            kind = context['recordType']
            from .workflow import workflow_fields
            labels = workflow_fields(kind, preview['modality'])
            lines = [payload['title'], kind.capitalize() + ' · ' + preview['modality'], '']
            if preview.get('publication_destination'):
                lines.extend(['Run / experiment', preview['publication_destination']['experiment_name'], ''])
            if context.get('methodStatus') == 'not-recorded':
                lines.extend(['Method not recorded', 'Historical measurement: method information is incomplete.', ''])
            if preview.get('native_inventory_plan'):
                from .inventory import inventory_summary
                lines.extend(['LIMS inventory actions', inventory_summary(preview['native_inventory_plan']), ''])
            for key, label in labels.items():
                if context.get(key) and key not in ('datasetId', 'submittingLab', 'acquisitionLab', 'processingLab'):
                    lines.extend([label.replace(' (optional)', ''), str(context[key]), ''])
            for row in search_procedures(self.available_library()):
                procedure = row['procedure']
                if procedure['id'] == context.get('procedureId' if kind == 'synthesis' else 'methodId') and str(procedure['version']) == context.get('procedureVersion' if kind == 'synthesis' else 'methodVersion'):
                    lines.extend(['Linked method', row['label'], ''])
            for row in search_samples(self.available_library()):
                if row['subject']['id'] == context.get('specimenId'):
                    lines.extend(['Linked sample', row['label'], row['subject'].get('description', ''), ''])
            lines.extend(['Attached files'] + [a['filename'] + ' · ' + str(a['size_bytes']) + ' bytes' for a in preview['artifacts']])
            if not preview['artifacts']: lines.append('No files. The information above is saved as a reusable record.')
            self.show_text(self.record_summary_text, '\n'.join(lines))
        rows = preview['standardized'].get('rows', [])
        mode = 'METADATA RECORD' if preview.get('data_status') == 'metadata_only' else ('ORIGINAL FILES' if adaptive else 'FILES + CONTEXT ONLY') if preview.get('data_status') == 'original_files_only' else f'{len(rows)} rows'
        self.review_heading.set(f'{payload["title"]} · {mode} · {errors} blocking errors · revision {payload["id"][:8]}')
        self.render_issues(issues)
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
        if self.revision and any(issue['severity'] == 'error' for issue in self.revision.value()['preview']['validation']['issues']):
            self.review_details.select(1)
            self.approval_status.set('Select an issue and use its correction button before approving.')
            return
        try:
            if self.busy or not self.revision or self.dirty:
                raise InputError('Build a fresh preview after changing files, context, or mapping.')
            self.approval = self.revision.approve(self.reviewer.get(), self.review_note.get(), self.acknowledge.get())
            from .contracts import approved_payload
            approved_payload(self.revision, self.approval)
            if self.review_batch_id:
                self.batch_queue.approve(self.review_batch_id, self.approval)
                self.refresh_batch()
            self.approval_status.set(f'Approved by {self.approval["reviewer"]} · SHA-256 {self.revision.sha256[:16]}…')
            self.status.set('Revision approved locally. It has not been sent to SciSure.')
        except InputError as e:
            self.approval = None
            self.show_action_error(str(e), target='review' if self.revision and not self.dirty else None)

    def publish(self):
        if self.busy:
            return
        if self.review_batch_id:
            self.tabs.select(self.batch_tab)
            self.status.set('Use Send in Batch review to preserve dependency order and per-record transfer status.')
            return
        if not self.revision or not self.approval or self.dirty:
            messagebox.showinfo('CATALYST', 'Approve the current revision first.', parent=self.root)
            return
        if not self.publisher or not self.destination:
            self.tabs.select(self.connection_tab)
            messagebox.showinfo('CATALYST', 'Connect and verify the destination first.', parent=self.root)
            return
        reviewed_destination = self.revision.value()['preview'].get('publication_destination') or self.destination
        if not messagebox.askyesno('Send approved revision to SciSure',
            f'Send {len(self.sources)} original files and the approved review to:\n\n'
            f'{reviewed_destination["experiment_name"]}\nExperiment {reviewed_destination["experiment_id"]} · Group {reviewed_destination["group_id"]}\n'
            f'{reviewed_destination["tenant"]}\n\n' + ('The reviewed inventory actions will also register/reuse the sample and link it to this experiment.\n\n' if self.revision.value()['preview']['context'].get('inventoryMode') == 'native' else '') + 'Existing transfer steps are checked before new writes.', parent=self.root):
            return
        publisher, revision, approval, sources = self.publisher, self.revision, dict(self.approval), tuple(self.sources)
        def done(receipt):
            self.status.set(f'Transfer verified. SciSure experiment {receipt["destination"]["experiment_id"]}, section {receipt["section_id"]}.')
            self.approval_status.set('Published: original file checksums and completion receipt verified in SciSure.')
        self.run('Starting direct SciSure transfer…', lambda: self.publisher_for_revision(revision).publish(revision, approval, sources,
            lambda message: self.messages.put(('progress', message))), done)

    def build_catalog(self):
        self.build_sample_workspace()

    def refresh_catalog(self):
        self.refresh_sample_workspace()

    def filter_catalog(self):
        self.filter_sample_workspace()

    def use_catalog_subject(self, derived=False):
        if self.adaptive:
            selection = self.catalog_table.selection()
            if self.busy or not selection: return
            from .library import sample_context
            row = self.catalog_rows[int(selection[0])]
            if not row.get('entry'):
                if derived:
                    self.status.set('Register this existing inventory sample in CATALYST before creating a derivative. Add a measurement to prepare its reference.')
                else:
                    self.start_selected_sample('measurement')
                return
            if 'model' in row['entry']['trace']:
                self.status.set('This is a computational model. Open its saved review to revise or reuse its calculation details.')
                return
            if derived:
                self.change_record_type('sample')
                self.context_vars['parentSampleId'].set(row['subject']['id'])
            else:
                if self.record_type.get() not in ('measurement', 'synthesis'):
                    self.change_record_type('measurement')
                if self.record_type.get() not in ('measurement', 'synthesis'): return
                self.apply_sample_choice(row)
            self.tabs.select(self.import_tab)
            return
        selection = self.catalog_table.selection()
        if self.busy or not selection:
            return
        row = self.catalog_rows[int(selection[0])]
        if not row.get('entry'):
            self.status.set('Open Add data / procedure to use native inventory with the adaptive workflow.')
            return
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
        if self.adaptive:
            if self.busy: return
            selection = self.catalog_table.selection()
            selected_id = self.catalog_rows[int(selection[0])]['subject']['id'] if selection else None
            prior = set()
            for entry in (self.catalog or {}).get('entries', []):
                trace = entry['trace']
                if trace.get('material', {}).get('id') == selected_id and trace.get('batch', {}).get('procedure'):
                    p = trace['batch']['procedure']; prior.add((p['id'], p['version']))
                if trace.get('synthesis_execution', {}).get('sample_id') == selected_id:
                    p = trace['synthesis_execution']['procedure']; prior.add((p['id'], p['version']))
            self.change_record_type('synthesis')
            if self.record_type.get() != 'synthesis': return
            matches = [row for row in search_procedures(self.available_library(), kind='synthesis', modality='synthesis') if row['key'] in prior]
            if len(matches) == 1:
                from .library import procedure_context
                for key, value in procedure_context(matches[0]).items(): self.context_vars[key].set(str(value))
                self.refresh_link_options()
            self.status.set('Choose the registered product sample and a saved synthesis procedure. Record only this execution’s date, operator and changes.')
            return
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
        if preview.get('schema_version') == 'catalyst-desktop-review/3':
            self.load_workflow_draft(payload, loaded['sources'])
            return
        self.adaptive = False
        self.workflow_card.pack_forget()
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
        if self.adaptive:
            return self.change_record_type(self.record_type.get(), confirm=ask)
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
        self.optional_context.set_open(False)
        self.refresh_requirements()
        self.import_tab.canvas.yview_moveto(0)
        self.data_table.delete(*self.data_table.get_children())
        self.show_text(self.issue_text, '')
        self.show_text(self.preview_text, '')
        self.show_text(self.trace_text, '')
        self.acknowledge.set(False)

    def close(self):
        if self.busy:
            messagebox.showinfo('CATALYST', 'Wait for the current operation to finish before closing. An interrupted transfer may need reconciliation.', parent=self.root)
            return
        metadata_draft = self.adaptive and any(self.context_vars.get(key) and self.context_vars[key].get().strip()
            for key in ('sampleDescription', 'procedureName', 'procedureText', 'procedureReference', 'modelDescription', 'acquiredAt', 'acquiredBy'))
        if not self.smoke and (self.sources or self.batch_queue.items or metadata_draft) and not messagebox.askyesno('Close CATALYST',
            'Close and discard the in-memory draft and batch queue? Records already sent to SciSure remain there.', parent=self.root):
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
