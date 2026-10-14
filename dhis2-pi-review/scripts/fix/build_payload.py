"""Build the import payloads from fixes.json and the new descriptions.

    python3 scripts/fix/build_payload.py [--config pireview.json] [--allow-missing]

Inputs  work_dir/fixes.json; work_dir/desc_out/*.json ({"<piUid>": {"description", "concern"}}, from the sub-agents);
        optional hand overrides in work_dir/desc_overrides.json or desc_out/zz_overrides.json (same shape, applied last);
        the program's categoryMappings from the metadata export
Outputs work_dir/proposed-programIndicators.json   {"programIndicators": [complete PI objects with the new description]}
                                                    -> POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL
        work_dir/proposed-categoryMappings.patch.json  [{"op": "replace", "path": "/categoryMappings", "value": [all
                                                    mappings of the program, fixed ones replaced]}]  (only when a mapping changed)
                                                    -> PATCH /api/programs/<program>  (Content-Type: application/json-patch+json)
        fixes.json updated in place: per PI descBefore, descAfter, concern, and `after` with the new description
Every PI in fixes.json needs a description, unless --allow-missing (keeps the exported description).
Config keys: metadata, program, work_dir.
"""
import os, sys, json, glob, copy
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config

MAXLEN = 450


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--allow-missing', action='store_true', help='keep the exported description where none was written')
    a = ap.parse_args()
    cfg = config.load(a)
    fpath = cfg.out('fixes.json')
    with open(fpath) as f:
        F = json.load(f)
    D = {}
    files = sorted(f for f in glob.glob(cfg.out('desc_out', '*.json')) if not os.path.basename(f).startswith('zz_'))
    files += sorted(glob.glob(cfg.out('desc_out', 'zz_*.json'))) + [p for p in [cfg.out('desc_overrides.json')] if os.path.exists(p)]
    for fn in files:
        with open(fn) as f:
            for k, v in json.load(f).items():
                v = {'description': v} if isinstance(v, str) else v
                D[k] = {**D.get(k, {}), **v}  # an override may change only the text and keep the concern
    missing, long_ = [], []
    pis = []
    for r in F['pis']:
        after = copy.deepcopy(r['after'])
        d = D.get(r['id'])
        if not d or not d.get('description'):
            missing.append(r.get('code') or r['id'])
        else:
            after['description'] = d['description'].strip()
            if len(after['description']) > MAXLEN:
                long_.append(f"{r.get('code') or r['id']} ({len(after['description'])})")
        r['descBefore'] = r['before'].get('description')
        r['descAfter'] = after.get('description')
        r['concern'] = (d or {}).get('concern') or None
        r['after'] = after
        pis.append(after)
    if missing and not a.allow_missing:
        sys.exit(f'{len(missing)} PIs have no new description (write them or pass --allow-missing): {", ".join(missing[:20])}')
    maps = copy.deepcopy(cfg.program.get('categoryMappings') or [])
    fixed = {m['id']: m['after'] for m in F.get('mappings', [])}
    unknown = set(fixed) - {m['id'] for m in maps}
    if unknown:
        sys.exit(f'fixed mappings not on the program: {sorted(unknown)}')
    maps = [fixed.get(m['id'], m) for m in maps]

    with open(fpath, 'w') as f:
        json.dump(F, f, indent=1, ensure_ascii=False)
    with open(cfg.out('proposed-programIndicators.json'), 'w') as f:
        json.dump({'programIndicators': pis}, f, indent=1, ensure_ascii=False)
    ppath = cfg.out('proposed-categoryMappings.patch.json')
    if fixed:
        with open(ppath, 'w') as f:
            json.dump([{'op': 'replace', 'path': '/categoryMappings', 'value': maps}], f, indent=1, ensure_ascii=False)
    elif os.path.exists(ppath):
        os.remove(ppath)  # stale from an earlier run
    print(f'{len(pis)} PIs -> proposed-programIndicators.json; {len(fixed)} of {len(maps)} mappings changed'
          + (f' -> proposed-categoryMappings.patch.json (PATCH /api/programs/{cfg.program_id})' if fixed else ''))
    print(f'descriptions from {len(files)} files; concerns: {sum(1 for r in F["pis"] if r["concern"])}')
    if missing:
        print('kept the exported description (none written):', ', '.join(missing))
    if long_:
        print(f'longer than {MAXLEN} characters:', ', '.join(long_))


if __name__ == '__main__':
    main()
