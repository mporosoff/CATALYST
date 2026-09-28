"""Human-readable validation with navigation back to the owning draft field."""
import tkinter as tk
from tkinter import ttk


def issue_action(issue):
    """Return a stable destination and button label without interpreting server text."""
    code = issue.get('code', '')
    for prefix in ('CONTEXT_INVALID_', 'CONTEXT_', 'ID_LAB_', 'ID_', 'LAB_'):
        if code.startswith(prefix):
            key = code[len(prefix):]
            if key in ('specimenId', 'sampleSourceRevisionId', 'sampleSourceRevisionSha256'):
                return 'sample', 'Choose or add sample'
            if key in ('methodId', 'methodVersion', 'procedureId', 'procedureVersion', 'procedureSourceRevisionId', 'procedureSourceRevisionSha256'):
                return 'procedure', 'Choose procedure / method'
            return key, 'Go to field'
    if code in ('DATE_INVALID',): return 'acquiredAt', 'Correct date'
    if code in ('PROCEDURE_CONTENT',): return 'procedureText', 'Add procedure instructions'
    if code.startswith(('PROCEDURE_', 'METHOD_')): return 'procedure', 'Review procedure / method'
    if code.startswith(('NATIVE_SAMPLE', 'SAMPLE_', 'SELF_PARENT', 'MODEL_SAMPLE')):
        return 'sample', 'Review sample'
    if code.startswith(('INVENTORY_', 'DESTINATION_')): return 'connection', 'Choose run / connection'
    if any(word in code for word in ('MAPPING', 'AXIS_UNIT', 'PROFILE', 'COLUMN', 'TARGET', 'UNIT')):
        return 'mapping', 'Review column mapping'
    if any(word in code for word in ('SOURCE', 'FILE', 'ARTIFACT', 'EMPTY')):
        return 'files', 'Review original files'
    return 'draft', 'Return to this record'


