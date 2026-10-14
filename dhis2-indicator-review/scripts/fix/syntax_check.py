"""Server syntax check of every proposed text of the changed items and aux objects (read-only).

    python3 scripts/fix/syntax_check.py [--config pireview.json] [--all-options]

Input   work_dir/fixes.json (after build_payload.py, or straight after build_fixes.py)
Output  work_dir/syntax-check.json  {"items": {"<code or uid>": {"<field>": {status, message, description}}},
                                     "aux": {"<aux / part>": {text, status, message, description}}}
Exit code 1 when any text fails.
The texts come from the type adapter (adapter.texts_to_validate, adapter.aux_texts_to_validate) and are checked with
adapter.validate_text (the type's server description endpoint).
This does not write to the instance, so it may run against any instance with the same metadata; syntax OK does not
prove the text runs in analytics (I3): query analytics. Aux texts: the changed parts, or every part of a changed aux
object with --all-options (one malformed aux text can break every item using it). An empty aux text is
an error. Connection from the environment (lib/d2.py). Config keys: work_dir (+ type keys).
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import adapters, config  # noqa: E402


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--all-options', action='store_true', help='check every part of each changed aux object, not only the changed ones')
    a = ap.parse_args()
    cfg = config.load(a)
    A = adapters.load(cfg)
    with open(cfg.out('fixes.json')) as f:
        F = json.load(f)
    out, bad, n = {'items': {}, 'aux': {}}, 0, 0
    for r in F['items']:
        res = {}
        for field, kind, txt in A.texts_to_validate(r['after']):
            ok, msg, desc = A.validate_text(kind, txt)
            res[field] = {'status': 'OK' if ok else 'ERROR', 'message': msg, 'description': (desc or '')[:300]}
            n += 1
            if not ok:
                bad += 1
                print('BAD', r.get('code') or r['id'], field, msg)
        out['items'][r.get('code') or r['id']] = res
    for m in F.get('aux', []):
        for label, kind, txt in A.aux_texts_to_validate(cfg, m['before'], m['after'], a.all_options):
            if not (txt or '').strip():
                out['aux'][label] = {'text': txt, 'status': 'ERROR', 'message': 'empty text', 'description': None}
                bad += 1; print('BAD', label, 'empty text'); continue
            ok, msg, desc = A.validate_text(kind, txt)
            out['aux'][label] = {'text': txt, 'status': 'OK' if ok else 'ERROR', 'message': msg, 'description': desc}
            n += 1
            if not ok:
                bad += 1
                print('BAD', label, msg)
    with open(cfg.out('syntax-check.json'), 'w') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f'checked {n} texts ({len(out["items"])} {A.LABELS["shorts"]}, {len(out["aux"])} {A.LABELS["aux_short"]} texts); failures: {bad}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
