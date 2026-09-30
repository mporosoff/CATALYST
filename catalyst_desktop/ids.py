"""Readable CATALYST identifiers.

Sample IDs:     <LAB>-<INITIALS>-<YYMMDD>-<NN>      e.g. UR-MDP-260925-01
Data IDs:       <SAMPLE ID>-<TECH>-<NN>             e.g. UR-MDP-260925-01-XRD-01
Procedure IDs:  PRC-<LAB>-<NNN>                     e.g. PRC-UR-001

The lab, researcher and synthesis date are read from the ID, so nobody types
them twice. The daily number is the next free number for that researcher on
that day, checked against SciSure immediately before saving.
"""
from __future__ import annotations

from datetime import date, datetime
import re

# key -> (ID code, display name, e-mail domains used to suggest the lab)
LABS = {
    'university-of-rochester': ('UR', 'Rochester', ('rochester.edu',)),
    'astar': ('ASTAR', 'A*STAR', ('a-star.edu.sg', 'astar.edu.sg')),
    'slac': ('SLAC', 'SLAC', ('slac.stanford.edu',)),
    'northwestern': ('NU', 'Northwestern', ('northwestern.edu',)),
    'virginia-tech': ('VT', 'Virginia Tech', ('vt.edu',)),
    'oxeon': ('OXEON', 'Oxeon', ('oxeon.com',)),
}
LAB_CODES = {code: key for key, (code, _, _) in LABS.items()}
LAB_NAMES = tuple(name for _, name, _ in LABS.values())

# Short technique codes used inside data IDs. Order = order shown in menus.
TECHNIQUES = {
    'RXN': 'Reactor / GC performance',
    'HTE': 'High-throughput reactor campaign',
    'PILOT': 'Pilot-scale test',
    'XRD': 'XRD',
    'BET': 'BET surface area / porosimetry',
    'CHEM': 'Chemisorption (CO, H2 uptake)',
    'TPR': 'TPR',
    'TPD': 'TPD',
    'TPO': 'TPO',
    'TEM': 'TEM / STEM',
    'SEM': 'SEM / EDS',
    'XPS': 'XPS',
    'XAS': 'XAS (XANES / EXAFS)',
    'INSITU': 'In situ / operando (XRD, XAS, DRIFTS)',
    'RAMAN': 'Raman',
    'IR': 'IR / DRIFTS',
    'ICP': 'ICP / elemental analysis',
    'TGA': 'TGA',
    'CALC': 'Modeling / calculation',
    'OTHER': 'Other',
}
TECHNIQUE_BY_LABEL = {label: code for code, label in TECHNIQUES.items()}

SAMPLE_RE = re.compile(r'(?P<lab>[A-Z]{2,5})-(?P<initials>[A-Z]{2,4})-(?P<date>\d{6})-(?P<n>\d{2,3})')
DATA_RE = re.compile(SAMPLE_RE.pattern + r'-(?P<tech>[A-Z]{2,6})-(?P<dn>\d{2,3})')
PROCEDURE_RE = re.compile(r'(?P<prefix>PRC|TST)-(?P<lab>[A-Z]{2,5})-(?P<n>\d{3,4})')  # PRC synthesis, TST testing


class IdError(ValueError):
    pass


def lab_key(value):
    """Accept a lab key, code or display name."""
    text = str(value or '').strip().casefold()
    for key, (code, name, _) in LABS.items():
        if text in (key, code.casefold(), name.casefold()):
            return key
    if text in ('va tech', 'virginia tech', 'vt'):
        return 'virginia-tech'
    raise IdError('Choose one of the six CATALYST laboratories.')


def lab_code(value):
    return LABS[lab_key(value)][0]


def lab_name(value):
    return LABS[lab_key(value)][1]


def lab_from_email(email):
    """Suggest a lab from an e-mail domain; None when unknown."""
    domain = str(email or '').rsplit('@', 1)[-1].strip().casefold()
    for key, (_, _, domains) in LABS.items():
        if any(domain == d or domain.endswith('.' + d) for d in domains):
            return key
    return None


def suggest_initials(full_name):
    parts = [p for p in re.split(r'[\s\-.]+', str(full_name or '')) if p and p[0].isalpha()]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0][:2].upper()
    return ''.join(p[0] for p in parts[:3]).upper()


def clean_initials(value):
    text = str(value or '').strip().upper()
    if not re.fullmatch(r'[A-Z]{2,4}', text):
        raise IdError('Initials must be 2–4 letters (for example MDP).')
    return text


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        raise IdError('Choose a date (YYYY-MM-DD).') from None


def sample_prefix(lab, initials, when):
    return f'{lab_code(lab)}-{clean_initials(initials)}-{as_date(when):%y%m%d}-'


def sample_id(lab, initials, when, number):
    if not 1 <= int(number) <= 999:
        raise IdError('A researcher can register at most 999 samples per day.')
    return sample_prefix(lab, initials, when) + f'{int(number):02d}'


def next_sample_id(existing, lab, initials, when):
    """Next free ID for this lab/researcher/day, given IDs already in SciSure."""
    prefix = sample_prefix(lab, initials, when)
    used = set()
    for value in existing:
        match = SAMPLE_RE.fullmatch(str(value))
        if match and str(value).startswith(prefix):
            used.add(int(match['n']))
    number = 1
    while number in used:
        number += 1
    return sample_id(lab, initials, when, number)


def parse_sample_id(value):
    match = SAMPLE_RE.fullmatch(str(value or '').strip())
    if not match or match['lab'] not in LAB_CODES:
        return None
    try:
        made = datetime.strptime(match['date'], '%y%m%d').date()
    except ValueError:
        return None
    return dict(id=match.group(0), lab=LAB_CODES[match['lab']], lab_code=match['lab'],
        lab_name=LABS[LAB_CODES[match['lab']]][1], initials=match['initials'], date=made.isoformat(),
        number=int(match['n']))


def technique_code(value):
    text = str(value or '').strip()
    if text.upper() in TECHNIQUES:
        return text.upper()
    if text in TECHNIQUE_BY_LABEL:
        return TECHNIQUE_BY_LABEL[text]
    raise IdError('Choose a technique from the list.')


def next_data_id(sample, technique, existing):
    code = technique_code(technique)
    if not parse_sample_id(sample):
        raise IdError('Choose a registered sample.')
    prefix = f'{sample}-{code}-'
    used = {int(m['dn']) for value in existing
        if (m := DATA_RE.fullmatch(str(value))) and str(value).startswith(prefix)}
    number = 1
    while number in used:
        number += 1
    return f'{prefix}{number:02d}'


def is_test_protocol(procedure_id):
    return str(procedure_id or '').startswith('TST-')


def next_procedure_id(lab, existing, prefix='PRC'):
    """PRC-LAB-NNN for a synthesis procedure, TST-LAB-NNN for a test protocol."""
    code = lab_code(lab)
    used = {int(m['n']) for value in existing
        if (m := PROCEDURE_RE.fullmatch(str(value))) and m['lab'] == code and m['prefix'] == prefix}
    number = 1
    while number in used:
        number += 1
    return f'{prefix}-{code}-{number:03d}'
