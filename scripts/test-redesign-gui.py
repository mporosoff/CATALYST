"""Drive the new interface against an in-memory SciSure with synthetic records.

Usage:  python scripts/test-redesign-gui.py [screenshot-folder]
Without a folder it only checks that every screen works. Screenshots need ImageMagick `import` (Linux/X11).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import tkinter as tk

root_dir = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root_dir), str(root_dir / 'tests')]

from catalyst_desktop import app as appmod, records
from catalyst_desktop.settings import Settings
from catalyst_desktop.scisure import SciSureClient
from catalyst_desktop.store import Store, FileItem
from fake_scisure import FakeSciSure

shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
PROFILE = dict(name='Marc D. Porosoff', initials='MDP', lab='university-of-rochester')
OTHERS = [dict(name='Jane Lee', initials='JL', lab='slac'), dict(name='Wei Tan', initials='WT', lab='astar'),
    dict(name='Ana Ruiz', initials='AR', lab='northwestern')]
RECIPE = dict(method='Incipient wetness impregnation', metals='Mo 10, K 1', support='γ-Al2O3 (Sasol, 200 m²/g)',
    precursors='Ammonium heptamolybdate tetrahydrate (Sigma, 99%)\nKNO3 (Alfa, 99.9%)', solvent='DI water, 0.9 mL/g',
    drying_T_C='110', drying_time_h='12', calcination_T_C='450', calcination_ramp_C_min='5', calcination_time_h='4',
    calcination_atmosphere='static air', activation_type='Carburization', activation_T_C='600',
    activation_ramp_C_min='5', activation_time_h='2', activation_gas='20% CH4/H2, 100 mL/min', batch_size_g='2',
    steps='1. Dissolve precursors.\n2. Impregnate dropwise.\n3. Dry, calcine, carburize, passivate in 1% O2/N2.')


def seed(store):
    store.create_workspace()
    def proc(name, recipe, profile):
        return store.create_procedure(lambda pid: records.procedure_record(procedure_id=pid or 'PRC-UR-000', version=1,
            name=name, profile=profile, recipe=recipe, description='Synthetic example procedure.')[0], [])['procedure']
    p1 = proc('K-promoted Mo2C/γ-Al2O3 (IWI + carburization)', RECIPE, PROFILE)
    p2 = proc('Cu/ZnO/Al2O3 co-precipitation', dict(method='Co-precipitation', metals='Cu 60, Zn 30', support='Al2O3',
        calcination_T_C='350', calcination_time_h='3', steps='Co-precipitate at pH 7, age 1 h, wash, dry, calcine.'), OTHERS[1])
    made = []
    for day, profile, pid, recipe in (('2026-09-22', PROFILE, p1, RECIPE), ('2026-09-24', PROFILE, p1, dict(RECIPE, calcination_T_C='500')),
            ('2026-09-25', PROFILE, p1, dict(RECIPE, metals='Mo 10, K 2')), ('2026-09-23', OTHERS[1], p2, None),
            ('2026-09-23', OTHERS[1], p2, None), ('2026-09-18', OTHERS[2], p1, dict(RECIPE, activation_T_C='650'))):
        versions = store.procedure_versions(pid)
        procedure = dict(pid, version=1, recipe=versions[-1]['record']['recipe'])
        recipe = recipe or versions[-1]['record']['recipe']
        saved = store.create_sample(lambda sid: records.sample_record(sample_id=sid or 'UR-XX-000000-00', profile=profile,
            synthesis_date=day, procedure=procedure, recipe=recipe, composition=records.suggest_composition(recipe),
            amount_g='1.8')[0], [])
        made.append(saved['sample'])
    first = made[0]
    for tech, who, when, cond in (('RXN', PROFILE, '2026-09-26', dict(temperature_C='250–350', pressure_bar='20',
            feed='H2:CO2 = 3:1', catalyst_mass_mg='100')), ('XRD', PROFILE, '2026-09-26', dict(radiation='Cu Kα')),
            ('XAS', OTHERS[0], '2026-10-02', dict(edge='Mo K-edge', mode='Transmission', beamline='SSRL 9-3'))):
        store.add_data(first, lambda did: records.data_record(data_id=did or 'x', sample_id=first['id'], technique=tech,
            profile=who, measured_date=when, conditions=cond, files=['f'])[0],
            [FileItem(name=f'{tech.lower()}-run.csv', content=b'x,y\n1,2\n')])
    store.add_shipment(first, lambda sid: records.shipment_record(shipment_id=sid, sample_id=first['id'], profile=PROFILE,
        to_lab='SLAC', ship_date='2026-09-28', amount='200 mg', tracking='FedEx 7789 0012')[0])


def snap(root, name):
    root.update(); root.update_idletasks()
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
        subprocess.run(['import', '-window', 'root', '-crop', f'{root.winfo_width()}x{root.winfo_height()}+0+0',
            str(shots / f'{name}.png')], check=True)


def main():
    with tempfile.TemporaryDirectory() as folder:
        os.environ['CATALYST_SETTINGS_DIR'] = folder
        settings = Settings(Path(folder) / 'settings.json')
        settings.set_profile(PROFILE['name'], PROFILE['initials'], PROFILE['lab'])
        root = tk.Tk()
        root.geometry('1360x860+0+0')
        app = appmod.App(root, settings=settings, smoke=True)
        store = Store(SciSureClient('synthetic-token', transport=FakeSciSure()), 'demo')
        app.connection = store.connect()
        seed(store)
        # A duplicated ID (e.g. two simultaneous saves) must not break the list.
        store.client.request('/api/v1/experiments', 'POST', dict(studyID=store.workspace['samples_study'],
            name='ASTAR-WT-260923-01 | duplicate for testing | —'))
        app.store, app.connection = store, store.connect()
        app.update_identity()
        app.refresh()
        assert len(app.samples) == 7, app.samples
        app.show('samples'); snap(root, '1-samples')
        page = app.pages['samples']
        page.search.set('Mo'); root.update()
        assert len(page.view.get_children()) == 4 and len(app.pages['samples'].view.get_children()) >= 4
        page.search.set('')
        app.open_sample(app.samples[-1]['id'] if app.samples[-1]['initials'] == 'MDP' else
            next(s['id'] for s in app.samples if s['id'].endswith('260922-01')))
        assert app.page == 'sample' and len(app.current['data']) == 3, app.current
        snap(root, '2-sample-page')
        app.show('new_sample')
        form = app.pages['new_sample']
        form.procedure_box.set_value('PRC-UR-001'); form.pick_procedure('PRC-UR-001')
        form.recipe.widgets['calcination_T_C'].delete(0, 'end'); form.recipe.widgets['calcination_T_C'].insert(0, '480')
        form.recipe_changed()
        assert form.composition.get() == '10 wt% Mo + 1 wt% K on γ-Al2O3', form.composition.get()
        # Tab goes through the recipe top-to-bottom, and leaves multi-line boxes instead of typing a tab.
        widgets = form.recipe.widgets
        root.update()
        order = []
        widget = widgets['method']
        for _ in range(60):
            widget = widget.tk_focusNext()
            order.append(widget)
        position = lambda w: order.index(w) if w in order else 10**6
        assert position(widgets['components'].rows[0][1]) < position(widgets['support']) < position(widgets['solvent']) \
            < position(widgets['calcination_T_C']) < position(widgets['precursors']) < position(widgets['steps'])
        widgets['steps'].focus_force(); root.update()
        widgets['steps'].event_generate('<Tab>'); root.update()
        assert root.focus_get() is not widgets['steps'] and '\t' not in widgets['steps'].get('1.0', 'end')
        # Two metals with explicit units.
        form.recipe.widgets['components'].set([dict(component='Mo', loading=10.0, unit='wt%'),
            dict(component='K', loading=2.0, unit='wt%'), dict(component='Cu', loading=0.5, unit='molar ratio')])
        form.recipe_changed()
        assert form.composition.get() == '10 wt% Mo + 2 wt% K + Cu (0.5 molar ratio) on γ-Al2O3', form.composition.get()
        form.recipe.widgets['components'].set([dict(component='Mo', loading=10.0, unit='wt%'), dict(component='K', loading=1.0, unit='wt%')])
        form.recipe_changed()
        record, problems = form.build_record('UR-MDP-260925-02')
        assert not problems and record['deviations'][0]['field'] == 'calcination_T_C', (problems, record['deviations'])
        snap(root, '3-new-sample')
        form.save()
        assert any(s['id'] == f"UR-MDP-{__import__('datetime').date.today():%y%m%d}-01" for s in app.samples), [s['id'] for s in app.samples]
        app.show('upload', sample=app.samples[0])
        upload = app.pages['upload']
        upload.technique.set('XRD'); upload.technique_changed()
        upload.files.items = [FileItem(name='xrd-scan.xy', content=b'10 1\n')]; upload.files.render()
        snap(root, '4-upload')
        PreviewShot = appmod.PreviewDialog
        record, problems = upload.build_record(upload.next_id())
        dialog = PreviewShot(app, 'Save data', record['id'], records.summary_lines(record), upload.files.items, problems, lambda: None)
        dialog.geometry('+380+160'); snap(root, '5-preview'); dialog.destroy()
        upload.save()
        assert app.page == 'sample' and app.current['data'][-1]['technique'] == 'XRD'
        pid = next(str(p['experiment_id']) for p in app.procedures if p['id'] == 'PRC-UR-001')
        app.show('procedures'); app.pages['procedures'].view.selection_set(pid); app.pages['procedures'].select()
        snap(root, '6-procedures')
        # A commercial / reference catalyst: no procedure, supplier details instead.
        app.show('new_sample')
        form.source.set('commercial'); form.source_changed()
        for key, value in dict(supplier='Clariant', product='MegaMax 800', lot='L-2231', form='6 × 4 mm tablets').items():
            form.commercial[key].insert(0, value)
        form.recipe.widgets['components'].set([dict(component='Cu', loading=60.0, unit='wt%'), dict(component='ZnO', loading=30.0, unit='wt%')])
        form.recipe.widgets['support'].delete(0, 'end'); form.recipe.widgets['support'].insert(0, 'Al2O3')
        form.recipe_changed()
        assert not form.recipe.widgets['calcination_T_C'].winfo_ismapped()
        snap(root, '3b-commercial')
        record, problems = form.build_record('UR-MDP-260925-09')
        assert not problems and record['source'] == 'commercial' and record['composition'] == '60 wt% Cu + 30 wt% ZnO on Al2O3', (problems, record)
        form.save()
        assert any(s.get('source') == 'commercial' for s in app.samples)
        app.show('samples'); snap(root, '1b-samples-with-commercial')
        app.show('export')
        export = app.pages['export']
        export.preview()
        assert export.built and len(export.view.get_children()) == 9, len(export.view.get_children())
        snap(root, '7-export')
        export.show_table('data_records'); snap(root, '7b-export-data')
        assert any(c.startswith('sample_recipe_load_Mo') for c in export.columns), export.columns
        out = Path(folder) / 'export'; out.mkdir()
        from catalyst_desktop.dataset import export_dataset
        export.originals.set(True); export.save_to(out)
        result = export_dataset(store, app.samples, out, originals=True)
        assert result['samples'] == 9 and result['data'] == 4 and result['files'] == 4, result
        assert len(list(out.iterdir())) == 2  # the preview-based export and the direct one
        # Corrections after publishing: fix a sample, replace a data file, withdraw data, retire a sample.
        previews = []
        real_preview = appmod.PreviewDialog
        def auto_preview(app_, title, id_text, lines, files, problems, save):
            previews.append(dict(lines=dict(lines), problems=problems))
            if not problems: save()
        appmod.PreviewDialog = auto_preview
        try:
            first = next(s for s in app.samples if s['id'].endswith('260922-01'))
            app.open_sample(first['id'])
            c = app.current
            fix = appmod.CorrectionDialog(app, c['record'], first['experiment_id'], c['sample_item'],
                lambda: app.refresh(then=lambda: app.open_sample(first['experiment_id'])))
            root.update(); snap(root, '9-correct-sample')
            fix.preview()
            assert 'Nothing has been changed yet.' in previews[-1]['problems'] and any('why' in p for p in previews[-1]['problems'])
            fix.recipe.widgets['components'].set([dict(component='Mo', loading=12.0, unit='wt%'), dict(component='K', loading=1.0, unit='wt%')])
            fix.recipe_changed()
            assert fix.composition.get() == '12 wt% Mo + 1 wt% K on γ-Al2O3', fix.composition.get()
            appmod.set_text(fix.reason, 'Loading was 12 wt%, not 10.')
            fix.preview()
            assert previews[-1]['problems'] == [] and previews[-1]['lines']['What changes'] == \
                'Composition, Active metals / phases and loadings', previews[-1]
            assert app.current['record']['revision'] == 2 and app.current['record']['composition'].startswith('12 wt% Mo')
            assert app.find_sample(first['id'])['composition'].startswith('12 wt% Mo')  # the list shows the correction
            page = app.pages['sample']
            assert page.title.cget('text') == first['id']
            # Another lab's record can't be corrected unless you are the coordinator.
            xas = next(d for d in app.current['data'] if d['technique'] == 'XAS')
            assert not app.can_change(xas['record'], quiet=True)
            settings.set_coordinator(True)
            assert app.can_change(xas['record'], quiet=True)
            settings.set_coordinator(False)
            xrd = next(d for d in app.current['data'] if d['technique'] == 'XRD')
            page.data_view.selection_set(str(xrd['section_id'])); root.update()
            fix = appmod.CorrectionDialog(app, xrd['record'], first['experiment_id'], xrd, page.reload)
            fix.supersede['xrd-run.csv'].set(True)
            fix.files.items = [FileItem(name='xrd-run.csv', content=b'x,y\n1,3\n')]; fix.files.render()
            appmod.set_text(fix.reason, 'Uploaded the wrong scan.')
            fix.preview()
            assert previews[-1]['problems'] == [], previews[-1]
            xrd = next(d for d in app.current['data'] if d['technique'] == 'XRD')
            assert [f['name'] for f in xrd['record']['files']] == ['xrd-run (r2).csv'], xrd['record']['files']
            rxn = next(d for d in app.current['data'] if d['technique'] == 'RXN')
            page.data_view.selection_set(str(rxn['section_id'])); root.update()
            page.withdraw_data()
            reason = next(w for w in root.winfo_children() if isinstance(w, appmod.ReasonDialog))
            appmod.set_text(reason.reason, 'Thermocouple failed during the run.'); reason.ok()
            assert [d['technique'] for d in app.current['data']] == ['XRD', 'XAS'] and len(app.current['withdrawn']) == 1
            page.show_withdrawn.set(True); page.shown(); snap(root, '9b-sample-corrected')
            assert len(page.data_view.get_children()) == 3
            appmod.HistoryDialog(app, app.current['record']).destroy()
            count = len(app.active_samples())
            page.retire_sample()
            reason = next(w for w in root.winfo_children() if isinstance(w, appmod.ReasonDialog))
            appmod.set_text(reason.reason, 'Entered twice.'); reason.ok()
            assert app.page == 'samples' and len(app.active_samples()) == count - 1
            assert first['id'] not in [app.pages['samples'].view.set(i, 'id') for i in app.pages['samples'].view.get_children()]
            app.pages['samples'].show_retired.set(True); app.pages['samples'].render()
            assert any('registered in error' in app.pages['samples'].view.set(i, 'id') for i in app.pages['samples'].view.get_children())
        finally:
            appmod.PreviewDialog = real_preview
        app.show('settings'); snap(root, '8-settings')
        root.destroy()
    print('Redesigned interface: all screens and the save/export flows work with synthetic data.')


if __name__ == '__main__':
    main()
