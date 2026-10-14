"""Screenshots for the evidence: the Maintenance app showing changed indicators after the import, and the saved pivot
tables (make_viz.py) in Data Visualizer. Playwright (Python) with Chromium; imported only when this script runs.

Credentials come from the environment like every other script: DHIS2_URL, and DHIS2_USER + DHIS2_PASSWORD (form
login through /api/auth/login) or DHIS2_TOKEN (sent as a header on every request). The browser is always closed.

Inputs : fixes.json (default PIs: the first item of each fix group, at most --max), viz-ids.json
Outputs: <work_dir>/shots/NN-<name>.png, shots/captions.json {name: caption} (read by evidence.py and build_fix_review.py)
Options: --pis UID|CODE ... (Maintenance pages to capture), --max N, --width/--height, --wait seconds per page
Config : work_dir, program, metadata.
"""
import os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
import config  # noqa: E402
import e2elib as E  # noqa: E402

# App routes as on 2.43 (bundled apps are served from these paths)
MAINT = '/dhis-web-maintenance/index.html#/edit/indicatorSection/programIndicator/{}'
DV = '/dhis-web-data-visualizer/index.html#/{}'


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--pis', nargs='*', help='PIs (uid or code) to show in the Maintenance app')
    ap.add_argument('--max', type=int, default=3)
    ap.add_argument('--width', type=int, default=1900)
    ap.add_argument('--height', type=int, default=1000)
    ap.add_argument('--wait', type=float, default=8, help='seconds to let each app render after network idle')
    args = ap.parse_args()
    cfg = config.load(args)
    base = (os.environ.get('DHIS2_URL') or sys.exit('set DHIS2_URL')).rstrip('/')
    tok, user, pw = os.environ.get('DHIS2_TOKEN'), os.environ.get('DHIS2_USER'), os.environ.get('DHIS2_PASSWORD')
    if not (tok or (user and pw)):
        sys.exit('set DHIS2_TOKEN, or DHIS2_USER and DHIS2_PASSWORD')
    F = E.fixes(cfg)
    md = E.after_metadata(cfg, write=False)
    if args.pis:
        pis = [E.resolve_pi(md, r) for r in args.pis]
    else:
        seen = []
        for g in F['groups']:
            r = next((r for r in F['pis'] if g['key'] in r['groups'] and r['id'] not in seen), None)
            if r:
                seen.append(r['id'])
        pis = [E.resolve_pi(md, i) for i in seen[:args.max]]
    targets = []
    for p in [p for p in pis if p]:
        lab = E.pi_label(p)
        targets.append((f'maintenance-{lab}', base + MAINT.format(p['id']), f'Maintenance app after the import: {lab} with its new definition and description.'))
    V = E.jload(cfg.out('viz-ids.json'), {})
    CAT = {c['id']: c['name'] for c in md['categories']}
    for k, u in V.items():
        cap = ('Data Visualizer: every scenario indicator in its scenario org unit after the fix (compare with the expected “after” values).'
               if k == 'scenarios' else f'Data Visualizer: breakdown by {CAT.get(k.split(":", 1)[-1], k)} on all test data after the fix.')
        targets.append((f'dv-{k.replace(":", "-")}', base + DV.format(u), cap))
    if not targets:
        sys.exit('nothing to capture')

    from playwright.sync_api import sync_playwright  # lazy: only this script needs Playwright
    cfg.out('shots', 'captions.json')  # creates shots/
    caps = {}
    with sync_playwright() as pw_:
        br = pw_.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            ctx = br.new_context(viewport={'width': args.width, 'height': args.height},
                                 extra_http_headers={'Authorization': 'ApiToken ' + tok} if tok else None)
            page = ctx.new_page()
            if not tok:
                r = page.request.post(base + '/api/auth/login', data={'username': user, 'password': pw})
                if r.status >= 400:
                    sys.exit(f'login failed: HTTP {r.status}')
            for i, (name, url, cap) in enumerate(targets, 1):
                fn = f'{i:02d}-{"".join(c if c.isalnum() or c in "-_" else "_" for c in name)}'
                page.goto(url, wait_until='networkidle', timeout=180000)
                time.sleep(args.wait)
                page.screenshot(path=cfg.out('shots', fn + '.png'), full_page=True)
                caps[fn] = cap
                print(fn, page.url)
        finally:
            br.close()
    E.jdump(caps, cfg.out('shots', 'captions.json'))


if __name__ == '__main__':
    main()
