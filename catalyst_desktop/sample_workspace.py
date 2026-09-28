"""Sample-centred discovery without attributing unrelated experiment files to samples."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from .catalog import load_catalog
from .design import Card, page_heading
from .exports import review_archive, save_bytes, suggested_filename
from .inventory import read_native_samples
from .library import search_samples
from .model import InputError, Source
from catalyst_ingest.readers import coordinate_parts, col_name
from .publication import read_review
from .scisure import SciSureError, remote_id


def merged_samples(catalog, native_rows=(), query=''):
    """Merge only explicit identities, never matching by an ambiguous display name."""
    saved = [dict(row, native_row=None) for row in search_samples(catalog)]
    for native in native_rows:
        info, sample = native['native'], native['sample']
        matches = []
        for row in saved:
            if (row.get('entry') or {}).get('trace', {}).get('model'): continue
            context = (row.get('entry') or {}).get('context', {})
            pinned = (str(context.get('nativeSampleId', '')) == str(info['sample_id'])
                and context.get('nativeSampleTenant') == info['tenant'])
            canonical = sample.get('altID') == row['subject']['id']
            if pinned or canonical: matches.append(row)
        if len(matches) > 1:
            raise InputError('An inventory sample has conflicting CATALYST identities. Reconcile its registrations before reuse.')
        if matches:
            if matches[0]['native_row'] is not None:
                raise InputError('More than one inventory sample uses the same CATALYST identity. Reconcile the duplicate before reuse.')
            matches[0]['native_row'] = native
        else:
            saved.append(dict(native_row=native, entry=None, aliases=set(), datasets=set(), origin_lab='',
                subject=dict(id=f"SciSure {info['sample_id']}", description=sample.get('description', '')),
                label=sample.get('name') or f"SciSure {info['sample_id']}"))
    terms = query.strip().casefold().split()
    result = []
    for row in saved:
        native, entry = row.get('native_row'), row.get('entry')
        row['display_name'] = ((entry or {}).get('context', {}).get('localSampleId')
            or (native or {}).get('sample', {}).get('name') or row['subject']['id'])
        row['source_label'] = ('LIMS + CATALYST' if entry and native else 'LIMS inventory' if native else 'CATALYST records')
        searchable = ' '.join([row['label'], row['display_name'], row['subject']['id'],
            row['subject'].get('description', ''), row.get('origin_lab', ''), *row.get('aliases', ()),
            str((native or {}).get('sample', {}).get('altID') or '')]).casefold()
        if all(term in searchable for term in terms): result.append(row)
    return sorted(result, key=lambda row: (row['display_name'].casefold(), row['subject']['id']))


def sample_records(catalog, row):
    """Only declared sample subjects or explicit computational relationships qualify."""
    if not row.get('entry'): return []
    sid, found, seen = row['subject']['id'], [], set()
    for entry in (catalog or {}).get('entries', []):
        trace, context = entry['trace'], entry.get('context', {})
        dataset = trace.get('dataset') or {}
        direct = sid in (dataset.get('subject_id'), (trace.get('material') or {}).get('id'),
            (trace.get('sample_ref') or {}).get('id'), (trace.get('model') or {}).get('id'))
        related = sid in (trace.get('model') or {}).get('related_sample_ids', [])
        if not (direct or related): continue
        key = (entry.get('revision_id'), entry.get('section_id'))
        if key in seen: continue
        seen.add(key)
        found.append(dict(entry=entry, kind=trace.get('record_type') or dataset.get('kind', 'record'),
            technique=dataset.get('modality', ''), date=dataset.get('acquired_at') or context.get('acquiredAt', ''),
            label=context.get('runId') or context.get('localSampleId') or dataset.get('id', ''),
            relationship='Related model' if related and not direct else 'Sample record'))
    return sorted(found, key=lambda item: (item['date'], item['label']), reverse=True)


def native_history(client, group_id, native_row):
    """Read SciSure's exact sample references, then verify ownership and membership."""
    info = native_row['native']
    if info['tenant'] != client.origin or info['group_id'] != group_id:
        raise InputError('The selected sample belongs to another connection. Refresh Samples.')
    sid = remote_id(info['sample_id'])
    client.assert_group(group_id)
    sections = client.list(f'/api/v1/samples/{sid}/experiments/sections')
    if len(sections) > 250:
        raise InputError('This sample has more than 250 experiment links. Open SciSure for its full history; no partial history is shown.')
    results, destinations, seen = [], {}, set()
    for item in sections:
        eid, section_id = remote_id((item.get('experiment') or {}).get('experimentID')), remote_id(item.get('expJournalID'))
        if section_id in seen: raise SciSureError('SciSure returned a duplicate sample history section. Refresh Samples.')
        seen.add(section_id)
        if item.get('sectionType') not in ('SAMPLESIN', 'SAMPLESOUT', 'BASESAMPLES', 'OUTPUTSAMPLES'): continue
        if eid not in destinations:
            destinations[eid] = client.destination(eid, group_id, writable=False)
        owned = [section for section in client.list(f'/api/v1/experiments/{eid}/sections')
            if section.get('expJournalID') == section_id and section.get('deleted') is False
            and section.get('sectionType') == item['sectionType']]
        if len(owned) != 1: raise SciSureError('A sample history section changed. Refresh Samples.')
        members = client.list(f'/api/v1/experiments/sections/{section_id}/samples')
        if not any(member.get('sampleID') == sid and member.get('archived') is False for member in members):
            raise SciSureError('The sample is no longer linked to this experiment section. Refresh Samples.')
        results.append(dict(destination=destinations[eid], section_id=section_id,
            heading=item.get('sectionHeader') or 'Sample link', date=item.get('created') or '',
            relationship='Generated sample' if item['sectionType'] in ('SAMPLESOUT', 'OUTPUTSAMPLES') else 'Used sample'))
    client.assert_group(group_id)
    return results