class IssueUI:
    def show_action_error(self, message, target=None):
        """Keep failures recoverable even when a preview could not be prepared."""
        window = tk.Toplevel(self.root)
        window.title('CATALYST · action needs attention')
        window.transient(self.root)
        body = ttk.Frame(window, padding=24); body.pack(fill='both', expand=True)
        ttk.Label(body, text='This action needs attention', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(body, text=message, wraplength=570, justify='left').pack(anchor='w', pady=14)
        lower = message.casefold()
        if target in ('mapping', 'files', 'review', 'connection'):
            label = {'mapping': 'Review column mapping', 'files': 'Review original files',
                'review': 'Return to review', 'connection': 'Go to connection'}[target]
            def action():
                window.destroy()
                if target == 'review': self.tabs.select(self.review_tab); return
                if target == 'connection': self.tabs.select(self.connection_tab); return
                self.tabs.select(self.import_tab)
                if target == 'mapping':
                    self.raw_only.set(False)
                    for name in ('mapping_disclosure', 'mapping_section'):
                        section = getattr(self, name, None)
                        if hasattr(section, 'set_open'): section.set_open(True)
                widget = getattr(self, 'mapping_section' if target == 'mapping' else 'originals_card', None)
                if widget is not None:
                    self.root.update_idletasks()
                    canvas, body = self.import_tab.canvas, self.import_tab.body
                    canvas.yview_moveto(max(0, widget.winfo_rooty() - body.winfo_rooty() - 35) / max(1, body.winfo_height()))
                else: self.import_tab.canvas.yview_moveto(0)
        elif any(word in lower for word in ('material v2', 'material configuration', 'inventory schema')):
            label = 'Set up sample inventory'
            def action():
                window.destroy()
                self.tabs.select(self.connection_tab)
                if self.client and self.group_id: self.prepare_configuration()
        elif any(word in lower for word in ('token', 'server', 'connection', 'connect', 'http 401', 'http 403', 'permission', 'group')):
            label = 'Go to connection'
            def action():
                window.destroy(); self.tabs.select(self.connection_tab); self.connection_tab.canvas.yview_moveto(0)
        elif any(word in lower for word in ('unconfirmed', 'unknown', 'check scisure', 'check this record', 'reconcile', 'check its saved')):
            label = 'Open SciSure to check transfer'
            def action():
                window.destroy()
                if self.client:
                    import webbrowser
                    webbrowser.open(self.client.origin)
        else:
            label = 'Return to record'
            def action():
                window.destroy()
                if self.review_batch_id: self.edit_batch_item(self.review_batch_id)
                self.tabs.select(self.import_tab)
                self.import_tab.canvas.yview_moveto(0)
        actions = ttk.Frame(body); actions.pack(fill='x')
        ttk.Button(actions, text=label, command=action, style='Primary.TButton').pack(side='left')
        ttk.Button(actions, text='Close', command=window.destroy).pack(side='right')
        window.update_idletasks()
        window.geometry(f'620x{max(220, body.winfo_reqheight())}')

    def build_issue_panel(self, parent):
        frame = ttk.Frame(parent, padding=10)
        self.issue_heading = ttk.Label(frame, text='Select an item to see how to resolve it.', wraplength=650)
        self.issue_heading.pack(anchor='w', pady=(0, 8))
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        self.issue_list = ttk.Treeview(table, columns=('severity', 'message'), show='headings', selectmode='browse', height=5)
        self.issue_list.heading('severity', text='Status')
        self.issue_list.heading('message', text='What needs attention')
        self.issue_list.column('severity', width=95, stretch=False)
        self.issue_list.column('message', width=520, minwidth=230)
        bar = ttk.Scrollbar(table, orient='vertical', command=self.issue_list.yview)
        self.issue_list.configure(yscrollcommand=bar.set)
        self.issue_list.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        detail = ttk.Frame(frame)
        self.issue_text = tk.Text(detail, height=2, wrap='word', font=('Segoe UI', 10),
            relief='flat', background='white', padx=0, pady=4, state='disabled')
        self.issue_text.pack(side='left', fill='both', expand=True)
        detail_bar = ttk.Scrollbar(detail, orient='vertical', command=self.issue_text.yview)
        detail_bar.pack(side='right', fill='y')
        self.issue_text.configure(yscrollcommand=detail_bar.set)
        detail.pack(fill='x', pady=8)
        self.fix_issue_button = ttk.Button(frame, text='Return to this record', style='Primary.TButton', command=self.resolve_selected_issue)
        # Give correction controls space before the table at small window sizes.
        detail.pack_configure(side='bottom', before=table)
        self.fix_issue_button.pack(side='bottom', anchor='w', before=detail)
        self.issue_list.bind('<<TreeviewSelect>>', lambda _: self.select_issue())
        self.issue_list.bind('<Double-1>', lambda _: self.resolve_selected_issue())
        self.issue_list.bind('<Return>', lambda _: self.resolve_selected_issue())
        self.validation_issues = []
        return frame

    def render_issues(self, issues):
        self.validation_issues = list(issues)
        self.issue_list.delete(*self.issue_list.get_children())
        for i, issue in enumerate(issues):
            severity = 'Needs a change' if issue['severity'] == 'error' else 'Check this' if issue['severity'] == 'warning' else 'Information'
            self.issue_list.insert('', 'end', iid=str(i), values=(severity, issue['message']))
        if issues:
            index = next((i for i, value in enumerate(issues) if value['severity'] == 'error'), 0)
            self.issue_list.selection_set(str(index))
            self.issue_list.focus(str(index))
            self.select_issue()
        else:
            self.show_text(self.issue_text, 'No changes needed. You can review and approve this record.')
            self.fix_issue_button.state(['disabled'])
        errors = sum(value['severity'] == 'error' for value in issues)
        self.issue_heading.configure(text=f'{errors} item(s) must be resolved before approval. Select one and use the button below.' if errors else 'Review any information below before approving.')

    def select_issue(self):
        selection = self.issue_list.selection()
        if not selection: return
        issue = self.validation_issues[int(selection[0])]
        _, label = issue_action(issue)
        self.show_text(self.issue_text, issue['message'] + '\n\n' + label + ', make the change, then build a new preview. Your other draft information is retained.')
        self.fix_issue_button.configure(text=label)
        self.fix_issue_button.state(['!disabled'])

    def resolve_selected_issue(self):
        if self.busy: return
        selection = self.issue_list.selection()
        if not selection: return
        issue = self.validation_issues[int(selection[0])]
        target, label = issue_action(issue)
        if self.review_batch_id:
            self.edit_batch_item(self.review_batch_id)
            if self.review_batch_id: return  # An attempted transfer cannot be edited.
        if target == 'connection':
            self.tabs.select(self.connection_tab)
            self.connection_tab.canvas.yview_moveto(0)
            self.status.set(issue['message'])
            return
        self.tabs.select(self.import_tab)
        widget = None
        if target in ('sample', 'procedure'):
            picker = getattr(self, 'link_widgets', {}).get(target)
            # Workflow pickers retain their search variable and visible combobox.
            if isinstance(picker, dict): widget = picker.get('choice') or picker.get('combo')
            elif isinstance(picker, (tuple, list)):
                widget = next((w for w in picker if isinstance(w, ttk.Combobox)), None)
            elif hasattr(picker, 'focus_set'): widget = picker
            if widget is None:
                widget = getattr(self, 'context_widgets', {}).get('localSampleId' if target == 'sample' else 'procedureText')
        else:
            widget = getattr(self, 'context_widgets', {}).get(target)
        if target == 'mapping':
            self.raw_only.set(False)
            for name in ('mapping_disclosure', 'mapping_section'):
                section = getattr(self, name, None)
                if hasattr(section, 'set_open'): section.set_open(True)
            widget = getattr(self, 'mapping_section', None) or getattr(self, 'source_choice', None)
        elif target == 'files': widget = getattr(self, 'originals_card', None)
        if widget is not None:
            # Open optional field groups before scrolling into them.
            optional = getattr(self, 'optional_context', None)
            if optional and target in getattr(self, 'context_fields', {}): optional.set_open(True)
            self.root.update_idletasks()
            canvas, body = self.import_tab.canvas, self.import_tab.body
            y = max(0, widget.winfo_rooty() - body.winfo_rooty() - 45)
            canvas.yview_moveto(y / max(1, body.winfo_height()))
            widget.focus_set()
        else:
            self.import_tab.canvas.yview_moveto(0)
        self.status.set(label + '. ' + issue['message'] + ' Rebuild the preview after correcting it.')
