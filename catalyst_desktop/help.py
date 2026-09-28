"""Compact, read-only task guides for the desktop workspace."""
from __future__ import annotations

from dataclasses import dataclass
from tkinter import ttk
from typing import Callable


@dataclass(frozen=True)
class HelpStep:
    title: str
    text: str
    actions: tuple[tuple[str, str], ...]


GUIDES = {
    'upload': (
        HelpStep('Connect to your lab or sandbox',
            'In SciSure connection, enter the server URL and an API token from that same server, then Connect. '
            'Create a token after normal or university SSO sign-in under Apps & Connections → Manage Authentication. '
            'The sandbox address is https://sandbox.elabjournal.com.',
            (('Open SciSure connection', 'connection'),)),
        HelpStep('Start with your sample',
            'Samples & data is the main workspace. Search inventory and saved CATALYST records by name or label, '
            'then open a sample to see its linked records across experiments. Choose Add measurement or Record synthesis. '
            'New sample registers a material once; later work reuses it.',
            (('Open Samples & data', 'catalog'),)),
        HelpStep('Choose the run',
            'Use Existing run or New run on the upload form. A run is a SciSure experiment within a project and study. '
            'You can create these from the chooser after reviewing their names. Several files from the same execution '
            'can share one run. Each connected preview records its chosen run, even if you select another run later.',
            (('Open Add data', 'submission'),)),
        HelpStep('Reuse a sample and procedure',
            'Search saved or staged samples and methods. Native inventory appears in the sample search. '
            'SciSure protocols… finds published native method versions. + Add new sample and + Add new procedure '
            'keep the current data draft intact while preparing an unapproved definition. Methods are filtered by technique.',
            (('Open Add data', 'submission'),)),
        HelpStep('Record only the relevant information',
            'Characterization requires the sample, files, date and operator, with a reusable method. '
            'Historical measurements may explicitly use Method not recorded; the missing method remains visible in review. '
            'Synthesis execution links the product and synthesis method; a reusable procedure alone requires no sample. '
            'Hover over a field for its meaning. IDs are created internally.',
            (('Open Add data', 'submission'),)),
        HelpStep('Set up native sample inventory once',
            'Connected new samples and selected native samples include reviewed inventory actions. '
            'For new CATALYST samples, Set up sample inventory prepares the additive Material v2 configuration. '
            'Review before applying. Existing inventory samples can be reused without installing a new type.',
            (('Open SciSure connection', 'connection'), ('Open Add data', 'submission'))),
        HelpStep('Attach files or sample documents',
            'Measurement files are preserved by default. Table conversion is optional. For certificates, images '
            'or other documents that belong directly to an existing native sample, open its sample page and use '
            'Attach sample documents. Review the selected files and sample before sending; no experiment is required. '
            'Each upload adds a separate document group and retains existing attachments.',
            (('Open Samples & data', 'catalog'), ('Open Add data', 'submission'))),
        HelpStep('Review files, sample, method and run together',
            'Stage this record adds a grouped record to Batch review. Batch files… creates one record per file using '
            'the displayed context. Check the Files, Sample, Procedure / version and Run / experiment columns; edit any '
            'incorrect association. Definitions must appear before records that reference them.',
            (('Open Batch review', 'batch'),)),
        HelpStep('Resolve an error at its source',
            'In Review → Validation, select the issue and click its correction button. CATALYST returns to the '
            'record and focuses the sample, method or field needing attention. A staged record is loaded for editing '
            'and its approval is cleared. Correct it and build or stage a new preview.',
            (('Open Review', 'review'),)),
        HelpStep('Approve and send',
            'Inspect each record and its inventory actions, enter reviewer name and note, acknowledge the review '
            'and approve. Send approved records shows the reviewed runs and transfers in order. If a transfer is '
            'uncertain, keep the app open and check SciSure before trying again. Each saved original is verified.',
            (('Open Batch review', 'batch'), ('Open Review', 'review'))),
    ),
    'access': (
        HelpStep('Find the sample',
            'Connect, then open Samples & data. Refresh or search by name, local label or ID. The workspace combines '
            'native inventory with completed CATALYST records visible to your account in its active group. '
            'Coverage notices explain incomplete or bounded searches.',
            (('Open Samples & data', 'catalog'), ('Open SciSure connection', 'connection'))),
        HelpStep('Open its data across experiments',
            'Open the sample to see synthesis, characterization and related calculations across runs. '
            'Open a record for its method, acquisition facts, original-file list and standardized data. '
            'Original tables previews CSV, XLSX and flat JSON; choose a file and worksheet. Tables show up to 1,000 rows. '
            'Only records explicitly linked to this sample are listed as its data.',
            (('Open Samples & data', 'catalog'),)),
        HelpStep('Download a review package',
            'From the sample record list, download a completed review as a ZIP containing unchanged originals, '
            'approved context, receipt and standardized JSON/CSV where available. All originals are checked before saving. '
            'Incomplete transfers must be reconciled before exporting a complete package.',
            (('Open Samples & data', 'catalog'),)),
        HelpStep('Read native experiment links',
            'A sample page also lists experiments where SciSure records it as Used or Generated. '
            'Experiment attachment browsing is labeled separately: an experiment with several samples does not prove '
            'that every attachment belongs to every sample. Open in SciSure to inspect the notebook context.',
            (('Open Samples & data', 'catalog'),)),
        HelpStep('Download sample documents',
            'On an inventory sample, Sample documents lists files linked directly through native FILE fields. '
            'Select a document and choose Download selected document. Attach sample documents reviews new additions '
            'while preserving existing links.',
            (('Open Samples & data', 'catalog'),)),
        HelpStep('Browse by experiment when useful',
            'Saved records remains available for experiment-based access. Select the experiment, then List saved reviews '
            'and Open review. More actions → Download review package… exports the complete package. '
            'Original files → Browse attachments lets you read supported tables or Download… an individual file.',
            (('Open Saved records', 'records'),)),
    ),
}

