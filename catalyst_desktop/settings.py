"""Small local settings file: researcher profile, server, recent items and unsent drafts.

No research files or downloaded data are stored here. Drafts keep typed form
values and the *paths* of files the researcher picked, so an unfinished upload
survives closing the app. Tokens stay in the operating-system credential store.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile

FORMAT = 'catalyst-settings/1'
MAX_RECENT = 15
DEFAULTS = dict(format=FORMAT, profile={}, server='https://sandbox.elabjournal.com', remember_token=True,
    recent_samples=[], drafts={})


def settings_dir():
    override = os.environ.get('CATALYST_SETTINGS_DIR')
    if override:
        return Path(override)
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return base / 'CATALYST'


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else settings_dir() / 'settings.json'
        self.data = deepcopy(DEFAULTS)
        self.load_error = None
        try:
            if self.path.is_file():
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(value, dict) and value.get('format') == FORMAT:
                    for key, default in DEFAULTS.items():
                        if isinstance(value.get(key), type(default)):
                            self.data[key] = value[key]
        except (OSError, ValueError) as error:
            self.load_error = str(error)

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(prefix='.settings-', suffix='.json', dir=self.path.parent)
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(self.data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
            return True
        except OSError:
            return False

    # Profile -----------------------------------------------------------
    @property
    def profile(self):
        return deepcopy(self.data['profile'])

    def has_profile(self):
        p = self.data['profile']
        return bool(p.get('name') and p.get('initials') and p.get('lab'))

    def set_profile(self, name, initials, lab, email=''):
        self.data['profile'] = dict(name=name.strip(), initials=initials.strip().upper(), lab=lab, email=email.strip())
        self.save()

    # Server --------------------------------------------------------------
    @property
    def server(self):
        return self.data['server']

    def set_server(self, server, remember_token):
        self.data['server'], self.data['remember_token'] = server, bool(remember_token)
        self.save()

    # Recent samples ------------------------------------------------------
    def remember_sample(self, sample_id):
        recent = [s for s in self.data['recent_samples'] if s != sample_id]
        self.data['recent_samples'] = [sample_id] + recent[:MAX_RECENT - 1]
        self.save()

    @property
    def recent_samples(self):
        return list(self.data['recent_samples'])

    # Drafts --------------------------------------------------------------
    def save_draft(self, key, values):
        if values:
            self.data['drafts'][key] = deepcopy(values)
        else:
            self.data['drafts'].pop(key, None)
        self.save()

    def draft(self, key):
        return deepcopy(self.data['drafts'].get(key))

    def clear_draft(self, key):
        if key in self.data['drafts']:
            del self.data['drafts'][key]
            self.save()
