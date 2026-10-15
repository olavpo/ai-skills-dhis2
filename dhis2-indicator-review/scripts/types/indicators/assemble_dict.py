"""Render the aggregate-indicator dictionary page from dictionary.json, merging per-indicator descriptions if any.

    python3 scripts/types/indicators/assemble_dict.py [--config pireview.json] [--descriptions DIR]

Inputs:  work_dir/dictionary.json (build_dictionary.py); optional --descriptions DIR with JSON files
         {uid: {"description": "...", "nameMismatch": null | "..."}} (default work_dir/dict-desc/out when it exists).
         Without them the indicator's own description is shown.
Output:  work_dir/indicator-dictionary.html: one self-contained page (no external files), searchable table with the
         numerator and denominator in words, the data elements read, and the review status.
Config:  work_dir, title
"""
import glob, html, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as IC  # noqa: E402


def main():
    ap = IC.parser(__doc__.split('\n')[0])
    ap.add_argument('--descriptions')
    args = ap.parse_args()
    cfg = IC.load(args)
    d = IC.read_json(cfg.out('dictionary.json'))
    ddir = args.descriptions or (cfg.out('dict-desc', 'out') if os.path.isdir(os.path.join(cfg.work_dir, 'dict-desc', 'out')) else None)
    desc = {}
    if ddir:
        for p in sorted(glob.glob(os.path.join(ddir, '*.json'))):
            desc.update(IC.read_json(p))
    e = html.escape
    rows = []
    for r in d['rows']:
        dd = desc.get(r['id']) or {}
        text = dd.get('description') or r['description'] or '<i>no description</i>'
        flag = f"<div class='warn'>{e(dd['nameMismatch'])}</div>" if dd.get('nameMismatch') else ''
        des = '; '.join(f"{e(x['name'])} <span class='m'>{e(x['aggregationType'] or '')}, {e(x['periodType'] or 'no data set')}</span>" for x in r['dataElements'])
        st = f"<span class='st st-{e(r['status'])}'>{e(r['status'])}</span>" if r['status'] else ''
        rows.append(f"""<tr><td><b>{e(r['name'] or '')}</b><div class='m'>{e(r['code'])} · {e(r['type'])}{' · annualized' if r['annualized'] else ''}{f" · {r['decimals']} decimals" if r['decimals'] is not None else ''}</div>{st}</td>
<td>{e(text) if dd.get('description') or r['description'] else text}{flag}</td>
<td><div class='side'><b>Numerator</b> {e(r['numerator']['text'])}</div><div class='side'><b>Denominator</b> {e(r['denominator']['text'])}</div>
<details><summary>expression</summary><code>{e(r['numerator']['expression'])}</code><br><code>{e(r['denominator']['expression'])}</code></details></td>
<td class='m'>{des}</td></tr>""")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Indicator dictionary</title><style>
:root{{--bg:#fff;--fg:#1d2733;--muted:#5b6876;--line:#d9dee4;--warn:#9a5b00;--ok:#2e7d32;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14181d;--fg:#e6e9ed;--muted:#9aa5b1;--line:#2c333b;--warn:#f0b54a;--ok:#7cc47f;--bad:#ff8a80}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} .m{{color:var(--muted);font-size:12px}} input{{width:100%;max-width:420px;padding:6px;margin:10px 0;font:inherit}}
table{{border-collapse:collapse;width:100%}} td{{border-top:1px solid var(--line);padding:8px 6px;vertical-align:top}}
td:first-child{{width:22%}} .side{{margin-bottom:4px}} code{{font-size:12px;word-break:break-all}} .warn{{color:var(--warn);margin-top:4px}}
.st{{font-size:11px;font-weight:600}} .st-OK{{color:var(--ok)}} .st-FAILS,.st-WRONG_VALUE,.st-ALWAYS_ZERO{{color:var(--bad)}} .st-REVIEW{{color:var(--warn)}}
@media (max-width:700px){{td{{display:block;width:auto!important}} tr{{display:block;border-top:1px solid var(--line)}} td{{border:0}}}}
</style></head><body><h1>{e(d['title'])}: indicator dictionary</h1><div class="m">{e(d['scope'])}</div>
<input id="q" placeholder="Filter by name, code or data element" aria-label="Filter">
<table><tbody id="t">{''.join(rows)}</tbody></table>
<script>document.getElementById('q').addEventListener('input',e=>{{const q=e.target.value.toLowerCase();for(const r of document.querySelectorAll('#t tr'))r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';}});</script>
</body></html>"""
    out = cfg.out('indicator-dictionary.html')
    with open(out, 'w') as f:
        f.write(page)
    print(f"{len(rows)} indicators ({len(desc)} descriptions merged) -> {out}")


if __name__ == '__main__':
    main()
