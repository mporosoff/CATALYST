"""CATALYST desktop app (version 1): samples first, one form per task, IDs made for you.

Screens
  Samples      searchable list of every sample in the consortium (home screen)
  Sample page  everything saved for one sample: its recipe, data from every lab, shipping log
  New sample   pick the shared procedure, adjust what you did differently, save → ID is generated
  Upload data  pick sample + technique + date, add files, preview, save
  Procedures   shared master recipes and their versions
  Export       tidy dataset for analysis / AI, plus the live read-only AI connection
  Settings     your profile, SciSure connection, coordinator tools
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import queue
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import __version__, ids, records
from .design import apply_theme, Card, Tooltip, icon, PAPER, INK, MUTED, GREEN, LINE
from .settings import Settings
from .store import Store, StoreError, FileItem, check_files
from .widgets import DateEntry, SearchCombo, ScrollFrame, ComponentsEditor, text_box, text_value, set_text
from .scisure import SciSureClient, SciSureError, tenant_origin, token_value

ALL = 'All'
RED = '#9B2C2C'
FONT = 'Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI'


def readable_note(saved):
    if not saved.get('readable_warning'):
        return ''
    return ('\n\nThe record is saved, but its readable copy in SciSure could not be written '
        f"({saved['readable_warning']}). Settings → Coordinator tools → “Write readable copies” adds it later.")


def friendly(error):
    if isinstance(error, (StoreError, SciSureError, ids.IdError, records.RecordError)):
        return str(error)
    if isinstance(error, OSError):
        return f'The file could not be read or written: {error}'
    return f'Something unexpected went wrong ({type(error).__name__}: {error}).'


def human_size(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'bytes' else f'{n:.1f} {unit}'
        n /= 1024


def unique_path(folder, name):
    target = Path(folder) / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = Path(folder) / f'{stem} ({n}){suffix}'
        n += 1
    return target


class App:
    def __init__(self, root, settings=None, smoke=False):
        self.root, self.smoke = root, smoke
        self.settings = settings or Settings()
        self.store = None
        self.connection = None
        self.samples, self.procedures = [], []
        self.procedure_cache = {}
        self.current = None           # opened sample (dict from Store.open_sample)
        self._queue = queue.Queue()
        self._busy = 0
        root.title('CATALYST')
        root.geometry('1280x820')
        root.minsize(980, 640)
        root.configure(background=PAPER)
        apply_theme(root)
        style = ttk.Style(root)
        style.configure('Title.TLabel', background=PAPER, foreground=INK, font=(FONT, 22, 'bold'))
        style.configure('Subtitle.TLabel', background=PAPER, foreground=MUTED, font=(FONT, 11))
        style.configure('Field.TLabel', background='white', foreground=INK, font=(FONT, 9, 'bold'))
        style.configure('Hint.TLabel', background='white', foreground=MUTED, font=(FONT, 9))
        style.configure('Value.TLabel', background='white', foreground=INK, font=(FONT, 10))
        style.configure('Problem.TLabel', background='white', foreground=RED, font=(FONT, 10))
        style.configure('ID.TLabel', background='white', foreground=GREEN, font=(FONT, 13, 'bold'))
        style.configure('Status.TLabel', background=PAPER, foreground=MUTED, font=(FONT, 9))
        style.configure('Chip.TLabel', background='#E7EFE8', foreground=GREEN, font=(FONT, 9, 'bold'), padding=(10, 4))
        style.configure('Warn.TLabel', background='#FFF6E5', foreground='#7A4B00', font=(FONT, 10), padding=(12, 8))
        self._build_shell()
        self.pages = {}
        for name, cls in (('samples', SamplesPage), ('sample', SamplePage), ('new_sample', SampleForm),
                ('upload', UploadForm), ('procedures', ProceduresPage), ('procedure_form', ProcedureForm),
                ('export', ExportPage), ('settings', SettingsPage), ('help', HelpPage)):
            self.pages[name] = cls(self.content, self)
        root.after(100, self._poll)
        root.protocol('WM_DELETE_WINDOW', self.close)
        if not smoke:
            root.after(1500, self.check_for_update)
        if not self.settings.has_profile():
            self.show('settings')
            self.pages['settings'].welcome()
        else:
            self.show('samples')
            if not smoke:
                self.auto_connect()

    # ------------------------------------------------------------------ shell
    def _build_shell(self):
        self.sidebar = ttk.Frame(self.root, style='Sidebar.TFrame', width=220)
        self.sidebar.pack(side='left', fill='y')
        self.sidebar.pack_propagate(False)
        ttk.Label(self.sidebar, text='CATALYST', style='Brand.TLabel').pack(anchor='w', padx=20, pady=(24, 0))
        ttk.Label(self.sidebar, text='Consortium catalyst data', style='Side.TLabel').pack(anchor='w', padx=20, pady=(0, 22))
        self.nav = {}
        self._icons = {}
        for key, label, glyph in (('samples', 'Samples', 'samples'), ('new_sample', 'New sample', 'library'),
                ('upload', 'Upload data', 'upload'), ('procedures', 'Procedures', 'procedure'),
                ('export', 'Export & AI', 'export'), ('settings', 'Settings', 'connection'), ('help', 'Help', 'help')):
            self._icons[key] = icon(self.root, glyph, color='#C4D7C9', size=18)
            button = ttk.Button(self.sidebar, text='  ' + label, image=self._icons[key], compound='left',
                style='Nav.TButton', command=lambda k=key: self.navigate(k))
            button.pack(fill='x', padx=12, pady=2)
            self.nav[key] = button
        self.version_label = ttk.Label(self.sidebar, text=f'Version {__version__}', style='SideCaption.TLabel')
        self.version_label.pack(side='bottom', anchor='w', padx=20, pady=(0, 14))
        self.update_button = ttk.Button(self.sidebar, text='', style='Primary.TButton',
            command=lambda: webbrowser.open(self.update_url))
        self.update_url = None
        self.profile_label = ttk.Label(self.sidebar, style='Side.TLabel', wraplength=190, justify='left')
        self.profile_label.pack(side='bottom', anchor='w', padx=20, pady=(0, 18))
        self.connection_label = ttk.Label(self.sidebar, style='Side.TLabel', wraplength=190, justify='left')
        self.connection_label.pack(side='bottom', anchor='w', padx=20, pady=(0, 6))
        main = ttk.Frame(self.root, style='Page.TFrame')
        main.pack(side='left', fill='both', expand=True)
        self.content = ttk.Frame(main, style='Page.TFrame')
        self.content.pack(fill='both', expand=True, padx=28, pady=(22, 6))
        status = ttk.Frame(main, style='Page.TFrame')
        status.pack(fill='x', padx=28, pady=(0, 10))
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=120)
        self.status = ttk.Label(status, text='', style='Status.TLabel')
        self.status.pack(side='left')
        self.update_identity()

    def update_identity(self):
        p = self.settings.profile
        self.profile_label.configure(text=(f"{p['name']} ({p['initials']})\n{ids.lab_name(p['lab'])}"
            if self.settings.has_profile() else 'No profile yet'))
        if self.connection:
            text = '● Connected to SciSure\n' + self.connection['group_name']
            if not self.store.workspace_ready():
                text += '\nCATALYST workspace not found'
        else:
            text = '○ Not connected'
        self.connection_label.configure(text=text)

    def show(self, name, **kwargs):
        for key, page in self.pages.items():
            page.frame.pack_forget()
        page = self.pages[name]
        page.frame.pack(fill='both', expand=True)
        for key, button in self.nav.items():
            active = key == name or (name == 'sample' and key == 'samples') or (name == 'procedure_form' and key == 'procedures')
            button.configure(style='SelectedNav.TButton' if active else 'Nav.TButton')
        page.shown(**kwargs)
        self.page = name

    def navigate(self, name):
        if name == 'upload':
            self.show('upload', sample=self.current['sample'] if self.page == 'sample' and self.current else None)
        else:
            self.show(name)

    def set_status(self, text):
        self.status.configure(text=text)

    # ------------------------------------------------------------------ background work
    def run(self, message, work, done=None, failed=None):
        """Run ``work(progress)`` off the UI thread; ``done(result)`` runs back on the UI thread."""
        self._busy += 1
        self.set_status(message)
        self.progress.pack(side='right')
        self.progress.start(12)
        def progress(text):
            self._queue.put(('progress', text, None))
        def target():
            try:
                result = work(progress)
            except Exception as error:  # reported to the user below
                traceback.print_exc()
                self._queue.put(('error', error, failed))
            else:
                self._queue.put(('done', result, done))
        if self.smoke:
            target(); self._drain()
        else:
            threading.Thread(target=target, daemon=True).start()

    def _drain(self):
        while True:
            try:
                kind, value, callback = self._queue.get_nowait()
            except queue.Empty:
                return
            if kind == 'progress':
                self.set_status(value)
                continue
            self._busy = max(0, self._busy - 1)
            if not self._busy:
                self.progress.stop()
                self.progress.pack_forget()
            if kind == 'done':
                self.set_status('')
                if callback: callback(value)
            else:
                self.set_status('')
                if callback:
                    callback(value)
                else:
                    self.error(value)

    def _poll(self):
        try:
            self._drain()
        finally:
            self.root.after(100, self._poll)

    def error(self, error, title='CATALYST'):
        message = friendly(error) if isinstance(error, Exception) else str(error)
        if self.smoke:
            self.last_error = message
            return
        messagebox.showerror(title, message, parent=self.root)

    def info(self, message, title='CATALYST'):
        if self.smoke:
            self.last_info = message
            return
        messagebox.showinfo(title, message, parent=self.root)

    # ------------------------------------------------------------------ updates
    def check_for_update(self, quiet=True):
        from .updates import latest_release, is_newer, DOWNLOAD_PAGE
        def work(_progress):
            return latest_release()
        def done(release):
            if release and is_newer(release['version'], __version__):
                self.update_url = release['url']
                self.update_button.configure(text=f"Update to {release['version']} ↗")
                self.update_button.pack(side='bottom', fill='x', padx=12, pady=(0, 10), before=self.version_label)
                self.set_status(f"CATALYST {release['version']} is available. Click “Update” in the sidebar to download it.")
            elif not quiet:
                self.info(f'You have the latest version ({__version__}).' if release else
                    'Could not reach GitHub to check for updates. Open the download page instead:\n' + DOWNLOAD_PAGE)
        # A failed check never interrupts work.
        self.run('Checking for updates…' if not quiet else '', work, done, failed=lambda _e: None)

    # ------------------------------------------------------------------ connection
    def auto_connect(self):
        try:
            from .credentials import load_token
            token = load_token(self.settings.server)
        except Exception:
            token = None
        if token:
            self.connect(self.settings.server, token, remember=False)
        else:
            self.pages['samples'].render()

    def connect(self, server, token, remember=True, then=None):
        try:
            server, token = tenant_origin(server), token_value(token)
        except SciSureError as error:
            return self.error(error)
        def work(progress):
            store = Store(SciSureClient(token, server), __version__)
            progress('Connecting to SciSure…')
            connection = store.connect()
            return store, connection
        def done(result):
            self.store, self.connection = result
            self.settings.set_server(server, remember)
            if remember:
                try:
                    from .credentials import save_token
                    save_token(server, token)
                except Exception:
                    self.set_status('Connected. The token could not be saved on this computer; enter it again next time.')
            self.update_identity()
            self.refresh()
            if then: then()
        self.run('Connecting to SciSure…', work, done)

    def disconnect(self, forget=False):
        if forget:
            try:
                from .credentials import forget_token
                forget_token(self.settings.server)
            except Exception:
                pass
        self.store, self.connection, self.samples, self.procedures = None, None, [], []
        self.procedure_cache.clear()
        self.update_identity()
        self.pages['samples'].render()

    def connected(self, quiet=False):
        if self.store and self.store.workspace_ready():
            return True
        if not quiet:
            if not self.store:
                self.error('Connect to SciSure first (Settings → Connection).')
            else:
                self.error('The shared CATALYST workspace was not found for this account. Ask the coordinator to share the '
                    '"CATALYST" project with your lab account.')
        return False

    def refresh(self, then=None):
        if not self.connected(quiet=True):
            self.pages['samples'].render()
            return
        def work(progress):
            progress('Loading samples…')
            samples = self.store.list_samples()
            progress('Loading procedures…')
            return samples, self.store.list_procedures()
        def done(result):
            self.samples, self.procedures = result
            self.procedure_cache.clear()
            for page in self.pages.values():
                page.data_changed()
            if then: then()
        self.run('Loading samples…', work, done)

    # ------------------------------------------------------------------ shared actions
    def find_sample(self, key):
        key = str(key)
        return next((s for s in self.samples if str(s['experiment_id']) == key), None) or \
            next((s for s in self.samples if s['id'] == key), None)

    def open_sample(self, key):
        sample = self.find_sample(key)
        if not sample or not self.connected():
            return
        sample_id = sample['id']
        self.settings.remember_sample(sample_id)
        def done(opened):
            self.current = opened
            self.show('sample')
        self.run(f'Opening {sample_id}…', lambda progress: self.store.open_sample(sample, progress), done)

    def load_procedure(self, procedure_id, done):
        """Latest version of a procedure (cached for this session)."""
        if procedure_id in self.procedure_cache:
            return done(self.procedure_cache[procedure_id])
        procedure = next((p for p in self.procedures if p['id'] == procedure_id), None)
        if not procedure:
            return done(None)
        def finished(versions):
            value = dict(procedure, versions=versions)
            self.procedure_cache[procedure_id] = value
            done(value)
        self.run(f'Loading {procedure_id}…', lambda progress: self.store.procedure_versions(procedure), finished)

    def download_files(self, section_id, file_rows, manifest):
        folder = filedialog.askdirectory(parent=self.root, title='Choose where to save the files') if not self.smoke else None
        if not folder:
            return
        checksums = {f['name']: f['sha256'] for f in manifest or []}
        def work(progress):
            saved = []
            for index, row in enumerate(file_rows, 1):
                progress(f'Downloading {row.get("realName")} ({index} of {len(file_rows)})…')
                content = self.store.download(section_id, row, checksums.get(row.get('realName')))
                target = unique_path(folder, row.get('realName') or f'file-{index}')
                target.write_bytes(content)
                saved.append(target)
            return saved
        self.run('Downloading…', work, lambda saved: self.info(f'Saved {len(saved)} file(s) to {folder}. Each file was '
            'checked against its original checksum.'))

    def sample_choices(self):
        recent = [s for s in self.settings.recent_samples if any(x['id'] == s for x in self.samples)]
        order = recent + [s['id'] for s in self.samples if s['id'] not in recent]
        by_id = {s['id']: s for s in self.samples}
        return [(sid, f"{sid}  ·  {by_id[sid]['composition']}") for sid in order]

    def close(self):
        for page in self.pages.values():
            page.save_draft()
        self.root.destroy()


# ============================================================================ page base
class Page:
    def __init__(self, parent, app):
        self.app = app
        self.frame = ttk.Frame(parent, style='Page.TFrame')
        self.build()

    def build(self):
        pass

    def shown(self, **kwargs):
        pass

    def data_changed(self):
        pass

    def save_draft(self):
        pass

    def heading(self, parent, title, subtitle=''):
        head = ttk.Frame(parent, style='Page.TFrame')
        head.pack(fill='x', pady=(0, 14))
        actions = ttk.Frame(head, style='Page.TFrame')
        actions.pack(side='right', anchor='n', padx=(12, 0))
        left = ttk.Frame(head, style='Page.TFrame')
        left.pack(side='left', fill='x', expand=True)
        self.title_widget = ttk.Label(left, text=title, style='Title.TLabel')
        self.title_widget.pack(anchor='w')
        self.subtitle_widget = ttk.Label(left, text=subtitle, style='Subtitle.TLabel', wraplength=640, justify='left')
        self.subtitle_widget.pack(anchor='w', pady=(4, 0), fill='x')
        label = self.subtitle_widget
        left.bind('<Configure>', lambda e: label.configure(wraplength=max(300, e.width - 10)))
        return actions


def field(parent, row, label, widget, hint='', column=0, span=1):
    caption = ttk.Label(parent, text=label, style='Field.TLabel')
    caption.grid(row=row, column=column, columnspan=span, sticky='w', pady=(10, 2), padx=(0, 18))
    widget.grid(row=row + 1, column=column, columnspan=span, sticky='ew', padx=(0, 18))
    widget._caption = caption
    if hint:
        Tooltip(widget, hint)
    return widget


def tree(parent, columns, height=12):
    frame = ttk.Frame(parent)
    view = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', height=height, selectmode='browse')
    for key, label, width in columns:
        view.heading(key, text=label)
        view.column(key, width=width, minwidth=40, stretch=True, anchor='w')
    bar = ttk.Scrollbar(frame, orient='vertical', command=view.yview)
    view.configure(yscrollcommand=bar.set)
    view.pack(side='left', fill='both', expand=True)
    bar.pack(side='right', fill='y')
    return frame, view


class FileList(ttk.Frame):
    """Files chosen for a record: add, remove, sizes. Only paths are kept until upload."""

    def __init__(self, parent, on_change=None, title='Add files…'):
        super().__init__(parent)
        self.items, self.on_change = [], on_change
        top = ttk.Frame(self)
        top.pack(fill='x')
        ttk.Button(top, text=title, command=self.add).pack(side='left')
        ttk.Button(top, text='Remove selected', command=self.remove).pack(side='left', padx=6)
        self.summary = ttk.Label(top, style='Hint.TLabel')
        self.summary.pack(side='left', padx=8)
        self.box = tk.Listbox(self, height=4, relief='flat', highlightthickness=1, highlightbackground=LINE, takefocus=0,
            activestyle='none', font=(FONT, 10))
        self.box.pack(fill='x', pady=(6, 0))
        self.render()

    def add(self, paths=None):
        paths = paths if paths is not None else filedialog.askopenfilenames(parent=self, title='Choose files')
        for path in paths:
            if not any(str(i.path) == str(Path(path)) for i in self.items):
                self.items.append(FileItem(path))
        self.render()

    def remove(self):
        for index in reversed(self.box.curselection()):
            del self.items[index]
        self.render()

    def set_paths(self, paths):
        self.items = [FileItem(p) for p in paths if Path(p).is_file()]
        self.render()

    def paths(self):
        return [str(i.path) for i in self.items if i.path]

    def render(self):
        self.box.delete(0, 'end')
        total = 0
        for item in self.items:
            size = item.path.stat().st_size if item.path and item.path.is_file() else 0
            total += size
            self.box.insert('end', f'  {item.name}    ({human_size(size)})')
        self.summary.configure(text=f'{len(self.items)} file(s), {human_size(total)}' if self.items else 'No files yet')
        if self.on_change: self.on_change()


# ============================================================================ samples (home)
class SamplesPage(Page):
    COLUMNS = (('id', 'Sample ID', 170), ('composition', 'Composition', 260), ('procedure', 'Procedure / source', 150),
        ('lab', 'Lab', 105), ('initials', 'Made by', 75), ('date', 'Synthesized', 100))

    def build(self):
        actions = self.heading(self.frame, 'Samples', 'Every catalyst sample in the consortium. Search, filter, and '
            'double-click a sample to see all of its data from every lab.')
        ttk.Button(actions, text='+ New sample', style='Primary.TButton', command=lambda: self.app.show('new_sample')).pack(side='left')
        ttk.Button(actions, text='Upload data', command=self.upload).pack(side='left', padx=6)
        ttk.Button(actions, text='Refresh', command=self.app.refresh).pack(side='left')
        self.notice = ttk.Label(self.frame, style='Warn.TLabel', wraplength=900, justify='left')
        card = Card(self.frame, padding=16)
        card.pack(fill='both', expand=True)
        self.card = card
        filters = ttk.Frame(card.body)
        filters.pack(fill='x', pady=(0, 10))
        self.search = tk.StringVar()
        entry = ttk.Entry(filters, textvariable=self.search)
        field(filters, 0, 'Search', entry, 'Matches sample ID, composition or procedure. Several words narrow the list.', span=4)
        ttk.Button(filters, text='Clear filters', style='Quiet.TButton', command=self.clear).grid(row=1, column=4, sticky='w')
        self.lab = ttk.Combobox(filters, values=(ALL,) + ids.LAB_NAMES, state='readonly', width=14)
        self.lab.set(ALL)
        field(filters, 2, 'Lab', self.lab)
        self.person = ttk.Combobox(filters, values=(ALL,), state='readonly', width=10)
        self.person.set(ALL)
        field(filters, 2, 'Made by', self.person, column=1)
        self.procedure = ttk.Combobox(filters, values=(ALL,), state='readonly', width=16)
        self.procedure.set(ALL)
        field(filters, 2, 'Procedure', self.procedure, column=2)
        self.since = DateEntry(filters, value='', width=11, on_change=self.render)
        field(filters, 2, 'Made on or after', self.since, 'Leave blank to show all dates.', column=3)
        for column in range(4):
            filters.columnconfigure(column, weight=1)
        for widget in (self.lab, self.person, self.procedure):
            widget.bind('<<ComboboxSelected>>', lambda _e: self.render())
        self.search.trace_add('write', lambda *_: self.render())
        foot = ttk.Frame(card.body)
        foot.pack(side='bottom', fill='x', pady=(10, 0))
        frame, self.view = tree(card.body, self.COLUMNS, height=8)
        frame.pack(fill='both', expand=True)
        for key, label, _ in self.COLUMNS:
            self.view.heading(key, text=label, command=lambda k=key: self.sort(k))
        self.view.bind('<Double-1>', lambda _e: self.open())
        self.view.bind('<Return>', lambda _e: self.open())
        self.count = ttk.Label(foot, style='Hint.TLabel')
        self.count.pack(side='left')
        ttk.Button(foot, text='Open sample', style='Primary.TButton', command=self.open).pack(side='right')
        self.sort_key, self.sort_reverse = 'date', True

    def shown(self, **kwargs):
        self.render()

    def data_changed(self):
        people = sorted({s['initials'] for s in self.app.samples})
        self.person.configure(values=(ALL,) + tuple(people))
        procedures = sorted({s['procedure'].split(' v')[0] for s in self.app.samples if s['procedure']})
        commercial = ('Commercial',) if any(s.get('source') == 'commercial' for s in self.app.samples) else ()
        self.procedure.configure(values=(ALL,) + commercial + tuple(procedures))
        self.render()

    def clear(self):
        self.search.set('')
        for w in (self.lab, self.person, self.procedure):
            w.set(ALL)
        self.since.set('')
        self.render()

    def sort(self, key):
        self.sort_reverse = not self.sort_reverse if self.sort_key == key else key == 'date'
        self.sort_key = key
        self.render()

    def filtered(self):
        words = self.search.get().casefold().split()
        since = self.since.date()
        result = []
        for s in self.app.samples:
            text = f"{s['id']} {s['composition']} {s.get('origin', s['procedure'])}".casefold()
            if words and not all(w in text for w in words): continue
            if self.lab.get() != ALL and s['lab_name'] != self.lab.get(): continue
            if self.person.get() != ALL and s['initials'] != self.person.get(): continue
            chosen = self.procedure.get()
            if chosen == 'Commercial' and s.get('source') != 'commercial': continue
            if chosen not in (ALL, 'Commercial') and not s['procedure'].startswith(chosen + ' '): continue
            if since and s['date'] < since.isoformat(): continue
            result.append(s)
        key = {'lab': 'lab_name'}.get(self.sort_key, self.sort_key)
        return sorted(result, key=lambda s: (str(s.get(key, '')).casefold(), s['id']), reverse=self.sort_reverse)

    def render(self):
        if not hasattr(self, 'view'):
            return
        self.notice.pack_forget()
        app = self.app
        message = None
        if not app.store:
            message = ('Not connected to SciSure. Go to Settings → Connection and paste your lab\'s token. '
                'You can still prepare new samples and uploads; they are kept as drafts.')
        elif not app.store.workspace_ready():
            message = ('Connected, but this account cannot see the shared "CATALYST" project yet. Ask the coordinator to '
                'share it with your lab account (or set it up in Settings → Coordinator tools).')
        if message:
            self.notice.configure(text=message)
            self.notice.pack(fill='x', pady=(0, 12), before=self.card)
        self.view.delete(*self.view.get_children())
        rows = self.filtered()
        for s in rows:
            self.view.insert('', 'end', iid=str(s['experiment_id']), values=(s['id'], s['composition'], s.get('origin', s['procedure']), s['lab_name'],
                s['initials'], s['date']))
        total = len(app.samples)
        self.count.configure(text=f'{total} sample(s)' + (f' · showing {len(rows)}' if len(rows) != total else ''))

    def selected(self):
        selection = self.view.selection()
        return selection[0] if selection else None

    def open(self):
        sample_id = self.selected()
        if sample_id:
            self.app.open_sample(sample_id)

    def upload(self):
        key = self.selected()
        self.app.show('upload', sample=self.app.find_sample(key) if key else None)


# ============================================================================ one sample
class SamplePage(Page):
    def build(self):
        top = ttk.Frame(self.frame, style='Page.TFrame')
        top.pack(fill='x')
        ttk.Button(top, text='← All samples', style='Quiet.TButton', command=lambda: self.app.show('samples')).pack(side='left')
        self.heading(self.frame, '')
        self.title, self.subtitle = self.title_widget, self.subtitle_widget
        self.actions = ttk.Frame(self.frame, style='Page.TFrame')
        self.actions.pack(fill='x', pady=(0, 12))
        upload = ttk.Button(self.actions, text='Upload data for this sample', style='Primary.TButton',
            command=lambda: self.app.show('upload', sample=self.app.current['sample']))
        upload.pack(side='left')
        ship = ttk.Button(self.actions, text='Log shipment', command=self.ship)
        ship.pack(side='left', padx=6)
        derive = ttk.Button(self.actions, text='New sample made from this', command=self.derive)
        derive.pack(side='left')
        self.record_buttons = [upload, ship, derive]
        ttk.Button(self.actions, text='Refresh', command=self.reload).pack(side='right')
        self.warning = ttk.Label(self.frame, style='Warn.TLabel', wraplength=900, justify='left')
        body = ttk.Frame(self.frame, style='Page.TFrame')
        body.pack(fill='both', expand=True)
        self.body = body
        left = Card(body, padding=16)
        left.pack(side='left', fill='y', padx=(0, 14))
        ttk.Label(left.body, text='About this sample', style='Sub.TLabel').pack(anchor='w')
        self.about = ScrollFrame(left.body)
        self.about.pack(fill='both', expand=True, pady=(8, 0))
        self.about.canvas.configure(width=300)
        self.sample_files = ttk.Button(left.body, text='Download synthesis files', command=self.download_sample_files)
        right = ttk.Frame(body, style='Page.TFrame')
        right.pack(side='left', fill='both', expand=True)
        data = Card(right, padding=16)
        data.pack(fill='both', expand=True)
        head = ttk.Frame(data.body)
        head.pack(fill='x')
        ttk.Label(head, text='Data from every lab', style='Sub.TLabel').pack(side='left')
        ttk.Button(head, text='Download files', command=self.download_data).pack(side='right')
        ttk.Button(head, text='Details', command=self.details).pack(side='right', padx=6)
        frame, self.data_view = tree(data.body, (('id', 'Data ID', 215), ('technique', 'Technique', 170),
            ('date', 'Measured', 90), ('lab', 'Lab', 90), ('by', 'By', 45), ('files', 'Files', 40)), height=6)
        frame.pack(fill='both', expand=True, pady=(8, 0))
        self.data_view.bind('<Double-1>', lambda _e: self.details())
        ship = Card(right, padding=16)
        ship.pack(fill='x', pady=(14, 0))
        head = ttk.Frame(ship.body)
        head.pack(fill='x')
        ttk.Label(head, text='Shipping log', style='Sub.TLabel').pack(side='left')
        ttk.Button(head, text='Log shipment', command=self.ship).pack(side='right')
        frame, self.ship_view = tree(ship.body, (('date', 'Shipped', 90), ('route', 'From → to', 170),
            ('amount', 'Amount', 80), ('by', 'By', 45), ('notes', 'Tracking / notes', 200)), height=3)
        frame.pack(fill='x', pady=(8, 0))

    def shown(self, **kwargs):
        current = self.app.current
        if not current:
            return self.app.show('samples')
        sample, record = current['sample'], current['record']
        self.title.configure(text=sample['id'])
        verb = 'received' if sample.get('source') == 'commercial' else 'made'
        self.subtitle.configure(text=f"{sample['composition']}   ·   {sample['lab_name']}   ·   {verb} by {sample['initials']} "
            f"on {sample['date']}   ·   {sample.get('origin') or sample['procedure'] or 'no procedure'}")
        for child in self.about.body.winfo_children():
            child.destroy()
        lines = records.summary_lines(record) if record else [('Registration not finished', 'This sample\'s '
            'registration did not finish (the app closed or the connection dropped while saving). If it is yours, '
            'register it again from New sample with the same details; the same ID is reused.')]
        for button in self.record_buttons:
            button.state(['!disabled'] if record else ['disabled'])
        if record:
            lines += records.recipe_lines(record['recipe'])
        for label, value in lines:
            ttk.Label(self.about.body, text=label, style='Hint.TLabel').pack(anchor='w', pady=(6, 0))
            ttk.Label(self.about.body, text=value, style='Value.TLabel', wraplength=285, justify='left').pack(anchor='w')
        if current['sample_files']:
            self.sample_files.pack(anchor='w', pady=(10, 0))
        else:
            self.sample_files.pack_forget()
        self.data_view.delete(*self.data_view.get_children())
        for item in current['data']:
            self.data_view.insert('', 'end', iid=str(item['section_id']), values=(item['id'],
                ids.TECHNIQUES.get(item['technique'], item['technique']), item['date'],
                ids.lab_name(item['lab']) if item['lab'] in ids.LAB_CODES else item['lab'], item['initials'], len(item['files'])))
        self.ship_view.delete(*self.ship_view.get_children())
        for item in current['shipments']:
            r = item['record']
            self.ship_view.insert('', 'end', values=(r['date'], f"{ids.lab_name(r['from_lab'])} → {ids.lab_name(r['to_lab'])}",
                r.get('amount', ''), r['created_by']['initials'], ' · '.join(x for x in (r.get('tracking'), r.get('notes')) if x)))
        self.warning.pack_forget()
        if current['incomplete']:
            self.warning.configure(text=f"{len(current['incomplete'])} upload(s) for this sample did not finish (the app was closed "
                'or the connection dropped). Upload those files again; the unfinished copies are ignored.')
            self.warning.pack(fill='x', pady=(0, 10), before=self.body)

    def reload(self):
        self.app.open_sample(self.app.current['sample']['id'])

    def selected_data(self):
        selection = self.data_view.selection()
        return next((d for d in self.app.current['data'] if str(d['section_id']) == selection[0]), None) if selection else None

    def details(self):
        item = self.selected_data()
        if item:
            RecordDialog(self.app, item['record'], item['files'], lambda: self.app.download_files(
                item['section_id'], item['files'], item['record']['files']))

    def download_data(self):
        item = self.selected_data()
        if not item:
            return self.app.error('Select a data record first.')
        self.app.download_files(item['section_id'], item['files'], item['record']['files'])

    def download_sample_files(self):
        c = self.app.current
        self.app.download_files(c['sample_section'], c['sample_files'], c['record']['files'])

    def ship(self):
        ShipmentDialog(self.app, self.app.current['sample'])

    def derive(self):
        form = self.app.pages['new_sample']
        if not self.app.current.get('record'):
            return
        if form.has_content() and not self.app.smoke and not messagebox.askyesno('CATALYST', 'You have an unsaved new '
                'sample in progress. Replace it with a new sample based on this one?', parent=self.app.root):
            return self.app.show('new_sample')
        self.app.show('new_sample', parent=self.app.current)


class RecordDialog(tk.Toplevel):
    def __init__(self, app, record, files, download=None):
        super().__init__(app.root)
        self.title(record['id'])
        self.configure(background='white', padx=22, pady=18)
        self.transient(app.root)
        ttk.Label(self, text=record['id'], style='ID.TLabel').pack(anchor='w')
        grid = ttk.Frame(self)
        grid.pack(fill='x', pady=(10, 0))
        for row, (label, value) in enumerate(records.summary_lines(record)):
            ttk.Label(grid, text=label, style='Hint.TLabel').grid(row=row, column=0, sticky='nw', padx=(0, 16), pady=2)
            ttk.Label(grid, text=value, style='Value.TLabel', wraplength=460, justify='left').grid(row=row, column=1, sticky='w', pady=2)
        if record.get('files'):
            ttk.Label(self, text='Files', style='Field.TLabel').pack(anchor='w', pady=(12, 2))
            for f in record['files']:
                ttk.Label(self, text=f"  {f['name']}   ({human_size(f['size_bytes'])})", style='Value.TLabel').pack(anchor='w')
        buttons = ttk.Frame(self)
        buttons.pack(fill='x', pady=(16, 0))
        ttk.Button(buttons, text='Close', command=self.destroy).pack(side='right')
        if download and files:
            ttk.Button(buttons, text='Download files', style='Primary.TButton',
                command=lambda: (self.destroy(), download())).pack(side='right', padx=6)


class PreviewDialog(tk.Toplevel):
    """One screen: what will be saved, any problems, then Save."""

    def __init__(self, app, title, id_text, lines, files, problems, save):
        super().__init__(app.root)
        self.app, self.save_callback = app, save
        self.title(title)
        self.configure(background='white', padx=24, pady=20)
        self.transient(app.root)
        ttk.Label(self, text=title, style='Sub.TLabel').pack(anchor='w')
        ttk.Label(self, text=id_text, style='ID.TLabel').pack(anchor='w', pady=(6, 0))
        grid = ttk.Frame(self)
        grid.pack(fill='x', pady=(10, 0))
        for row, (label, value) in enumerate(lines):
            ttk.Label(grid, text=label, style='Hint.TLabel').grid(row=row, column=0, sticky='nw', padx=(0, 16), pady=2)
            ttk.Label(grid, text=value, style='Value.TLabel', wraplength=480, justify='left').grid(row=row, column=1, sticky='w', pady=2)
        if files:
            ttk.Label(self, text=f'{len(files)} file(s) will be uploaded unchanged and checked after upload:', style='Field.TLabel').pack(anchor='w', pady=(12, 2))
            for item in files[:12]:
                ttk.Label(self, text='  ' + item.name, style='Value.TLabel').pack(anchor='w')
            if len(files) > 12:
                ttk.Label(self, text=f'  … and {len(files) - 12} more', style='Hint.TLabel').pack(anchor='w')
        if problems:
            ttk.Label(self, text='Please fix before saving:', style='Problem.TLabel').pack(anchor='w', pady=(14, 2))
            for text in problems:
                ttk.Label(self, text='•  ' + text, style='Problem.TLabel', wraplength=520, justify='left').pack(anchor='w')
        buttons = ttk.Frame(self)
        buttons.pack(fill='x', pady=(18, 0))
        self.save_button = ttk.Button(buttons, text='Save to SciSure', style='Primary.TButton', command=self.save)
        self.save_button.pack(side='right')
        ttk.Button(buttons, text='Back to editing', command=self.destroy).pack(side='right', padx=6)
        if problems:
            self.save_button.state(['disabled'])
        self.grab_set()

    def save(self):
        self.destroy()
        self.save_callback()


class ShipmentDialog(tk.Toplevel):
    def __init__(self, app, sample):
        super().__init__(app.root)
        self.app, self.sample = app, sample
        self.title('Log a shipment')
        self.configure(background='white', padx=22, pady=18)
        self.transient(app.root)
        ttk.Label(self, text=f"Log a shipment of {sample['id']}", style='Sub.TLabel').pack(anchor='w')
        ttk.Label(self, text='Record where the sample went so everyone can follow it between labs.', style='Hint.TLabel').pack(anchor='w')
        form = ttk.Frame(self)
        form.pack(fill='x')
        own = ids.lab_name(app.settings.profile.get('lab')) if app.settings.has_profile() else ''
        self.to = ttk.Combobox(form, values=[n for n in ids.LAB_NAMES if n != own], state='readonly', width=24)
        field(form, 0, 'Sent to', self.to)
        self.when = DateEntry(form)
        field(form, 0, 'Date shipped', self.when, column=1)
        self.amount = ttk.Entry(form, width=24)
        field(form, 2, 'Amount sent', self.amount, 'e.g. 200 mg')
        self.tracking = ttk.Entry(form, width=30)
        field(form, 2, 'Carrier / tracking number', self.tracking, column=1)
        self.notes = text_box(form, height=3, width=50)
        field(form, 4, 'Notes', self.notes, span=2)
        buttons = ttk.Frame(self)
        buttons.pack(fill='x', pady=(16, 0))
        self.save_button = ttk.Button(buttons, text='Save shipment', style='Primary.TButton', command=self.save)
        self.save_button.pack(side='right')
        ttk.Button(buttons, text='Cancel', command=self.destroy).pack(side='right', padx=6)
        self.grab_set()

    def save(self):
        app = self.app
        if not app.connected():
            return
        values = dict(to_lab=self.to.get(), ship_date=self.when.get(), amount=self.amount.get(),
            tracking=self.tracking.get(), notes=text_value(self.notes))
        try:
            record, problems = records.shipment_record(shipment_id='', sample_id=self.sample['id'],
                profile=app.settings.profile, app_version=__version__, **values)
        except ids.IdError as error:
            return app.error(error)
        if problems:
            return app.error('\n'.join(problems))
        profile = app.settings.profile
        build = lambda sid: records.shipment_record(shipment_id=sid, sample_id=self.sample['id'],
            profile=profile, app_version=__version__, **values)[0]
        self.save_button.state(['disabled'])
        def done(saved):
            self.destroy()
            app.info(f"Shipment {saved['record']['id']} logged." + readable_note(saved))
            app.open_sample(self.sample['experiment_id'])
        def failed(error):
            if self.winfo_exists():
                self.save_button.state(['!disabled'])
            app.error(error)
        app.run('Saving shipment…', lambda progress: app.store.add_shipment(self.sample, build, progress), done, failed)


# ============================================================================ recipe fields
class RecipeFields:
    """The shared synthesis template, laid out (and tabbed through) top to bottom, left to right."""

    def __init__(self, parent, on_change, start_row=2):
        self.widgets = {}
        ordered = [f for f in records.RECIPE_FIELDS if f[2] != 'long'] + [f for f in records.RECIPE_FIELDS if f[2] == 'long']
        row, column = start_row, 0
        for key, label, kind, choices, hint in ordered:
            if kind in ('components', 'long'):
                if column:
                    row, column = row + 2, 0
                if kind == 'components':
                    widget = ComponentsEditor(parent, records.LOADING_UNITS, on_change)
                else:
                    widget = text_box(parent, height=5 if key == 'steps' else 3)
                    widget.bind('<KeyRelease>', lambda _e: on_change())
                field(parent, row, label, widget, hint, span=2)
                row += 2
            else:
                if kind == 'choice':
                    widget = ttk.Combobox(parent, values=choices, width=34)
                    widget.bind('<<ComboboxSelected>>', lambda _e: on_change())
                else:
                    widget = ttk.Entry(parent, width=36)
                widget.bind('<KeyRelease>', lambda _e: on_change())
                field(parent, row, label, widget, hint, column=column)
                column = 1 - column
                if column == 0:
                    row += 2
            self.widgets[key] = widget
        self.next_row = row + 2
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

    def show_only(self, keys=None):
        """Show every field (keys=None) or just the named ones (e.g. composition for a commercial material)."""
        for key, widget in self.widgets.items():
            visible = keys is None or key in keys
            for w in (widget, widget._caption):
                w.grid() if visible else w.grid_remove()

    def get(self):
        result = {}
        for key, w in self.widgets.items():
            result[key] = w.get() if isinstance(w, ComponentsEditor) else text_value(w) if isinstance(w, tk.Text) else w.get()
        return result

    def set(self, values):
        values = dict(values or {})
        if 'components' not in values and values.get('metals'):
            values['components'] = records.parse_metals(values['metals'])
        for key, widget in self.widgets.items():
            value = values.get(key)
            if isinstance(widget, ComponentsEditor):
                widget.set(value if isinstance(value, list) else records.clean_components(value or [])[0])
                continue
            value = '' if value is None else (f'{value:g}' if isinstance(value, float) else str(value))
            if isinstance(widget, tk.Text):
                set_text(widget, value)
            else:
                widget.delete(0, 'end')
                widget.insert(0, value)


# ============================================================================ new sample
class SampleForm(Page):
    DRAFT = 'new_sample'

    def build(self):
        self.procedure = None
        self.parent_record = None
        self._composition_auto = True
        self._loading = False
        actions = self.heading(self.frame, 'New sample', 'Choose the shared procedure, then change anything you did '
            'differently. Differences are recorded automatically. The sample ID is generated when you save.')
        ttk.Button(actions, text='Clear form', command=self.clear).pack(side='left')
        scroll = ScrollFrame(self.frame, style='Page.TFrame')
        scroll.pack(fill='both', expand=True)
        self.scroll = scroll
        one = Card(scroll.body, padding=18)
        one.pack(fill='x', pady=(0, 12))
        ttk.Label(one.body, text='1   Where did this sample come from?', style='Sub.TLabel').pack(anchor='w')
        self.source = tk.StringVar(value='synthesized')
        choice = ttk.Frame(one.body)
        choice.pack(anchor='w', pady=(6, 4))
        for value, text in (('synthesized', 'We made it (synthesis in a CATALYST lab)'),
                ('commercial', 'Commercial or reference material (bought, donated or an industry standard)')):
            ttk.Radiobutton(choice, text=text, value=value, variable=self.source, command=self.source_changed).pack(anchor='w', pady=2)
        self.made = ttk.Frame(one.body)
        self.made.pack(fill='x')
        self.procedure_box = SearchCombo(self.made, width=60, on_select=self.pick_procedure)
        field(self.made, 0, 'Synthesis procedure', self.procedure_box, 'Type to search the shared procedures.')
        ttk.Button(self.made, text='+ New procedure', command=lambda: self.app.show('procedure_form')).grid(row=1, column=1, sticky='w')
        self.procedure_note = ttk.Label(self.made, style='Hint.TLabel', wraplength=760, justify='left')
        self.procedure_note.grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.made.columnconfigure(0, weight=1)
        self.bought = ttk.Frame(one.body)
        self.commercial = {}
        for index, (key, label, hint) in enumerate(records.COMMERCIAL_FIELDS):
            widget = ttk.Entry(self.bought, width=36)
            widget.bind('<KeyRelease>', lambda _e: self.changed())
            field(self.bought, (index // 2) * 2, label, widget, hint, column=index % 2)
            self.commercial[key] = widget
        self.bought.columnconfigure(0, weight=1)
        self.bought.columnconfigure(1, weight=1)
        two = Card(scroll.body, padding=18)
        two.pack(fill='x', pady=(0, 12))
        ttk.Label(two.body, text='2   This sample', style='Sub.TLabel').grid(row=0, column=0, sticky='w')
        self.when = DateEntry(two.body, on_change=self.changed)
        field(two.body, 1, 'Synthesis date', self.when, 'The date the sample was made (or received). It becomes part of the ID. '
            'Click the calendar or press Alt+Down to choose.')
        self.who = ttk.Label(two.body, style='Value.TLabel')
        field(two.body, 1, 'Made by', self.who, 'From your profile (Settings).', column=1)
        self.composition = ttk.Entry(two.body, width=50)
        self.composition.bind('<KeyRelease>', lambda _e: self.composition_typed())
        field(two.body, 3, 'Composition', self.composition, records.COMPOSITION_HELP)
        self.amount = ttk.Entry(two.body, width=20)
        self.amount.bind('<KeyRelease>', lambda _e: self.changed())
        field(two.body, 3, 'Amount made (g)', self.amount, column=1)
        ttk.Label(two.body, text='Filled in from the metals/phases and support in section 3, e.g. “10 wt% Mo + 1 wt% K on '
            'γ-Al2O3”. Edit it if you prefer a different name.', style='Hint.TLabel', wraplength=420,
            justify='left').grid(row=5, column=0, sticky='w', pady=(4, 0))
        self.label = ttk.Entry(two.body, width=40)
        self.label.bind('<KeyRelease>', lambda _e: self.changed())
        field(two.body, 6, 'Your notebook label (optional)', self.label, 'Whatever you wrote on the vial or in your notebook.')
        self.parent = SearchCombo(two.body, width=40, on_select=lambda _v: self.changed())
        field(two.body, 6, 'Made from another sample (optional)', self.parent,
            'For a derivative: e.g. a spent, reduced or pelletized version of an existing sample.', column=1)
        two.body.columnconfigure(0, weight=1)
        two.body.columnconfigure(1, weight=1)
        three = Card(scroll.body, padding=18)
        three.pack(fill='x', pady=(0, 12))
        self.recipe_title = ttk.Label(three.body, text='3   Recipe used', style='Sub.TLabel')
        self.recipe_title.grid(row=0, column=0, sticky='w')
        self.recipe_hint = ttk.Label(three.body, text='Prefilled from the procedure. Change only what you did differently.',
            style='Hint.TLabel')
        self.recipe_hint.grid(row=0, column=1, sticky='e')
        self.recipe = RecipeFields(three.body, self.recipe_changed)
        self.deviations = text_box(three.body, height=3)
        self.deviations.bind('<KeyRelease>', lambda _e: self.changed())
        field(three.body, 400, 'Anything else that differed (optional)', self.deviations,
            'e.g. different furnace, precursor lot, humidity', span=2)
        self.notes = text_box(three.body, height=3)
        self.notes.bind('<KeyRelease>', lambda _e: self.changed())
        field(three.body, 402, 'Notes (optional)', self.notes, span=2)
        four = Card(scroll.body, padding=18)
        four.pack(fill='x', pady=(0, 12))
        ttk.Label(four.body, text='4   Files (optional)', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(four.body, text='Photos, notebook scans, precursor certificates…', style='Hint.TLabel').pack(anchor='w', pady=(0, 6))
        self.files = FileList(four.body, on_change=self.changed)
        self.files.pack(fill='x')
        foot = ttk.Frame(self.frame, style='Page.TFrame')
        foot.pack(fill='x', pady=(10, 0))
        self.id_preview = ttk.Label(foot, style='Subtitle.TLabel')
        self.id_preview.pack(side='left')
        ttk.Button(foot, text='Preview & save', style='Primary.TButton', command=self.preview).pack(side='right')
        self.restore_draft()

    # draft handling ---------------------------------------------------
    def values(self):
        return dict(source=self.source.get(), commercial={k: w.get() for k, w in self.commercial.items()},
            procedure=self.procedure_box.get_value() or '', date=self.when.get(), composition=self.composition.get(),
            composition_auto=self._composition_auto, amount=self.amount.get(), label=self.label.get(),
            parent=self.parent.get_value() or '', recipe=self.recipe.get(), deviations=text_value(self.deviations),
            notes=text_value(self.notes), files=self.files.paths())

    def save_draft(self):
        if self._loading:
            return
        values = self.values()
        reserved = (self.app.settings.draft(self.DRAFT) or {}).get('reserved_id')
        if reserved:
            values['reserved_id'] = reserved
        meaningful = any(values[k] for k in ('procedure', 'amount', 'label', 'deviations', 'notes', 'files')) or any(
            values['commercial'].values())
        self.app.settings.save_draft(self.DRAFT, values if meaningful or reserved else None)

    def restore_draft(self):
        values = self.app.settings.draft(self.DRAFT)
        if not values:
            return self.clear(keep_draft=True)
        self._loading = True
        try:
            self.source.set(values.get('source') or 'synthesized')
            for key, widget in self.commercial.items():
                widget.delete(0, 'end'); widget.insert(0, (values.get('commercial') or {}).get(key, ''))
            self.source_changed(quiet=True)
            self.when.set(values.get('date') or date.today())
            self.composition.delete(0, 'end'); self.composition.insert(0, values.get('composition', ''))
            self._composition_auto = values.get('composition_auto', True)
            for widget, key in ((self.amount, 'amount'), (self.label, 'label')):
                widget.delete(0, 'end'); widget.insert(0, values.get(key, ''))
            self.recipe.set(values.get('recipe'))
            set_text(self.deviations, values.get('deviations')); set_text(self.notes, values.get('notes'))
            self.files.set_paths(values.get('files', []))
            self._pending_procedure, self._pending_parent = values.get('procedure'), values.get('parent')
        finally:
            self._loading = False
        self.app.set_status('Restored your unsaved new-sample draft.')

    def clear(self, keep_draft=False):
        self._loading = True
        try:
            self.procedure, self.parent_record, self._composition_auto = None, None, True
            self.procedure_box.set_value(None); self.parent.set_value(None)
            self.source.set('synthesized')
            for widget in self.commercial.values():
                widget.delete(0, 'end')
            self.source_changed(quiet=True)
            self.when.set(date.today())
            for widget in (self.composition, self.amount, self.label):
                widget.delete(0, 'end')
            self.recipe.set({})
            set_text(self.deviations, ''); set_text(self.notes, '')
            self.files.set_paths([])
            self.procedure_note.configure(text='')
            self._pending_procedure = self._pending_parent = None
        finally:
            self._loading = False
        if not keep_draft:
            self.app.settings.clear_draft(self.DRAFT)
        self.update_preview()

    # events -------------------------------------------------------------
    def shown(self, parent=None, procedure_id=None, **kwargs):
        self.update_who()
        self.data_changed()
        if parent:
            self.from_parent(parent)
        if procedure_id:
            self.procedure_box.set_value(procedure_id)
            self.pick_procedure(procedure_id)
        self.update_preview()

    def data_changed(self):
        self.procedure_box.set_items([(p['id'], f"{p['id']}  ·  {p['name']}") for p in self.app.procedures])
        self.parent.set_items(self.app.sample_choices())
        if getattr(self, '_pending_procedure', None) and self.app.procedures:
            pid, self._pending_procedure = self._pending_procedure, None
            self.procedure_box.set_value(pid)
            self.pick_procedure(pid, keep_recipe=True)
        if getattr(self, '_pending_parent', None) and self.app.samples:
            self.parent.set_value(self._pending_parent)
            self._pending_parent = None
        self.update_preview()

    def from_parent(self, opened):
        record = opened['record']
        self.clear()
        self.parent.set_value(record['id'])
        self.source.set(record.get('source') or 'synthesized')
        for key, widget in self.commercial.items():
            widget.insert(0, (record.get('commercial') or {}).get(key, ''))
        self.source_changed(quiet=True)
        self.recipe.set(record['recipe'])
        self.composition.insert(0, record['composition'])
        self._composition_auto = False
        if record['procedure'].get('id'):
            self.procedure_box.set_value(record['procedure']['id'])
            self.pick_procedure(record['procedure']['id'], keep_recipe=True)
        set_text(self.notes, f"Made from {record['id']}. ")
        self.app.set_status(f"Started a new sample from {record['id']}. Describe what changed (e.g. reduced, spent, pelletized).")

    def pick_procedure(self, procedure_id, keep_recipe=False):
        if not procedure_id:
            self.procedure = None
            return self.changed()
        def loaded(value):
            if not value or not value['versions']:
                self.procedure = None
                self.procedure_note.configure(text='This procedure has no saved versions yet.')
                return
            latest = value['versions'][-1]['record']
            self.procedure = dict(id=value['id'], version=latest['version'], name=latest['name'], recipe=latest['recipe'])
            self.procedure_note.configure(text=f"Using {value['id']} version {latest['version']} (latest), written by "
                f"{latest['created_by']['name']} ({ids.lab_name(latest['created_by']['lab'])}). "
                + (f"Purpose: {latest['description']}" if latest.get('description') else ''))
            if not keep_recipe:
                self.recipe.set(latest['recipe'])
                self._composition_auto = True
                self.recipe_changed()
            self.changed()
        self.app.load_procedure(procedure_id, loaded)

    def source_changed(self, quiet=False):
        bought = self.source.get() == 'commercial'
        (self.made.pack_forget if bought else lambda: self.made.pack(fill='x'))()
        (self.bought.pack(fill='x') if bought else self.bought.pack_forget())
        self.when._caption.configure(text='Date received' if bought else 'Synthesis date')
        self.amount._caption.configure(text='Amount received (g)' if bought else 'Amount made (g)')
        self.recipe_title.configure(text='3   Stated composition' if bought else '3   Recipe used')
        self.recipe_hint.configure(text='From the supplier\'s data sheet or certificate.' if bought else
            'Prefilled from the procedure. Change only what you did differently.')
        self.recipe.show_only(('components', 'support') if bought else None)
        for widget in (self.deviations,):
            for w in (widget, widget._caption):
                w.grid_remove() if bought else w.grid()
        self.update_who()
        if not quiet:
            self.changed()

    def update_who(self):
        p = self.app.settings.profile
        self.who._caption.configure(text='Registered by' if self.source.get() == 'commercial' else 'Made by')
        self.who.configure(text=f"{p.get('name', '—')} ({p.get('initials', '—')}) · {ids.lab_name(p['lab'])}"
            if self.app.settings.has_profile() else 'Set up your profile in Settings first')

    def composition_typed(self):
        self._composition_auto = False
        self.changed()

    def recipe_changed(self):
        if self._composition_auto:
            self.composition.delete(0, 'end')
            self.composition.insert(0, records.suggest_composition(self.recipe.get()))
        self.changed()

    def changed(self):
        self.update_preview()
        if not self._loading:
            if hasattr(self, '_draft_after'):
                self.frame.after_cancel(self._draft_after)
            self._draft_after = self.frame.after(800, self.save_draft)

    def update_preview(self):
        if not hasattr(self, 'id_preview'):
            return
        p = self.app.settings.profile
        when = self.when.date()
        if not self.app.settings.has_profile() or not when:
            return self.id_preview.configure(text='Set your profile and synthesis date to see the sample ID.')
        try:
            sample_id = ids.next_sample_id([s['id'] for s in self.app.samples], p['lab'], p['initials'], when)
        except ids.IdError as error:
            return self.id_preview.configure(text=str(error))
        suffix = '' if self.app.store else '  (number confirmed when saved)'
        self.id_preview.configure(text=f'Will be saved as  {sample_id}{suffix}')

    def inputs(self):
        """Everything typed in the form, read on the UI thread (never from the save thread)."""
        bought = self.source.get() == 'commercial'
        return dict(profile=self.app.settings.profile, synthesis_date=self.when.get(),
            procedure=None if bought else self.procedure, source=self.source.get(),
            commercial={k: w.get() for k, w in self.commercial.items()} if bought else None,
            recipe=self.recipe.get(), composition=self.composition.get(), label=self.label.get(),
            amount_g=self.amount.get(), parent_id=self.parent.get_value() or '', notes=text_value(self.notes),
            deviation_notes=text_value(self.deviations), app_version=__version__)

    def build_record(self, sample_id, inputs=None):
        return records.sample_record(sample_id=sample_id, **(inputs or self.inputs()))

    def has_content(self):
        values = self.values()
        return any(values[k] for k in ('procedure', 'amount', 'label', 'deviations', 'notes', 'files')) or any(
            values['commercial'].values())

    def preview(self):
        app = self.app
        if getattr(self, '_saving', False):
            return app.error('This sample is still being saved.')
        if not app.settings.has_profile():
            return app.error('Set up your profile in Settings first.')
        if not self.when.date():
            return app.error('Choose the date received.' if self.source.get() == 'commercial' else 'Choose the synthesis date.')
        p = app.settings.profile
        sample_id = ids.next_sample_id([s['id'] for s in app.samples], p['lab'], p['initials'], self.when.date())
        try:
            record, problems = self.build_record(sample_id)
            check_files(self.files.items, required=False)
        except (records.RecordError, StoreError, ids.IdError) as error:
            return app.error(error)
        if not app.connected(quiet=True):
            problems = problems + ['Connect to SciSure to save (your draft is kept).']
        PreviewDialog(app, 'Save new sample', f'{sample_id}  (final number confirmed when saving)',
            records.summary_lines(record), self.files.items, problems, self.save)

    def save(self):
        app, files, inputs, values = self.app, list(self.files.items), self.inputs(), self.values()
        reserved = (app.settings.draft(self.DRAFT) or {}).get('reserved_id')
        self._saving = True
        def work(progress):
            return app.store.create_sample(lambda sid: records.sample_record(sample_id=sid or 'UR-XX-000000-00', **inputs)[0],
                files, progress, reserved_id=reserved)
        def done(saved):
            self._saving = False
            sample_id = saved['sample']['id']
            if self.values() == values:
                self.clear()
            else:
                self.app.settings.clear_draft(self.DRAFT)
                self.save_draft()
            warning = ('\n\nNote: another sample with the same ID appeared at the same moment. Tell the coordinator.'
                if saved['duplicate_warning'] else '')
            app.info(f'Saved as {sample_id}.\n\nWrite this ID on the vial. Everyone in the consortium can now find it '
                f'and attach their data to it.{warning}' + readable_note(saved))
            app.refresh(then=lambda: app.open_sample(sample_id))
        def failed(error):
            self._saving = False
            if app.store and app.store.last_reserved:
                app.settings.save_draft(self.DRAFT, dict(self.values(), reserved_id=app.store.last_reserved))
            app.error(str(friendly(error)) + '\n\nYour form is kept. Click Preview & save again to finish; the same '
                'sample ID will be used.')
        app.run('Saving sample…', work, done, failed)


# ============================================================================ upload data
class UploadForm(Page):
    DRAFT = 'upload'

    def build(self):
        self._loading = False
        self.condition_widgets = {}
        actions = self.heading(self.frame, 'Upload data', 'Pick the sample and technique, add your files, and save. '
            'Your name, lab and the data ID are filled in for you.')
        ttk.Button(actions, text='Clear form', command=self.clear).pack(side='left')
        scroll = ScrollFrame(self.frame, style='Page.TFrame')
        scroll.pack(fill='both', expand=True)
        card = Card(scroll.body, padding=18)
        card.pack(fill='x', pady=(0, 12))
        body = card.body
        self.sample = SearchCombo(body, width=60, on_select=lambda _v: self.changed())
        field(body, 0, 'Sample', self.sample, 'Type part of the ID or composition. Your recent samples are listed first.', span=2)
        self.technique = ttk.Combobox(body, values=list(ids.TECHNIQUES.values()), state='readonly', width=36)
        self.technique.bind('<<ComboboxSelected>>', lambda _e: self.technique_changed())
        field(body, 2, 'Technique', self.technique)
        self.when = DateEntry(body, on_change=self.changed)
        field(body, 2, 'Date measured', self.when, column=1)
        self.title_entry = ttk.Entry(body, width=50)
        self.title_entry.bind('<KeyRelease>', lambda _e: self.changed())
        field(body, 4, 'Short description (optional)', self.title_entry, 'e.g. "post-reaction XRD" or "250–350 °C screening"', span=2)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self.conditions_card = Card(scroll.body, padding=18)
        self.conditions_card.pack(fill='x', pady=(0, 12))
        ttk.Label(self.conditions_card.body, text='Conditions (optional but very useful for comparing labs and for AI models)',
            style='Sub.TLabel').pack(anchor='w')
        self.conditions = ttk.Frame(self.conditions_card.body)
        self.conditions.pack(fill='x')
        more = Card(scroll.body, padding=18)
        more.pack(fill='x', pady=(0, 12))
        self.pooled = ttk.Entry(more.body, width=60)
        self.pooled.bind('<KeyRelease>', lambda _e: self.changed())
        field(more.body, 0, 'Tested together with other samples (optional)', self.pooled,
            'For pooled / multi-catalyst tests: other sample IDs, separated by commas.')
        self.notes = text_box(more.body, height=3)
        self.notes.bind('<KeyRelease>', lambda _e: self.changed())
        field(more.body, 2, 'Notes (optional)', self.notes)
        more.body.columnconfigure(0, weight=1)
        files = Card(scroll.body, padding=18)
        files.pack(fill='x', pady=(0, 12))
        ttk.Label(files.body, text='Data files', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(files.body, text='Add the original instrument exports, spreadsheets, images or PDFs. They are stored '
            'unchanged (up to 20 MB each).', style='Hint.TLabel').pack(anchor='w', pady=(0, 6))
        self.files = FileList(files.body, on_change=self.changed)
        self.files.pack(fill='x')
        foot = ttk.Frame(self.frame, style='Page.TFrame')
        foot.pack(fill='x', pady=(10, 0))
        self.id_preview = ttk.Label(foot, style='Subtitle.TLabel')
        self.id_preview.pack(side='left')
        ttk.Button(foot, text='Preview & save', style='Primary.TButton', command=self.preview).pack(side='right')
        self.technique.set(ids.TECHNIQUES['RXN'])
        self.build_conditions()
        self.restore_draft()

    def build_conditions(self, values=None):
        for child in self.conditions.winfo_children():
            child.destroy()
        self.condition_widgets = {}
        try:
            fields = records.condition_fields(self.technique.get())
        except ids.IdError:
            fields = records.COMMON_CONDITIONS
        for index, (key, label, kind, choices, hint) in enumerate(fields):
            widget = ttk.Combobox(self.conditions, values=choices, width=30) if kind == 'choice' else ttk.Entry(self.conditions, width=32)
            widget.bind('<KeyRelease>', lambda _e: self.changed())
            widget.bind('<<ComboboxSelected>>', lambda _e: self.changed())
            if values and values.get(key):
                widget.insert(0, values[key])
            field(self.conditions, (index // 3) * 2, label, widget, hint, column=index % 3)
            self.condition_widgets[key] = widget
        for column in range(3):
            self.conditions.columnconfigure(column, weight=1)

    def technique_changed(self):
        keep = {k: w.get() for k, w in self.condition_widgets.items()}
        self.build_conditions(keep)
        self.changed()

    def values(self):
        return dict(sample=self.sample.get_value() or '', technique=self.technique.get(), date=self.when.get(),
            title=self.title_entry.get(), conditions={k: w.get() for k, w in self.condition_widgets.items()},
            pooled=self.pooled.get(), notes=text_value(self.notes), files=self.files.paths())

    def save_draft(self):
        if self._loading:
            return
        values = self.values()
        reserved = (self.app.settings.draft(self.DRAFT) or {}).get('reserved_id')
        if reserved:
            values['reserved_id'] = reserved
        meaningful = any(values[k] for k in ('title', 'pooled', 'notes', 'files')) or any(values['conditions'].values())
        self.app.settings.save_draft(self.DRAFT, values if meaningful or reserved else None)

    def restore_draft(self):
        values = self.app.settings.draft(self.DRAFT)
        if not values:
            return
        self._loading = True
        try:
            if values.get('technique') in ids.TECHNIQUE_BY_LABEL:
                self.technique.set(values['technique'])
            self.build_conditions(values.get('conditions'))
            self.when.set(values.get('date') or date.today())
            self.title_entry.insert(0, values.get('title', ''))
            self.pooled.insert(0, values.get('pooled', ''))
            set_text(self.notes, values.get('notes'))
            self.files.set_paths(values.get('files', []))
            self._pending_sample = values.get('sample')
        finally:
            self._loading = False

    def clear(self):
        self._loading = True
        try:
            self.sample.set_value(None)
            self.when.set(date.today())
            self.title_entry.delete(0, 'end'); self.pooled.delete(0, 'end')
            set_text(self.notes, '')
            self.build_conditions()
            self.files.set_paths([])
        finally:
            self._loading = False
        self.app.settings.clear_draft(self.DRAFT)
        self.update_preview()

    def shown(self, sample=None, **kwargs):
        self.data_changed()
        if sample:
            self.sample.set_value(sample['id'])
        self.update_preview()

    def data_changed(self):
        self.sample.set_items(self.app.sample_choices())
        if getattr(self, '_pending_sample', None) and self.app.samples:
            self.sample.set_value(self._pending_sample)
            self._pending_sample = None
        self.update_preview()

    def changed(self):
        self.update_preview()
        if not self._loading:
            if hasattr(self, '_draft_after'):
                self.frame.after_cancel(self._draft_after)
            self._draft_after = self.frame.after(800, self.save_draft)

    def next_id(self):
        sample_id = self.sample.get_value()
        if not sample_id:
            return None
        code = ids.TECHNIQUE_BY_LABEL.get(self.technique.get(), 'OTHER')
        current = self.app.current
        if current and current['sample']['id'] == sample_id:
            return ids.next_data_id(sample_id, code, [d['id'] for d in current['data']])
        return f'{sample_id}-{code}-##'

    def update_preview(self):
        if hasattr(self, 'id_preview'):
            data_id = self.next_id()
            self.id_preview.configure(text=f'Will be saved as  {data_id}' if data_id else 'Choose a sample to see the data ID.')

    def inputs(self):
        """Everything typed in the form, read on the UI thread."""
        return dict(sample_id=self.sample.get_value() or '', technique=self.technique.get(), profile=self.app.settings.profile,
            measured_date=self.when.get(), conditions={k: w.get() for k, w in self.condition_widgets.items()},
            notes=text_value(self.notes), title=self.title_entry.get(), pooled_with=self.pooled.get().split(','),
            files=[i.name for i in self.files.items], app_version=__version__)

    def build_record(self, data_id, inputs=None):
        return records.data_record(data_id=data_id, **(inputs or self.inputs()))

    def preview(self):
        app = self.app
        if getattr(self, '_saving', False):
            return app.error('This upload is still in progress.')
        if not app.settings.has_profile():
            return app.error('Set up your profile in Settings first.')
        sample_id = self.sample.get_value()
        if not sample_id or not app.find_sample(sample_id):
            return app.error('Choose the sample this data belongs to (refresh the sample list if it is new).')
        if not self.when.date():
            return app.error('Choose the measurement date.')
        try:
            record, problems = self.build_record(self.next_id())
            check_files(self.files.items)
        except (records.RecordError, ids.IdError) as error:
            return app.error(error)
        except StoreError as error:
            record, problems = self.build_record(self.next_id())[0], [str(error)]
        if not app.connected(quiet=True):
            problems = problems + ['Connect to SciSure to save (your draft is kept).']
        PreviewDialog(app, 'Save data', record['id'], records.summary_lines(record), self.files.items, problems, self.save)

    def save(self):
        app, files, inputs, values = self.app, list(self.files.items), self.inputs(), self.values()
        sample = app.find_sample(inputs['sample_id'])
        if not sample:
            return app.error('Choose the sample this data belongs to.')
        reserved = (app.settings.draft(self.DRAFT) or {}).get('reserved_id')
        self._saving = True
        def work(progress):
            return app.store.add_data(sample, lambda did: records.data_record(data_id=did or 'x', **inputs)[0], files,
                progress, reserved_id=reserved)
        def done(saved):
            self._saving = False
            if self.values() == values:
                self.clear()
            else:
                app.settings.clear_draft(self.DRAFT)
                self.save_draft()
            app.info(f"Saved {saved['record']['id']} with {len(saved['record']['files'])} file(s). Every file was checked "
                'after upload.' + readable_note(saved))
            app.open_sample(sample['experiment_id'])
        def failed(error):
            self._saving = False
            if app.store and app.store.last_reserved:
                app.settings.save_draft(self.DRAFT, dict(self.values(), reserved_id=app.store.last_reserved))
            app.error(str(friendly(error)) + '\n\nYour form is kept. Click Preview & save again to finish; files that '
                'already arrived are not sent twice.')
        app.run('Uploading…', work, done, failed)


# ============================================================================ procedures
class ProceduresPage(Page):
    def build(self):
        actions = self.heading(self.frame, 'Procedures', 'Shared master recipes. Each lab follows the same procedure and '
            'records only what it did differently on each sample.')
        ttk.Button(actions, text='+ New procedure', style='Primary.TButton', command=lambda: self.app.show('procedure_form')).pack(side='left')
        body = ttk.Frame(self.frame, style='Page.TFrame')
        body.pack(fill='both', expand=True)
        left = Card(body, padding=14)
        left.pack(side='left', fill='y', padx=(0, 14))
        frame, self.view = tree(left.body, (('id', 'ID', 120), ('name', 'Name', 180)), height=18)
        frame.pack(fill='both', expand=True)
        self.view.bind('<<TreeviewSelect>>', lambda _e: self.select())
        right = Card(body, padding=18)
        right.pack(side='left', fill='both', expand=True)
        top = ttk.Frame(right.body)
        top.pack(fill='x')
        self.name = ttk.Label(top, text='Select a procedure', style='ID.TLabel')
        self.name.pack(side='left')
        self.version = ttk.Combobox(top, state='readonly', width=12)
        self.version.bind('<<ComboboxSelected>>', lambda _e: self.render_version())
        self.version.pack(side='right')
        buttons = ttk.Frame(right.body)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='Make a sample with this', style='Primary.TButton', command=self.make_sample).pack(side='left')
        ttk.Button(buttons, text='Save a new version', command=self.new_version).pack(side='left', padx=6)
        ttk.Button(buttons, text='Download documents', command=self.download).pack(side='left')
        buttons.pack_configure(anchor='w')
        self.details = ScrollFrame(right.body)
        self.details.pack(fill='both', expand=True, pady=(10, 0))
        self.selected_procedure = None

    def shown(self, **kwargs):
        self.data_changed()

    def data_changed(self):
        self.view.delete(*self.view.get_children())
        for p in self.app.procedures:
            self.view.insert('', 'end', iid=str(p['experiment_id']), values=(p['id'], p['name']))

    def select(self):
        selection = self.view.selection()
        match = next((p for p in self.app.procedures if str(p['experiment_id']) == selection[0]), None) if selection else None
        if match:
            self.app.load_procedure(match['id'], self.loaded)

    def loaded(self, value):
        self.selected_procedure = value
        if not value:
            return
        self.name.configure(text=f"{value['id']}  ·  {value['name']}")
        self.version.configure(values=[f"version {v['version']}" for v in value['versions']])
        if value['versions']:
            self.version.set(f"version {value['versions'][-1]['version']}")
        self.render_version()

    def current_version(self):
        value = self.selected_procedure
        if not value or not value['versions']:
            return None
        chosen = self.version.get().replace('version ', '')
        return next((v for v in value['versions'] if str(v['version']) == chosen), value['versions'][-1])

    def render_version(self):
        for child in self.details.body.winfo_children():
            child.destroy()
        version = self.current_version()
        if not version:
            return
        for label, value in records.summary_lines(version['record']):
            ttk.Label(self.details.body, text=label, style='Hint.TLabel').pack(anchor='w', pady=(6, 0))
            ttk.Label(self.details.body, text=value, style='Value.TLabel', wraplength=620, justify='left').pack(anchor='w')
        for f in version['files']:
            ttk.Label(self.details.body, text='Document: ' + str(f.get('realName')), style='Hint.TLabel').pack(anchor='w', pady=(6, 0))

    def make_sample(self):
        if self.selected_procedure:
            self.app.show('new_sample', procedure_id=self.selected_procedure['id'])

    def new_version(self):
        version = self.current_version()
        if version:
            self.app.show('procedure_form', base=dict(self.selected_procedure, record=version['record']))

    def download(self):
        version = self.current_version()
        if version and version['files']:
            self.app.download_files(version['section_id'], version['files'], version['record']['files'])


class ProcedureForm(Page):
    DRAFT = 'procedure'

    def build(self):
        self.base = None
        self.head_actions = self.heading(self.frame, 'New procedure', 'Write the master recipe once. Every lab picks it '
            'when registering a sample; the fields below become the sample form\'s starting point.')
        self.title_label = self.title_widget
        ttk.Button(self.head_actions, text='Cancel', command=lambda: self.app.show('procedures')).pack(side='left')
        scroll = ScrollFrame(self.frame, style='Page.TFrame')
        scroll.pack(fill='both', expand=True)
        card = Card(scroll.body, padding=18)
        card.pack(fill='x', pady=(0, 12))
        self.name = ttk.Entry(card.body, width=50)
        field(card.body, 0, 'Procedure name', self.name, 'Short and specific, e.g. "K-promoted Mo2C/γ-Al2O3 by IWI + carburization"')
        self.description = text_box(card.body, height=2)
        field(card.body, 2, 'Purpose / when to use it (optional)', self.description)
        card.body.columnconfigure(0, weight=1)
        recipe = Card(scroll.body, padding=18)
        recipe.pack(fill='x', pady=(0, 12))
        ttk.Label(recipe.body, text='Recipe', style='Sub.TLabel').grid(row=0, column=0, sticky='w')
        self.recipe = RecipeFields(recipe.body, lambda: None)
        files = Card(scroll.body, padding=18)
        files.pack(fill='x', pady=(0, 12))
        ttk.Label(files.body, text='Procedure documents (optional)', style='Sub.TLabel').pack(anchor='w')
        self.files = FileList(files.body)
        self.files.pack(fill='x')
        foot = ttk.Frame(self.frame, style='Page.TFrame')
        foot.pack(fill='x', pady=(10, 0))
        self.id_preview = ttk.Label(foot, style='Subtitle.TLabel')
        self.id_preview.pack(side='left')
        ttk.Button(foot, text='Preview & save', style='Primary.TButton', command=self.preview).pack(side='right')

    def shown(self, base=None, **kwargs):
        self.base = base
        self.files.set_paths([])
        if base:
            record = base['record']
            self.title_label.configure(text=f"New version of {base['id']}")
            self.name.delete(0, 'end'); self.name.insert(0, record['name'])
            set_text(self.description, record.get('description', ''))
            self.recipe.set(record['recipe'])
            self.id_preview.configure(text=f"Will be saved as  {base['id']} version {base['versions'][-1]['version'] + 1}")
        else:
            self.title_label.configure(text='New procedure')
            self.name.delete(0, 'end'); set_text(self.description, ''); self.recipe.set({})
            p = self.app.settings.profile
            try:
                pid = ids.next_procedure_id(p['lab'], [x['id'] for x in self.app.procedures])
                self.id_preview.configure(text=f'Will be saved as  {pid} version 1')
            except (KeyError, ids.IdError):
                self.id_preview.configure(text='')

    def inputs(self):
        return dict(version=1, name=self.name.get(), profile=self.app.settings.profile, recipe=self.recipe.get(),
            description=text_value(self.description), files=[i.name for i in self.files.items], app_version=__version__)

    def build_record(self, procedure_id, inputs=None):
        return records.procedure_record(procedure_id=procedure_id, **(inputs or self.inputs()))

    def preview(self):
        app = self.app
        if not app.settings.has_profile():
            return app.error('Set up your profile in Settings first.')
        pid = self.base['id'] if self.base else ids.next_procedure_id(app.settings.profile['lab'], [x['id'] for x in app.procedures])
        record, problems = self.build_record(pid)
        try:
            check_files(self.files.items, required=False)
        except StoreError as error:
            problems.append(str(error))
        if not app.connected(quiet=True):
            problems.append('Connect to SciSure to save.')
        PreviewDialog(app, 'Save procedure', self.id_preview.cget('text').replace('Will be saved as  ', ''),
            records.summary_lines(record)[1:], self.files.items, problems, self.save)

    def save(self):
        app, files, base, inputs = self.app, list(self.files.items), self.base, self.inputs()
        if getattr(self, '_saving', False):
            return app.error('This procedure is still being saved.')
        self._saving = True
        build = lambda pid: records.procedure_record(procedure_id=pid or 'PRC-UR-000', **inputs)[0]
        def work(progress):
            if base:
                return app.store.add_procedure_version(base, build, files, progress)
            return app.store.create_procedure(build, files, progress)
        def failed(error):
            self._saving = False
            app.error(str(friendly(error)) + '\n\nYour procedure is kept. Click Preview & save again to finish.')
        def done(saved):
            self._saving = False
            record = saved['record']
            app.procedure_cache.pop(record['id'], None)
            app.info(f"Saved {record['id']} version {record['version']}." + readable_note(saved))
            app.refresh(then=lambda: app.show('procedures'))
        app.run('Saving procedure…', work, done, failed)


# ============================================================================ export & AI
class ExportPage(Page):
    TABLES = (('samples', 'Samples'), ('data_records', 'Data records'), ('procedures', 'Procedures'),
        ('original_files', 'Original files'))

    def build(self):
        self.built, self.built_filters, self.table = None, None, 'samples'
        actions = self.heading(self.frame, 'Export & AI access', 'Preview exactly what is in the database, then save it as '
            'analysis-ready files. Nothing here changes data in SciSure.')
        ttk.Button(actions, text='AI connection…', command=self.ai_dialog).pack(side='left')
        choose = Card(self.frame, padding=16)
        choose.pack(fill='x', pady=(0, 12))
        body = choose.body
        self.search = ttk.Entry(body, width=30)
        field(body, 0, 'Samples matching (optional)', self.search, 'Words in the sample ID, composition or procedure. '
            'Leave blank for every sample.')
        self.lab = ttk.Combobox(body, values=(ALL,) + ids.LAB_NAMES, state='readonly', width=16)
        self.lab.set(ALL)
        field(body, 0, 'Samples from', self.lab, column=1)
        self.technique = ttk.Combobox(body, values=(ALL,) + tuple(ids.TECHNIQUES.values()), state='readonly', width=30)
        self.technique.set(ALL)
        field(body, 0, 'Data type', self.technique, 'Choosing a data type keeps only samples that have that kind of data.',
            column=2)
        ttk.Button(body, text='Preview', style='Primary.TButton', command=self.preview).grid(row=1, column=3, sticky='w')
        body.columnconfigure(0, weight=1)
        for widget in (self.lab, self.technique):
            widget.bind('<<ComboboxSelected>>', lambda _e: self.filters_changed())
        self.search.bind('<KeyRelease>', lambda _e: self.filters_changed())
        preview = Card(self.frame, padding=16)
        preview.pack(fill='both', expand=True, pady=(0, 12))
        top = ttk.Frame(preview.body)
        top.pack(fill='x')
        self.tabs = {}
        for key, label in self.TABLES:
            button = ttk.Button(top, text=label, style='Segment.TButton', command=lambda k=key: self.show_table(k))
            button.pack(side='left', padx=(0, 6))
            self.tabs[key] = button
        self.filter = ttk.Entry(top, width=24)
        self.filter.pack(side='right')
        self.filter.bind('<KeyRelease>', lambda _e: self.show_table(self.table))
        ttk.Label(top, text='Find in table', style='Hint.TLabel').pack(side='right', padx=6)
        self.summary = ttk.Label(preview.body, style='Hint.TLabel', wraplength=900, justify='left',
            text='Choose what to export and click Preview. Everything that would be saved is shown here first.')
        self.summary.pack(anchor='w', pady=(8, 6))
        frame = ttk.Frame(preview.body)
        frame.pack(fill='both', expand=True)
        self.view = ttk.Treeview(frame, show='headings', selectmode='browse', height=8)
        ybar = ttk.Scrollbar(frame, orient='vertical', command=self.view.yview)
        xbar = ttk.Scrollbar(frame, orient='horizontal', command=self.view.xview)
        self.view.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.view.grid(row=0, column=0, sticky='nsew')
        ybar.grid(row=0, column=1, sticky='ns')
        xbar.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.view.bind('<Double-1>', lambda _e: self.row_details())
        self.view.bind('<Return>', lambda _e: self.row_details())
        ttk.Label(preview.body, text='Scroll sideways for more columns. Double-click a row to see all of its values.',
            style='Hint.TLabel').pack(anchor='w', pady=(6, 0))
        save = Card(self.frame, padding=16)
        save.pack(fill='x')
        self.originals = tk.BooleanVar(value=False)
        self.originals_box = ttk.Checkbutton(save.body, text='Also download the original data files', variable=self.originals)
        self.originals_box.pack(side='left')
        self.export_button = ttk.Button(save.body, text='Choose folder & export', style='Primary.TButton', command=self.export)
        self.export_button.pack(side='right')
        self.export_button.state(['disabled'])
        self.stale = ttk.Label(save.body, style='Hint.TLabel')
        self.stale.pack(side='right', padx=10)

    def filters(self):
        return (self.search.get().strip(), self.lab.get(), self.technique.get())

    def filters_changed(self):
        if self.built is not None and self.filters() != self.built_filters:
            self.export_button.state(['disabled'])
            self.stale.configure(text='Filters changed — click Preview again to see what will be exported.')
        elif self.built is not None:
            self.export_button.state(['!disabled'])
            self.stale.configure(text='')

    def preview(self):
        app = self.app
        if not app.connected():
            return
        from .dataset import build_dataset
        search, lab_name, technique_label = self.filters()
        lab = None if lab_name == ALL else ids.lab_key(lab_name)
        technique = None if technique_label == ALL else ids.TECHNIQUE_BY_LABEL[technique_label]
        words = search.casefold().split()
        samples = [s for s in app.samples if (lab is None or s['lab'] == lab)
            and all(w in f"{s['id']} {s['composition']} {s.get('origin', '')}".casefold() for w in words)]
        filters = self.filters()
        def done(built):
            self.built, self.built_filters = built, filters
            self.export_button.state(['!disabled'])
            self.stale.configure(text='')
            self.show_table('samples')
        app.run(f'Reading {len(samples)} sample(s) for the preview…',
            lambda progress: build_dataset(app.store, samples, technique, progress), done)

    def show_table(self, key):
        self.table = key
        for name, button in self.tabs.items():
            count = len(self.built['tables'][name]) if self.built else 0
            label = dict(self.TABLES)[name] + (f'  ({count})' if self.built else '')
            button.configure(text=label, style='SelectedSegment.TButton' if name == key else 'Segment.TButton')
        self.view.delete(*self.view.get_children())
        if not self.built:
            return
        rows = self.built['tables'][key]
        words = self.filter.get().casefold().split()
        if words:
            rows = [r for r in rows if all(w in ' '.join(str(v) for v in r.values()).casefold() for w in words)]
        columns = []
        for row in rows or self.built['tables'][key][:1]:
            for column in row:
                if column not in columns:
                    columns.append(column)
        self.columns = columns
        self.view.configure(columns=[f'c{i}' for i in range(len(columns))], displaycolumns='#all')
        for i, column in enumerate(columns):
            sample = [self.text(r.get(column)) for r in rows[:50]]
            width = min(320, max(90, 10 * max([len(column)] + [len(x) for x in sample]) + 16))
            self.view.heading(f'c{i}', text=column, anchor='w')
            self.view.column(f'c{i}', width=width, minwidth=60, stretch=False, anchor='w')
        self.visible_rows = rows
        for index, row in enumerate(rows):
            self.view.insert('', 'end', iid=str(index), values=[self.text(row.get(c)) for c in columns])
        b = self.built
        size = human_size(b['file_bytes']) if b['file_count'] else '0 bytes'
        self.summary.configure(text=f"{len(b['tables']['samples'])} samples · {len(b['tables']['data_records'])} data records · "
            f"{len(b['tables']['procedures'])} procedure versions · {b['file_count']} original files ({size}). "
            + (f"Showing {len(rows)} matching rows." if words else 'This is exactly what the export will contain.'))
        self.originals_box.configure(text=f'Also download the {b["file_count"]} original data files ({size})')

    @staticmethod
    def text(value):
        if value is None:
            return ''
        if isinstance(value, float):
            return f'{value:g}'
        return str(value).replace('\n', ' ')

    def row_details(self):
        selection = self.view.selection()
        if not selection:
            return
        row = self.visible_rows[int(selection[0])]
        window = tk.Toplevel(self.app.root)
        window.title(str(next(iter(row.values()), 'Row')))
        window.configure(background='white', padx=16, pady=14)
        window.transient(self.app.root)
        box = text_box(window, height=24, width=90)
        box.pack(fill='both', expand=True)
        width = max(len(k) for k in row)
        set_text(box, '\n'.join(f'{k.ljust(width)}   {self.text(v)}' for k, v in row.items() if self.text(v) != ''))
        box.configure(state='disabled', font=('Consolas' if sys.platform == 'win32' else 'Menlo', 10))
        ttk.Button(window, text='Close', command=window.destroy).pack(anchor='e', pady=(10, 0))

    def export(self):
        app = self.app
        if not self.built or self.filters() != self.built_filters:
            return app.error('Click Preview first, so you can see what will be exported.')
        folder = filedialog.askdirectory(parent=app.root, title='Choose a folder for the export') if not app.smoke else None
        if not folder:
            return
        self.save_to(folder)

    def save_to(self, folder):
        from .dataset import write_dataset
        app, built, originals = self.app, self.built, self.originals.get()
        work = lambda progress: write_dataset(app.store, built, folder, originals=originals, progress=progress)
        app.run('Exporting…', work, lambda result: app.info(f"Exported {result['samples']} samples, {result['data']} data "
            f"records" + (f" and {result['files']} original files" if result['files'] else '') + f" to\n{result['folder']}"))

    def ai_dialog(self):
        window = tk.Toplevel(self.app.root)
        window.title('AI connection')
        window.configure(background='white', padx=22, pady=18)
        window.transient(self.app.root)
        ttk.Label(window, text='Live, read-only connection for AI tools', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(window, text='CATALYST includes a read-only connector (an MCP server) that lets an AI assistant such as '
            'Claude search samples, read recipes and conditions, and pull data tables straight from SciSure with a lab '
            'token. It cannot change or delete anything. Model pipelines can use the same reader from Python '
            '(catalyst_query). Setup: docs/ai-access.md.', style='Value.TLabel', wraplength=560, justify='left').pack(anchor='w', pady=(6, 12))
        buttons = ttk.Frame(window)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Close', command=window.destroy).pack(side='right')
        ttk.Button(buttons, text='Copy setup snippet', style='Primary.TButton', command=self.copy_snippet).pack(side='right', padx=6)

    def copy_snippet(self):
        from .dataset import mcp_snippet
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(mcp_snippet(self.app.settings.server))
        self.app.info('Copied. Paste it into your AI assistant\'s MCP configuration and replace PASTE-LAB-TOKEN with a lab '
            'token (see docs/ai-access.md).')


# ============================================================================ settings
class SettingsPage(Page):
    def build(self):
        self.heading(self.frame, 'Settings', 'Your details are saved on this computer so you never retype them.')
        scroll = ScrollFrame(self.frame, style='Page.TFrame')
        scroll.pack(fill='both', expand=True)
        self.welcome_note = ttk.Label(scroll.body, style='Warn.TLabel', wraplength=860, justify='left',
            text='Welcome to CATALYST. Enter your details once and connect with your lab\'s SciSure token. That\'s all the setup there is.')
        profile = Card(scroll.body, padding=18)
        profile.pack(fill='x', pady=(0, 12))
        self.profile_card = profile
        ttk.Label(profile.body, text='You', style='Sub.TLabel').grid(row=0, column=0, sticky='w')
        self.name = ttk.Entry(profile.body, width=36)
        self.name.bind('<KeyRelease>', lambda _e: self.suggest())
        field(profile.body, 1, 'Full name', self.name)
        self.initials = ttk.Entry(profile.body, width=8)
        field(profile.body, 1, 'Initials (2–4 letters)', self.initials, 'Used in your sample IDs, e.g. UR-MDP-260925-01. '
            'Keep them the same for the whole project.', column=1)
        self.lab = ttk.Combobox(profile.body, values=ids.LAB_NAMES, state='readonly', width=18)
        field(profile.body, 1, 'Lab', self.lab, column=2)
        self.email = ttk.Entry(profile.body, width=36)
        field(profile.body, 3, 'E-mail (optional)', self.email)
        ttk.Button(profile.body, text='Save profile', style='Primary.TButton', command=self.save_profile).grid(row=4, column=2, sticky='e')
        self.id_example = ttk.Label(profile.body, style='Hint.TLabel')
        self.id_example.grid(row=5, column=0, columnspan=3, sticky='w', pady=(8, 0))
        connection = Card(scroll.body, padding=18)
        connection.pack(fill='x', pady=(0, 12))
        ttk.Label(connection.body, text='SciSure connection', style='Sub.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(connection.body, text='Paste your lab\'s API token (the coordinator gives each lab one). CATALYST keeps '
            'it in this computer\'s secure credential store, never in a file.', style='Hint.TLabel', wraplength=820,
            justify='left').grid(row=1, column=0, columnspan=3, sticky='w')
        self.server = ttk.Entry(connection.body, width=40)
        field(connection.body, 2, 'SciSure server', self.server)
        self.token = ttk.Entry(connection.body, width=40, show='•')
        field(connection.body, 2, 'Lab token', self.token, column=1)
        self.remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(connection.body, text='Remember on this computer', variable=self.remember).grid(row=4, column=0, sticky='w', pady=(8, 0))
        buttons = ttk.Frame(connection.body)
        buttons.grid(row=5, column=0, columnspan=3, sticky='w', pady=(8, 0))
        ttk.Button(buttons, text='Connect', style='Primary.TButton', command=self.connect).pack(side='left')
        ttk.Button(buttons, text='Disconnect', command=lambda: self.app.disconnect()).pack(side='left', padx=6)
        ttk.Button(buttons, text='Forget saved token', command=lambda: self.app.disconnect(forget=True)).pack(side='left')
        self.connection_status = ttk.Label(connection.body, style='Hint.TLabel', wraplength=820, justify='left')
        self.connection_status.grid(row=6, column=0, columnspan=3, sticky='w', pady=(8, 0))
        coordinator = Card(scroll.body, padding=18)
        coordinator.pack(fill='x', pady=(0, 12))
        ttk.Label(coordinator.body, text='Coordinator tools', style='Sub.TLabel').pack(anchor='w')
        ttk.Label(coordinator.body, text='Only needed once for the whole consortium. Creates the shared "CATALYST" project '
            'with its Samples and Procedures studies. Afterwards, share the project with each lab account in SciSure '
            '(collaborators with edit rights).', style='Hint.TLabel', wraplength=820, justify='left').pack(anchor='w', pady=(4, 8))
        tools = ttk.Frame(coordinator.body)
        tools.pack(anchor='w')
        ttk.Button(tools, text='Set up CATALYST workspace', command=self.setup_workspace).pack(side='left')
        ttk.Button(tools, text='Write readable copies for older records', command=self.write_readable).pack(side='left', padx=6)
        about = Card(scroll.body, padding=18)
        about.pack(fill='x')
        ttk.Label(about.body, text=f'CATALYST desktop {__version__}', style='Sub.TLabel').pack(anchor='w')
        links = ttk.Frame(about.body)
        links.pack(anchor='w', pady=(6, 6))
        ttk.Button(links, text='Check for updates', command=lambda: self.app.check_for_update(quiet=False)).pack(side='left')
        ttk.Button(links, text='Open download page', command=self.open_downloads).pack(side='left', padx=6)
        ttk.Button(links, text='Copy download link for a colleague', command=self.copy_link).pack(side='left')
        ttk.Label(about.body, text='The previous review-based interface can still be opened with '
            '"CATALYST --classic" for older saved reviews.', style='Hint.TLabel').pack(anchor='w')

    def open_downloads(self):
        from .updates import DOWNLOAD_PAGE
        webbrowser.open(DOWNLOAD_PAGE)

    def copy_link(self):
        from .updates import DOWNLOAD_PAGE
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(DOWNLOAD_PAGE)
        self.app.info('Download link copied:\n' + DOWNLOAD_PAGE + '\n\nPaste it in an e-mail. Your colleague also needs '
            'their lab\'s SciSure token (from the coordinator).')

    def welcome(self):
        self.welcome_note.pack(fill='x', pady=(0, 12), before=self.profile_card)

    def shown(self, **kwargs):
        p = self.app.settings.profile
        for widget, key in ((self.name, 'name'), (self.initials, 'initials'), (self.email, 'email')):
            widget.delete(0, 'end'); widget.insert(0, p.get(key, ''))
        self.lab.set(ids.lab_name(p['lab']) if p.get('lab') else '')
        self.server.delete(0, 'end'); self.server.insert(0, self.app.settings.server)
        self.remember.set(self.app.settings.data.get('remember_token', True))
        self.render_status()
        self.example()

    def suggest(self):
        if not self.initials.get() or self.initials.get() == getattr(self, '_suggested', None):
            self._suggested = ids.suggest_initials(self.name.get())
            self.initials.delete(0, 'end'); self.initials.insert(0, self._suggested)
        self.example()

    def example(self):
        try:
            self.id_example.configure(text='Your sample IDs will look like  ' + ids.sample_id(self.lab.get() or 'UR',
                self.initials.get() or 'XX', date.today(), 1))
        except ids.IdError:
            self.id_example.configure(text='')

    def save_profile(self, quiet=False):
        try:
            initials = ids.clean_initials(self.initials.get())
            lab = ids.lab_key(self.lab.get())
        except ids.IdError as error:
            return self.app.error(error)
        if not self.name.get().strip():
            return self.app.error('Enter your full name.')
        others = {s['initials'] for s in self.app.samples if s['lab'] == lab}
        old = self.app.settings.profile.get('initials')
        self.app.settings.set_profile(self.name.get(), initials, lab, self.email.get())
        self.welcome_note.pack_forget()
        self.app.update_identity()
        if not quiet:
            note = (f'\n\nHeads-up: samples with the initials {initials} already exist in your lab. If they are not yours, '
                'choose different initials.' if initials in others and initials != old else '')
            self.app.info('Profile saved.' + note)
        return True

    def connect(self):
        if not self.app.settings.has_profile() and self.name.get().strip():
            if not self.save_profile(quiet=True):
                return
        server, token = self.server.get().strip(), self.token.get().strip()
        if not token:
            try:
                from .credentials import load_token
                token = load_token(server) or ''
            except Exception:
                token = ''
        if not token:
            return self.app.error('Paste your lab\'s SciSure token.')
        def after():
            self.token.delete(0, 'end')
            self.render_status()
            account = self.app.connection['account']
            if not self.app.settings.has_profile():
                suggested = ids.lab_from_email(account.get('email'))
                if suggested: self.lab.set(ids.lab_name(suggested))
            if self.app.settings.has_profile():
                self.app.show('samples')
        self.app.connect(server, token, remember=self.remember.get(), then=after)

    def render_status(self):
        app = self.app
        if not app.connection:
            text = 'Not connected.'
        else:
            account = app.connection['account']
            text = (f"Connected to {app.store.client.origin} as {account.get('name') or 'the lab account'} "
                f"({account.get('email') or 'no e-mail'}) in group “{app.connection['group_name']}”. ")
            text += ('The shared CATALYST workspace is available.' if app.store.workspace_ready() else
                'This account cannot see the shared CATALYST workspace yet.')
        self.connection_status.configure(text=text)

    def write_readable(self):
        app = self.app
        if not app.connected():
            return
        app.run('Adding readable copies in SciSure…', lambda progress: app.store.write_missing_readable(progress),
            lambda r: app.info(f"Checked {r['checked']} samples and procedures: added {r['written']} readable cop"
                f"{'y' if r['written'] == 1 else 'ies'}" + (f", {r['failed']} could not be written." if r['failed'] else '.')))

    def setup_workspace(self):
        app = self.app
        if not app.store:
            return app.error('Connect first.')
        if not app.smoke and not messagebox.askyesno('CATALYST', 'Create the shared "CATALYST" project and its Samples and '
                'Procedures studies in this SciSure group (only if they do not already exist)?', parent=app.root):
            return
        app.run('Setting up the CATALYST workspace…', lambda progress: app.store.create_workspace(),
            lambda _w: (self.render_status(), app.update_identity(), app.refresh(),
                app.info('The CATALYST workspace is ready. In SciSure, share the "CATALYST" project with every lab account '
                    '(collaborators, edit rights) so their experiments are visible to everyone.')))


class HelpPage(Page):
    TEXT = (
        ('The idea', 'Every physical catalyst gets one sample ID, for example UR-MDP-260925-01 (lab – your initials – '
            'synthesis date – number). Everything anyone measures on it, in any lab, is attached to that ID.'),
        ('Register a sample', 'New sample → pick the shared procedure → the recipe fills in → change only what you did '
            'differently → Preview & save. The ID is created for you. Write it on the vial.'),
        ('Upload data', 'Upload data → pick the sample (type part of the ID or composition) → technique → date → add '
            'files → Preview & save. Your name and lab are added automatically. Files are stored unchanged and '
            'checked after upload.'),
        ('Find data', 'Samples → search or filter → double-click a sample. Its page shows the recipe, every data '
            'record from every lab, and the shipping log. Select a record and click Download files.'),
        ('Ship a sample', 'Open the sample → Log shipment. The receiving lab uploads its data to the same sample ID.'),
        ('A derivative', 'If you change a sample physically (reduce, passivate, pelletize, or recover it after '
            'reaction and want to track it separately), open it and click "New sample made from this". It gets its own '
            'ID linked to the parent.'),
        ('Drafts', 'Unfinished forms are saved on this computer automatically and come back when you reopen the app. '
            'Only typed text and file locations are kept, not the files themselves.'),
        ('Procedures', 'A procedure is the shared master recipe. Improve it with "Save a new version"; samples always '
            'record which version they followed.'),
        ('For AI and analysis', 'Export & AI access → Export a dataset, or connect an AI assistant with the read-only '
            'connector (docs/ai-access.md).'),
        ('Install on another computer', 'Settings → "Copy download link for a colleague", or send '
            'github.com/mporosoff/CATALYST/releases/latest. They download one file (Windows or Mac) and need their '
            'lab\'s SciSure token. When a new version is out, an Update button appears in the sidebar.'),
    )

    def build(self):
        self.heading(self.frame, 'Help', 'CATALYST in two minutes.')
        scroll = ScrollFrame(self.frame, style='Page.TFrame')
        scroll.pack(fill='both', expand=True)
        for title, text in self.TEXT:
            card = Card(scroll.body, padding=16)
            card.pack(fill='x', pady=(0, 10))
            ttk.Label(card.body, text=title, style='Sub.TLabel').pack(anchor='w')
            ttk.Label(card.body, text=text, style='Value.TLabel', wraplength=860, justify='left').pack(anchor='w', pady=(4, 0))


# ============================================================================ entry point
def self_test():
    """Offline start-up check used by the release build (no network, no real data)."""
    import tempfile
    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory() as folder:
        settings = Settings(Path(folder) / 'settings.json')
        settings.set_profile('Synthetic Researcher', 'SR', 'university-of-rochester')
        app = App(root, settings=settings, smoke=True)
        for name in app.pages:
            if name not in ('sample', 'procedure_form'):
                app.show(name)
        root.update_idletasks()
        assert ids.next_sample_id([], 'UR', 'SR', date(2026, 9, 25)) == 'UR-SR-260925-01'
        from .credentials import system_store
        assert system_store() is not None
    root.destroy()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--classic' in argv:
        from .gui import main as classic
        return classic()
    if '--self-test' in argv:
        return self_test()
    root = tk.Tk()
    App(root)
    root.mainloop()