def record_details(loaded, catalog=None):
    """Readable record and file summary; the verified package retains full metadata."""
    payload = loaded['revision'].value()
    preview, trace = payload['preview'], payload['preview'].get('traceability', {})
    context, dataset = preview.get('context', {}), trace.get('dataset', {})
    method = dataset.get('method') or {}
    method_name = next((entry['trace']['procedure'].get('name') for entry in (catalog or {}).get('entries', [])
        if entry['trace'].get('procedure', {}).get('id') == method.get('id')
        and str(entry['trace']['procedure'].get('version')) == str(method.get('version'))), None)
    lines = [payload.get('title') or 'Saved record', '',
        'Record: ' + str(trace.get('record_type') or dataset.get('kind') or 'record'),
        'Technique: ' + str(preview.get('modality', '')),
        'Sample / model: ' + str(dataset.get('subject_id') or context.get('specimenId') or 'Not applicable'),
        'Date performed: ' + str(dataset.get('acquired_at') or 'Not recorded'),
        'Researcher: ' + str(dataset.get('acquired_by') or 'Not recorded'),
        'Procedure: ' + (str(method_name or method.get('id')) + ' · version ' + str(method.get('version')) if method.get('id') else 'Not recorded'),
        'Experiment: ' + str(loaded.get('destination', {}).get('experiment_name') or loaded.get('destination', {}).get('experiment_id', '')),
        'Transfer: ' + str(loaded.get('state', 'unknown')), '', 'Original files']
    for artifact in preview.get('artifacts', []):
        lines.append('• ' + artifact['filename'] + ' (' + f"{artifact['size_bytes']:,}" + ' bytes)')
    if not preview.get('artifacts'): lines.append('No original files; this is a metadata record.')
    notes = context.get('notes') or dataset.get('notes')
    if notes: lines.extend(['', 'Notes', str(notes)])
    description = (trace.get('material') or trace.get('model') or {}).get('description')
    if description: lines.extend(['', 'Description', str(description)])
    lines.extend(['', 'Download the review package for verified originals and the complete provenance record.'])
    return '\n'.join(lines)


