"""Command-line helpers shared by the core scripts and the type scripts (validate steps and later phases).

Shared options (add_common(ap)):
  --metadata FILE [FILE ...]  overlays applied on top of the export named in the config, in order; each is applied by
                              the type adapter (adapter.apply_overlay), e.g. a proposed payload ({"<collection>": [...]},
                              objects replaced or added by id) or the type's aux patch
  --items IDS (alias --pis)   restrict to these items: a JSON file with a list of uids (or {"ids": [...]}),
                              or a comma-separated list of uids/codes
  --tag TAG                   suffix for output files: analytics-<tag>.json, compare-<tag>.json, syntax-<tag>.json
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adapters, config  # noqa: E402


def parser(doc):
    """config.arg_parser with the script's whole docstring as --help text."""
    return config.arg_parser(doc, formatter_class=argparse.RawDescriptionHelpFormatter)


def add_common(ap, tag=True):
    ap.add_argument('--metadata', nargs='+', default=[], metavar='FILE',
                    help='overlay files applied on top of the configured export (payloads by id, or the type\'s aux patch)')
    ap.add_argument('--items', '--pis', dest='items', metavar='IDS',
                    help='JSON file with a list of uids, or a comma-separated list of uids/codes')
    if tag:
        ap.add_argument('--tag', help='suffix for output files, e.g. before / after')
    return ap


def load(args):
    """Config with overlays applied (cfg.md and everything derived from it reflect them)."""
    cfg = config.load(args)
    for path in getattr(args, 'metadata', None) or []:
        apply_overlay_file(cfg, path if os.path.exists(path) else cfg.resolve(path))
    return cfg


def apply_overlay_file(cfg, path):
    with open(path) as f:
        ov = json.load(f)
    adapters.load(cfg).apply_overlay(cfg, ov, path)


def select(cfg, spec):
    """Items under review (adapter.items), optionally restricted by --items."""
    items = adapters.load(cfg).items(cfg)
    if not spec:
        return items
    if os.path.exists(spec):
        with open(spec) as f:
            ids = json.load(f)
        if isinstance(ids, dict):
            ids = ids.get('ids') or list(ids)
    else:
        ids = [x.strip() for x in spec.split(',') if x.strip()]
    want = set(ids)
    out = [p for p in items if p['id'] in want or p.get('code') in want]
    missing = want - {p['id'] for p in out} - {p.get('code') for p in out}
    if missing:
        print(f'warning: {len(missing)} requested items not in the metadata: {sorted(missing)[:5]}', file=sys.stderr)
    return out


def tagged(cfg, name, tag):
    """work_dir/name, with -<tag> before the extension when a tag is given."""
    if tag:
        b, e = os.path.splitext(name)
        name = f'{b}-{tag}{e}'
    return cfg.out(name)


def read_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        sys.exit(f'missing input {path}')
    with open(path) as f:
        return json.load(f)


def write_json(path, obj, indent=1):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False, default=str)
