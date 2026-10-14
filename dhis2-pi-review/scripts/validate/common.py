"""Helpers shared by the validate scripts (and usable by later phases through the same CLI options).

Shared options (add_common(ap)):
  --metadata FILE [FILE ...]  overlays applied on top of the export named in the config, in order:
                              * {"programIndicators": [...], "programs": [...], ...}  objects replaced/added by id
                                (a proposed payload such as proposed-programIndicators.json, or a full export)
                              * [{"op": "replace", "path": "/categoryMappings", "value": [...]}]  JSON Patch on the
                                program under review (proposed-categoryMappings.patch.json); other paths are applied
                                as top-level replaces on the program object
  --pis IDS                   restrict to these PIs: a JSON file with a list of uids (or {"ids": [...]}),
                              or a comma-separated list of uids/codes
  --tag TAG                   suffix for output files: analytics-<tag>.json, compare-<tag>.json, syntax-<tag>.json
"""
import json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config  # noqa: E402


def parser(doc):
    """config.arg_parser with the script's whole docstring as --help text."""
    import argparse
    return config.arg_parser(doc, formatter_class=argparse.RawDescriptionHelpFormatter)


def add_common(ap, tag=True):
    ap.add_argument('--metadata', nargs='+', default=[], metavar='FILE',
                    help='overlay files applied on top of the configured export (payloads by id, or a JSON Patch '
                         'for the program\'s categoryMappings)')
    ap.add_argument('--pis', help='JSON file with a list of PI uids, or a comma-separated list of uids/codes')
    if tag:
        ap.add_argument('--tag', help='suffix for output files, e.g. before / after')
    return ap


def load(args):
    """Config with overlays applied (cfg.md, cfg.program and cfg.pis reflect them)."""
    cfg = config.load(args)
    for path in getattr(args, 'metadata', None) or []:
        apply_overlay(cfg, cfg.resolve(path) if not os.path.exists(path) else path)
    return cfg


def apply_overlay(cfg, path):
    with open(path) as f:
        ov = json.load(f)
    md = cfg.md
    prog = cfg.program
    if isinstance(ov, list):                       # JSON Patch on the program
        for op in ov:
            if op.get('op') not in ('replace', 'add') or not op.get('path', '').startswith('/'):
                sys.exit(f'{path}: unsupported patch op {op}')
            prog[op['path'][1:].split('/')[0]] = op['value']
        return
    for key, objs in ov.items():
        if not isinstance(objs, list) or not all(isinstance(o, dict) for o in objs):
            continue
        cur = md.setdefault(key, [])
        idx = {o.get('id'): i for i, o in enumerate(cur)}
        for o in objs:
            if o.get('id') in idx:
                if key == 'programs' and o['id'] == prog['id']:
                    prog.clear(); prog.update(o); prog.setdefault('categoryMappings', [])
                else:
                    cur[idx[o['id']]] = o
            else:
                cur.append(o)


def select_pis(cfg, spec):
    """PIs of the program, optionally restricted by --pis."""
    pis = cfg.pis
    if not spec:
        return pis
    if os.path.exists(spec):
        with open(spec) as f:
            ids = json.load(f)
        if isinstance(ids, dict):
            ids = ids.get('ids') or list(ids)
    else:
        ids = [x.strip() for x in spec.split(',') if x.strip()]
    want = set(ids)
    out = [p for p in pis if p['id'] in want or p.get('code') in want]
    missing = want - {p['id'] for p in out} - {p.get('code') for p in out}
    if missing:
        print(f'warning: {len(missing)} requested PIs not in the metadata: {sorted(missing)[:5]}', file=sys.stderr)
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
