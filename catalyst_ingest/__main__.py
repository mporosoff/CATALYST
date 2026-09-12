"""Offline entry point; all research outputs stay outside tracked source."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from . import __version__
from .readers import InputError, MAX_FILE_BYTES, read_artifact
from .preview import preview_artifact, review_pair
from .toolkit import preview_toolkit_bundle


def preserve_original(content, filename, root):
    """Local development store; verify existing bytes instead of overwriting them."""
    digest = hashlib.sha256(content).hexdigest()
    directory = root.resolve() / 'raw'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + Path(filename).suffix.lower())
    try:
        with target.open('xb') as stream:
            stream.write(content)
    except FileExistsError:
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise InputError('Stored original failed its integrity check.')
    return {'key': 'raw/' + target.name, 'sha256_verified': hashlib.sha256(target.read_bytes()).hexdigest() == digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Create an offline CATALYST source-preserving draft review. Does not publish or execute spreadsheet formulas.')
    parser.add_argument('files', type=Path, nargs='+')
    parser.add_argument('--entity', required=True)
    parser.add_argument('--modality', required=True, choices=['reactor', 'synthesis', 'spectroscopy'])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--store-root', type=Path)
    parser.add_argument('--reactor-type', choices=['packed_bed'], help='Explicitly supplied reactor configuration; never inferred from a filename.')
    parser.add_argument('--same-run', action='store_true', help='Explicit user confirmation that the two files are raw and reprocessed versions of one run, in that order.')
    parser.add_argument('--row-labels-superseded', action='store_true', help='Explicit user correction; original source labels remain preserved.')
    parser.add_argument('--toolkit-gc-bundle', action='store_true', help='Import one Rochester toolkit RWGS revision: original XLSX, analysis XLSX, summary CSV, and flows CSV.')
    args = parser.parse_args(argv)
    if args.output.resolve() in [p.resolve() for p in args.files]:
        parser.error('Output cannot overwrite an input artifact.')
    if args.same_run and len(args.files) != 2 or args.row_labels_superseded and not args.same_run:
        parser.error('Relationship flags require exactly two files in raw, processed order and explicit same-run confirmation.')
    if args.toolkit_gc_bundle and (args.same_run or args.row_labels_superseded):
        parser.error('Toolkit bundle verification is separate from the legacy two-file relationship flags.')
    artifacts = []; previews = []
    try:
        for path in args.files:
            with path.open('rb') as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            artifact = read_artifact(content, path.name)
            preview = preview_artifact(artifact, args.entity, args.modality)
            if args.store_root:
                artifact['original_storage'] = preserve_original(content, path.name, args.store_root)
            artifacts.append(artifact); previews.append(preview)
        relationship = review_pair(*previews, same_run_confirmed=True, labels_superseded=args.row_labels_superseded) if args.same_run else None
        if args.toolkit_gc_bundle:
            previews = [preview_toolkit_bundle(artifacts, args.entity, args.modality)]
        report = {'schema_version': '0.1.0', 'created_at': datetime.now(timezone.utc).isoformat(),
                  'software_version': __version__, 'artifacts': artifacts, 'previews': previews,
                  'submission_context': {'source_entity': args.entity, 'reactor_type': args.reactor_type, 'basis': 'caller_supplied'},
                  'relationship': relationship, 'publication': {'status': 'not_published', 'eligible': False},
                  'limitations': ['Offline development importer; no authenticated upload service, approval system, or SciSure writes.',
                                  'Draft profiles do not authorize production normalization.',
                                  'Formula text is preserved without execution; cached values are unverified.']}
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    except (InputError, OSError) as error:
        print(f'Input review failed: {error}', file=sys.stderr)
        return 2
    print(json.dumps({'artifacts': len(artifacts), 'profiles': [p['profile']['id'] if p['profile'] else None for p in previews],
                      'issues': sum(len(p['issues']) for p in previews) + len((relationship or {}).get('issues', [])),
                      'output': str(args.output), 'publication': 'not_published'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
