"""Shared helpers for the phase-4 (end-to-end) scripts. Not a command.

- fixes / proposed payload / aux file from the work dir (written by phase 3);
- the "after" metadata (export with the proposed items and aux objects applied, through the type adapter's
  apply_overlay) as <work_dir>/metadata-after.json;
- the affected item ids (changed items + every item that uses a changed aux object) as <work_dir>/affected-ids.json;
- a step log (<work_dir>/e2e-steps.jsonl);
- calls into the type scripts (generator, query, compare), which own the type's DHIS2 semantics.

Assumed CLI of the type scripts (spelled out only here and in overlay_args(); change it here if theirs differs).
--metadata takes OVERLAYS applied on top of the configured export (lib/cli.py): nothing for the original state,
the proposed payload + aux file for the proposed state.
    types/<type>/gen.py     --config C [--metadata OVERLAYS] --items IDS --tag T [--seed N] [adapter.WITNESS_ARGS]
    types/<type>/query.py   --config C [--metadata OVERLAYS] --items IDS --tag T            -> <work>/analytics-T.json
    types/<type>/compare.py --config C [--metadata OVERLAYS] --items IDS --tag T --truth F  -> <work>/compare-T.json
                                                                                           (reads analytics-T.json)
"""
import copy, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SCRIPTS, 'lib'))
import adapters, config  # noqa: E402


def jload(path, default=None):
    if default is not None and not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def jdump(obj, path, indent=1):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)


def fixes(cfg):
    p = os.path.join(cfg.work_dir, 'fixes.json')
    if not os.path.exists(p):
        sys.exit(f'no {p}: run the fix phase first')
    F = jload(p)
    for k in ('groups', 'items', 'aux', 'excluded'):
        F.setdefault(k, [])
    return F


def payload_files(cfg):
    """The proposed payload and the aux file that exist in the work dir (in that order)."""
    A = adapters.load(cfg)
    return [os.path.join(cfg.work_dir, f) for f in (A.PROPOSED_FILE, A.AUX_FILE) if f and os.path.exists(os.path.join(cfg.work_dir, f))]


def proposed_items(cfg):
    A = adapters.load(cfg)
    p = os.path.join(cfg.work_dir, A.PROPOSED_FILE)
    return jload(p)[A.PAYLOAD_KEY] if os.path.exists(p) else []


def after_cfg(cfg):
    """A copy of cfg whose metadata has the proposed payload and aux file applied (what `apply` puts on the instance)."""
    c2 = copy.copy(cfg)
    c2._md = copy.deepcopy(cfg.md)
    A = adapters.load(cfg)
    for path in payload_files(cfg):
        A.apply_overlay(c2, jload(path), path)
    return c2


def after_metadata(cfg, write=True):
    """The export with the proposed items and aux objects applied."""
    md = after_cfg(cfg).md
    if write:
        jdump(md, metadata_after_path(cfg))
    return md


def overlay_args(cfg, state):
    """--metadata overlays for the type scripts: none for 'before', the proposed payload (+ aux file) for 'after'."""
    if state == 'before':
        return []
    files = payload_files(cfg)
    return ['--metadata', *files] if files else []


def metadata_after_path(cfg):
    return os.path.join(cfg.work_dir, 'metadata-after.json')


def changed_aux_ids(cfg):
    return {m['id'] for m in fixes(cfg)['aux']}


def affected(cfg, write=True):
    """Changed items + every item (before or after) that uses a changed aux object."""
    A = adapters.load(cfg)
    F = fixes(cfg)
    ids = {r['id'] for r in F['items']}
    aux = changed_aux_ids(cfg)
    for c in (cfg, after_cfg(cfg)):
        objs = A.items(c)
        for a in aux:
            ids |= set(A.aux_users(c, a, objs))
    out = sorted(ids)
    if write:
        jdump(out, os.path.join(cfg.work_dir, 'affected-ids.json'), indent=None)
    return out


def log_step(cfg, name, data):
    with open(cfg.out('e2e-steps.jsonl'), 'a') as f:
        f.write(json.dumps({'step': name, **data}, ensure_ascii=False) + '\n')


def item_label(p):
    return p.get('code') or p.get('shortName') or p.get('name') or p['id']


def import_stats(j):
    r = j.get('response', j) if isinstance(j.get('response', j), dict) else j
    return r.get('stats')


def import_errors(j):
    r = j.get('response', j) if isinstance(j.get('response', j), dict) else j
    return [e.get('message') for tr in r.get('typeReports', []) for o in tr.get('objectReports', []) for e in o.get('errorReports', [])]


def run_type(cfg, script, *args):
    """Run scripts/types/<type>/<script> as a subprocess with --config. Returns CompletedProcess (output printed)."""
    cmd = [sys.executable, adapters.type_script(cfg, script), '--config', cfg.path, *map(str, args)]
    print('$', ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout[-4000:])
    if r.returncode:
        sys.stderr.write(r.stderr[-4000:])
    return r


def same_value(a, b):
    """Scenario value comparison: 'error' matches only 'error'; numbers within 1e-6."""
    if a == 'error' or b == 'error':
        return a == b
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def scenario_item(s):
    """Item id of a scenario check (generic "item", older files "pi")."""
    return s.get('item') or s.get('pi')
