"""Test fixture (not part of the skill): export the aggregate indicators of a Sierra Leone 2.43 demo instance with
everything they need, as one metadata file.

    SRC_URL=http://<sl-demo> SRC_USER=… SRC_PASSWORD=… python3 export_sl.py [--out export/metadata-raw.json]

What goes in:
  * the dependency export (/api/dataSets/{id}/metadata.json) of every data set that holds a data element the
    indicators reference (data elements, category model incl. COCs, sections, legend sets, option sets);
  * every indicator except those built on program data (I{...} / D{...}: their program indicators and programs are
    not part of an aggregate review), all indicator types, all constants;
  * the org unit groups the indicators reference (OUG{...}), without their members (the test instance has other
    org units; import_data.py puts test facilities in them);
  * the legend sets of the indicators and data elements (an atomic indicator import refuses unknown references);
  * referenced data elements that are in no data set, and option sets the data elements use as comment option
    sets (missing from dependency exports; without them value imports fail with HTTP 500);
  * data sets lose their organisationUnits (assigned to the test org units by import_data.py) and indicators
    outside the scope.
Read-only on the source instance.
"""
import argparse, base64, json, os, re, urllib.request

SKIP_OPERANDS = re.compile(r'\b(I|D)\{')


def get(path):
    b = os.environ['SRC_URL'].rstrip('/')
    auth = base64.b64encode(f"{os.environ['SRC_USER']}:{os.environ['SRC_PASSWORD']}".encode()).decode()
    r = urllib.request.Request(b + path, headers={'Authorization': 'Basic ' + auth, 'Accept': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='export/metadata-raw.json')
    args = ap.parse_args()
    inds = get('/api/indicators?paging=false&fields=:owner,!createdBy,!lastUpdatedBy,!sharing,!translations')['indicators']
    inds = [i for i in inds if not SKIP_OPERANDS.search((i.get('numerator') or '') + (i.get('denominator') or ''))]
    refs = {m for i in inds for t in (i.get('numerator') or '', i.get('denominator') or '') for m in re.findall(r'#\{(\w+)', t)}
    ougs = {m for i in inds for t in (i.get('numerator') or '', i.get('denominator') or '') for m in re.findall(r'OUG\{(\w+)\}', t)}
    des = get(f"/api/dataElements?paging=false&filter=id:in:[{','.join(sorted(refs))}]"
              '&fields=id,dataSetElements[dataSet[id]]')['dataElements']
    ds_ids = sorted({e['dataSet']['id'] for d in des for e in d.get('dataSetElements', [])})
    out = {}

    def merge(objs):
        for k, v in objs.items():
            if not isinstance(v, list):
                continue
            cur = out.setdefault(k, {})
            for o in v:
                cur.setdefault(o['id'], o)

    for ds in ds_ids:
        merge(get(f'/api/dataSets/{ds}/metadata.json'))
    no_ds = [d['id'] for d in des if not d.get('dataSetElements')]
    if no_ds:
        merge(get(f"/api/dataElements?paging=false&filter=id:in:[{','.join(no_ds)}]&fields=:owner"))
    # option sets the data elements point at that the dependency export leaves out (comment option sets): a data
    # element whose commentOptionSet is missing makes every data value import fail with HTTP 500 on 2.43.2
    have_os = set(out.get('optionSets', {}))
    want_os = {x[k]['id'] for x in out.get('dataElements', {}).values() for k in ('optionSet', 'commentOptionSet') if x.get(k)} - have_os
    if want_os:
        merge(get(f"/api/optionSets?paging=false&filter=id:in:[{','.join(sorted(want_os))}]&fields=:owner"))
        merge(get(f"/api/options?paging=false&filter=optionSet.id:in:[{','.join(sorted(want_os))}]&fields=:owner"))
    merge({'indicators': inds})
    # legend sets the indicators and data elements point at (an import with atomicMode=ALL refuses the indicators
    # otherwise: E5002 invalid reference)
    want_ls = {x['id'] for o in list(out.get('dataElements', {}).values()) + inds for x in o.get('legendSets') or []} - set(out.get('legendSets', {}))
    if want_ls:
        merge(get(f"/api/legendSets?paging=false&filter=id:in:[{','.join(sorted(want_ls))}]&fields=:owner"))
    merge(get('/api/indicatorTypes?paging=false&fields=:owner'))
    merge(get('/api/constants?paging=false&fields=:owner'))
    if ougs:
        g = get(f"/api/organisationUnitGroups?paging=false&filter=id:in:[{','.join(sorted(ougs))}]&fields=:owner")
        for x in g['organisationUnitGroups']:
            x['organisationUnits'] = []
        merge(g)
    md = {k: list(v.values()) for k, v in out.items()}
    md.pop('indicators', None)
    md['indicators'] = inds                          # only the indicators in scope (data sets bring their own)
    scope = {i['id'] for i in inds}
    for ds in md.get('dataSets', []):
        ds['organisationUnits'] = []
        ds['indicators'] = [x for x in ds.get('indicators', []) if x['id'] in scope]
    for objs in md.values():
        for o in objs:
            for k in ('createdBy', 'lastUpdatedBy', 'user'):
                o.pop(k, None)
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w') as f:
        json.dump(md, f, indent=1)
    print({k: len(v) for k, v in md.items()}, '->', args.out)
    print('data sets:', ds_ids)


if __name__ == '__main__':
    main()
