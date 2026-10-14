"""Reviewer guide for the approval page: screenshots of the standalone page + manual_template.html -> PDF.

Drives <p>-fix-review-standalone.html in headless Chromium (Python Playwright, imported lazily) with fictional
reviewers ("Reviewer A", "Reviewer B", "Coordinator"), each in a fresh browser context, so nothing touches real
decisions: overview, a fix group with an opened card, decisions, a partly approved item, two decisions files with one
disagreement and their merge. Groups and items are picked from the data (the largest item group; an item with
several fixes); a figure whose situation doesn't exist in the data (e.g. no item with several fixes) is left out.
Then fills the template placeholders from the config and fix-review.json (labels, export formats) and prints it to
PDF (page.pdf, A4). Parts about aux objects (<!--aux-->...<!--/aux-->) are dropped when the type has none.
The browser is always closed.

Inputs : <p>-fix-review-standalone.html (make_standalone.py), fix-review.json
Outputs: <work_dir>/manual/*.png, manual/guide.html, manual/dec-*.json (the sample decisions files),
         <P>-Fix-Approvals-reviewer-guide.pdf
Config : title, work_dir, dhis2_version, exported.
"""
import datetime as dt, html, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'lib'))
import adapters, config  # noqa: E402


def shots(page_url, D, out):
    from playwright.sync_api import sync_playwright  # lazy: only this script needs Playwright
    ik = (D.get('kinds') or {}).get('item')
    pi_groups = sorted([g for g in D['groups'] if g['kind'] == ik], key=lambda g: -len(g['items']))
    G1 = pi_groups[0] if pi_groups else (D['groups'][0] if D['groups'] else None)
    decided = set(G1['items'][:3]) if G1 else set()
    multi = next((p for p in D['items'] if len(p['groups']) > 1 and p['id'] not in decided), None) or \
        next((p for p in D['items'] if len(p['groups']) > 1), None)
    made = set()
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            def reviewer(name):
                ctx = b.new_context(accept_downloads=True, viewport={'width': 1360, 'height': 860}, device_scale_factor=2)
                pg = ctx.new_page()
                pg.goto(page_url)
                pg.fill('#reviewer', name)
                pg.press('#reviewer', 'Tab')
                return ctx, pg

            def shot(loc, name):
                loc.screenshot(path=os.path.join(out, name + '.png'))
                made.add(name)

            def card(pg, item_id):
                return pg.locator(f'article[data-card="{item_id}"]')

            def save_decisions(pg, name):
                with pg.expect_download() as dl:
                    pg.click('#saveDec')
                path = os.path.join(out, name)
                dl.value.save_as(path)
                return path

            ctx, pg = reviewer('Reviewer A')
            shot(pg, 'm1-overview')
            files = []
            if G1:
                pg.click(f'[data-view="{G1["key"]}"]')
                first = G1['items'][min(1, len(G1['items']) - 1)]
                if G1['kind'] == ik:
                    pg.click(f'[data-open="{first}"]')
                    pg.check('#readable')
                pg.wait_for_timeout(300)
                shot(pg, 'm2-group')
                shot(card(pg, first), 'm3-card')
                for i, item in enumerate(G1['items'][:3]):
                    card(pg, item).locator(f'[data-set="{"rejected" if i == 2 else "approved"}"]').click()
                shot(pg.locator('header.top'), 'm4-header')
            if multi:
                others = [x for x in multi['groups'] if x != (G1 or {}).get('key')]
                g = others[0] if others else multi['groups'][0]
                pg.click(f'[data-view="{g}"]')
                c = card(pg, multi['id'])
                if 'Approved' not in (c.locator('[data-set="approved"]').text_content() or ''):
                    c.locator('[data-set="approved"]').click()
                pg.wait_for_timeout(200)
                if card(pg, multi['id']).locator('[data-allfixes]').count():
                    shot(card(pg, multi['id']), 'm5-partial')
            if G1:
                files.append(save_decisions(pg, 'dec-reviewer-a.json'))
            ctx.close()
            if G1:
                ctx, pg = reviewer('Reviewer B')
                pg.wait_for_timeout(1100)  # B's decision is newer
                pg.click(f'[data-view="{G1["key"]}"]')
                card(pg, G1['items'][0]).locator('[data-set="rejected"]').click()
                files.append(save_decisions(pg, 'dec-reviewer-b.json'))
                ctx.close()
                ctx, pg = reviewer('Coordinator')
                pg.set_input_files('#loadDec', files)
                pg.wait_for_timeout(500)
                pg.click('[data-view="overview"]')
                pg.wait_for_timeout(200)
                box = pg.locator('.flag.warn', has_text='reviewers disagree')
                if box.count():
                    shot(box.first, 'm7-conflict')
                ctx.close()
        finally:
            b.close()
    return made