def standardized_view(preview, limit=1000):
    """Bound display work while retaining exact saved values and quantity units."""
    limit = min(limit, 1000)
    rows = preview.get('standardized', {}).get('rows', [])
    flat = []
    for row in rows[:limit]:
        # Identity/checksum columns remain in the downloaded provenance; keep the
        # on-screen table focused on measured values and their source row.
        values = {key: value for key, value in row.items() if key not in (
            'quantities', 'canonical_dataset_id', 'canonical_subject_id', 'source_artifact_sha256')}
        for quantity in row.get('quantities', []):
            column = quantity['field'] + ' [' + quantity['unit'] + ']'
            if column in values:
                raise InputError('The stored values contain conflicting column names. Download the complete record to inspect them without losing information.')
            values[column] = quantity['value_decimal']
        flat.append(values)
    columns = list(dict.fromkeys(key for row in flat for key in row))
    if rows:
        message = f'Showing {len(flat):,} of {len(rows):,} standardized rows. Values and units are shown as saved; download the record for the complete dataset.'
    elif preview.get('data_status') == 'original_files_only':
        message = 'This upload preserved original files without creating a standardized table. Open Original tables to preview supported files, or download the verified originals.'
    else:
        message = 'This record contains descriptive information, with no standardized measurement rows. See Record details and any attached original files.'
    signal_unit = preview.get('context', {}).get('signalUnit')
    labels = [('signal [' + signal_unit + ']') if column == 'signal' and signal_unit else
        'Source row' if column == 'source_row' else column for column in columns]
    return dict(columns=labels, rows=[[row.get(column) for column in columns] for row in flat],
        total_rows=len(rows), message=message)


def original_table_view(source, sheet_name, limit=1000):
    """Show source coordinates and lexical numbers; never infer units or run formulas."""
    limit = min(limit, 1000)
    sheets = source.artifact.get('sheets', {})
    if sheet_name not in sheets:
        raise InputError('Choose a worksheet from this original file.')
    by_row, width = {}, 0
    for address, cell in sheets[sheet_name]['cells'].items():
        row, column = coordinate_parts(address)
        width = max(width, column)
        value = cell.get('formula') if 'formula' in cell else cell.get('value')
        if 'formula' not in cell and cell.get('lexical_value') is not None and cell.get('source_type') in ('n', 'int', 'float'):
            value = cell['lexical_value']
        by_row.setdefault(row, {})[column] = value
    selected = sorted(by_row)[:limit]
    return dict(columns=['Source row'] + [col_name(index) for index in range(1, width + 1)],
        rows=[[row] + [by_row[row].get(column) for column in range(1, width + 1)] for row in selected],
        total_rows=len(by_row), message=f'{source.name} · {sheet_name}: showing {len(selected):,} of {len(by_row):,} populated source rows. '
            'Column letters and row numbers refer to the original cells. Formulas are displayed as text and never calculated; source values have not been normalized.')


def verified_original_table(client, entry, revision_sha256, source_index):
    """Preview only originals verified against the exact record currently open."""
    loaded = read_review(client, entry['destination'], entry['section_id'], True)
    if loaded['revision'].sha256 != revision_sha256:
        raise InputError('The record changed after it was opened. Open the record again before previewing its files.')
    if type(source_index) is not int or not 0 <= source_index < len(loaded['sources']):
        raise InputError('Choose an original file from the open record.')
    source = loaded['sources'][source_index]
    if Path(source.name).suffix.lower() not in ('.csv', '.xlsx', '.json'):
        raise InputError('This file type has no inline table preview. Download record + originals to open it in its usual application.')
    return Source.from_bytes(source.name, source.content)


