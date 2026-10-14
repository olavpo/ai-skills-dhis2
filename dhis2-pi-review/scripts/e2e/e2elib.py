"""Shared helpers for the phase-4 (end-to-end) scripts. Not a command.

- fixes / proposed payload / mapping patch from the work dir (written by phase 3);
- the "after" metadata (export with the proposed PIs and mappings applied) as <work_dir>/metadata-after.json;
- the affected PI ids (changed PIs + every PI that uses a changed mapping) as <work_dir>/affected-ids.json;
- a step log (<work_dir>/e2e-steps.jsonl);
- calls into the validate scripts (generator, query, compare), which own the DHIS2 semantics.

Assumed CLI of the validate scripts (spelled out only here and in overlay_args(); change it here if theirs differs).
--metadata takes OVERLAYS applied on top of the configured export (validate/common.py): nothing for the original
state, the proposed payload + mapping patch for the proposed state.
    validate/gen.py     --config C [--metadata OVERLAYS] --pis IDS --tag T            -> <work>/tracker-T.json, truth-T.json
    validate/query.py   --config C [--metadata OVERLAYS] --pis IDS --tag T            -> <work>/analytics-T.json
    validate/compare.py --config C [--metadata OVERLAYS] --pis IDS --tag T --truth F  -> <work>/compare-T.json
                                                                                         (reads analytics-T.json)
"""
import copy, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SCRIPTS, 'lib'))
import config  # noqa: E402

PI_FILE = 'proposed-programIndicators.json'
PATCH_FILE = 'proposed-categoryMappings.patch.json'


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
    for k in ('groups', 'pis', 'mappings', 'excluded'):
        F.setdefault(k, [])
    return F


def proposed_pis(cfg):
    p = os.path.join(cfg.work_dir, PI_FILE)
    return jload(p)['programIndicators'] if os.path.exists(p) else []


def mapping_patch(cfg):
    """The JSON Patch for the program's categoryMappings, or None when no mapping is changed."""
    p = os.path.join(cfg.work_dir, PATCH_FILE)
    return jload(p) if os.path.exists(p) else None


def after_metadata(cfg, write=True):
    """The export with the proposed PIs and the mapping patch applied (what the instance holds after `apply`)."""
    md = copy.deepcopy(cfg.md)
    new = {p['id']: p for p in proposed_pis(cfg)}
    md['programIndicators'] = [new.pop(p['id'], p) for p in md['programIndicators']] + list(new.values())
    patch = mapping_patch(cfg)
    if patch:
        for prog in md['programs']:
            if prog['id'] == cfg.program_id:
                for op in patch:
                    if op.get('op') == 'replace' and op.get('path') == '/categoryMappings':
                        prog['categoryMappings'] = op['value']
    if write:
        jdump(md, metadata_after_path(cfg))
    return md


def overlay_args(cfg, state):
    """--metadata overlays for the validate scripts: none for 'before', the proposed payload (+ patch) for 'after'."""
    if state == 'before':
        return []
    files = [os.path.join(cfg.work_dir, f) for f in (PI_FILE, PATCH_FILE) if os.path.exists(os.path.join(cfg.work_dir, f))]
    return ['--metadata', *files] if files else []


def metadata_after_path(cfg):
    return os.path.join(cfg.work_dir, 'metadata-after.json')


def program_of(md, pid):
    return next(p for p in md['programs'] if p['id'] == pid)


def changed_mapping_ids(cfg):
    return {m['id'] for m in fixes(cfg)['mappings']}


def affected(cfg, write=True):
    """Changed PIs + every PI (before or after) that uses a changed mapping."""
    F = fixes(cfg)
    ids = {r['id'] for r in F['pis']}
    maps = changed_mapping_ids(cfg)
    for md in (cfg.md, after_metadata(cfg, write=False)):
        for p in md['programIndicators']:
            if (p.get('program') or {}).get('id', cfg.program_id) == cfg.program_id and maps & set(x for x in p.get('categoryMappingIds') or [] if x):
                ids.add(p['id'])
    out = sorted(ids)
    if write:
        jdump(out, os.path.join(cfg.work_dir, 'affected-ids.json'), indent=None)
    return out


def log_step(cfg, name, data):
    with open(cfg.out('e2e-steps.jsonl'), 'a') as f:
        f.write(json.dumps({'step': name, **data}, ensure_ascii=False) + '\n')


def pi_label(p):
    return p.get('code') or p.get('shortName') or p.get('name') or p['id']


def run_validate(cfg, script, *args):
    """Run scripts/validate/<script> as a subprocess with --config. Returns CompletedProcess (output printed)."""
    cmd = [sys.executable, os.path.join(SCRIPTS, 'validate', script), '--config', cfg.path, *map(str, args)]
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