def fill(cfg, D, made, out):
    with open(os.path.join(HERE, 'manual_template.html')) as f:
        t = f.read()
    e = html.escape
    LB, AX = D.get('labels') or {}, D.get('auxExport')
    capf = lambda x: x[:1].upper() + x[1:]
    via = len({c for m in D['aux'] for c in m['users']})
    stem = D.get('fileStem', 'review')
    method, _, path = ((AX or {}).get('endpoint') or ' ').partition(' ')
    aux_file = f"{stem}-{AX['file']}-&lt;date&gt;{'.patch' if AX['kind'] == 'jsonpatch' else ''}.json" if AX else ''
    vals = {'TITLE': e(cfg.title), 'DATE': dt.date.today().strftime('%d %B %Y').lstrip('0'),
            'VERSION_NOTE': f' (DHIS2 {e(cfg.dhis2_version)})' if cfg.dhis2_version else '',
            'NPIS': str(len(D['items'])), 'NMAPS': str(len(D['aux'])), 'NVIA': str(via), 'NEXCL': str(len(D['excluded'])),
            'STEM': e(stem), 'ITEMS_STEM': e(f"{stem}-{LB.get('items', 'items').replace(' ', '-')}"), 'PREFIX': e(D.get('prefix', 'review')),
            'SHORT': e(LB.get('short', '')[:1].upper() + LB.get('short', '')[1:]), 'SHORTS': e(LB.get('shorts', 'items')), 'ITEM': e(LB.get('item', 'item')),
            'ITEMS': e(LB.get('items', 'items')), 'ITEMS_CAP': e(capf(LB.get('items', 'items'))), 'FIELDS': e(LB.get('fields', 'the definition')),
            'AUXS': e(LB.get('auxs', '')), 'AUXS_CAP': e(capf(LB.get('auxs', ''))), 'AUX_SHORT': e(LB.get('aux_short', '')),
            'AUX_SHORTS': e(LB.get('aux_shorts', '')), 'AUX_SHORT_CAP': e(capf(LB.get('aux_short', ''))), 'AUX_SHORTS_CAP': e(capf(LB.get('aux_shorts', ''))),
            'AUX_FILE': aux_file, 'AUX_METHOD': e(method), 'AUX_PATH': e(path),
            'AUX_CTYPE': e((AX or {}).get('contentType') or 'application/json'),
            'AUX_WHAT': e(f"Approved {LB.get('aux', '')} changes, for {(AX or {}).get('endpoint', '')}" + (f" ({AX['why']})." if (AX or {}).get('why') else '.')),
            'AUX_IMPORT_NOTE': e(f"the {LB.get('aux_shorts', '')} file needs the API call" if AX and AX['kind'] == 'jsonpatch' else f"so can the {LB.get('aux_shorts', '')} file"),
            'EXPORTED_NOTE': f' ({e(cfg.exported)})' if cfg.exported else ''}
    t = re.sub(r'<!--aux-->(.*?)<!--/aux-->', lambda m: m.group(1) if AX else '', t, flags=re.S)
    t = re.sub(r'\{\{(\w+)\}\}', lambda m: vals[m.group(1)], t)
    # drop figures whose screenshot was not made
    t = re.sub(r'<figure data-shot="([\w-]+)">.*?</figure>\n?', lambda m: m.group(0) if m.group(1) in made else '', t, flags=re.S)
    path = os.path.join(out, 'guide.html')
    with open(path, 'w') as f:
        f.write(t)
    return path


def pdf(guide, target):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--disable-dev-shm-usage'])
        try:
            pg = b.new_page()
            pg.goto('file://' + guide)
            pg.wait_for_timeout(300)
            pg.emulate_media(media='print')
            pg.pdf(path=target, format='A4', print_background=True, prefer_css_page_size=True,
                   display_header_footer=True, header_template='<span></span>',
                   footer_template='<div style="font-size:8px;width:100%;text-align:center;color:#777"><span class="pageNumber"></span> / <span class="totalPages"></span></div>')
        finally:
            b.close()


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    cfg = config.load(ap.parse_args())
    A = adapters.load(cfg)
    page = cfg.out(adapters.out_name(A, 'fix-review-standalone.html'))
    if not os.path.exists(page):
        sys.exit(f'no {page}: run make_standalone.py first')
    with open(cfg.out('fix-review.json')) as f:
        D = json.load(f)
    out = os.path.dirname(cfg.out('manual', 'guide.html'))
    made = shots('file://' + page, D, out)
    guide = fill(cfg, D, made, out)
    target = cfg.out(adapters.out_name(A, 'Fix-Approvals-reviewer-guide.pdf'))
    pdf(guide, target)
    print('screenshots', sorted(made), '->', target)


if __name__ == '__main__':
    main()
