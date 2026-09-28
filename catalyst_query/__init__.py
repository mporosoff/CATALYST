"""Read-only access to CATALYST data in SciSure for notebooks, ML pipelines and AI assistants.

    from catalyst_query import CatalystReader
    db = CatalystReader(token='...', server='https://sandbox.elabjournal.com')   # or env CATALYST_TOKEN / CATALYST_SERVER
    db.samples(search='Mo2C')             # list of samples
    db.sample('UR-MDP-260925-01')         # recipe, every data record, shipping log
    db.data(technique='RXN')              # one flat row per data record (ready for pandas.DataFrame)
    db.file_bytes('UR-MDP-260925-01-RXN-01', 'gc-summary.csv')

Nothing in this package can create, change or delete anything in SciSure.
"""
from .reader import CatalystReader, ReadOnlyError

__all__ = ['CatalystReader', 'ReadOnlyError']