class HelpPane(ttk.Frame):
    """Show one complete guide step; action buttons only navigate the workspace.

    ``navigate`` receives submission, review, batch, connection, records or catalog.
    The caller controls showing/hiding the pane, so progress survives toggles.
    """

    def __init__(self, parent, navigate: Callable[[str], None]):
        super().__init__(parent, padding=(18, 12))
        self.navigate = navigate
        self.mode = 'upload'
        self.positions = {name: 0 for name in GUIDES}
        self.columnconfigure(0, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='Help', style='Sub.TLabel').pack(side='left')
        modes = ttk.Frame(header)
        modes.pack(side='right')
        self.mode_buttons = {}
        for mode, label in (('upload', 'Upload data'), ('access', 'Access / download')):
            button = ttk.Button(modes, text=label, style='Segment.TButton',
                command=lambda mode=mode: self.show_guide(mode))
            button.pack(side='left', padx=(5, 0))
            self.mode_buttons[mode] = button

        self.step_label = ttk.Label(self, style='Small.TLabel')
        self.step_label.grid(row=1, column=0, sticky='w', pady=(8, 3))
        self.title_label = ttk.Label(self, style='Sub.TLabel', wraplength=600)
        self.title_label.grid(row=2, column=0, sticky='ew')
        self.body_label = ttk.Label(self, style='Muted.TLabel', wraplength=600, justify='left')
        self.body_label.grid(row=3, column=0, sticky='ew', pady=(5, 9))
        self.actions = ttk.Frame(self)
        self.actions.grid(row=4, column=0, sticky='ew')

        footer = ttk.Frame(self)
        footer.grid(row=5, column=0, sticky='ew', pady=(8, 0))
        self.previous_button = ttk.Button(footer, text='← Previous', command=self.previous_step)
        self.previous_button.pack(side='left')
        self.next_button = ttk.Button(footer, text='Next →', command=self.next_step)
        self.next_button.pack(side='right')
        self.bind('<Configure>', self._resize)
        self._render()

    @property
    def step_index(self):
        return self.positions[self.mode]

    def show_guide(self, mode):
        """Switch guides while retaining each guide's current position."""
        if mode not in GUIDES:
            raise ValueError(f'Unknown help guide: {mode}')
        self.mode = mode
        self._render()

    def previous_step(self):
        self.positions[self.mode] = max(0, self.step_index - 1)
        self._render()

    def next_step(self):
        self.positions[self.mode] = min(len(GUIDES[self.mode]) - 1, self.step_index + 1)
        self._render()

    def _resize(self, event):
        width = max(260, event.width - 36)
        self.title_label.configure(wraplength=width)
        self.body_label.configure(wraplength=width)

    def _render(self):
        steps = GUIDES[self.mode]
        step = steps[self.step_index]
        for mode, button in self.mode_buttons.items():
            button.configure(style='SelectedSegment.TButton' if mode == self.mode else 'Segment.TButton')
        self.step_label.configure(text=f'Step {self.step_index + 1} of {len(steps)}')
        self.title_label.configure(text=step.title)
        self.body_label.configure(text=step.text)
        for child in self.actions.winfo_children():
            child.destroy()
        for label, target in step.actions:
            ttk.Button(self.actions, text=label, command=lambda target=target: self.navigate(target)).pack(
                side='left', padx=(0, 8))
        self.previous_button.state(['disabled'] if self.step_index == 0 else ['!disabled'])
        self.next_button.state(['disabled'] if self.step_index == len(steps) - 1 else ['!disabled'])
