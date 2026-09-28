"""Native, reusable batch table; callbacks own review dialogs and transfer work."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .design import Tooltip


STATUS_LABELS = {
    'needs_review': 'Review needed', 'approved': 'Approved', 'blocked': 'Needs changes',
    'sending': 'Sending…', 'complete': 'Saved & verified', 'needs_attention': 'Check SciSure',
}


class BatchPanel(ttk.Frame):
    """Callbacks inspect/edit/remove/approve/move_up/move_down receive item IDs.

    ``send`` receives no arguments. Double-click and Return inspect the selected
    record; no table gesture can approve or publish a record.
    """

    def __init__(self, parent, callbacks):
        super().__init__(parent, padding=16)
        self.callbacks = dict(callbacks)
        self._items = {}
        self._busy = False
        self._buttons = {}
        ttk.Label(self, text='Batch review', style='Section.TLabel').pack(anchor='w')
        ttk.Label(self, text='Stage files together, check each record, then send the approved records in order.',
            wraplength=660).pack(anchor='w', pady=(5, 10))
        self.summary = tk.StringVar(value='No records staged.')
        ttk.Label(self, textvariable=self.summary).pack(anchor='w', pady=(0, 8))
        table = ttk.Frame(self)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('position', 'title', 'type', 'files', 'sample', 'method', 'run', 'state'),
            show='headings', selectmode='browse', height=8)
        for name, label, width, stretch in (
                ('position', '#', 34, False), ('title', 'Record', 230, True),
                ('type', 'Data type', 115, True), ('files', 'Files', 150, True),
                ('sample', 'Sample', 155, True), ('method', 'Procedure / version', 165, True), ('run', 'Run / experiment', 180, True),
                ('state', 'Review status', 120, True)):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=width if not stretch else 70, stretch=stretch)
        bar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        bar.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', self._selection_changed)
        self.tree.bind('<Double-1>', lambda _: self._invoke('inspect'))
        self.tree.bind('<Return>', lambda _: self._invoke('inspect'))
        self.detail = tk.StringVar(value='Select a record to inspect its original files, sample and procedure links, and review issues.')
        ttk.Label(self, textvariable=self.detail, wraplength=680, justify='left').pack(fill='x', pady=(9, 6))
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(3, 0))
        for position, (key, label, hint) in enumerate((
                ('inspect', 'Review selected', 'Inspect this exact record and its original files before approval.'),
                ('edit', 'Edit', 'Return this record to the form. Saving changes clears its approval.'),
                ('approve', 'Approve…', 'Approve only the selected revision after reviewing its details.'),
                ('remove', 'Remove', 'Remove an unsent record from the memory-only queue.'),
                ('move_up', '↑', 'Move a record earlier. Referenced samples and procedures must come first.'),
                ('move_down', '↓', 'Move a record later; invalid dependencies must be resolved before sending.'))):
            if key in self.callbacks:
                button = ttk.Button(actions, text=label, width=3 if key.startswith('move_') else None,
                    command=lambda action=key: self._invoke(action))
                button.grid(row=position // 3, column=position % 3, sticky='w', padx=(0, 5), pady=(0, 5))
                Tooltip(button, hint)
                self._buttons[key] = button
        if 'send' in self.callbacks:
            button = ttk.Button(self, text='Send approved records', command=lambda: self._invoke('send'))
            button.pack(anchor='e', pady=(12, 0))
            Tooltip(button, 'Send approved records in queue order. Transfer stops at the first failure; saved records are never resent automatically.')
            self._buttons['send'] = button
        self._update_actions()

    def selected_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def select(self, identifier):
        if identifier in self._items:
            self.tree.selection_set(identifier)
            self.tree.focus(identifier)
            self.tree.see(identifier)
            self._selection_changed()

    def refresh(self, items):
        selected = self.selected_id()
        items = tuple(items)
        self._items = {item.id: item for item in items}
        self.tree.delete(*self.tree.get_children())
        sample_names, method_names = {}, {}
        for item in items:
            preview = item.revision.value()['preview']
            context, trace = preview.get('context', {}), preview.get('traceability', {})
            if trace.get('material'): sample_names[trace['material']['id']] = context.get('localSampleId') or trace['material']['id']
            if trace.get('procedure'):
                p = trace['procedure']; method_names[(p['id'], p['version'])] = p['name']
        for position, item in enumerate(items, 1):
            preview = item.revision.value()['preview']
            context, trace = preview.get('context', {}), preview.get('traceability', {})
            dataset = trace.get('dataset', {})
            sample = context.get('localSampleId') or sample_names.get(dataset.get('subject_id')) or dataset.get('subject_id', '')
            method = dataset.get('method') or trace.get('procedure') or {}
            method_label = method_names.get((method.get('id'), method.get('version'))) or method.get('name') or method.get('id', '')
            if method_label: method_label += ' · v' + str(method.get('version', ''))
            if context.get('methodStatus') == 'not-recorded': method_label = 'Not recorded'
            destination = preview.get('publication_destination') or (item.receipt or {}).get('destination') or {}
            run = destination.get('experiment_name') or context.get('runId') or 'Choose run before sending'
            record_type = preview.get('record_type') or preview.get('context', {}).get('recordType')
            kind = (record_type or preview.get('modality', '')).replace('_', ' ').capitalize()
            self.tree.insert('', 'end', iid=item.id, values=(position, item.title, kind,
                ', '.join(s.name for s in item.sources) or 'Details only', sample, method_label, run, STATUS_LABELS.get(item.status, item.status)))
        approved = sum(item.status == 'approved' for item in items)
        completed = sum(item.status == 'complete' for item in items)
        size = sum(len(source.content) for item in items for source in item.sources) / (1024 * 1024)
        self.summary.set(f'{len(items)} records · {approved} approved · {completed} saved · {size:.1f} MiB of originals'
            if items else 'No records staged. Add files from the submission form.')
        if selected in self._items:
            self.select(selected)
        elif items:
            self.select(items[0].id)
        else:
            self._selection_changed()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_actions()

    def _selection_changed(self, _=None):
        item = self._items.get(self.selected_id())
        if item:
            preview = item.revision.value()['preview']
            trace = preview.get('traceability', {})
            context = preview.get('context', {})
            sample = (trace.get('sample_ref') or trace.get('material') or trace.get('model') or {}).get('id')
            method = trace.get('dataset', {}).get('method') or trace.get('procedure') or {}
            lines = ['Files: ' + ', '.join(source.name for source in item.sources)]
            if sample:
                lines.append('Sample / model: ' + (context.get('localSampleId') or sample))
            if method.get('id'):
                lines.append('Procedure: ' + method.get('name', method['id']) + ' · v' + str(method.get('version', '')))
            lines.extend(item.errors[:2])
            self.detail.set('\n'.join(lines))
        else:
            self.detail.set('Select a record to inspect its original files, sample and procedure links, and review issues.')
        self._update_actions()

    def _update_actions(self):
        item = self._items.get(self.selected_id())
        locked = bool(item and item.status in ('sending', 'complete', 'needs_attention'))
        for key, button in self._buttons.items():
            if key == 'send':
                enabled = any(row.status == 'approved' for row in self._items.values())
                enabled = enabled and not any(row.status == 'needs_attention' for row in self._items.values())
            else:
                enabled = bool(item) and (key == 'inspect' or not locked)
                if key == 'approve':
                    enabled = enabled and item.status == 'needs_review'
            button.configure(state='normal' if enabled and not self._busy else 'disabled')

    def _invoke(self, action):
        button = self._buttons.get(action)
        if button is None or str(button.cget('state')) == 'disabled':
            return
        if action == 'send':
            self.callbacks[action]()
        else:
            identifier = self.selected_id()
            if identifier:
                self.callbacks[action](identifier)
