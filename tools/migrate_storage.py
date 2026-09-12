#!/usr/bin/env python3
"""Migrate a chain without modifying or deleting its source. Stop ComfyUI first."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import storage_migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('migrate', 'preview', 'copy', 'verify', 'activate'))
    parser.add_argument('--workspace', required=True, help='New short path outside the source output directory')
    parser.add_argument('--source', help='Existing output/h3_chains/project directory; migrate or preview')
    parser.add_argument('--path-budget', type=int, default=240)
    args = parser.parse_args()
    if args.action == 'migrate':
        if not args.source:
            parser.error('migrate requires --source')
        if not Path(args.workspace).exists():
            print('Inventorying source (read only)...', flush=True)
            storage_migrate.prepare(args.source, args.workspace, path_budget=args.path_budget)
        else:
            _, source, _ = storage_migrate._load(args.workspace)
            if source != Path(args.source).absolute():
                parser.error('workspace belongs to another source')
        print('Copying and checking source identity...', flush=True)
        storage_migrate.copy_project(args.workspace, after_copy=lambda n:
            print('Copied %d payload files' % n, flush=True) if n % 250 == 0 else None)
        print('Verifying all copied bytes...', flush=True)
        storage_migrate.verify(args.workspace)
        result = storage_migrate.activate(args.workspace)
    elif args.action == 'preview':
        if not args.source:
            parser.error('preview requires --source')
        result = storage_migrate.prepare(args.source, args.workspace, path_budget=args.path_budget)
    else:
        function = {'copy':storage_migrate.copy_project, 'verify':storage_migrate.verify,
                    'activate':storage_migrate.activate}[args.action]
        result = function(args.workspace)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