class SampleWorkspace:
    def build_sample_workspace(self):
        p = self.catalog_tab
        page_heading(p, 'Samples & data', 'Find a sample, follow its research history, or add the next measurement.', 'WORKSPACE  /  SAMPLES')
        card = Card(p, padding=18)
        card.pack(fill='both', expand=True)
        p = card.body
        actions = ttk.Frame(p); actions.pack(fill='x', pady=(0, 10))
        self.catalog_query = tk.StringVar()
        search = ttk.Entry(actions, textvariable=self.catalog_query)
        search.pack(side='left', fill='x', expand=True)
        search.bind('<Return>', lambda _: self.refresh_catalog())
        ttk.Button(actions, text='Search / refresh', command=self.refresh_catalog).pack(side='left', padx=(8, 0))
        ttk.Button(actions, text='New sample', command=lambda: self.change_record_type('sample')).pack(side='left', padx=(6, 0))
        self.catalog_table = self.scrolling_table(p, ('name', 'source', 'id', 'datasets'))
        for key, label, width in [('name', 'Sample / model', 190), ('source', 'Stored in', 145), ('id', 'Identity', 240), ('datasets', 'Records', 70)]:
            self.catalog_table.heading(key, text=label)
            self.catalog_table.column(key, width=width, stretch=key == 'name')
        self.catalog_table.bind('<<TreeviewSelect>>', lambda _: self.sample_selection_changed())
        self.catalog_table.bind('<Double-1>', lambda _: self.open_sample_workspace())
        self.sample_selection_summary = tk.StringVar(value='Select a sample to see its records and available actions.')
        ttk.Label(p, textvariable=self.sample_selection_summary, wraplength=610, style='Muted.TLabel').pack(fill='x', pady=(8, 5))
        buttons = ttk.Frame(p); buttons.pack(fill='x')
        for label, command in [('Open sample / data', self.open_sample_workspace),
                ('Add measurement', lambda: self.start_selected_sample('measurement')),
                ('Record synthesis', lambda: self.start_selected_sample('synthesis'))]:
            ttk.Button(buttons, text=label, command=command).pack(side='left', padx=(0, 6))
        more = ttk.Menubutton(buttons, text='More ▾'); menu = tk.Menu(more, tearoff=False)
        menu.add_command(label='Register new sample', command=lambda: self.change_record_type('sample'))
        menu.add_command(label='Use sample / model', command=lambda: self.use_catalog_subject(False))
        menu.add_command(label='Create derivative', command=lambda: self.use_catalog_subject(True))
        menu.add_command(label='Repeat synthesis', command=self.repeat_catalog_procedure)
        more.configure(menu=menu); more.pack(side='left')
        self.catalog_status = tk.StringVar(value='Connect, then search by sample label, identity or description. Results cover records visible in the active group.')
        ttk.Label(p, textvariable=self.catalog_status, wraplength=610, style='Muted.TLabel').pack(fill='x', pady=(10, 0))
        self.native_workspace_rows = []
        self.sample_workspace_scope = None
        self.sample_detail_window = None

    def refresh_sample_workspace(self):
        if self.busy: return
        if not self.client or self.group_id is None:
            self.tabs.select(self.connection_tab); self.status.set('Connect to SciSure before searching samples.'); return
        client, group, query = self.client, self.group_id, self.catalog_query.get().strip()
        def work():
            notices, catalog, native = [], None, []
            try: catalog = load_catalog(client, group, lambda text: self.messages.put(('progress', text)))
            except (InputError, SciSureError) as exc: notices.append('CATALYST records unavailable: ' + str(exc))
            try: native = read_native_samples(client, group, query)
            except (InputError, SciSureError) as exc: notices.append('Native inventory unavailable: ' + str(exc))
            client.assert_group(group)
            return catalog, native, notices
        def done(result):
            if self.client is not client or self.group_id != group: return
            self.catalog, self.native_workspace_rows, notices = result
            self.sample_workspace_scope = (client.origin, group)
            self.native_sample_choices = self.native_workspace_rows
            self.native_sample_scope = self.sample_workspace_scope
            self.filter_catalog()
            if self.adaptive: self.refresh_link_options(); self.refresh_batch()
            count = len((self.catalog or {}).get('entries', []))
            self.catalog_status.set(f'{len(self.catalog_rows)} matching samples / models. {count} completed CATALYST reviews indexed across accessible experiments; '
                f'{len(self.native_workspace_rows)} matching native samples. Active group {group}; permissions limit coverage. '
                + ' '.join(notices))
        self.run('Finding sample records and native inventory…', work, done)

    def filter_sample_workspace(self):
        scope = (self.client.origin, self.group_id) if self.client else None
        native = self.native_workspace_rows if scope and self.sample_workspace_scope == scope else []
        self.catalog_rows = merged_samples(self.catalog, native, self.catalog_query.get())
        self.catalog_table.delete(*self.catalog_table.get_children())
        for index, row in enumerate(self.catalog_rows):
            self.catalog_table.insert('', 'end', iid=str(index), values=(row['display_name'], row['source_label'],
                row['subject']['id'], len(sample_records(self.catalog, row))))
        self.sample_selection_changed()

    def selected_sample_row(self):
        selected = self.catalog_table.selection()
        return self.catalog_rows[int(selected[0])] if selected else None

    def sample_selection_changed(self):
        row = self.selected_sample_row()
        self.sample_selection_summary.set((row['display_name'] + ' · ' + str(len(sample_records(self.catalog, row)))
            + ' linked CATALYST records. Open sample / data for files and native experiment links.') if row else
            'Select a sample to see its records and available actions.')

    def start_selected_sample(self, kind):
        row = self.selected_sample_row()
        if self.busy or not row: return
        if (row.get('entry') or {}).get('trace', {}).get('model'):
            self.status.set('This is a computational model. Open its data to inspect or reuse the saved calculation.'); return
        previous = self.context_vars
        self.change_record_type(kind)
        if self.record_type.get() != kind or self.context_vars is previous: return
        self.apply_sample_choice(row.get('native_row') or row)
        self.tabs.select(self.import_tab)

    def open_sample_workspace(self):
        row = self.selected_sample_row()
        if self.busy or not row: return
        if self.sample_detail_window is not None and self.sample_detail_window.winfo_exists(): self.sample_detail_window.destroy()
        window = self.sample_detail_window = tk.Toplevel(self.root)
        window.title('CATALYST · ' + row['display_name']); window.geometry('940x680'); window.minsize(760, 640)
        body = ttk.Frame(window, padding=18); body.pack(fill='both', expand=True)
        ttk.Label(body, text=row['display_name'][:180], style='Sub.TLabel', wraplength=850).pack(anchor='w')
        description = row['subject'].get('description') or row['subject']['id']
        summary = description if len(description) <= 200 else description[:197] + '…'
        ttk.Label(body, text=summary, wraplength=850).pack(anchor='w', pady=(5, 12))
        compact = ttk.Style(self.root)
        compact.configure('SampleData.Treeview', rowheight=26)
        compact.configure('SampleData.Treeview.Heading', padding=(8, 4))
        notebook = ttk.Notebook(body); notebook.pack(fill='both', expand=True)
        records_page = ttk.Frame(notebook, padding=10); native_page = ttk.Frame(notebook, padding=10)
        notebook.add(records_page, text='Sample records & files'); notebook.add(native_page, text='Linked experiments')
        records = sample_records(self.catalog, row)
        table = self.scrolling_table(records_page, ('type', 'technique', 'date', 'run', 'relation'))
        table.configure(height=3, style='SampleData.Treeview')
        table.master.pack_configure(expand=False)
        for key, text, width in [('type', 'Record', 100), ('technique', 'Technique', 100), ('date', 'Date', 100), ('run', 'Experiment', 200), ('relation', 'Relationship', 110)]:
            table.heading(key, text=text); table.column(key, width=width, stretch=key == 'run')
        for i, item in enumerate(records):
            table.insert('', 'end', iid=str(i), values=(item['kind'], item['technique'], item['date'],
                item['entry']['destination'].get('experiment_name') or item['entry']['destination']['experiment_id'], item['relationship']))
        actions = ttk.Frame(records_page); actions.pack(fill='x', pady=(7, 7))
        showing = tk.StringVar(value='Select a record and open it to view data. Only explicitly linked records appear here.' if records else
            'No CATALYST records are linked to this sample yet. Native experiment links and sample documents may still be available.')
        ttk.Label(records_page, textvariable=showing, wraplength=800).pack(anchor='w', pady=(0, 7))
        viewer = ttk.Notebook(records_page); viewer.pack(fill='both', expand=True)
        values_page = ttk.Frame(viewer, padding=8)
        originals_page = ttk.Frame(viewer, padding=8)
        details_page = ttk.Frame(viewer)
        viewer.add(values_page, text='Standardized values')
        viewer.add(originals_page, text='Original tables')
        viewer.add(details_page, text='Record details')
        data_note = tk.StringVar(value='Open a record to display its saved standardized values.')
        ttk.Label(values_page, textvariable=data_note, wraplength=760).pack(anchor='w', pady=(0, 6))
        values_table = self.scrolling_table(values_page, ())
        values_table.configure(height=6, style='SampleData.Treeview')
        original_controls = ttk.Frame(originals_page); original_controls.pack(fill='x')
        ttk.Label(original_controls, text='File').pack(side='left')
        original_choice = ttk.Combobox(original_controls, state='readonly', height=8)
        original_choice.pack(side='left', fill='x', expand=True, padx=6)
        original_note = tk.StringVar(value='Open a record to choose an original CSV, XLSX or flat-record JSON file. Other formats are available in the verified download.')
        ttk.Label(originals_page, textvariable=original_note, wraplength=760).pack(anchor='w', pady=6)
        sheet_controls = ttk.Frame(originals_page); sheet_controls.pack(fill='x', pady=(0, 6))
        ttk.Label(sheet_controls, text='Worksheet').pack(side='left')
        sheet_choice = ttk.Combobox(sheet_controls, state='readonly', height=8)
        sheet_choice.pack(side='left', fill='x', expand=True, padx=6)
        original_table = self.scrolling_table(originals_page, ())
        original_table.configure(height=6, style='SampleData.Treeview')
        detail, detail_text = self.text_panel(details_page, height=8)
        detail.pack(fill='both', expand=True)
        self.show_text(detail_text, 'Open a record to see its method, dates, notes and original-file list.')
        opened = dict(entry=None, loaded=None, source=None)
        def render_table(widget, view):
            widget.delete(*widget.get_children())
            widget.configure(columns=[str(index) for index in range(len(view['columns']))])
            for index, label in enumerate(view['columns']):
                widget.heading(str(index), text=label)
                widget.column(str(index), width=155, minwidth=70, stretch=False)
            for values in view['rows']:
                widget.insert('', 'end', values=['—' if value is None else str(value) for value in values])
        def clear_original(*_):
            opened['source'] = None
            sheet_choice.configure(values=[]); sheet_choice.set('')
            render_table(original_table, dict(columns=[], rows=[]))
            loaded = opened['loaded']
            artifacts = loaded['revision'].value()['preview']['artifacts'] if loaded else []
            index = original_choice.current()
            if not 0 <= index < len(artifacts):
                original_note.set('No original files are attached to this metadata record.'); return
            name = artifacts[index]['filename']
            original_note.set('Preview original table to read verified source cells. CSV, XLSX and flat-record JSON are supported.'
                if Path(name).suffix.lower() in ('.csv', '.xlsx', '.json') else
                f'{name} has no inline table preview. Use Download record + originals to open it in its usual application.')
        original_choice.bind('<<ComboboxSelected>>', clear_original)
        def show_sheet(*_):
            if not opened['source'] or not sheet_choice.get(): return
            view = original_table_view(opened['source'], sheet_choice.get())
            render_table(original_table, view); original_note.set(view['message'])
        sheet_choice.bind('<<ComboboxSelected>>', show_sheet)
        def preview_original():
            index = original_choice.current()
            loaded = opened['loaded']
            if self.busy or not self.client or not loaded or index < 0: return
            artifact = loaded['revision'].value()['preview']['artifacts'][index]
            if Path(artifact['filename']).suffix.lower() not in ('.csv', '.xlsx', '.json'):
                clear_original(); return
            client, entry = self.client, dict(opened['entry'])
            sha256 = loaded['revision'].sha256
            def done(source):
                if not window.winfo_exists(): return
                opened['source'] = source
                names = list(source.artifact['sheets'])
                sheet_choice.configure(values=names)
                if names: sheet_choice.current(0); show_sheet()
                else: original_note.set('This original contains no previewable worksheets. Download the file to inspect it.')
            self.run('Reading checksum-verified original tables…', lambda: verified_original_table(client, entry, sha256, index), done)
        ttk.Button(original_controls, text='Preview original table', command=preview_original).pack(side='left')
        def selected_record():
            selected = table.selection()
            return records[int(selected[0])]['entry'] if selected else None
        def open_record():
            entry = selected_record()
            if not entry or self.busy or not self.client: return
            client = self.client
            def done(loaded):
                if not window.winfo_exists(): return
                opened.update(entry=entry, loaded=loaded, source=None)
                payload = loaded['revision'].value(); preview = payload['preview']
                showing.set('Showing: ' + payload['title'] + ' · ' + loaded['destination']['experiment_name'])
                self.show_text(detail_text, record_details(loaded, self.catalog))
                view = standardized_view(preview)
                render_table(values_table, view); data_note.set(view['message'])
                names = [artifact['filename'] for artifact in preview['artifacts']]
                original_choice.configure(values=names); original_choice.set('')
                if names: original_choice.current(0)
                clear_original()
                viewer.select(values_page if view['rows'] else originals_page if names else details_page)
            self.run('Opening the selected sample record…', lambda: read_review(client, entry['destination'], entry['section_id']), done)
        def download_record():
            entry = selected_record()
            if not entry or self.busy or not self.client: return
            path = filedialog.asksaveasfilename(parent=window, title='Download sample record and originals',
                initialfile=suggested_filename('CATALYST-' + entry['revision_id'] + '.zip'), defaultextension='.zip', filetypes=[('Review package', '*.zip')])
            if not path: return
            client = self.client
            def work(): save_bytes(path, review_archive(read_review(client, entry['destination'], entry['section_id'], True)))
            self.run('Downloading verified originals and record…', work, lambda _: self.status.set('Sample record downloaded to ' + path))
        table.bind('<Double-1>', lambda _: open_record())
        ttk.Button(actions, text='Open record details', command=open_record).pack(side='left')
        ttk.Button(actions, text='Download record + originals…', command=download_record).pack(side='left', padx=6)
        native = row.get('native_row')
        if native:
            docs = ttk.Frame(body); docs.pack(fill='x', pady=(10, 0))
            ttk.Button(docs, text='Sample documents…', command=lambda: self.browse_sample_documents(native)).pack(side='left')
            ttk.Button(docs, text='Attach sample documents…', command=lambda: self.attach_sample_documents(native)).pack(side='left', padx=6)
        linked = []
        links = self.scrolling_table(native_page, ('experiment', 'relationship', 'section'))
        for key, text, width in [('experiment', 'Experiment', 240), ('relationship', 'Sample relationship', 145), ('section', 'Section', 240)]:
            links.heading(key, text=text); links.column(key, width=width, stretch=key != 'relationship')
        coverage = tk.StringVar(value='Load native history to see where this sample was used or generated.' if native else
            'This sample has no verified native inventory match in the current results. Its CATALYST records remain available on the first tab.')
        ttk.Label(native_page, textvariable=coverage, wraplength=800).pack(fill='x', pady=10)
        def load_links():
            if not native or self.busy or not self.client: return
            client, group = self.client, self.group_id
            def done(rows):
                if not window.winfo_exists(): return
                linked[:] = rows; links.delete(*links.get_children())
                for i, item in enumerate(rows): links.insert('', 'end', iid=str(i), values=(item['destination']['experiment_name'], item['relationship'], item['heading']))
                coverage.set(f'{len(rows)} verified sample links visible in active group {group}. Experiment attachments may include other samples; no file ownership is inferred.')
            self.run('Reading this sample’s native experiment links…', lambda: native_history(client, group, native), done)
        def open_experiment_files():
            selected = links.selection()
            if self.busy or not selected: return
            destination = linked[int(selected[0])]['destination']
            index = next((i for i, item in enumerate(self.experiments) if item['experimentID'] == destination['experiment_id']), None)
            if index is None: self.status.set('Reconnect to refresh the experiment list before browsing these attachments.'); return
            self.experiment.current(index)
            self.tabs.select(self.history_tab); self.library_sections.select(self.library_files)
            self.browse_files()
            self.status.set('Showing all attachments in this linked experiment; they may include other samples.')
            window.destroy()
        actions = ttk.Frame(native_page); actions.pack(fill='x')
        ttk.Button(actions, text='Load linked experiments', command=load_links, state='normal' if native else 'disabled').pack(side='left')
        ttk.Button(actions, text='Browse experiment attachments…', command=open_experiment_files).pack(side='left', padx=6)
        if native:
            if not records: notebook.select(native_page)
            load_links()
