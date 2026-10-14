"""Build the import payloads from fixes.json and the new descriptions.

    python3 scripts/fix/build_payload.py [--config pireview.json] [--allow-missing]

Inputs  work_dir/fixes.json; work_dir/desc_out/*.json ({"<itemUid>": {"description", "concern"}}, from the sub-agents);
        optional hand overrides in work_dir/desc_overrides.json or desc_out/zz_overrides.json (same shape, applied last);
        the aux objects from the metadata export (adapter.aux_items)
Outputs written by the type adapter (adapter.write_payload): adapter.PROPOSED_FILE ({PAYLOAD_KEY: [complete objects
        with the new description]} -> POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL) and, when an aux object
        changed, adapter.AUX_FILE (format and import call in the adapter; the script prints both);
        fixes.json updated in place: per item descBefore, descAfter, concern, and `after` with the new description
Every item in fixes.json needs a description, unless --allow-missing (keeps the exported description).
Config keys: metadata, work_dir (+ type keys).
"""
import os, sys, json, glob, copy
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config  # noqa: E402

MAXLEN = 450


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--allow-missing', action='store_true', help='keep the exported description where none was written')
    a = ap.parse_args()
    cfg = config.load(a)
    A = adapters.load(cfg)
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
    objs = []
    for r in F['items']:
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
        objs.append(after)
    if missing and not a.allow_missing:
        sys.exit(f'{len(missing)} {A.LABELS["items"]} have no new description (write them or pass --allow-missing): {", ".join(missing[:20])}')
    aux = copy.deepcopy(A.aux_items(cfg)) if A.AUX_KIND else []
    fixed = {m['id']: m['after'] for m in F.get('aux', [])}
    unknown = set(fixed) - {m['id'] for m in aux}
    if unknown:
        sys.exit(f'fixed {A.LABELS["auxs"]} not in scope: {sorted(unknown)}')
    aux = [fixed.get(m['id'], m) for m in aux]

    with open(fpath, 'w') as f:
        json.dump(F, f, indent=1, ensure_ascii=False)
    written = A.write_payload(cfg, objs, aux, sorted(fixed))
    for w in written:
        print(f"{w['what']} -> {w['file']} ({w['import']})")
    print(f'descriptions from {len(files)} files; concerns: {sum(1 for r in F["items"] if r["concern"])}')
    if missing:
        print('kept the exported description (none written):', ', '.join(missing))
    if long_:
        print(f'longer than {MAXLEN} characters:', ', '.join(long_))


if __name__ == '__main__':
    main()
