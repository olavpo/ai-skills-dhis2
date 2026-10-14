"""Helpers shared by the program-indicator scripts: the core CLI helpers (lib/cli.py) plus the PI overlay.

Shared options (add_common(ap), see lib/cli.py):
  --metadata FILE [FILE ...]  overlays applied on top of the export named in the config, in order:
                              * {"programIndicators": [...], "programs": [...], ...}  objects replaced/added by id
                                (a proposed payload such as proposed-programIndicators.json, or a full export)
                              * [{"op": "replace", "path": "/categoryMappings", "value": [...]}]  JSON Patch on the
                                program under review (proposed-categoryMappings.patch.json); other paths are applied
                                as top-level replaces on the program object
  --items IDS (alias --pis)   restrict to these PIs: a JSON file with a list of uids (or {"ids": [...]}),
                              or a comma-separated list of uids/codes
  --tag TAG                   suffix for output files: analytics-<tag>.json, compare-<tag>.json, syntax-<tag>.json
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, '..', '..', 'lib')
if LIB not in sys.path:
    sys.path.insert(0, LIB)
import config  # noqa: E402,F401
from cli import parser, add_common, load, select, tagged, read_json, write_json  # noqa: E402,F401

select_pis = select


def apply_overlay(cfg, ov, path):
    """One --metadata overlay (parsed JSON) applied to cfg.md (called through adapter.apply_overlay)."""
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


def validate_text(kind, text):
    """Server check of a PI expression or filter (kind 'expression' or 'filter'), behaviour I2.
    Returns (ok, message, description)."""
    import d2
    s, j = d2.req('POST', f'/api/programIndicators/{kind}/description', text or '1', ctype='text/plain', log=False, timeout=60)
    ok = s == 200 and j.get('status') == 'OK'
    return ok, j.get('message') or (j.get('_raw') or '')[:300], j.get('description')


# --- lookups used by the e2e scripts (scen.py, probes.py, make_viz.py) --------------------------------------------
def pi_label(p):
    return p.get('code') or p.get('shortName') or p.get('name') or p['id']


def program_of(md, pid):
    return next(p for p in md['programs'] if p['id'] == pid)


def resolve_pi(md, ref):
    """PI by uid or code."""
    for p in md['programIndicators']:
        if ref in (p['id'], p.get('code')):
            return p
    return None


def resolve_category(md, ref):
    """Category uid from uid or name."""
    for c in md['categories']:
        if ref in (c['id'], c.get('name')):
            return c['id']
    return None
