"""Review and retrieve supporting documents on the selected native sample."""
import tkinter as tk
import uuid
from tkinter import ttk, filedialog

from .exports import save_bytes, suggested_filename
from .model import load_sources
from .sample_documents import plan_documents, sample_documents, download_document, SampleDocumentTransfer
from .inventory import _sample, supporting_document_field


class SampleDocumentsUI:
    def _document_connection_current(self, client, group):
        if self.client is not client or self.group_id != group:
            self.status.set('The connection changed. Close this document window and refresh Samples before continuing.')
            return False
        return True

    def _document_sample(self, row):
        native = row.get('native') or (row.get('native_row') or {}).get('native')
        if not self.client or not self.group_id:
            self.tabs.select(self.connection_tab)
            self.status.set('Connect to SciSure to use sample documents.')
            return None
        if not native or native.get('tenant') != self.client.origin or native.get('group_id') != self.group_id:
            self.status.set('Choose a sample saved in this SciSure inventory first. Refresh Samples after saving a new sample.')
            return None
        return native['sample_id']

    def attach_sample_documents(self, row):
        if self.busy: return
        sid = self._document_sample(row)
        if sid is None: return
        paths = filedialog.askopenfilenames(parent=self.root, title='Choose supporting documents for this sample')
        if not paths: return
        client, group = self.client, self.group_id
        def work():
            sources = tuple(load_sources(paths, parse_tables=False))
            sample = _sample(client, sid, group)
            return sample, sources
        self.run('Preparing documents for this sample…', work,
            lambda result: self._review_sample_documents(client, group, sid, *result))

    def _review_sample_documents(self, client, group, sid, sample, sources):
        if not self._document_connection_current(client, group): return
        window = tk.Toplevel(self.root)
        window.title('Sample documents · ' + (sample.get('name') or str(sid)))
        window.geometry('720x500')
        body = ttk.Frame(window, padding=22); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Attach supporting documents to ' + (sample.get('name') or str(sid)), style='Sub.TLabel', wraplength=650).pack(anchor='w')
        ttk.Label(body, text='These files form a new document group directly on this sample. Existing documents are retained. No experiment or method is required. Use Add measurement for results that need a run and method.', wraplength=640).pack(anchor='w', pady=10)
        summary, text = self.text_panel(body, height=10); text.configure(wrap='word', font=('Segoe UI', 10)); summary.pack(fill='both', expand=True)
        self.show_text(text, '\n'.join(f'{source.name} · {len(source.content):,} bytes' for source in sources))
        # Keep one review identity for this entire window, including retries.
        state = {'plan': None, 'plan_id': uuid.uuid4().hex}
        status = tk.StringVar(value='Review the destination and documents before sending.')
        ttk.Label(body, textvariable=status, wraplength=640).pack(anchor='w', pady=10)
        actions = ttk.Frame(body); actions.pack(fill='x')
        def reviewed(plan):
            if not window.winfo_exists() or not self._document_connection_current(client, group): return
            state['plan'] = plan
            self.show_text(text, 'Sample: ' + plan['sample_name'] + '\nServer: ' + plan['tenant'] + '\n\nNew document group on this sample\n\n' +
                '\n'.join(f'{source.name} · {len(source.content):,} bytes' for source in sources) + '\n\nExisting documents and sample-type settings are retained. Each new file is downloaded and checksum-checked before its sample link is created.')
            status.set('Review ready. Send documents adds exactly these files to the sample.')
            send.state(['!disabled'])
        def review():
            if self.busy or not self._document_connection_current(client, group): return
            send.state(['disabled']); state['plan'] = None
            self.run('Preparing sample-document review…', lambda: plan_documents(client, group, sid, sources, plan_id=state['plan_id']), reviewed)
        def transmit():
            if self.busy or not self._document_connection_current(client, group): return
            plan = state['plan']
            if not plan: return
            if not hasattr(self, 'sample_document_operations'): self.sample_document_operations = {}
            transfer = SampleDocumentTransfer(client, self.sample_document_operations)
            def done(receipt):
                self.status.set(f'{len(receipt["files"])} document(s) saved and verified on sample {receipt["sample_name"]}.')
                if window.winfo_exists(): window.destroy()
                self.browse_sample_documents(row={'native': dict(tenant=client.origin, group_id=group, sample_id=sid)})
            self.run('Sending reviewed sample documents…', lambda: transfer.apply(plan, sources,
                lambda value: self.messages.put(('progress', value))), done)
        ttk.Button(actions, text='Review documents', command=review).pack(side='left')
        send = ttk.Button(actions, text='Send documents', style='Primary.TButton', command=transmit); send.pack(side='right'); send.state(['disabled'])

    def browse_sample_documents(self, row):
        if self.busy: return
        sid = self._document_sample(row)
        if sid is None: return
        client, group = self.client, self.group_id
        def show(result):
            if not self._document_connection_current(client, group): return
            window = tk.Toplevel(self.root); window.title('Sample documents'); window.geometry('760x470')
            body = ttk.Frame(window, padding=20); body.pack(fill='both', expand=True)
            ttk.Label(body, text=result['sample'].get('name') or str(sid), style='Sub.TLabel').pack(anchor='w')
            ttk.Label(body, text='Documents attached directly to this sample.', wraplength=680).pack(anchor='w', pady=8)
            listing = self.scrolling_table(body, ('name', 'field'))
            listing.configure(selectmode='browse')
            listing.heading('name', text='Document'); listing.heading('field', text='Document group')
            listing.column('name', width=430); listing.column('field', width=200)
            groups = {}
            for i, doc in enumerate(result['documents']):
                if supporting_document_field(dict(key=doc['field'], sampleDataType='FILE')):
                    groups.setdefault(doc['field'], 'Supporting documents ' + str(len(groups) + 1))
                listing.insert('', 'end', iid=str(i), values=(doc['name'], groups.get(doc['field'], doc['field'])))
            if not result['documents']: ttk.Label(body, text='No documents are attached yet. Use Attach sample documents to add files.').pack(anchor='w', pady=10)
            def download():
                if self.busy or not self._document_connection_current(client, group): return
                selection = listing.selection()
                if not selection: return
                doc = result['documents'][int(selection[0])]
                path = filedialog.asksaveasfilename(parent=window, title='Download sample document', initialfile=suggested_filename(doc['name']))
                if not path: return
                def work():
                    data = download_document(client, group, doc); save_bytes(path, data); return len(data)
                self.run('Downloading and checking sample document…', work, lambda size: self.status.set(f'Saved {size:,} bytes to {path}.'))
            ttk.Button(body, text='Download selected document…', command=download).pack(anchor='e', pady=(12, 0))
        self.run('Reading sample document links…', lambda: sample_documents(client, group, sid), show)
