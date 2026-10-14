"""Render the PI dictionary page from dictionary.json, merging the per-PI descriptions if there are any.

Inputs:  work_dir/dictionary.json (build_dictionary.py); optional --descriptions DIR with JSON files
         {uid: {"description": "...", "nameMismatch": null | "..."}} written by the description sub-agents
         (default: work_dir/dict-desc/out when it exists). Without descriptions the PI's own description is shown.
Output:  work_dir/pi-dictionary.html (dict_template.html with __TITLE__ and __DATA__ filled in)
Config:  work_dir, title
"""
import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config
import glob, json, re

HERE = os.path.dirname(os.path.abspath(__file__))
ap = config.arg_parser(__doc__.split('\n')[0])
ap.add_argument('--descriptions', help='folder of per-PI description JSON files (default work_dir/dict-desc/out)')
args = ap.parse_args()
cfg = config.load(args)

with open(cfg.out('dictionary.json')) as f:
    d = json.load(f)
ddir = args.descriptions or (cfg.out('dict-desc', 'out') if os.path.isdir(os.path.join(cfg.work_dir, 'dict-desc', 'out')) else None)
desc = {}
if ddir:
    for path in sorted(glob.glob(os.path.join(ddir, '*.json'))):
        with open(path) as f:
            desc.update(json.load(f))
    missing = [r['code'] or r['id'] for r in d['rows'] if not (desc.get(r['id']) or {}).get('description')]
    print(f'descriptions {len(desc)}, missing {len(missing)}', missing[:10])

maps, key = {}, {}
for r in d['rows']:
    x = desc.get(r['id']) or {}
    r['description'] = x.get('description') or r.get('description')
    r['nameMismatch'] = x.get('nameMismatch') or x.get('concern') or r.get('nameMismatch')
    nd = []
    for c in r['disagg']:  # store each distinct mapping once
        sig = json.dumps([c['mapping'], c['options']], ensure_ascii=False)
        if c['mapping'] and sig not in key:
            key[sig] = f'm{len(key)}'
            maps[key[sig]] = {'name': c['mapping'], 'options': c['options']}
        nd.append({'category': c['category'], 'mapping': key.get(sig) if c['mapping'] else None})
    r['disagg'] = nd
d['mappings'] = maps
out = json.dumps(d, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
with open(os.path.join(HERE, 'dict_template.html')) as f:
    t = f.read()
path = cfg.out('pi-dictionary.html')
with open(path, 'w') as f:
    f.write(t.replace('__TITLE__', re.sub(r'[<&>]', '', d.get('title') or cfg.title)).replace('__DATA__', out))
print(f"{len(d['rows'])} PIs, {sum(1 for r in d['rows'] if r['nameMismatch'])} name mismatches, {len(out) // 1024} KB -> {path}")
