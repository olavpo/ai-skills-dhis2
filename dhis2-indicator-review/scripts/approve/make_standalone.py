"""Standalone approval page for reviewers without a Claude account -> <p>-fix-review-standalone.html (<p> = adapter.PREFIX).

Same template and data as the artifact page (fix_review_template.html + fix-review.json), in "standalone" mode:
- decisions are kept in the browser (localStorage, per data version) instead of the shared store;
- a "Your name" field; every decision records who and when;
- "Save decisions file" downloads a small file
    {"format": "pireview-fix-decisions/1", "dataVersion": "<hash of the proposals>", "title", "reviewer", "savedAt",
     "approvals": {"<group>": {"states": {"<itemId>": "approved|rejected"}, "meta": {"<itemId>": {"by", "at"}}}},
     "descriptions": {"<itemId>": {"text", "by", "at"}}}
- "Load decisions files" merges any number of them: per (group, item) the newest "at" wins; when two reviewers made
  different non-pending decisions on the same item, it is listed as a disagreement on the overview; a file from
  another data version is loaded with a warning;
- exports are plain file downloads (approved items / approved but still failing / aux objects / summary CSV).
A single offline file (fonts fall back to system fonts without network).

Inputs : fix-review.json (build_fix_review.py)
Outputs: <p>-fix-review-standalone.html
Config : work_dir (+ type, for the file name).
"""
import html, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'lib'))
import adapters, config  # noqa: E402
from build_fix_review import TEMPLATE, page  # noqa: E402


def standalone(data):
    t = page(TEMPLATE, data, 'standalone')
    short = (data.get('labels') or {}).get('short', '')
    title = f'<title>{html.escape(short)} Fix Approvals: {html.escape(data.get("title", ""))}</title>'
    assert t.startswith('<title>Fix Approvals</title>') and '</style>\n' in t, 'template head changed'
    t = t.replace('<title>Fix Approvals</title>', '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1">\n' + title, 1)
    t = t.replace('</style>\n', '</style>\n</head><body>\n', 1)
    return t + '</body></html>\n'


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    cfg = config.load(ap.parse_args())
    p = cfg.out('fix-review.json')
    if not os.path.exists(p):
        sys.exit(f'no {p}: run build_fix_review.py first')
    with open(p) as f:
        d = json.load(f)
    out = standalone(d)
    path = cfg.out(adapters.out_name(adapters.load(cfg), 'fix-review-standalone.html'))
    with open(path, 'w') as f:
        f.write(out)
    print('version', d['version'], 'KB', len(out.encode()) // 1024, '->', path)


if __name__ == '__main__':
    main()
