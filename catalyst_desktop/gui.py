"""Native Tk desktop UI. All research state stays in memory until SciSure transfer."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__
from .model import (Source, Revision, InputError, MODALITIES, COMMON_CONTEXT, MODALITY_CONTEXT,
    FIELDS, build_preview, make_profile, table, check_sources)
from .scisure import SciSureClient, SciSureError, SANDBOX, remote_id
from .credentials import load_token, save_token, forget_token, CredentialError
from .publication import Publisher, history, read_review
from .traceability import (LAB_CHOICES, MATERIAL_KINDS, MODEL_RELATIONS, PHYSICAL_CONTEXT,
    COMPUTATIONAL_CONTEXT, lab_id, lab_name, new_id, trace_context)
from .catalog import load_catalog, search_catalog


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, background='#f6f8fb')
        bar = ttk.Scrollbar(self, orient='vertical', command=canvas.yview)
        self.body = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=self.body, anchor='nw')
        canvas.configure(yscrollcommand=bar.set)
        self.body.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')


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
        self.root.title(f'CATALYST {__version__} — Local review · SciSure storage')
        self.root.geometry('1160x820')
        self.root.minsize(850, 620)
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f6f8fb')
        style.configure('TLabel', background='#f6f8fb', foreground='#183047')
        style.configure('Heading.TLabel', font=('Segoe UI', 20, 'bold'))
        style.configure('Sub.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('TButton', padding=(12, 7))
        style.configure('Treeview', rowheight=26)
        style.configure('TNotebook.Tab', padding=(18, 9))
        self.status = tk.StringVar(value='Ready. Files stay on this computer until you choose Send to SciSure.')
        header = ttk.Frame(root, padding=(20, 16))
        header.pack(fill='x')
        ttk.Label(header, text='CATALYST', style='Heading.TLabel').pack(side='left')
        ttk.Label(header, text='  Catalysis data · Review locally, keep the record in SciSure').pack(side='left', padx=16)
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill='both', expand=True, padx=16)
        self.connection_tab = ttk.Frame(self.tabs, padding=20)
        self.import_tab = ScrollFrame(self.tabs)
        self.review_tab = ttk.Frame(self.tabs, padding=14)
        self.history_tab = ttk.Frame(self.tabs, padding=14)
        self.catalog_tab = ttk.Frame(self.tabs, padding=14)
        for frame, label in [(self.import_tab, '1  Files & mapping'), (self.review_tab, '2  Review & approve'),
                (self.connection_tab, '3  SciSure connection'), (self.history_tab, '4  Read from SciSure'),
                (self.catalog_tab, '5  Samples & models')]:
            self.tabs.add(frame, text=label)
        self.build_connection()
        self.build_import()
        self.build_review()
        self.build_history()
        self.build_catalog()
        ttk.Label(root, textvariable=self.status, padding=(20, 12), wraplength=1050).pack(fill='x')
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.report_callback_exception = self.callback_error
        self.root.after(100, self.poll)

    def callback_error(self, *_):
        messagebox.showerror('CATALYST', 'This action could not be completed. Your in-memory review is retained.', parent=self.root)

    def invalidate(self, *_):
        self.dirty = True
        self.approval = None
        if hasattr(self, 'approval_status'):
            self.approval_status.set('Changes require a new preview and approval.')

    def watched(self, value=''):
        var = tk.StringVar(value=value)
        var.trace_add('write', self.invalidate)
        return var

    def label_entry(self, parent, row, label, variable, width=50, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
        widget = ttk.Entry(parent, textvariable=variable, width=width, show=show or '')
        widget.grid(row=row, column=1, sticky='ew', pady=5)
        parent.columnconfigure(1, weight=1)
        return widget

    def run(self, description, work, done):
        if self.busy:
            messagebox.showinfo('CATALYST', 'Wait for the current operation to finish.', parent=self.root)
            return
        self.busy = True
        self.locked_widgets = []
        def lock(parent):
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button)):
                    self.locked_widgets.append((child, child.state()))
                    child.state(['disabled'])
                lock(child)
        lock(self.root)
        self.status.set(description)
        self.root.configure(cursor='watch')
        future = self.executor.submit(work)
        future.add_done_callback(lambda f: self.messages.put(('result', f, done)))

    def poll(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == 'progress':
                    self.status.set(message[1])
                    continue
                _, future, done = message
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
                    self.status.set(str(e))
                    messagebox.showerror('CATALYST — action paused', str(e), parent=self.root)
                except Exception:
                    self.status.set('The operation could not be completed. Your review remains in memory.')
                    messagebox.showerror('CATALYST', 'The operation could not be completed. No raw error or credential was logged.', parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def build_connection(self):
        p = self.connection_tab
        ttk.Label(p, text='Connect directly to SciSure', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(p, text='The token is sent only to the verified SciSure sandbox. No hosted proxy is used.', wraplength=900).pack(anchor='w', pady=8)
        fields = ttk.Frame(p)
        fields.pack(fill='x', pady=12)
        self.tenant = tk.StringVar(value=SANDBOX)
        self.token = tk.StringVar()
        tenant_widget = self.label_entry(fields, 0, 'SciSure tenant', self.tenant)
        tenant_widget.configure(state='readonly')
        self.label_entry(fields, 1, 'API token', self.token, show='•')
        self.remember = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text='Remember in this computer’s operating system credential store', variable=self.remember).pack(anchor='w')
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=12)
        ttk.Button(actions, text='Connect / refresh experiments', command=self.connect).pack(side='left')
        ttk.Button(actions, text='Use saved token', command=self.use_saved_token).pack(side='left', padx=8)
        ttk.Button(actions, text='Forget token & disconnect', command=self.forget).pack(side='left')
        self.connection_status = tk.StringVar(value='Not connected. You can prepare a review offline.')
        ttk.Label(p, textvariable=self.connection_status, wraplength=950).pack(anchor='w', pady=12)
        ttk.Separator(p).pack(fill='x', pady=12)
        ttk.Label(p, text='Choose the experiment that will receive your files', style='Sub.TLabel').pack(anchor='w')
        self.experiment = ttk.Combobox(p, state='readonly', width=90)
        self.experiment.pack(fill='x', pady=10)
        self.experiment.bind('<<ComboboxSelected>>', lambda _: self.clear_destination())
        ttk.Button(p, text='Verify selected destination', command=self.verify_destination).pack(anchor='w')
        self.destination_status = tk.StringVar(value='No destination verified.')
        ttk.Label(p, textvariable=self.destination_status, wraplength=950).pack(anchor='w', pady=10)
        ttk.Label(p, text='Shared tokens use the token account’s permissions and SciSure identity. Reviewer names in CATALYST are self-reported.', wraplength=900).pack(anchor='w', pady=12)

    def connect(self):
        if self.busy:
            return
        token = self.token.get()
        remember = self.remember.get()
        self.clear_destination()
        self.client = None
        self.experiments = []
        self.experiment.configure(values=[])
        self.experiment.set('')
        def work():
            client = SciSureClient(token)
            connection = client.check_connection()
            warning = None
            if remember:
                try: save_token(SANDBOX, token)
                except CredentialError as e: warning = str(e)
            return client, connection, warning
        def done(result):
            self.client, connection, warning = result
            self.group_id = connection['group_id']
            self.experiments = connection['experiments']
            self.experiment.configure(values=[f'{e["experimentID"]} · {e.get("name", "Unnamed")} · {e.get("signatureStatus", "Unknown status")}' for e in self.experiments])
            self.token.set('')
            self.connection_status.set(f'Connected to {connection["group_name"]} (group {self.group_id}). {len(self.experiments)} experiments available.')
            self.status.set(warning or 'Connection verified. Select an experiment and verify it before sending data.')
        self.run('Checking the SciSure connection…', work, done)

    def use_saved_token(self):
        def done(token):
            if not token:
                self.status.set('No saved token was found. Enter a token to connect.')
                return
            self.token.set(token)
            self.status.set('Saved token loaded. Click Connect to check SciSure.')
        self.run('Opening the system credential store…', lambda: load_token(SANDBOX), done)

    def forget(self):
        if self.busy:
            return
        self.token.set('')
        self.client = None
        self.clear_destination()
        self.connection_status.set('Disconnected.')
        self.run('Removing the saved token…', lambda: forget_token(SANDBOX),
            lambda _: self.status.set('Saved token removed. Research files were not changed.'))

    def clear_destination(self):
        self.destination = None
        self.publisher = None
        self.history_rows = []
        self.remote_files = []
        self.loaded_review = None
        self.catalog = None
        self.catalog_rows = []
        if hasattr(self, 'catalog_table'):
            self.catalog_table.delete(*self.catalog_table.get_children())
        if hasattr(self, 'history_table'):
            self.history_table.delete(*self.history_table.get_children())
            self.file_table.delete(*self.file_table.get_children())
        if hasattr(self, 'destination_status'):
            self.destination_status.set('No destination verified.')

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
            self.publisher = Publisher(client, destination)
            self.destination_status.set(f'Verified: {destination["experiment_name"]} · experiment {eid} · group {gid}')
            self.status.set('Destination verified. Sending still requires an approved revision and a transfer confirmation.')
        self.run('Verifying the destination…', lambda: client.destination(eid, gid), done)

    def build_import(self):
        p = self.import_tab.body
        ttk.Label(p, text='Start with the original files', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(p, text='CSV, XLSX, or flat-record JSON · Up to 6 files · 20 MiB each / 40 MiB combined', wraplength=950).pack(anchor='w', pady=7)
        actions = ttk.Frame(p)
        actions.pack(fill='x')
        ttk.Button(actions, text='Select files…', command=self.choose_files).pack(side='left')
        ttk.Button(actions, text='New submission', command=self.new_submission).pack(side='left', padx=8)
        self.file_summary = tk.StringVar(value='No files selected.')
        ttk.Label(p, textvariable=self.file_summary, wraplength=950).pack(anchor='w', pady=8)
        form = ttk.Frame(p)
        form.pack(fill='x', pady=8)
        self.title = self.watched()
        self.entity = self.watched('Rochester')
        self.modality = self.watched('reactor')
        self.label_entry(form, 0, 'Submission title', self.title)
        ttk.Label(form, text='Data format / source lab').grid(row=1, column=0, sticky='w', pady=6)
        ttk.Combobox(form, textvariable=self.entity, values=LAB_CHOICES, state='readonly').grid(row=1, column=1, sticky='ew')
        ttk.Label(form, text='Data modality').grid(row=2, column=0, sticky='w', pady=6)
        combo = ttk.Combobox(form, textvariable=self.modality, values=MODALITIES, state='readonly')
        combo.grid(row=2, column=1, sticky='ew')
        combo.bind('<<ComboboxSelected>>', lambda _: self.rebuild_context())
        self.toolkit = tk.BooleanVar(value=False)
        self.toolkit.trace_add('write', self.invalidate)
        ttk.Checkbutton(p, text='Import a Rochester toolkit RWGS bundle (original XLSX + analysis XLSX + summary CSV + flows CSV)',
            variable=self.toolkit).pack(anchor='w', pady=8)
        ttk.Label(p, text='Scientific context', style='Sub.TLabel').pack(anchor='w', pady=(12, 4))
        ttk.Label(p, text='For existing materials, start in Samples & models to reuse the lab, batch, and procedure identity. '
            'Local labels are kept separately from canonical IDs.', wraplength=950).pack(anchor='w', pady=5)
        identity_actions = ttk.Frame(p)
        identity_actions.pack(fill='x', pady=5)
        ttk.Button(identity_actions, text='New batch + sample IDs', command=lambda: self.generate_identity('batch')).pack(side='left')
        ttk.Button(identity_actions, text='New model ID', command=lambda: self.generate_identity('model')).pack(side='left', padx=6)
        ttk.Button(identity_actions, text='New dataset ID', command=self.generate_dataset_id).pack(side='left')
        self.context_frame = ttk.Frame(p)
        self.context_frame.pack(fill='x')
        self.rebuild_context()
        ttk.Separator(p).pack(fill='x', pady=15)
        ttk.Label(p, text='Versioned field mapping', style='Sub.TLabel').pack(anchor='w')
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
        ttk.Button(p, text='Build review preview', command=self.preview).pack(anchor='e', pady=16)

    def rebuild_context(self):
        old = {k: v.get() for k, v in self.context_vars.items()}
        for child in self.context_frame.winfo_children():
            child.destroy()
        self.context_vars = {}
        labs = {'submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab', 'modelCreatedLab', 'custodyFromLab'}
        choices = {**{key: LAB_CHOICES for key in labs}, 'materialKind': MATERIAL_KINDS, 'modelRelation': MODEL_RELATIONS}
        for row, (key, label) in enumerate((COMMON_CONTEXT | trace_context(self.modality.get()) | MODALITY_CONTEXT[self.modality.get()]).items()):
            default = self.entity.get() if key in labs - {'custodyFromLab'} else ''
            var = self.watched(old.get(key, default))
            self.context_vars[key] = var
            if key in choices:
                ttk.Label(self.context_frame, text=label).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 12))
                ttk.Combobox(self.context_frame, textvariable=var, values=choices[key], state='readonly').grid(row=row, column=1, sticky='ew', pady=5)
            else:
                self.label_entry(self.context_frame, row, label, var)

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
            filetypes=[('Supported data', '*.csv *.xlsx *.json'), ('All files', '*.*')])
        if not paths:
            return
        if len(paths) > 6:
            messagebox.showerror('CATALYST', 'Select no more than six files.', parent=self.root)
            return
        def work():
            sources = [Source.from_path(p) for p in paths]
            check_sources(sources)
            return sources
        self.run('Reading source files into memory…', work, self.set_sources)

    def set_sources(self, sources):
        self.sources = sources
        self.file_summary.set('\n'.join(f'{s.name}  ·  {len(s.content):,} bytes  ·  SHA-256 {s.artifact["sha256"][:12]}…' for s in sources))
        self.source_choice.configure(values=[s.name for s in sources])
        if sources:
            self.source_choice.current(0)
            if not self.title.get():
                self.title.set(Path(sources[0].name).stem)
        else:
            self.source_choice.set('')
        self.source_changed()
        if sources and self.pending_profile:
            profile = self.pending_profile
            if (lab_id(profile['entity']), profile['modality'], profile['source_format']) == (lab_id(self.entity.get()), self.modality.get(), sources[0].artifact['format']):
                self.sheet_choice.set(profile['sheet'])
                self.read_columns(profile)
        self.invalidate()
        self.status.set('Source bytes are in memory. No extra research files were written to disk.')

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
            index = self.source_choice.current()
            if index < 0:
                raise InputError('Select source files first.')
            headers, _ = table(self.sources[index], self.sheet_choice.get(), int(self.header_row.get()))
            for child in self.mapping_frame.winfo_children():
                child.destroy()
            self.rules = []
            for col, heading in enumerate(('Source field', 'Canonical field', 'Source unit', 'Exact aliases (JSON, optional)')):
                ttk.Label(self.mapping_frame, text=heading, style='Sub.TLabel').grid(row=0, column=col, sticky='w', padx=4)
            known = {r['source']: r for r in profile['rules']} if profile else {}
            for row, name in enumerate(headers, 1):
                saved = known.get(name, {})
                target = self.watched(saved.get('target', 'Ignore'))
                unit = self.watched(saved.get('unit', ''))
                aliases = self.watched(json.dumps(saved.get('aliases', {})) if saved.get('aliases') else '')
                ttk.Label(self.mapping_frame, text=name, wraplength=210).grid(row=row, column=0, sticky='w', padx=4, pady=4)
                target_combo = ttk.Combobox(self.mapping_frame, textvariable=target, values=['Ignore'] + list(FIELDS), state='readonly', width=25)
                target_combo.grid(row=row, column=1, sticky='ew', padx=4)
                units = ttk.Combobox(self.mapping_frame, textvariable=unit, values=FIELDS.get(target.get(), ('', []))[1], state='readonly', width=17)
                units.grid(row=row, column=2, sticky='ew', padx=4)
                def changed(_, target=target, unit=unit, units=units):
                    values = FIELDS.get(target.get(), ('', []))[1]
                    units.configure(values=values)
                    unit.set(values[0] if len(values) == 1 else '')
                target_combo.bind('<<ComboboxSelected>>', changed)
                ttk.Entry(self.mapping_frame, textvariable=aliases, width=30).grid(row=row, column=3, sticky='ew', padx=4)
                self.rules.append((name, target, unit, aliases))
            self.invalidate()
        except (InputError, ValueError):
            messagebox.showerror('CATALYST', 'Choose a valid source worksheet and header row with unique text column names.', parent=self.root)

    def current_profile(self):
        if self.toolkit.get():
            return None
        index = self.source_choice.current()
        if index < 0:
            raise InputError('Select source files first.')
        rules = []
        for source, target, unit, aliases in self.rules:
            if target.get() == 'Ignore':
                continue
            try: names = json.loads(aliases.get()) if aliases.get().strip() else {}
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
            index, toolkit = self.source_choice.current(), self.toolkit.get()
            parent = self.revision.value()['id'] if self.revision else self.parent
        except InputError as e:
            messagebox.showerror('CATALYST', str(e), parent=self.root)
            return
        def work():
            preview = build_preview(sources, entity, modality, context, profile, index, toolkit)
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
        widget = tk.Text(frame, height=height, wrap='none', font=('Consolas', 10), background='white', foreground='#183047')
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
        self.review_heading = tk.StringVar(value='Build a preview to review the standardized data.')
        ttk.Label(p, textvariable=self.review_heading, style='Sub.TLabel', wraplength=1000).pack(anchor='w')
        detail_tabs = ttk.Notebook(p)
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
        detail_tabs.add(issues_frame, text='Validation & warnings')
        trace_frame, self.trace_text = self.text_panel(detail_tabs)
        detail_tabs.add(trace_frame, text='Lab & sample lineage')
        json_frame, self.preview_text = self.text_panel(detail_tabs)
        detail_tabs.add(json_frame, text='Full revision & provenance')
        form = ttk.Frame(p)
        form.pack(fill='x', pady=8)
        self.reviewer = tk.StringVar()
        self.review_note = tk.StringVar()
        self.label_entry(form, 0, 'Reviewer name (self-reported)', self.reviewer)
        self.label_entry(form, 1, 'Review note / warning acknowledgments', self.review_note)
        self.acknowledge = tk.BooleanVar(value=False)
        ttk.Checkbutton(p, text='I reviewed the exact revision, source mapping, scientific context, and all warnings.', variable=self.acknowledge).pack(anchor='w')
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='Approve this revision', command=self.approve).pack(side='left')
        ttk.Button(actions, text='Send / check transfer to SciSure', command=self.publish).pack(side='left', padx=8)
        self.approval_status = tk.StringVar(value='Not approved.')
        ttk.Label(p, textvariable=self.approval_status, wraplength=1000).pack(anchor='w')

    def render_review(self, revision):
        payload = revision.value()
        preview = payload['preview']
        self.show_text(self.trace_text, json.dumps(preview.get('traceability', {
            'legacy_review': 'This older review has no structured consortium identity. Supply identity context before republishing.'}), ensure_ascii=False, indent=2))
        issues = preview['validation']['issues']
        errors = sum(i['severity'] == 'error' for i in issues)
        rows = preview['standardized'].get('rows', [])
        self.review_heading.set(f'{payload["title"]} · {len(rows)} rows · {errors} blocking errors · revision {payload["id"][:8]}')
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
        columns = list(dict.fromkeys(k for row in flat for k in row))
        self.data_table.configure(columns=[str(i) for i in range(len(columns))])
        for i, col in enumerate(columns):
            self.data_table.heading(str(i), text=col)
            self.data_table.column(str(i), width=155, minwidth=80, stretch=False)
        for row in flat:
            self.data_table.insert('', 'end', values=[str(row.get(c, '')) if row.get(c) is not None else '—' for c in columns])
        self.approval_status.set('Resolve blocking errors before approval.' if errors else 'Ready for review. Approval applies only to this immutable revision.')
        self.status.set(f'Preview ready. Showing up to 1,000 rows; all {len(rows)} rows remain in the revision.')

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
        ttk.Label(p, text='Shared sample and model catalog', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(p, text='Read registered identities across experiments in the token’s active SciSure group. '
            'Choose an existing material for a new measurement, or create a linked aliquot / treated material.', wraplength=1000).pack(anchor='w', pady=8)
        actions = ttk.Frame(p)
        actions.pack(fill='x')
        ttk.Button(actions, text='Refresh from SciSure', command=self.refresh_catalog).pack(side='left')
        self.catalog_query = tk.StringVar()
        ttk.Entry(actions, textvariable=self.catalog_query, width=40).pack(side='left', padx=8)
        ttk.Button(actions, text='Search IDs / labels / labs', command=self.filter_catalog).pack(side='left')
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
        buttons = ttk.Frame(p)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Use for a new measurement / calculation', command=lambda: self.use_catalog_subject(False)).pack(side='left')
        ttk.Button(buttons, text='Create a derived sample', command=lambda: self.use_catalog_subject(True)).pack(side='left', padx=8)
        ttk.Button(p, text='Repeat selected sample’s procedure as a new synthesis', command=self.repeat_catalog_procedure).pack(anchor='w', pady=7)
        self.catalog_status = tk.StringVar(value='Connect first, then refresh. No catalog is cached on disk.')
        ttk.Label(p, textvariable=self.catalog_status, wraplength=1000).pack(anchor='w', pady=10)

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
        self.tabs.select(self.import_tab)
        self.status.set('Shared procedure copied into a new lab-specific synthesis draft. '
            'Enter this execution’s actual record, date, batch label, deviations, state, and scale.')

    def build_history(self):
        p = self.history_tab
        ttk.Label(p, text='Read from the selected SciSure experiment', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(p, text='Connect and select an experiment in the SciSure connection tab. Signed experiments can be read.', wraplength=1000).pack(anchor='w', pady=7)
        actions = ttk.Frame(p)
        actions.pack(fill='x', pady=8)
        ttk.Button(actions, text='List saved CATALYST reviews', command=self.load_history).pack(side='left')
        ttk.Button(actions, text='Open selected review', command=lambda: self.open_history(False)).pack(side='left', padx=6)
        ttk.Button(actions, text='Load review + originals', command=lambda: self.open_history(True)).pack(side='left')
        ttk.Button(actions, text='Reuse mapping / revise', command=self.revise_loaded).pack(side='left', padx=6)
        self.history_table = ttk.Treeview(p, columns=('id', 'section', 'status'), show='headings', height=7)
        for key, name in [('id', 'Revision identifier'), ('section', 'SciSure section'), ('status', 'Transfer record')]:
            self.history_table.heading(key, text=name)
        self.history_table.pack(fill='x')
        ttk.Separator(p).pack(fill='x', pady=12)
        file_actions = ttk.Frame(p)
        file_actions.pack(fill='x')
        ttk.Button(file_actions, text='Browse all experiment files', command=self.browse_files).pack(side='left')
        ttk.Button(file_actions, text='Read selected file', command=self.read_file).pack(side='left', padx=6)
        self.file_table = ttk.Treeview(p, columns=('name', 'section', 'size'), show='headings', height=6)
        for key, name in [('name', 'File'), ('section', 'Section'), ('size', 'Bytes')]:
            self.file_table.heading(key, text=name)
        self.file_table.pack(fill='x', pady=7)
        frame, self.remote_text = self.text_panel(p, height=8)
        frame.pack(fill='both', expand=True)
        self.loaded_review = None

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
            self.show_text(self.remote_text, json.dumps(loaded['revision'].value(), indent=2, ensure_ascii=False)[:500000])
            self.status.set('Review loaded into memory. ' + ('Original checksums verified.' if with_sources else 'Original files were not downloaded.'))
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
                    result.append(dict(file, section_id=sid, section_name=section.get('sectionHeader', str(sid))))
            return destination, result
        def done(result):
            self.files_destination, self.remote_files = result
            self.file_table.delete(*self.file_table.get_children())
            for i, f in enumerate(self.remote_files):
                self.file_table.insert('', 'end', iid=str(i), values=(f.get('realName'), f['section_name'], f.get('fileSize')))
            self.status.set(f'{len(self.remote_files)} files listed. Reading a file does not save a local copy.')
        self.run('Listing experiment files…', work, done)

    def read_file(self):
        selection = self.file_table.selection()
        if not selection or not self.client: return
        f = dict(self.remote_files[int(selection[0])])
        client, destination = self.client, dict(self.files_destination)
        def work():
            client.verify_destination(destination, writable=False)
            if f.get('origin') == 'ONSITE':
                raise InputError('This file uses eLABHybrid institutional storage. Open it in SciSure; this app contacts only the verified SciSure tenant.')
            content = client.request(f'/api/v1/experiments/sections/{f["section_id"]}/files/{remote_id(f["experimentFileID"])}', binary=True)
            if len(content) != f.get('fileSize'):
                raise InputError('Downloaded file size differs from SciSure metadata.')
            name = str(f.get('realName', 'file'))
            if name.lower().endswith(('.csv', '.xlsx', '.json')):
                try:
                    source = Source.from_bytes(name, content)
                    return json.dumps(source.artifact, indent=2, ensure_ascii=False)[:500000]
                except InputError:
                    if name.lower().endswith('.json'):
                        return json.dumps(json.loads(content), indent=2, ensure_ascii=False)[:500000]
                    raise
            return f'{name}\n{len(content):,} bytes received into memory. This file type has no desktop preview.'
        self.run('Reading the selected file from SciSure…', work,
            lambda text: (self.show_text(self.remote_text, text), self.status.set('File read into memory. No local copy was saved.')))

    def new_submission(self, ask=True):
        if self.busy: return
        if ask and self.sources and not messagebox.askyesno('New submission', 'Discard the current in-memory review and start a new submission? SciSure records and source files will remain unchanged.', parent=self.root):
            return
        self.revision = self.approval = self.parent = None
        self.pending_profile = None
        self.title.set('')
        for var in self.context_vars.values(): var.set('')
        for key in ('submittingLab', 'acquisitionLab', 'processingLab', 'originLab', 'sampleCreatedLab', 'modelCreatedLab'):
            if key in self.context_vars: self.context_vars[key].set(lab_name(self.entity.get()))
        self.sources = []
        self.set_sources([])
        self.review_heading.set('Build a preview to review the standardized data.')
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
