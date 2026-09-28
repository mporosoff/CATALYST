"""Command line:  python -m catalyst_query samples [search]
                  python -m catalyst_query export OUTPUT_FOLDER [--technique XRD] [--originals]
Uses CATALYST_TOKEN and CATALYST_SERVER from the environment."""
import json
import sys

from .reader import CatalystReader


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ('samples', 'export', 'sample'):
        print(__doc__)
        return 2
    db = CatalystReader()
    if argv[0] == 'samples':
        for s in db.samples(search=' '.join(argv[1:])):
            print(f"{s['sample_id']:22} {s['synthesis_date']}  {s['lab']:13} {s['composition']}  [{s['procedure']}]")
    elif argv[0] == 'sample':
        print(json.dumps(db.sample(argv[1]), indent=2, ensure_ascii=False))
    else:
        from catalyst_desktop.dataset import export_dataset
        technique = argv[argv.index('--technique') + 1] if '--technique' in argv else None
        result = export_dataset(db.store, db.store.list_samples(), argv[1], technique=technique,
            originals='--originals' in argv, progress=lambda text: print(text, file=sys.stderr))
        print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
