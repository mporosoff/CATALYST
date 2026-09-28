"""A shared existing/new run chooser for connection and upload workflows."""
import tkinter as tk
from tkinter import ttk, messagebox

from .model import InputError
from .publication import Publisher
from .runs import RunWorkspace, creation_summary
from .scisure import SciSureError


class RunUI:
    def publisher_for_revision(self, revision):
        """Resolve a staged record's reviewed destination without reading Tk variables."""
        if not self.client or not self.group_id:
            raise InputError('Connect to the LIMS before sending staged records.')
        preview = revision.value()['preview']
        target = preview.get('publication_destination')
        inventory = preview.get('native_inventory_plan')
        if target:
            if target.get('tenant') != self.client.origin or target.get('group_id') != self.group_id:
                raise InputError('This staged record belongs to another LIMS server or group. Connect to its reviewed destination.')
            destination = self.client.verify_destination(target)
        elif inventory:
            if inventory.get('tenant') != self.client.origin or inventory.get('group_id') != self.group_id:
                raise InputError('This inventory review belongs to another LIMS server or group.')
            destination = self.client.destination(inventory['experiment_id'], self.group_id)
        elif self.destination:
            destination = self.client.verify_destination(self.destination)
        else:
            raise InputError('Select a run before sending this record.')
        if inventory and any(inventory.get(key) != destination.get(key) for key in ('tenant', 'group_id', 'experiment_id')):
            raise InputError('This record and its inventory review name different runs. Prepare a new review.')
        key = (destination['tenant'], destination['group_id'], destination['experiment_id'])
        return Publisher(self.client, destination, self.transfer_operations.setdefault(key, {}))

    def reset_run_browser(self):
        window = getattr(self, 'run_window', None)
        if window is not None and window.winfo_exists(): window.destroy()
        self.run_window = None

    def ensure_run_selected(self, callback=None):
        if self.destination:
            if callback: callback()
        else:
            self.open_run_picker(callback=callback)

    def run_workspace(self):
        if not hasattr(self, 'run_operations'): self.run_operations = {}
        return RunWorkspace(self.client, self.group_id, self.run_operations.setdefault((self.client.origin, self.group_id), {}))

    def accept_run_destination(self, destination, callback=None):
        self.clear_destination()
        self.destination = destination
        key = (destination['tenant'], destination['group_id'], destination['experiment_id'])
        self.publisher = Publisher(self.client, destination, self.transfer_operations.setdefault(key, {}))
        row = next((r for r in self.experiments if r.get('experimentID') == destination['experiment_id']), None)
        if row is None:
            row = dict(experimentID=destination['experiment_id'], name=destination['experiment_name'],
                groupID=destination['group_id'], projectID=destination['project_id'], studyID=destination['study_id'],
                signatureStatus=destination['signature_status'], deleted=False, template=False)
            self.experiments.append(row)
        self.experiment.configure(values=[f'{e["experimentID"]} · {e.get("name", "Unnamed")} · {e.get("signatureStatus", "Unknown status")}' for e in self.experiments])
        self.experiment.current(self.experiments.index(row))
        self.destination_status.set(f'Verified: {destination["experiment_name"]} · experiment {destination["experiment_id"]} · group {destination["group_id"]}')
        self.status.set('Run selected. Prepare and review your data before sending it.')
        if callback:
            callback()
        elif self.adaptive:
            self.refresh_workflow_library()

    def open_run_picker(self, mode='existing', callback=None):
        if self.busy: return
        if not self.client or not self.group_id:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect to your LIMS account, then choose an existing run or create a new one.')
            return
        self.reset_run_browser()
        window = self.run_window = tk.Toplevel(self.root)
        window.title('Choose where to store this work')
        window.geometry('720x640')
        window.minsize(640, 570)
        window.transient(self.root)
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Run / experiment', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Choose a project and study, then use an existing run or create one.',
            style='Muted.TLabel', wraplength=640).pack(anchor='w', pady=(5, 14))
        state = self.run_picker_state = dict(client=self.client, group=self.group_id, callback=callback,
            workspace=self.run_workspace(), projects=[], studies=[], experiments=[], window=window)
        state['mode'] = tk.StringVar(value='new' if mode == 'new' else 'existing')
        actions = ttk.Frame(panel)
        actions.pack(fill='x')
        for value, label in [('existing', 'Existing run'), ('new', 'New run')]:
            ttk.Radiobutton(actions, text=label, variable=state['mode'], value=value,
                command=self._run_mode_changed).pack(side='left', padx=(0, 20))
        fields = ttk.Frame(panel)
        fields.pack(fill='x', pady=16)
        fields.columnconfigure(1, weight=1)
        for index, (kind, label) in enumerate([('project', 'Project'), ('study', 'Study')]):
            ttk.Label(fields, text=label, style='Muted.TLabel').grid(row=index, column=0, sticky='w', padx=(0, 12), pady=6)
            combo = state[kind + '_combo'] = ttk.Combobox(fields, state='readonly')
            combo.grid(row=index, column=1, sticky='ew', pady=6)
            combo.bind('<<ComboboxSelected>>', lambda event, k=kind: self._run_parent_changed(k))
            ttk.Button(fields, text=f'New {kind}…', command=lambda k=kind: self._new_run_parent(k)).grid(row=index, column=2, padx=(10, 0), pady=6)
        state['existing_frame'] = existing = ttk.Frame(panel)
        search = ttk.Frame(existing)
        search.pack(fill='x')
        state['search'] = tk.StringVar()
        ttk.Entry(search, textvariable=state['search']).pack(side='left', fill='x', expand=True)
        ttk.Button(search, text='Search runs', command=self._load_runs).pack(side='left', padx=(10, 0))
        tree = state['tree'] = ttk.Treeview(existing, columns=('name', 'status'), show='headings', height=8, selectmode='browse')
        tree.heading('name', text='Run / experiment')
        tree.heading('status', text='Status')
        tree.column('name', width=410)
        tree.column('status', width=140, stretch=False)
        tree.pack(fill='both', expand=True, pady=12)
        state['new_frame'] = new = ttk.Frame(panel)
        ttk.Label(new, text='Run name *', style='Muted.TLabel').pack(anchor='w')
        state['name'] = tk.StringVar()
        ttk.Entry(new, textvariable=state['name']).pack(fill='x', pady=(6, 14))
        ttk.Label(new, text='The run will inherit the project or study collaborators. You can attach your samples, methods and files after reviewing them.',
            style='Muted.TLabel', wraplength=640).pack(anchor='w')
        state['notice'] = tk.StringVar(value='Loading projects…')
        ttk.Label(panel, textvariable=state['notice'], style='Muted.TLabel', wraplength=640).pack(side='bottom', fill='x', pady=10)
        bottom = ttk.Frame(panel)
        bottom.pack(side='bottom', fill='x', pady=(12, 0))
        state['submit'] = ttk.Button(bottom, text='Use selected run', style='Primary.TButton', command=self._run_submit)
        state['submit'].pack(side='left')
        ttk.Button(bottom, text='Close', command=window.destroy).pack(side='right')
        self._run_mode_changed(load=False)
        self._load_run_projects()

    def _run_live(self, state):
        return (state is getattr(self, 'run_picker_state', None) and state['window'].winfo_exists()
            and self.client is state['client'] and self.group_id == state['group'])

    def _run_selection(self, kind):
        state = self.run_picker_state
        index = state[kind + '_combo'].current()
        rows = state[kind + 's' if kind == 'project' else 'studies']
        return rows[index] if 0 <= index < len(rows) else None

    def _run_mode_changed(self, load=True):
        state = self.run_picker_state
        for name in ('existing_frame', 'new_frame'): state[name].pack_forget()
        new = state['mode'].get() == 'new'
        state['new_frame' if new else 'existing_frame'].pack(fill='both', expand=True)
        state['submit'].configure(text='Review new run' if new else 'Use selected run')
        if load and not self.busy: self._load_runs()

    def _load_run_projects(self, select=None):
        state = self.run_picker_state
        def done(rows):
            if not self._run_live(state): return
            state['projects'] = rows
            combo = state['project_combo']
            combo.configure(values=[f'{r["name"]} · {r["projectID"]}' for r in rows])
            combo.set('')
            if rows:
                selected = select or (self.destination or {}).get('project_id')
                combo.current(next((i for i, r in enumerate(rows) if r['projectID'] == selected), 0))
                self._run_parent_changed('project')
            else:
                state['notice'].set('No active projects are available. Choose New project to create one, or ask the project owner to grant access.')
        self.run('Loading available projects…', state['workspace'].projects, done)

    def _run_parent_changed(self, kind, select=None):
        if self.busy: return
        state = self.run_picker_state
        state['tree'].delete(*state['tree'].get_children())
        state['experiments'] = []
        if kind == 'study':
            self._load_runs()
            return
        state['studies'] = []
        state['study_combo'].configure(values=[])
        state['study_combo'].set('')
        project = self._run_selection('project')
        if not project: return
        def done(rows):
            if not self._run_live(state): return
            state['studies'] = rows
            combo = state['study_combo']
            combo.configure(values=[f'{r["name"]} · {r["studyID"]}' for r in rows])
            if rows:
                selected = select or (self.destination or {}).get('study_id')
                combo.current(next((i for i, r in enumerate(rows) if r['studyID'] == selected), 0))
                self._load_runs()
            else:
                state['notice'].set('This project has no available studies. Choose New study to organize this work.')
        self.run('Loading studies in this project…', lambda: state['workspace'].studies(project['projectID']), done)

    def _load_runs(self):
        if self.busy: return
        state = self.run_picker_state
        project, study = self._run_selection('project'), self._run_selection('study')
        if not project or not study: return
        if state['mode'].get() == 'new':
            state['notice'].set('Enter a name, then review exactly where this run will be created.')
            return
        search = state['search'].get()
        def done(rows):
            if not self._run_live(state): return
            state['experiments'] = rows
            state['tree'].delete(*state['tree'].get_children())
            for index, row in enumerate(rows):
                state['tree'].insert('', 'end', iid=str(index), values=(row.get('name', 'Unnamed'),
                    'Ready for upload' if row.get('signatureStatus') == 'None' else 'Signed / locked'))
            if rows: state['tree'].selection_set('0')
            state['notice'].set(f'{len(rows)} runs found. Select an unsigned run for uploads.' if rows else
                'No runs match this search. Clear the search or choose New run.')
        self.run('Finding runs in this study…', lambda: state['workspace'].experiments(project['projectID'], study['studyID'], search), done)

    def _run_submit(self):
        if self.busy: return
        state = self.run_picker_state
        if not self._run_live(state): return
        project, study = self._run_selection('project'), self._run_selection('study')
        if not project or not study:
            state['notice'].set('Choose a project and study first. Use New project or New study if needed.')
            return
        if state['mode'].get() == 'new':
            name = state['name'].get()
            self.run('Preparing a new run for review…',
                lambda: state['workspace'].plan('run', name, project['projectID'], study['studyID']),
                lambda plan: self._review_run_creation(plan, self._run_selected))
        else:
            selected = state['tree'].selection()
            if not selected:
                state['notice'].set('Select a run, or choose New run to create one.')
                return
            row = state['experiments'][int(selected[0])]
            self.run('Verifying the selected run…', lambda: state['client'].destination(row['experimentID'], state['group']), self._run_selected)

    def _run_selected(self, destination):
        state = self.run_picker_state
        if not self._run_live(state): return
        callback = state['callback']
        self.reset_run_browser()
        self.accept_run_destination(destination, callback)

    def _new_run_parent(self, kind):
        if self.busy: return
        state = self.run_picker_state
        project = self._run_selection('project')
        if kind == 'study' and not project:
            state['notice'].set('Choose or create a project before adding a study.')
            return
        window = tk.Toplevel(state['window'])
        window.title('New ' + kind)
        window.geometry('600x380')
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill='both', expand=True)
        name, notes, approve = tk.StringVar(), tk.StringVar(), tk.StringVar(value='Not required')
        ttk.Label(panel, text=f'{kind.title()} name *', style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel, textvariable=name).pack(fill='x', pady=(6, 16))
        if kind == 'project':
            ttk.Label(panel, text='Purpose / notes *', style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(panel, textvariable=notes).pack(fill='x', pady=(6, 10))
            ttk.Label(panel, text='The LIMS requires notes when creating a project.', style='Small.TLabel').pack(anchor='w')
        else:
            ttk.Label(panel, text='Project: ' + project['name'], wraplength=550).pack(anchor='w', pady=(0, 16))
            ttk.Label(panel, text='Study approval policy', style='Muted.TLabel').pack(anchor='w')
            ttk.Combobox(panel, textvariable=approve, values=['Not required', 'Study manager'], state='readonly').pack(fill='x', pady=(6, 10))
        def prepared(plan):
            if not self._run_live(state) or not window.winfo_exists(): return
            window.destroy()
            self._review_run_creation(plan, lambda row: self._load_run_projects(row['projectID']) if kind == 'project'
                else self._run_parent_changed('project', row['studyID']))
        def review():
            values = dict(name=name.get(), notes=notes.get(), approve='BYSTUDYMANAGER' if approve.get() == 'Study manager' else 'NOTREQUIRED')
            self.run(f'Preparing the new {kind} for review…', lambda: state['workspace'].plan(kind,
                project_id=project['projectID'] if project else None, **values), prepared)
        actions = ttk.Frame(panel)
        actions.pack(side='bottom', fill='x')
        ttk.Button(actions, text='Review new ' + kind, command=review).pack(side='left')
        ttk.Button(actions, text='Cancel', command=window.destroy).pack(side='right')

    def _review_run_creation(self, plan, done):
        state = self.run_picker_state
        if not self._run_live(state): return
        window = tk.Toplevel(state['window'])
        window.title('Review new ' + plan['kind'])
        window.geometry('620x430')
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text=creation_summary(plan), justify='left', wraplength=570).pack(fill='both', expand=True)
        def create():
            if self.busy or not self._run_live(state): return
            def complete(result):
                if window.winfo_exists(): window.destroy()
                if self._run_live(state): done(result)
            self.run(f'Creating the reviewed {plan["kind"]}…', lambda: state['workspace'].create(plan), complete)
        actions = ttk.Frame(panel)
        actions.pack(fill='x', pady=14)
        ttk.Button(actions, text='Create ' + plan['kind'], style='Primary.TButton', command=create).pack(side='left')
        ttk.Button(actions, text='Back', command=window.destroy).pack(side='right')
