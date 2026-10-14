"""Prepare the description batches for the sub-agents that write the new PI descriptions.

    python3 scripts/fix/prep_desc.py [--config pireview.json] [--batches 3] [--fixes fixes.json]

Input   work_dir/fixes.json (build_fixes.py); optional work_dir/dictionary.json (phase 2: plain-language description of
        the old definition per PI, used as context)
Output  work_dir/desc_in/batchN.json, N = 0..batches-1 (old batch files are removed). Per changed PI: the corrected
        definition with UIDs replaced by names ("Stage.Field", attribute names), boundaries in words, categories it is
        disaggregated by, and for context the old definition, its description and the change notes.
Hand each batch to a sub-agent with scripts/fix/description_brief.md; they write work_dir/desc_out/batchN.json.
Config keys: metadata, program, work_dir.
"""
import os, sys, re, json, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config
import patterns as P


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--batches', type=int, default=3, help='number of batch files (one sub-agent each; ~45 PIs per batch works well)')
    ap.add_argument('--fixes', help='fixes.json (default: work_dir/fixes.json)')
    a = ap.parse_args()
    cfg = config.load(a)
    M = P.Meta(cfg.md, cfg.program)
    with open(a.fixes or cfg.out('fixes.json')) as f:
        F = json.load(f)
    old_desc = {}
    dpath = cfg.out('dictionary.json')
    if os.path.exists(dpath):
        with open(dpath) as f:
            d = json.load(f)
        for r in (d.get('rows', d.get('pis', [])) if isinstance(d, dict) else d):
            if r.get('id') and r.get('description'):
                old_desc[r['id']] = r['description']

    def named(t):
        t = re.sub(r'#\{(\w+)\.(\w+)\}', lambda m: '#{%s.%s}' % (M.stage.get(m[1], {}).get('name', m[1]),
                                                                 (M.de.get(m[2]) or {}).get('formName') or (M.de.get(m[2]) or {}).get('name', m[2])), t or '')
        t = re.sub(r'A\{(\w+)\}', lambda m: 'A{%s}' % (M.tea.get(m[1]) or {}).get('name', m[1]), t)
        # stage UIDs compared with V{program_stage_id}, quoted or not
        return re.sub(r"(['\"]?)\b(\w{11})\b\1", lambda m: f"'{M.stage[m[2]]['name']}'" if m[2] in M.stage else m[0], t)

    def bnd(p):
        out = []
        for b in p.get('analyticsPeriodBoundaries') or []:
            s = f"{b['boundaryTarget']} {b['analyticsPeriodBoundaryType']}"
            if b.get('offsetPeriods'):
                s += f" offset {b['offsetPeriods']} {b.get('offsetPeriodType') or '(no period type: offset ignored)'}"
            out.append(s)
        return out

    def cats(p):
        return [M.cat[c]['name'] for c in sorted(M.pi_categories(p)) if c in M.cat]

    def stage(p):
        s = (p.get('programStage') or {}).get('id')
        return M.stage.get(s, {}).get('name') if s else None

    items = []
    for r in F['pis']:
        a_, b = r['after'], r['before']
        items.append({'id': r['id'], 'code': r.get('code'), 'name': r['name'], 'program': cfg.program.get('name'),
                      'analyticsType': a_.get('analyticsType'), 'aggregationType': a_.get('aggregationType'), 'stage': stage(a_),
                      'expression': named(a_.get('expression')), 'filter': named(a_.get('filter')), 'boundaries': bnd(a_),
                      'disaggregatedBy': cats(a_),
                      'oldExpression': named(b.get('expression')), 'oldFilter': named(b.get('filter')), 'oldBoundaries': bnd(b),
                      'oldDescription': old_desc.get(r['id']) or b.get('description'),
                      'changesMade': [named(c['note']) for c in r['changes']]})
    n = max(1, min(a.batches, len(items) or 1))
    for f in glob.glob(cfg.out('desc_in', 'batch*.json')):
        os.remove(f)
    for i in range(n):
        with open(cfg.out('desc_in', f'batch{i}.json'), 'w') as f:
            json.dump(items[i::n], f, indent=1, ensure_ascii=False)
    print(f'{len(items)} PIs in {n} batches {[len(items[i::n]) for i in range(n)]} -> {cfg.out("desc_in")}')
    print(f'brief: {os.path.join(os.path.dirname(os.path.abspath(__file__)), "description_brief.md")}; outputs go to {cfg.out("desc_out")}/batchN.json')


if __name__ == '__main__':
    main()
