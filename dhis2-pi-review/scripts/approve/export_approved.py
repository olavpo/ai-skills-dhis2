"""Import files from the approvals, without the page: the same four files the page downloads.

Decisions come from either
  --dump DIR         a dump of the artifact store: DIR/approvals/<group>.json and DIR/descriptions/<piId>.json
                     (e.g. ArtifactData "list" with out_dir; a document may be wrapped as {"data": {...}}), or
  --decisions F ...  decisions files saved from the standalone page, merged like the page does: per (group, item)
                     the newest "at" wins; disagreements between reviewers are printed; files from another data
                     version are used with a warning.
Export rule: an item is exported only when EVERY fix on it is approved (the proposed object combines them); one
rejection blocks it. Edited descriptions replace the proposed ones.

Outputs (in --out, default <work_dir>/export):
  <stem>-program-indicators-approved.json                  POST /api/metadata?importStrategy=UPDATE&atomicMode=ALL
  <stem>-program-indicators-approved-still-failing.json    approved, but analytics still fails after the fix
  <stem>-categoryMappings.patch.json                       PATCH /api/programs/<program> (application/json-patch+json)
  <stem>-fix-review-summary.csv                            every item and fix with its decision (UTF-8 with BOM)
Inputs : fix-review.json (build_fix_review.py)
Config : work_dir.
"""
import csv, glob, io, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config  # noqa: E402


def load_dump(d):
    def coll(name):
        r = {}
        for f in glob.glob(os.path.join(d, name, '*.json')):
            with open(f) as fh:
                j = json.load(fh)
            r[os.path.basename(f)[:-5]] = j.get('data', j) if isinstance(j, dict) else j
        return r
    return coll('approvals'), coll('descriptions'), []


def merge_files(files, version, groups):
    ap, de, conflicts = {}, {}, []
    for path in files:
        with open(path) as f:
            j = json.load(f)
        if not str(j.get('format', '')).startswith('pireview-fix-decisions/'):
            sys.exit(f'{path}: not a decisions file')
        if j.get('dataVersion') and j['dataVersion'] != version:
            print(f'warning: {path} was saved from another version of the page ({j["dataVersion"]} vs {version})')
        for g, o in (j.get('approvals') or {}).items():
            if g not in groups:
                continue
            cur = ap.setdefault(g, {'states': {}, 'meta': {}})
            for i, m in (o.get('meta') or {}).items():
                mine, theirs, ours = cur['meta'].get(i), (o.get('states') or {}).get(i, 'pending'), cur['states'].get(i, 'pending')
                if mine and theirs != ours and 'pending' not in (theirs, ours) and (mine.get('by') or '') != (m.get('by') or ''):
                    conflicts.append((g, i, mine.get('by'), ours, m.get('by'), theirs))
                if not mine or (m.get('at') or '') > (mine.get('at') or ''):
                    if theirs == 'pending':
                        cur['states'].pop(i, None)
                    else:
                        cur['states'][i] = theirs
                    cur['meta'][i] = m
        for i, d in (j.get('descriptions') or {}).items():
            if i not in de or (d.get('at') or '') > (de[i].get('at') or ''):
                de[i] = d
    return ap, de, conflicts


def main():
    ap_ = config.arg_parser(__doc__.split('\n')[0])
    src = ap_.add_mutually_exclusive_group(required=True)
    src.add_argument('--dump', help='artifact store dump directory')
    src.add_argument('--decisions', nargs='+', help='decisions files from the standalone page')
    ap_.add_argument('--out', help='output directory (default <work_dir>/export)')
    args = ap_.parse_args()
    cfg = config.load(args)
    with open(cfg.out('fix-review.json')) as f:
        F = json.load(f)
    groups = {g['key']: g for g in F['groups']}
    ap, de, conflicts = load_dump(args.dump) if args.dump else merge_files(args.decisions, F['version'], groups)
    st = lambda g, i: ((ap.get(g) or {}).get('states') or {}).get(i, 'pending')
    status = lambda it: 'rejected' if any(st(g, it['id']) == 'rejected' for g in it['groups']) else (
        'approved' if all(st(g, it['id']) == 'approved' for g in it['groups']) else 'pending')
    desc = lambda p: ((de.get(p['id']) or {}).get('text')) if (de.get(p['id']) or {}).get('text') is not None else p['full'].get('description')
    fails = lambda p: bool(((p.get('test') or {}).get('after') or {}).get('error'))
    ok = [p for p in F['pis'] if status(p) == 'approved']
    out = args.out or os.path.join(cfg.work_dir, 'export')
    os.makedirs(out, exist_ok=True)
    stem = F.get('fileStem', 'program')

    def dump(name, obj):
        with open(os.path.join(out, name), 'w') as f:
            json.dump(obj, f, indent=1, ensure_ascii=False)
        return name

    written = [dump(f'{stem}-program-indicators-approved.json', {'programIndicators': [{**p['full'], 'description': desc(p)} for p in ok if not fails(p)]})]
    if any(fails(p) for p in ok):
        written.append(dump(f'{stem}-program-indicators-approved-still-failing.json',
                            {'programIndicators': [{**p['full'], 'description': desc(p)} for p in ok if fails(p)]}))
    okm = {m['id']: m['afterFull'] for m in F['mappings'] if status(m) == 'approved'}
    if okm:
        written.append(dump(f'{stem}-categoryMappings.patch.json',
                            [{'op': 'replace', 'path': '/categoryMappings', 'value': [okm.get(m['id'], m) for m in F['allMappings']]}]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\r\n', quoting=csv.QUOTE_ALL)
    w.writerow(['type', 'code', 'name', 'overall', 'fix', 'decision', 'by', 'at', 'still fails after fix', 'description edited'])
    for kind, items in (('indicator', F['pis']), ('mapping', F['mappings'])):
        for it in items:
            for g in it['groups']:
                m = ((ap.get(g) or {}).get('meta') or {}).get(it['id']) or {}
                w.writerow([kind, it.get('code', ''), it['name'], status(it), groups.get(g, {}).get('short', g), st(g, it['id']), m.get('by', ''), m.get('at', ''),
                            (((it.get('test') or {}).get('after') or {}).get('error') or '') if kind == 'indicator' else '',
                            'yes' if kind == 'indicator' and it['id'] in de else ''])
    with open(os.path.join(out, f'{stem}-fix-review-summary.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        f.write(buf.getvalue())
    written.append(f'{stem}-fix-review-summary.csv')
    blocked = [p.get('code') or p['id'] for p in F['pis'] if status(p) == 'rejected']
    pending = [p.get('code') or p['id'] for p in F['pis'] if status(p) == 'pending']
    print(f'{len(ok)} PIs approved ({sum(fails(p) for p in ok)} still failing), {len(okm)} mappings approved, '
          f'{len(blocked)} blocked by a rejection, {len(pending)} pending; edited descriptions used: {sum(1 for p in ok if p["id"] in de)}')
    for c in conflicts:
        print(f'disagreement {c[0]}/{c[1]}: {c[2] or "unnamed"} {c[3]}, {c[4] or "unnamed"} {c[5]}')
    print('wrote', ', '.join(written), 'in', out)


if __name__ == '__main__':
    main()
