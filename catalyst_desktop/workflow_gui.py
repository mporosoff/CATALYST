"""Researcher-facing record workflows, reusable links, and batch staging."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from .design import Card, Tooltip
from .model import MODALITIES, Source, Revision, InputError, build_preview, load_sources
from .traceability import LAB_CHOICES, new_id, lab_name
from .workflow import workflow_fields, workflow_required
from .library import search_procedures, search_samples, procedure_context, sample_context, load_native_procedures, native_procedure_context
from .catalog import load_catalog, check_publication
from .batch import BatchQueue
from .batch_gui import BatchPanel


RECORD_LABELS = {
    'Characterization / measurement data': 'measurement',
    'Sample information': 'sample',
    'Synthesis execution': 'synthesis',
    'Reusable procedure / method': 'procedure',
    'Computational results': 'computation',
}
RECORD_HINTS = {
    'measurement': 'Select the sample and method already in the library. Add this run’s files, date and operator.',
    'sample': 'Register a material once. Later measurements link to this sample without repeating its description.',
    'synthesis': 'Link the sample produced to the procedure used. Record this execution’s date, operator and any changes.',
    'procedure': 'Save a versioned procedure once: attach a document, enter instructions, or link an existing protocol.',
    'computation': 'Link a calculation method, describe the model, and attach the results for this calculation.',
}
INTERNAL_FIELDS = {
    'workflowVersion', 'recordType', 'uploadMode', 'datasetId', 'submittingLab', 'acquisitionLab', 'processingLab',
    'sampleCreatedLab', 'modelCreatedLab', 'synthesisExecutionId', 'procedureSourceRevisionId',
    'procedureSourceRevisionSha256', 'sampleSourceRevisionId', 'sampleSourceRevisionSha256',
    'inventoryMode', 'nativeSampleId', 'nativeSampleTypeId', 'nativeSampleSnapshotSha256', 'nativeSampleTenant',
    'methodStatus',
}
FIELD_HELP = {
    'sampleDescription': 'Composition or a short description that distinguishes this material. Save it here once; measurements will link to this sample.',
    'procedureName': 'A recognizable name for this reusable method. Researchers can search for this name in the procedure library.',
    'procedureVersion': 'The version of these instructions. Use a new version when updating a saved procedure; existing measurements retain their link to the version they used.',
    'procedureType': 'Choose whether this procedure describes synthesis, a measurement, or a computation. This determines which records can use it.',
    'procedureModality': 'The technique covered by this version of the method. Only matching methods are offered when linking data.',
    'procedureText': 'Enter the actual instructions, or attach the procedure document or provide a reference below. This is saved once per version.',
    'procedureReference': 'An existing protocol or document reference. Native SciSure choices retain their exact version address.',
    'notes': 'Optional run-specific notes, instrument conditions, or departures from the linked method. The method itself does not need to be uploaded again.',
    'methodId': 'Select a saved or staged procedure version. The data links to that method rather than repeating its instructions.',
    'specimenId': 'Select a saved or staged sample. The measurement keeps a link to its existing description and synthesis history.',
}


class WorkflowUI:
    def build_workflow_start(self, parent):
        self.batch_queue = BatchQueue()
        self.editing_batch_id = self.review_batch_id = None
        self.native_procedures = []
        self.native_sample_choices = []
        self.native_sample_scope = None
        self.workflow_selection = tk.StringVar(value=next(iter(RECORD_LABELS)))
        self.record_type = self.watched('measurement')
        self.stage_requested = False
        self.link_return_drafts = []
        self.workflow_card = Card(parent)
        if self.adaptive: self.workflow_card.pack(fill='x', pady=(0, 18))
        p = self.workflow_card.body
        ttk.Label(p, text='What are you adding?', style='Sub.TLabel').pack(anchor='w', pady=(0, 8))
        choose = ttk.Combobox(p, textvariable=self.workflow_selection, values=tuple(RECORD_LABELS), state='readonly')
        choose.pack(fill='x')
        choose.bind('<<ComboboxSelected>>', lambda _: self.change_record_type(RECORD_LABELS[self.workflow_selection.get()]))
        self.workflow_hint = tk.StringVar(value=RECORD_HINTS['measurement'])
        ttk.Label(p, textvariable=self.workflow_hint, style='Muted.TLabel', wraplength=540).pack(anchor='w', pady=(8, 12))
        actions = ttk.Frame(p)
        actions.pack(fill='x')
        ttk.Button(actions, text='Stage this record', command=self.stage_current_record).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.batch_files_button = ttk.Button(actions, text='Batch files…', command=self.choose_batch_files)
        self.batch_files_button.grid(row=0, column=1, sticky='w', padx=(0, 8))
        ttk.Button(actions, text='Open batch review', command=lambda: self.tabs.select(self.batch_tab)).grid(row=0, column=2, sticky='w')
        self.return_draft_button = ttk.Button(p, text='Back to linked data draft', command=self.return_to_linked_draft)

    def build_batch_page(self):
        self.batch_panel = BatchPanel(self.batch_tab, {
            'inspect': self.inspect_batch_item, 'edit': self.edit_batch_item,
            'remove': self.remove_batch_item, 'approve': self.inspect_batch_item,
            'send': self.send_batch, 'move_up': lambda key: self.move_batch_item(key, -1),
            'move_down': lambda key: self.move_batch_item(key, 1),
        })
        self.batch_panel.pack(fill='both', expand=True)
        self.refresh_batch()

    def change_record_type(self, kind, confirm=True):
        if self.busy: return
        if confirm and (self.sources or (self.adaptive and any(v.get().strip() for k, v in self.context_vars.items()
                if k not in INTERNAL_FIELDS | {'procedureId', 'procedureVersion', 'specimenId'}))):
            if not messagebox.askyesno('Change record type', 'Start a new draft of this type? Stage the current record first if you want to keep it. Records already in the batch are retained.', parent=self.root):
                self.workflow_selection.set(next(k for k, v in RECORD_LABELS.items() if v == self.record_type.get()))
                return
        self.adaptive = True
        self.record_type.set(kind)
        self.workflow_selection.set(next(k for k, v in RECORD_LABELS.items() if v == kind))
        self.context_vars = {}
        self.revision = self.approval = self.parent = None
        self.editing_batch_id = self.review_batch_id = None
        self.pending_profile = None
        self.title.set('')
        self.toolkit.set(False)
        self.raw_only.set(True)
        self.modality.set({'procedure': 'procedure', 'sample': 'sample', 'synthesis': 'synthesis', 'computation': 'computational'}.get(kind, 'XRD'))
        self.rebuild_context()
        self.set_sources([])
        self.workflow_card.pack(fill='x', pady=(0, 18), before=self.originals_card)
        self.tabs.select(self.import_tab)
        self.import_tab.canvas.yview_moveto(0)

    def workflow_values(self):
        context = {key: var.get() for key, var in self.context_vars.items()}
        context.update(workflowVersion='2', recordType=self.record_type.get(),
            uploadMode='mapped' if not self.raw_only.get() else 'originals' if self.sources else 'metadata')
        return context

    def workflow_lab_changed(self):
        if not self.adaptive: return
        native_sample = bool(self.context_vars.get('nativeSampleId') and self.context_vars['nativeSampleId'].get())
        for key in ('submittingLab', 'acquisitionLab', 'processingLab', 'sampleCreatedLab', 'modelCreatedLab'):
            if native_sample and key == 'sampleCreatedLab': continue
            if key in self.context_vars: self.context_vars[key].set(self.entity.get())
        self.context_vars['datasetId'].set(new_id(self.entity.get(), 'DS'))
        kind = self.record_type.get()
        if kind in ('sample', 'computation') and not native_sample:
            self.context_vars['specimenId'].set(new_id(self.entity.get(), 'MDL' if kind == 'computation' else 'SMP'))
        if kind == 'procedure': self.context_vars['procedureId'].set(new_id(self.entity.get(), 'PRC'))
        if kind == 'synthesis': self.context_vars['synthesisExecutionId'].set(new_id(self.entity.get(), 'SYN'))
        self.refresh_link_options()

    def workflow_modality_changed(self):
        for key in ('methodId', 'methodVersion', 'procedureId', 'procedureVersion', 'procedureSourceRevisionId', 'procedureSourceRevisionSha256'):
            if key in self.context_vars: self.context_vars[key].set('')
        self.rebuild_context()

    def prepare_table_sources(self):
        if not self.adaptive or self.raw_only.get() or self.busy: return
        sources = tuple(self.sources)
        if not any(s.artifact['format'] == 'binary' and Path(s.name).suffix.lower() in ('.csv', '.xlsx', '.json') for s in sources): return
        def work():
            result = [Source.from_bytes(s.name, s.content) if s.artifact['format'] == 'binary' and Path(s.name).suffix.lower() in ('.csv', '.xlsx', '.json') else s for s in sources]
            from .model import check_sources
            check_sources(result)
            return result
        self.run('Reading selected originals for optional table conversion…', work, self.set_sources, failed=lambda: self.raw_only.set(True))

    def procedure_purpose_changed(self):
        kind = self.context_vars['procedureType'].get()
        techniques = ('synthesis',) if kind == 'synthesis' else ('computational',) if kind == 'computation' else tuple(m for m in MODALITIES if m not in ('sample', 'procedure', 'synthesis', 'computational'))
        self.context_widgets['procedureModality'].configure(values=techniques)
        if self.context_vars['procedureModality'].get() not in techniques:
            self.context_vars['procedureModality'].set(techniques[0] if len(techniques) == 1 else '')

    def rebuild_workflow_context(self):
        from .gui import Disclosure
        old = {key: var.get() for key, var in self.context_vars.items()}
        kind, modality = self.record_type.get(), self.modality.get()
        fields = workflow_fields(kind, modality)
        for widget in self.context_frame.winfo_children(): widget.destroy()
        self.context_vars, self.context_widgets, self.context_labels, self.context_fields = {}, {}, {}, {}
        self.required_keys = frozenset()
        defaults = {'workflowVersion': '2', 'recordType': kind, 'uploadMode': 'originals',
            'datasetId': new_id(self.entity.get(), 'DS'), 'procedureVersion': '1',
            'methodStatus': 'recorded',
            'inventoryMode': 'native' if kind == 'sample' and self.client else 'records'}
        for key in ('submittingLab', 'acquisitionLab', 'processingLab', 'sampleCreatedLab', 'modelCreatedLab'):
            defaults[key] = self.entity.get()
        if kind == 'procedure': defaults['procedureId'] = new_id(self.entity.get(), 'PRC')
        if kind in ('sample', 'computation'): defaults['specimenId'] = new_id(self.entity.get(), 'MDL' if kind == 'computation' else 'SMP')
        if kind == 'synthesis': defaults['synthesisExecutionId'] = new_id(self.entity.get(), 'SYN')
        for key in dict.fromkeys([*fields, *INTERNAL_FIELDS, 'specimenId', 'methodId', 'methodVersion', 'procedureId', 'procedureVersion', 'localSampleId']):
            self.context_vars[key] = self.watched(old.get(key, defaults.get(key, '')))
            self.context_vars[key].trace_add('write', self.schedule_requirements)
        self.workflow_hint.set(RECORD_HINTS[kind])
        self.new_identity_button.master.pack_forget()
        self.context_heading.configure(text={'procedure': 'Procedure definition', 'sample': 'Sample description',
            'synthesis': 'Record this synthesis', 'measurement': 'Link this measurement', 'computation': 'Record this calculation'}[kind])
        self.context_intro.configure(text='IDs and laboratory provenance are recorded automatically. Expand optional details only when relevant.')
        self.title_frame.pack_forget()
        if kind not in ('procedure', 'sample'): self.title_frame.pack(fill='x', before=self.selectors_frame)
        if kind in ('procedure', 'sample', 'synthesis', 'computation'):
            self.modality_label.grid_remove()
            self.submission_widgets['modality'].grid_remove()
        else:
            self.modality_label.grid()
            self.submission_widgets['modality'].grid()
            self.submission_widgets['modality'].configure(values=tuple(m for m in MODALITIES if m not in ('sample', 'procedure', 'synthesis', 'computational')))
        self.batch_files_button.state(['!disabled'] if kind in ('measurement', 'computation') else ['disabled'])
        self.toolkit_widget.pack_forget()
        if kind == 'measurement' and modality == 'reactor': self.toolkit_widget.pack(anchor='w', before=self.raw_only_widget)
        self.raw_only_widget.pack_forget()
        if kind in ('measurement', 'computation'): self.raw_only_widget.pack(anchor='w', before=self.context_separator)
        self.link_frame = ttk.Frame(self.context_frame)
        self.link_frame.pack(fill='x')
        if kind in ('sample', 'measurement', 'synthesis'):
            native = ttk.Checkbutton(self.link_frame, text='Also register / link this sample in LIMS inventory',
                variable=self.context_vars['inventoryMode'], onvalue='native', offvalue='records')
            native.pack(anchor='w', pady=(0, 7))
            Tooltip(native, 'Include reviewed native sample inventory actions when sending. Samples use Material v2; measurements link Used samples and synthesis links Generated samples. Existing inventory samples are reused. Connect and verify an experiment to review these actions.')
            if kind == 'sample':
                ttk.Button(self.link_frame, text='Choose existing LIMS inventory sample…', command=self.browse_native_samples).pack(anchor='w', pady=(0, 10))
        self.link_widgets, self.link_rows = {}, {}
        if kind in ('measurement', 'synthesis'):
            self.make_link_picker('sample', '* Sample produced' if kind == 'synthesis' else '* Measured sample', self.link_frame)
        if kind in ('measurement', 'synthesis', 'computation'):
            self.make_link_picker('procedure', '* Procedure / method version', self.link_frame)
        if kind == 'measurement':
            missing_method = ttk.Checkbutton(self.link_frame, text='Historical data: method was not recorded',
                variable=self.context_vars['methodStatus'], onvalue='not-recorded', offvalue='recorded',
                command=self.method_status_changed)
            missing_method.pack(anchor='w', pady=(0, 10))
            Tooltip(missing_method, 'Use only when the method for an existing measurement is unavailable. The review explicitly records that the method is unknown; no placeholder procedure is created.')
        self.workflow_fields_frame = ttk.Frame(self.context_frame)
        self.workflow_fields_frame.pack(fill='x')
        self.workflow_fields_frame.columnconfigure(0, weight=1, uniform='workflow')
        self.workflow_fields_frame.columnconfigure(1, weight=1, uniform='workflow')
        self.optional_context = Disclosure(self.context_frame, 'Optional details · run notes & additional context')
        self.optional_context.body.columnconfigure(0, weight=1, uniform='optional')
        self.optional_context.body.columnconfigure(1, weight=1, uniform='optional')
        hidden = INTERNAL_FIELDS | {'datasetId', 'methodId', 'methodVersion', 'specimenId'}
        if kind != 'procedure': hidden |= {'procedureId', 'procedureVersion'}
        else: hidden |= {'procedureId'}
        choices = {'procedureType': ('synthesis', 'measurement', 'computation'),
            'procedureModality': tuple(m for m in MODALITIES if m not in ('sample', 'procedure')),
            'modelRelation': ('no physical link', 'represents', 'derived from', 'compared with')}
        for key, label in fields.items():
            if key in hidden: continue
            field = self.context_fields[key] = ttk.Frame(self.context_frame, padding=(0, 0, 12, 12))
            caption = self.context_labels[key] = ttk.Label(field, text=label, style='Muted.TLabel', wraplength=225)
            caption.pack(anchor='w', pady=(0, 5))
            var = self.context_vars[key]
            if key == 'procedureText':
                widget = tk.Text(field, height=5, width=18, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
                widget.insert('1.0', var.get())
                widget.edit_modified(False)
                def sync_text(*_, widget=widget, var=var):
                    if widget.winfo_exists() and widget.get('1.0', 'end-1c') != var.get():
                        widget.delete('1.0', 'end'); widget.insert('1.0', var.get()); widget.edit_modified(False)
                def sync_var(_, widget=widget, var=var):
                    if widget.edit_modified():
                        var.set(widget.get('1.0', 'end-1c')); widget.edit_modified(False)
                var.trace_add('write', sync_text)
                widget.bind('<<Modified>>', sync_var)
            else:
                widget = ttk.Combobox(field, textvariable=var, state='readonly', values=choices[key], width=18) if key in choices else ttk.Entry(field, textvariable=var, width=18)
            widget.pack(side='bottom', fill='x')
            self.context_widgets[key] = widget
            for target in (caption, widget):
                Tooltip(target, lambda key=key: self.workflow_field_help(key))
            widget.bind('<FocusIn>', lambda e: self.root.after_idle(lambda widget=e.widget: self.ensure_field_visible(widget)), add='+')
        if kind == 'procedure':
            self.context_widgets['procedureType'].bind('<<ComboboxSelected>>', lambda _: self.procedure_purpose_changed())
        self.refresh_link_options()
        self.refresh_workflow_requirements()
        self.install_wheel_handlers(self.context_frame)

    def workflow_field_help(self, key):
        from .guidance import field_help
        return FIELD_HELP.get(key) or field_help(key, self.modality.get()) or 'Information stored with this record so collaborators can interpret and reuse it.'

    def refresh_workflow_requirements(self):
        pending = getattr(self, '_requirements_pending', None)
        if pending is not None:
            self.root.after_cancel(pending)
            self._requirements_pending = None
        context = self.workflow_values()
        kind = self.record_type.get()
        if kind in ('sample', 'procedure'):
            self.title.set(context.get('procedureName' if kind == 'procedure' else 'localSampleId', '')) if self.title.get() != context.get('procedureName' if kind == 'procedure' else 'localSampleId', '') else None
        required = frozenset(workflow_required(context, self.modality.get(), self.toolkit.get()))
        if required != self.required_keys or not getattr(self, '_workflow_layout_ready', False):
            self.required_keys = required
            fields = workflow_fields(kind, self.modality.get())
            counts = [0, 0]
            for key, frame in self.context_fields.items():
                frame.grid_forget()
                optional = key not in required and not (kind == 'procedure' and key in ('procedureText', 'procedureReference'))
                index = counts[int(optional)]
                counts[int(optional)] += 1
                frame.grid(in_=self.optional_context.body if optional else self.workflow_fields_frame,
                    row=index // 2, column=index % 2, sticky='nsew')
                label = fields[key].replace(' (optional)', '')
                one_of = kind == 'procedure' and key in ('procedureText', 'procedureReference')
                self.context_labels[key].configure(text=('* ' if key in required else '') + label + (' · Optional' if optional else ' · or attach document' if one_of else ''))
            if counts[1]: self.optional_context.pack(fill='x', pady=(6, 12))
            else: self.optional_context.pack_forget()
            self._workflow_layout_ready = True
        if not hasattr(self, 'mapping_section'): return
        mapping_needed = kind in ('measurement', 'computation') and not (self.raw_only.get() or self.toolkit.get())
        if mapping_needed:
            self.mapping_note.pack_forget()
            self.mapping_section.pack(fill='x', pady=(0, 12))
        else:
            self.mapping_section.pack_forget()
            self.mapping_note.configure(text='Files are preserved with this record. Table conversion is optional.' if kind in ('measurement', 'computation') else 'This record stores reusable information. Attach supporting documents if needed; no table mapping is required.')
            self.mapping_note.pack(fill='x', pady=(8, 12))
        values = [(context.get(key, ''), self.context_widgets[key]) for key in self.context_widgets if key in required]
        for name, widget in self.link_widgets.items():
            if name == 'procedure' and kind == 'measurement' and context.get('methodStatus') == 'not-recorded':
                continue
            key = 'specimenId' if name == 'sample' else 'procedureId' if kind == 'synthesis' else 'methodId'
            values.append((context.get(key, ''), widget))
        if mapping_needed:
            values.extend((getattr(self, key).get(), widget) for key, widget in self.mapping_widgets.items())
            values.extend((unit.get(), self.rule_widgets[name][1]) for name, target, unit, _ in self.rules if target.get() != 'Ignore')
        self.missing_widgets = [w for value, w in values if not value.strip()]
        if kind == 'procedure' and not (context.get('procedureText', '').strip() or context.get('procedureReference', '').strip() or self.sources):
            self.missing_widgets.append(self.context_widgets['procedureText'])
        missing = len(self.missing_widgets)
        files_needed = kind in ('measurement', 'computation') and not self.sources
        self.required_summary.set(f'{missing} required field{"s" if missing != 1 else ""} remaining.' + (' Add data files.' if files_needed else '') + '\nExisting sample and procedure records are linked; their details are reused.')
        if hasattr(self, 'review_button'): self.review_button.state(['!disabled'] if self.sources or kind in ('procedure', 'sample', 'synthesis') else ['disabled'])

    def make_link_picker(self, name, label, parent):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(0, 14))
        caption = ttk.Label(row, text=label, style='Sub.TLabel')
        caption.pack(anchor='w')
        search = ttk.Entry(row)
        search.pack(fill='x', pady=(6, 4))
        Tooltip(search, 'Search by name, local label, canonical ID or version. Matches come from the connected LIMS and records staged in this batch.')
        choice = ttk.Combobox(row, state='readonly', height=12)
        choice.pack(fill='x')
        for target in (caption, choice): Tooltip(target, FIELD_HELP['specimenId' if name == 'sample' else 'methodId'])
        controls = ttk.Frame(row)
        controls.pack(fill='x', pady=(5, 0))
        ttk.Button(controls, text='Search LIMS' if name == 'sample' else 'Refresh LIMS library', command=self.refresh_workflow_library).pack(side='left')
        ttk.Button(controls, text='+ Add new ' + name, command=lambda name=name: self.add_linked_record(name)).pack(side='left', padx=6)
        if name == 'procedure': ttk.Button(controls, text='SciSure protocols…', command=self.browse_native_procedures).pack(side='left')
        self.link_widgets[name] = choice
        self.link_rows[name] = {'search': search, 'rows': []}
        search.bind('<KeyRelease>', lambda _, name=name: self.refresh_link_options(name))
        choice.bind('<<ComboboxSelected>>', lambda _, name=name: self.select_link(name))

    def available_library(self):
        catalog = dict(self.catalog or dict(entries=[], pending=[], profiles=[]))
        catalog['entries'] = list(catalog['entries']) + list(self.batch_queue.catalog_entries())
        return catalog

    def refresh_link_options(self, only=None):
        if not self.adaptive or not hasattr(self, 'link_rows'): return
        catalog = self.available_library()
        for name, state in self.link_rows.items():
            if only and name != only: continue
            query = state['search'].get()
            if name == 'sample':
                rows = self.sample_choices(catalog, query)
            else:
                kind = 'synthesis' if self.record_type.get() == 'synthesis' else 'computation' if self.record_type.get() == 'computation' else 'measurement'
                rows = search_procedures(catalog, query, kind=kind, modality=self.modality.get())
                sample_id = self.context_vars['specimenId'].get()
                used = {(entry['trace']['dataset']['method']['id'], entry['trace']['dataset']['method']['version'])
                    for entry in catalog['entries'] if entry['trace'].get('dataset', {}).get('subject_id') == sample_id and entry['trace']['dataset'].get('method')}
                rows.sort(key=lambda row: row['key'] not in used)
                for row in rows:
                    if row['key'] in used: row['label'] += ' · used with this sample'
            state['rows'] = rows
            labels = [row.get('label') or row.get('subject', {}).get('id', '') for row in rows]
            self.link_widgets[name].configure(values=labels)
            context = self.workflow_values()
            selected_id = context.get('specimenId') if name == 'sample' else context.get('procedureId' if self.record_type.get() == 'synthesis' else 'methodId')
            version = context.get('procedureVersion' if self.record_type.get() == 'synthesis' else 'methodVersion')
            matches = [i for i, row in enumerate(rows) if (self.sample_choice_id(row) == selected_id if name == 'sample' else row['procedure']['id'] == selected_id and str(row['procedure']['version']) == version)]
            if matches: self.link_widgets[name].current(matches[0])
            elif selected_id: self.link_widgets[name].set('Linked: ' + selected_id + ((' · v' + version) if name == 'procedure' else ''))
            elif name == 'procedure' and context.get('methodStatus') == 'not-recorded': self.link_widgets[name].set('Method not recorded for this historical measurement')
            else: self.link_widgets[name].set('Choose an existing record or add new…')

    def select_link(self, name):
        if self.busy: return
        index = self.link_widgets[name].current()
        rows = self.link_rows[name]['rows']
        if not 0 <= index < len(rows): return
        if name == 'sample':
            self.apply_sample_choice(rows[index])
            return
        self.context_vars['methodStatus'].set('recorded')
        changes = procedure_context(rows[index])
        for key, value in changes.items():
            if key not in self.context_vars: self.context_vars[key] = self.watched()
            self.context_vars[key].set(str(value))
        self.refresh_workflow_requirements()

    def method_status_changed(self):
        if self.context_vars['methodStatus'].get() == 'not-recorded':
            for key in ('methodId', 'methodVersion', 'procedureId', 'procedureVersion',
                    'procedureSourceRevisionId', 'procedureSourceRevisionSha256'):
                self.context_vars[key].set('')
            self.link_widgets['procedure'].set('Method not recorded for this historical measurement')
        else:
            self.refresh_link_options('procedure')
        self.refresh_workflow_requirements()

    def sample_choice_id(self, row):
        if row.get('source') == 'native':
            from .inventory import native_sample_context
            return native_sample_context(row, self.entity.get())['specimenId']
        return row.get('subject', {}).get('id', '')

    def sample_choices(self, catalog, query=''):
        """One sample selector combines saved, queued and native identities."""
        rows = [row for row in search_samples(catalog, query) if row['entry']['trace'].get('material')]
        all_saved = [row for row in search_samples(catalog) if row['entry']['trace'].get('material')]
        registered_ids = {row['subject']['id']: row for row in all_saved}
        registered_native = {(row['entry'].get('context', {}).get('nativeSampleTenant'),
            row['entry'].get('context', {}).get('nativeSampleId')): row for row in all_saved}
        scope = (self.client.origin, self.group_id) if self.client else None
        if scope != self.native_sample_scope: return rows
        terms = query.strip().casefold().split()
        for row in self.native_sample_choices:
            native = row['native']
            searchable = ' '.join(str(row['sample'].get(key) or '') for key in ('name', 'altID', 'description')) + ' ' + row['label']
            if not all(term in searchable.casefold() for term in terms): continue
            registered = registered_ids.get(self.sample_choice_id(row)) or registered_native.get((native['tenant'], str(native['sample_id'])))
            if registered:
                if not any(existing.get('subject', {}).get('id') == registered['subject']['id'] for existing in rows):
                    registered['label'] += ' · SciSure ' + str(native['sample_id'])
                    rows.append(registered)
            else: rows.append(row)
        return rows

    def apply_sample_choice(self, row):
        """Select a saved/native sample without replacing the current data draft."""
        if self.busy: return
        if row.get('source') == 'native':
            from .inventory import native_sample_context
            values = native_sample_context(row, self.entity.get())
            if not self.client or values['nativeSampleTenant'] != self.client.origin or row['native']['group_id'] != self.group_id:
                self.status.set('The connection changed. Search this server’s samples again.'); return
            existing = next((saved for saved in search_samples(self.available_library())
                if saved['subject']['id'] == values['specimenId'] or (
                    saved['entry'].get('context', {}).get('nativeSampleId') == values['nativeSampleId']
                    and saved['entry']['context'].get('nativeSampleTenant') == values['nativeSampleTenant'])), None)
            if existing:
                self.apply_sample_choice(existing)
                self.context_vars['inventoryMode'].set('native')
                return
            if not values['sampleDescription'].strip():
                self.add_sample_dialog(values)
            else:
                self.stage_linked_sample(values)
            return
        changes = sample_context(row)
        for key, value in changes.items():
            if key not in self.context_vars: self.context_vars[key] = self.watched()
            self.context_vars[key].set(str(value))
        self.context_vars['inventoryMode'].set('native' if row['entry'].get('context', {}).get('inventoryMode') == 'native' else 'records')
        self.refresh_link_options()
        self.refresh_workflow_requirements()

    def add_sample_dialog(self, initial=None):
        """Collect sample facts in place; staged definitions remain unapproved."""
        if self.busy: return
        initial = dict(initial or {})
        native_reference = bool(initial.get('nativeSampleId'))
        window = tk.Toplevel(self.root)
        window.title('Link existing LIMS sample' if native_reference else 'Add new sample')
        window.transient(self.root)
        window.geometry('570x470')
        window.minsize(500, 430)
        frame = ttk.Frame(window, padding=20); frame.pack(fill='both', expand=True)
        text = ('Add a short description for the linked record. The existing LIMS sample will be reused.' if native_reference
            else 'Describe the sample once. Your current data draft stays open and will use this sample.')
        ttk.Label(frame, text=text, wraplength=510).pack(anchor='w', pady=(0, 12))
        label = tk.StringVar(value=initial.get('localSampleId', ''))
        description = tk.StringVar(value=initial.get('sampleDescription', ''))
        state = tk.StringVar(value=initial.get('materialState', ''))
        for caption, variable in (('* Sample label', label), ('* Description / composition', description), ('State / treatment (optional)', state)):
            heading = ttk.Label(frame, text=caption)
            heading.pack(anchor='w', pady=(5, 3))
            entry = ttk.Entry(frame, textvariable=variable)
            entry.pack(fill='x')
            help_text = ('The name used for this sample in your lab. The toolkit generates its stable identifier automatically.' if variable is label
                else FIELD_HELP['sampleDescription'] if variable is description else 'An optional treatment or state that distinguishes this material from its parent sample.')
            for target in (heading, entry): Tooltip(target, help_text)
            if caption.startswith('* Sample'): entry.focus_set()
        native = tk.BooleanVar(value=native_reference or bool(self.client))
        inventory = ttk.Checkbutton(frame, text='Register / link in LIMS sample inventory', variable=native)
        inventory.pack(anchor='w', pady=(12, 8))
        if native_reference: inventory.state(['disabled'])
        ttk.Label(frame, text='The sample is added to Batch review. Nothing is uploaded or approved here.', wraplength=510).pack(anchor='w')
        error = tk.StringVar()
        ttk.Label(frame, textvariable=error, wraplength=510).pack(anchor='w', pady=(8, 0))
        def add():
            if not label.get().strip() or not description.get().strip():
                error.set('Enter a sample label and a short description.'); return
            values = dict(initial, localSampleId=label.get().strip(), sampleDescription=description.get().strip(),
                materialState=state.get().strip(), inventoryMode='native' if native.get() else 'records')
            window.grab_release()
            self.stage_linked_sample(values, on_done=lambda: window.destroy() if window.winfo_exists() else None,
                on_error=lambda: error.set('Your sample details are kept here. Check the connection or use Set up sample inventory, then try adding the sample again.'))
        def setup():
            window.grab_release()
            if not self.client:
                self.tabs.select(self.connection_tab)
                error.set('Connect to the LIMS first. Your sample details are kept in this window.')
                return
            error.set('Review the inventory setup, then return here and add this sample. Your entered details are kept.')
            if not self.destination and hasattr(self, 'ensure_run_selected'):
                self.ensure_run_selected(callback=self.prepare_configuration)
            else:
                self.prepare_configuration()
        actions = ttk.Frame(frame); actions.pack(side='bottom', fill='x', pady=(12, 0))
        if not native_reference:
            ttk.Button(actions, text='Set up sample inventory', command=setup).pack(side='left')
        ttk.Button(actions, text='Cancel', command=window.destroy).pack(side='right')
        ttk.Button(actions, text='Add sample to draft', command=add).pack(side='right', padx=8)
        window.bind('<Return>', lambda _: add())
        window.bind('<Escape>', lambda _: window.destroy())
        window.grab_set()

    def stage_linked_sample(self, values, on_done=None, on_error=None):
        """Prepare a sample definition locally and link its exact queued review."""
        if self.busy: return
        if values.get('inventoryMode') == 'native' and self.client and not self.destination:
            choose_run = getattr(self, 'ensure_run_selected', None)
            if choose_run:
                choose_run(callback=lambda: self.stage_linked_sample(values, on_done, on_error)); return
        lab = self.entity.get()
        context = dict(workflowVersion='2', recordType='sample', uploadMode='metadata',
            datasetId=new_id(lab, 'DS'), specimenId=new_id(lab, 'SMP'), sampleCreatedLab=lab,
            submittingLab=lab, acquisitionLab=lab, processingLab=lab)
        context.update(values)
        catalog, client = self.available_library(), self.client
        destination = dict(self.destination) if self.destination else None
        def work():
            preview = build_preview([], lab, 'sample', context, raw_only=True)
            preview = self.prepare_inventory_preview(preview, catalog, client, destination)
            problems = [issue['message'] for issue in preview['validation']['issues'] if issue['severity'] == 'error']
            if problems: raise InputError('\n'.join(problems))
            return Revision.create(preview, context['localSampleId'])
        def done(revision):
            self.batch_queue.add(revision, ())
            self.refresh_batch()
            row = next(row for row in search_samples(self.available_library()) if row['subject']['id'] == context['specimenId'])
            self.apply_sample_choice(row)
            self.status.set('Sample added and selected. Review it with this data in Batch review before sending; nothing has been uploaded.')
            if on_done: on_done()
        self.run('Preparing the linked sample for batch review…', work, done, failed=on_error)

    def add_procedure_dialog(self, initial=None):
        """Define a method without replacing the open measurement/run form."""
        if self.busy: return
        initial = dict(initial or {})
        kind = 'synthesis' if self.record_type.get() == 'synthesis' else 'computation' if self.record_type.get() == 'computation' else 'measurement'
        modality = self.modality.get()
        window = tk.Toplevel(self.root)
        window.title('Add new procedure')
        window.transient(self.root)
        window.geometry('600x570')
        window.minsize(540, 520)
        frame = ttk.Frame(window, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=f'Procedure for {kind} · {modality}. Your current data and sample selection stay open.', wraplength=550).pack(anchor='w', pady=(0, 10))
        name = tk.StringVar(value=initial.get('procedureName', ''))
        version = tk.StringVar(value=initial.get('procedureVersion', '1'))
        reference = tk.StringVar(value=initial.get('procedureReference', ''))
        for caption, variable, help_key in (('* Procedure name', name, 'procedureName'), ('* Version', version, 'procedureVersion')):
            heading = ttk.Label(frame, text=caption); heading.pack(anchor='w', pady=(4, 3))
            entry = ttk.Entry(frame, textvariable=variable); entry.pack(fill='x')
            for target in (heading, entry): Tooltip(target, FIELD_HELP[help_key])
            if variable is name: entry.focus_set()
        ttk.Label(frame, text='Instructions · or provide a document reference / attachment').pack(anchor='w', pady=(10, 3))
        instructions = tk.Text(frame, height=5, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        instructions.insert('1.0', initial.get('procedureText', ''))
        instructions.pack(fill='both', expand=True)
        Tooltip(instructions, FIELD_HELP['procedureText'])
        ttk.Label(frame, text='Document / protocol reference (optional)').pack(anchor='w', pady=(8, 3))
        ref_entry = ttk.Entry(frame, textvariable=reference); ref_entry.pack(fill='x')
        Tooltip(ref_entry, FIELD_HELP['procedureReference'])
        paths = []
        attachment_note = tk.StringVar(value='No procedure document attached.')
        def attach():
            selected = filedialog.askopenfilenames(parent=window, title='Attach procedure documents')
            if selected:
                paths[:] = selected
                attachment_note.set(', '.join(Path(path).name for path in paths))
        ttk.Button(frame, text='Attach procedure document…', command=attach).pack(anchor='w', pady=(8, 3))
        ttk.Label(frame, textvariable=attachment_note, wraplength=550).pack(anchor='w')
        status = tk.StringVar(value='This procedure will be queued for review. Nothing is uploaded or approved here.')
        ttk.Label(frame, textvariable=status, wraplength=550).pack(anchor='w', pady=(8, 0))
        def add():
            text = instructions.get('1.0', 'end-1c').strip()
            if not name.get().strip() or not version.get().strip():
                status.set('Enter a procedure name and version.'); return
            if not (text or reference.get().strip() or paths):
                status.set('Add instructions, a document reference, or an attached procedure document.'); return
            values = dict(initial, procedureName=name.get().strip(), procedureVersion=version.get().strip(),
                procedureType=kind, procedureModality=modality, procedureText=text, procedureReference=reference.get().strip())
            window.grab_release()
            self.stage_linked_procedure(values, tuple(paths),
                on_done=lambda: window.destroy() if window.winfo_exists() else None,
                on_error=lambda: status.set('The procedure details are kept here. Resolve the reported problem and try adding it again.'))
        actions = ttk.Frame(frame); actions.pack(fill='x', pady=(12, 0))
        ttk.Button(actions, text='Cancel', command=window.destroy).pack(side='right')
        ttk.Button(actions, text='Add procedure to draft', command=add).pack(side='right', padx=8)
        window.bind('<Escape>', lambda _: window.destroy())
        window.grab_set()

    def stage_linked_procedure(self, values, paths=(), on_done=None, on_error=None):
        """Queue a reusable definition, preserving the exact current data draft."""
        if self.busy: return
        lab = self.entity.get()
        context = dict(workflowVersion='2', recordType='procedure', uploadMode='originals' if paths else 'metadata',
            datasetId=new_id(lab, 'DS'), procedureId=new_id(lab, 'PRC'),
            submittingLab=lab, acquisitionLab=lab, processingLab=lab)
        context.update(values)
        context['uploadMode'] = 'originals' if paths else 'metadata'
        destination = dict(self.destination) if self.destination else None
        catalog, client = self.available_library(), self.client
        def work():
            sources = tuple(load_sources(paths, parse_tables=False)) if paths else ()
            preview = build_preview(sources, lab, 'procedure', context, raw_only=True)
            preview = self.prepare_inventory_preview(preview, catalog, client, destination)
            problems = [issue['message'] for issue in preview['validation']['issues'] if issue['severity'] == 'error']
            if problems: raise InputError('\n'.join(problems))
            return Revision.create(preview, context['procedureName']), sources
        def done(result):
            revision, sources = result
            self.batch_queue.add(revision, sources)
            self.refresh_batch()
            row = next(row for row in search_procedures(self.available_library())
                if row['procedure']['id'] == context['procedureId'] and str(row['procedure']['version']) == context['procedureVersion'])
            for key, value in procedure_context(row).items():
                if key not in self.context_vars: self.context_vars[key] = self.watched()
                self.context_vars[key].set(str(value))
            self.context_vars['methodStatus'].set('recorded')
            self.refresh_link_options(); self.refresh_workflow_requirements()
            self.status.set('Procedure added and selected. Review it with this data in Batch review before sending; nothing has been uploaded.')
            if on_done: on_done()
        self.run('Preparing the linked procedure for batch review…', work, done, failed=on_error)

    def prepare_inventory_preview(self, preview, catalog, client, destination):
        if destination:
            preview['publication_destination'] = dict(destination)
        if preview['context'].get('inventoryMode') != 'native': return preview
        if any(issue['severity'] == 'error' for issue in preview['validation']['issues']): return preview
        if not client or not destination:
            raise InputError('Connect and verify an experiment before reviewing native inventory actions. Prepare Material v2 in SciSure connection when registering a new sample.')
        from .inventory import plan_inventory
        preview['native_inventory_plan'] = plan_inventory(client, destination['group_id'], preview['traceability'],
            preview['context'], catalog, destination)
        return preview

    def browse_native_samples(self):
        if self.busy: return
        if not self.client or not self.group_id:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect to search existing LIMS inventory samples.')
            return
        client, group = self.client, self.group_id
        window = tk.Toplevel(self.root)
        window.title('LIMS inventory samples')
        window.geometry('760x540')
        frame = ttk.Frame(window, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Search existing inventory and review its link before use.', wraplength=660).pack(anchor='w')
        search = tk.StringVar()
        ttk.Entry(frame, textvariable=search).pack(fill='x', pady=10)
        listing = tk.Listbox(frame, exportselection=False, height=9)
        listing.pack(fill='both', expand=True)
        details = tk.StringVar(value='Search by sample name or identifier. Existing samples keep their LIMS identity.')
        ttk.Label(frame, textvariable=details, wraplength=660).pack(anchor='w', pady=10)
        shown = []
        def found(rows):
            if not window.winfo_exists(): return
            shown[:] = rows
            listing.delete(0, 'end')
            for row in rows: listing.insert('end', row['label'])
            details.set(f'{len(rows)} matching samples. Select one to prepare a reviewed reference.')
        def lookup():
            if self.busy: return
            if self.client is not client or self.group_id != group:
                details.set('The connection changed. Close this window and search again.'); return
            from .inventory import read_native_samples
            query = search.get().strip()
            self.run('Searching native LIMS samples…', lambda: read_native_samples(client, group, query), found)
        def use():
            if self.busy or not listing.curselection(): return
            if self.client is not client or self.group_id != group:
                details.set('The connection changed. Close this window and search again.'); return
            selected = shown[listing.curselection()[0]]
            if self.record_type.get() in ('measurement', 'synthesis'):
                window.destroy()
                self.apply_sample_choice(selected)
                return
            else:
                from .inventory import native_sample_context
                values = native_sample_context(selected, self.entity.get())
                for key, value in values.items():
                    if key not in self.context_vars: self.context_vars[key] = self.watched()
                    self.context_vars[key].set(str(value))
                self.refresh_workflow_requirements()
                self.status.set('Existing inventory sample loaded. Review and stage this reference; it will reuse the existing LIMS sample.')
            window.destroy()
        actions = ttk.Frame(frame); actions.pack(fill='x')
        ttk.Button(actions, text='Search inventory', command=lookup).pack(side='left')
        ttk.Button(actions, text='Use selected sample', command=use).pack(side='right')
        window.bind('<Return>', lambda _: lookup())

    def refresh_workflow_library(self):
        if not self.client or not self.group_id:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect to the LIMS to load saved samples and procedures. Staged records are available offline.')
            return
        client, group = self.client, self.group_id
        query = self.link_rows['sample']['search'].get() if 'sample' in self.link_rows else ''
        def work():
            catalog = load_catalog(client, group)
            native, warning = [], ''
            if 'sample' in self.link_rows:
                from .inventory import read_native_samples
                from .scisure import SciSureError
                try: native = read_native_samples(client, group, query)
                except (InputError, SciSureError) as exc: warning = str(exc)
            return catalog, native, warning
        def done(result):
            catalog, native, warning = result
            self.catalog = catalog
            self.native_sample_choices = native
            self.native_sample_scope = (client.origin, group)
            self.filter_catalog()
            self.refresh_link_options()
            self.refresh_batch()
            self.status.set(('Saved records loaded. Native sample search: ' + warning) if warning else
                'Sample choices now include saved records, queued samples and matching LIMS inventory. Select one or add a sample.')
        self.run('Searching saved records and LIMS inventory…', work, done)

    def add_linked_record(self, name):
        if self.busy: return
        if name == 'sample':
            self.add_sample_dialog()
        elif name == 'procedure':
            self.add_procedure_dialog()

    def capture_workflow_draft(self):
        return dict(kind=self.record_type.get(), modality=self.modality.get(), entity=self.entity.get(),
            context=self.workflow_values(), title=self.title.get(), sources=tuple(self.sources),
            raw_only=self.raw_only.get(), toolkit=self.toolkit.get(), parent=self.parent,
            editing_batch_id=self.editing_batch_id, source=self.source_choice.get(), sheet=self.sheet_choice.get(),
            mapping={key: getattr(self, key).get() for key in ('header_row', 'profile_name', 'profile_version', 'source_version')},
            rules=[dict(source=name, target=target.get(), unit=unit.get(), aliases=aliases.get()) for name, target, unit, aliases in self.rules])

    def restore_workflow_draft(self, draft):
        self.change_record_type(draft['kind'], confirm=False)
        self.entity.set(draft['entity']); self.modality.set(draft['modality'])
        self.context_vars = {}
        self.rebuild_context()
        for key, value in draft['context'].items():
            if key not in self.context_vars: self.context_vars[key] = self.watched()
            self.context_vars[key].set(value)
        self.raw_only.set(draft['raw_only']); self.toolkit.set(draft['toolkit'])
        self.title.set(draft['title']); self.set_sources(list(draft['sources']))
        self.parent = draft['parent']; self.editing_batch_id = draft['editing_batch_id']
        for key, value in draft['mapping'].items(): getattr(self, key).set(value)
        if draft['rules'] and draft['source'] in [s.name for s in self.sources]:
            self.source_choice.set(draft['source']); self.source_changed(); self.sheet_choice.set(draft['sheet'])
            self.read_columns({'rules': [{k: v for k, v in rule.items() if k != 'aliases'} for rule in draft['rules']]})
            for rule, (_, _, _, aliases) in zip(draft['rules'], self.rules): aliases.set(rule['aliases'])
        self.refresh_link_options(); self.refresh_workflow_requirements()

    def return_to_linked_draft(self):
        if self.busy: return
        if not self.link_return_drafts: return
        if not messagebox.askyesno('Return to data draft', 'Return without staging this new record? Its current changes will be discarded.', parent=self.root): return
        _, draft = self.link_return_drafts.pop()
        self.restore_workflow_draft(draft)
        if not self.link_return_drafts: self.return_draft_button.pack_forget()

    def stage_current_record(self):
        if self.busy: return
        self.stage_requested = True
        self.preview()

    def workflow_preview_done(self, revision):
        if self.stage_requested:
            self.stage_requested = False
            if self.editing_batch_id:
                item = self.batch_queue.replace(self.editing_batch_id, revision, tuple(self.sources))
            else:
                item = self.batch_queue.add(revision, tuple(self.sources))
            self.review_batch_id = self.editing_batch_id = item.id
            self.refresh_batch()
            self.refresh_link_options()
            if self.link_return_drafts and not any(i['severity'] == 'error' for i in revision.value()['preview']['validation']['issues']):
                name, draft = self.link_return_drafts.pop()
                trace = revision.value()['preview']['traceability']
                catalog = self.available_library()
                rows = search_samples(catalog) if name == 'sample' else search_procedures(catalog)
                identifier = trace['material']['id'] if name == 'sample' else trace['procedure']['id']
                row = next((row for row in rows if (row['subject']['id'] if name == 'sample' else row['procedure']['id']) == identifier), None)
                self.restore_workflow_draft(draft)
                if row:
                    changes = sample_context(row) if name == 'sample' else procedure_context(row)
                    for key, value in changes.items():
                        if key not in self.context_vars: self.context_vars[key] = self.watched()
                        self.context_vars[key].set(str(value))
                    if name == 'sample' and row['entry'].get('context', {}).get('inventoryMode') == 'native':
                        self.context_vars['inventoryMode'].set('native')
                    if name == 'procedure': self.context_vars['methodStatus'].set('recorded')
                if not self.link_return_drafts: self.return_draft_button.pack_forget()
                self.refresh_link_options(); self.refresh_workflow_requirements()
                self.status.set('New record staged and linked to the preserved data draft. Review all records in the batch before sending.')
            else:
                self.tabs.select(self.batch_tab)

    def refresh_batch(self):
        if hasattr(self, 'batch_panel'):
            self.batch_queue.validate(self.catalog or dict(entries=[], pending=[]))
            self.batch_panel.refresh(self.batch_queue.items)

    def inspect_batch_item(self, key):
        if self.busy or not key: return
        item = self.batch_queue.get(key)
        self.revision, self.approval = item.revision, item.approval
        self.review_batch_id = key
        self.dirty = False
        self.acknowledge.set(False)
        self.render_review(item.revision)
        if item.errors:
            issues = list(item.revision.value()['preview']['validation']['issues'])
            issues.extend(dict(severity='error', code='BATCH_DEPENDENCY', message=message) for message in item.errors)
            self.render_issues(issues)
            self.review_details.select(1)
            self.approval_status.set('Resolve the batch review and dependency errors before approving this record.')
        self.tabs.select(self.review_tab)

    def edit_batch_item(self, key):
        if self.busy or not key: return
        item = self.batch_queue.get(key)
        if item.status in ('complete', 'needs_attention'):
            self.status.set('A transfer was already attempted. Reconcile that item before changing it.')
            return
        self.batch_queue.revoke(key)
        payload = item.revision.value()
        self.load_workflow_draft(payload, item.sources, as_revision=False)
        self.parent = payload.get('parent')
        self.editing_batch_id = key
        self.review_batch_id = None
        self.approval = None
        self.invalidate()
        self.status.set('Editing a staged record. Stage it again to replace that item; its previous approval will be cleared.')

    def load_workflow_draft(self, payload, sources, as_revision=True):
        preview = payload['preview']
        self.change_record_type(preview['context']['recordType'], confirm=False)
        self.entity.set(lab_name(preview['entity']))
        self.modality.set(preview['modality'])
        self.rebuild_context()
        for key, value in preview['context'].items():
            if key not in self.context_vars: self.context_vars[key] = self.watched()
            self.context_vars[key].set(value)
        self.title.set(payload['title'])
        self.raw_only.set(preview['context'].get('uploadMode') != 'mapped')
        self.toolkit.set('toolkit_source_review' in preview)
        self.set_sources(list(sources))
        self.pending_profile = preview['normalization'].get('profile')
        if self.pending_profile and self.pending_profile.get('format') == 'catalyst-mapping/1':
            profile = self.pending_profile
            self.profile_name.set(profile['name']); self.profile_version.set(str(profile['version']))
            self.source_version.set(profile['source_version']); self.header_row.set(str(profile['header_row']))
            sha = preview['normalization']['source_artifact_sha256']
            index = next((i for i, s in enumerate(sources) if s.artifact['sha256'] == sha), -1)
            if index >= 0:
                self.source_choice.current(index); self.source_changed()
                self.sheet_choice.set(profile['sheet']); self.read_columns(profile)
        self.parent = payload['id'] if as_revision else payload.get('parent')
        if as_revision: self.context_vars['datasetId'].set(new_id(self.context_vars['acquisitionLab'].get() or self.entity.get(), 'DS'))
        if self.record_type.get() == 'procedure': self.procedure_purpose_changed()
        self.refresh_link_options()
        self.refresh_workflow_requirements()

    def remove_batch_item(self, key):
        if self.busy: return
        if key:
            self.batch_queue.remove(key)
            self.refresh_batch()
            self.refresh_link_options()

    def move_batch_item(self, key, offset):
        if self.busy: return
        if key:
            ids = [item.id for item in self.batch_queue.items]
            self.batch_queue.move(key, max(0, min(len(ids) - 1, ids.index(key) + offset)))
            self.refresh_batch()

    def send_batch(self):
        if self.busy: return
        if not self.publisher or not self.destination:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect and verify the destination experiment before sending the reviewed batch.')
            return
        approved = [item for item in self.batch_queue.items if item.approval and item.status != 'complete']
        if not approved:
            self.status.set('Review and approve each record you want to send first.')
            return
        runs = list(dict.fromkeys((item.revision.value()['preview'].get('publication_destination') or self.destination)['experiment_name'] for item in approved))
        if not messagebox.askyesno('Send reviewed batch', f'Send {len(approved)} approved records, in the displayed order, to their reviewed runs?\n\n' + '\n'.join(runs) + '\n\nProcedures and samples must precede records that link to them. Sending stops if a record fails.', parent=self.root): return
        publisher = self.publisher
        def work():
            catalog = load_catalog(publisher.client, publisher.destination['group_id'])
            return self.batch_queue.publish(publisher, catalog, progress=lambda text: self.messages.put(('progress', text)),
                publisher_factory=self.publisher_for_revision)
        def done(_):
            self.refresh_batch()
            self.status.set('Approved batch records transferred and verified in the LIMS.')
        self.run('Sending approved batch records…', work, done, failed=self.refresh_batch)

    def choose_batch_files(self):
        if self.busy: return
        if self.record_type.get() not in ('measurement', 'computation'): return
        paths = filedialog.askopenfilenames(parent=self.root, title='Batch: one measurement record per selected file')
        if not paths: return
        if len(paths) > 50:
            self.show_action_error('Select up to 50 files per batch.', target='files'); return
        if not self.raw_only.get() or self.toolkit.get():
            messagebox.showinfo('Batch files', 'Batch file import preserves originals. For mapped tables or toolkit bundles, review and stage each file group with its mapping.', parent=self.root); return
        if not messagebox.askyesno('Stage batch files', f'Create {len(paths)} separate draft records using the currently selected sample, method, date and operator?\n\nEach file becomes one record. Review or edit every record in Batch review before approving. Nothing is uploaded now.', parent=self.root): return
        context, entity, modality, kind = self.workflow_values(), self.entity.get(), self.modality.get(), self.record_type.get()
        catalog, client, destination = self.available_library(), self.client, dict(self.destination) if self.destination else None
        def work():
            records = []
            total = 0
            for path in paths:
                source = Source.from_path(path, parse=False)
                total += len(source.content)
                if total > 200 * 1024 * 1024: raise InputError('A batch is limited to 200 MiB in memory.')
                c = dict(context, datasetId=new_id(entity, 'DS'), uploadMode='originals')
                if kind == 'computation':
                    # Each file is a separate calculation on the same explicitly described model.
                    c['specimenId'] = context['specimenId']
                preview = build_preview([source], entity, modality, c, raw_only=True)
                preview = self.prepare_inventory_preview(preview, catalog, client, destination)
                records.append((Revision.create(preview, Path(source.name).stem), (source,)))
            return records
        def done(records):
            # Validate the complete proposed queue before replacing its state.
            candidate = BatchQueue()
            for item in self.batch_queue.items: candidate.add(item.revision, item.sources, item.approval)
            for revision, sources in records: candidate.add(revision, sources)
            for revision, sources in records: self.batch_queue.add(revision, sources)
            self.refresh_batch()
            self.tabs.select(self.batch_tab)
            self.status.set(f'{len(records)} files staged as separate records. Inspect links and values before approving.')
        self.run('Preparing batch previews in memory…', work, done)

    def browse_native_procedures(self):
        if not self.client or not self.group_id:
            self.tabs.select(self.connection_tab); self.status.set('Connect to browse published SciSure protocols.'); return
        client, group = self.client, self.group_id
        self.run('Reading published SciSure protocol versions…', lambda: load_native_procedures(client, group), self.show_native_procedures)

    def show_native_procedures(self, rows):
        client, group = self.client, self.group_id
        window = tk.Toplevel(self.root)
        window.title('Published SciSure procedures')
        window.geometry('720x440')
        frame = ttk.Frame(window, padding=20); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Find a published protocol to register as a reusable method.', wraplength=650).pack(anchor='w')
        query = tk.StringVar()
        ttk.Entry(frame, textvariable=query).pack(fill='x', pady=10)
        listing = tk.Listbox(frame, exportselection=False, height=10)
        listing.pack(fill='both', expand=True)
        shown = []
        def filter_rows(*_):
            shown[:] = [row for row in rows if query.get().casefold() in row['label'].casefold()]
            listing.delete(0, 'end')
            for row in shown: listing.insert('end', row['label'])
        query.trace_add('write', filter_rows); filter_rows()
        ttk.Label(frame, text='Use a protocol version for this record’s technique. Its reusable reference is queued for review without changing your current data draft.', wraplength=650).pack(anchor='w', pady=10)
        def use():
            if not listing.curselection(): return
            if self.busy: return
            if self.client is not client or self.group_id != group:
                self.status.set('The connection changed. Search this server’s procedures again.'); return
            row = shown[listing.curselection()[0]]
            kind = 'synthesis' if self.record_type.get() == 'synthesis' else 'computation' if self.record_type.get() == 'computation' else 'measurement'
            modality = self.modality.get()
            values = native_procedure_context(row, kind, modality, self.entity.get())
            existing = next((r for r in search_procedures(self.available_library(), kind=kind, modality=modality)
                if r['procedure'].get('reference') == values['procedureReference']), None)
            if existing:
                for key, value in procedure_context(existing).items():
                    if key not in self.context_vars: self.context_vars[key] = self.watched()
                    self.context_vars[key].set(str(value))
                self.context_vars['methodStatus'].set('recorded')
                self.refresh_link_options(); self.refresh_workflow_requirements()
                self.status.set('Linked the procedure version already registered in the library.')
                window.destroy()
                return
            self.stage_linked_procedure(values, on_done=lambda: window.destroy() if window.winfo_exists() else None)
        ttk.Button(frame, text='Prepare procedure draft', command=use).pack(anchor='e')
