"""Prepare the description batches for the sub-agents that write the new descriptions of the changed items.

    python3 scripts/fix/prep_desc.py [--config pireview.json] [--batches 3] [--fixes fixes.json]

Input   work_dir/fixes.json (build_fixes.py); optional work_dir/dictionary.json (phase 2: plain-language description of
        the old definition per item, used as context)
Output  work_dir/desc_in/batchN.json, N = 0..batches-1 (old batch files are removed). One entry per changed item, built
        by the type adapter (adapter.desc_input): the corrected definition in words (UIDs replaced by names), and for
        context the old definition, its description and the change notes.
Hand each batch to a sub-agent with the type's description brief (adapter.DESCRIPTION_BRIEF, e.g.
types/program-indicators/description_brief.md); they write work_dir/desc_out/batchN.json.
Config keys: metadata, work_dir (+ type keys).
"""
import os, sys, json, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config  # noqa: E402


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--batches', type=int, default=3, help='number of batch files (one sub-agent each; ~45 items per batch works well)')
    ap.add_argument('--fixes', help='fixes.json (default: work_dir/fixes.json)')
    a = ap.parse_args()
    cfg = config.load(a)
    A = adapters.load(cfg)
    with open(a.fixes or cfg.out('fixes.json')) as f:
        F = json.load(f)
    old_desc = {}
    dpath = cfg.out('dictionary.json')
    if os.path.exists(dpath):
        with open(dpath) as f:
            d = json.load(f)
        for r in (d.get('rows', d.get('items', [])) if isinstance(d, dict) else d):
            if r.get('id') and r.get('description'):
                old_desc[r['id']] = r['description']
    items = [A.desc_input(cfg, r, old_desc.get(r['id'])) for r in F['items']]
    n = max(1, min(a.batches, len(items) or 1))
    for f in glob.glob(cfg.out('desc_in', 'batch*.json')):
        os.remove(f)
    for i in range(n):
        with open(cfg.out('desc_in', f'batch{i}.json'), 'w') as f:
            json.dump(items[i::n], f, indent=1, ensure_ascii=False)
    print(f'{len(items)} {A.LABELS["shorts"]} in {n} batches {[len(items[i::n]) for i in range(n)]} -> {cfg.out("desc_in")}')
    print(f'brief: {getattr(A, "DESCRIPTION_BRIEF", "(the type has no description brief)")}; outputs go to {cfg.out("desc_out")}/batchN.json')


if __name__ == '__main__':
    main()
