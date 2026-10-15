"""Scenario builder: turns the hand-written intent scenarios into test data, one org unit per scenario.

A scenario is a handful of clients with the value an indicator should have BEFORE and AFTER the fix, worked out by
hand from what the indicator is meant to count (never from the evaluator). The agent writes them per program into
<work_dir>/scenarios.json; see scenarios.example.json next to this script (a fictional program) for the format:

    {"scenarios": [{
        "id": "late-visit",                    # unique key
        "group": "window+agebands",            # fix group key(s) from fixes.json, '+'-joined
        "title": "...", "note": "why these values are expected",
        "pe": "2026Q1",                        # optional; query period, default cfg.test_period
        "choose": {"<category>": "<option>"},  # optional; option to place clients in when the scenario doesn't care
        "clients": [{"label": "A: counts", "enrolled": "2025-11-02", "incident": "2025-11-02",
                     "attributes": {"<attribute>": "value"},
                     "events": [{"stage": "<stage>", "date": "2026-02-10", "values": {"<data element>": "value"}}]}],
        "expect": [{"item": "<PI uid or code>", "before": "error" | 0, "after": 1,     # "pi" is accepted too
                    "cellsAfter": {"<category>": {"<option>": 1}},       # optional
                    "probe": {"before": "<uid or probe key>", "after": "..."}}]   # optional, see probes.py
    }]}

Stages, data elements, attributes, categories and options may be given by uid, code or name; option-set values by
code or option name (written as the code). Values are DHIS2 strings.

Disaggregation: for every expectation, every category of the PI's category combo and attribute combo with a
mapping on the program, each counted record (event row for EVENT PIs, enrollment for ENROLLMENT PIs) must match
exactly one option (behaviours C1/C2). Fields the scenario doesn't set are filled automatically from the mapping
filters of the PROPOSED definitions; a record whose own values match 0 or 2+ options is refused, because event PIs
drop it from the total and the test would prove nothing. Expectations with a probe are not filled (probe PIs carry
no breakdown).

Inputs : <work_dir>/scenarios.json, fixes + proposed payload (for the proposed mappings), the metadata export.
Outputs: <work_dir>/data/scen-tracker.json (POST /api/tracker), data/scen-truth.json (truth.json format,
         witness_for "scenario:<id>"), data/scen-ous.json (org units), scenario-checks.json (one row per expectation:
         key, scenario, group, item (= pi, kept for older readers), code, title, note, ou, pe, persons, expectBefore,
         expectAfter, expectCellsAfter {category: {option name: value}}, probe).
Config : metadata, program, work_dir, test_period, org_units.root, org_units.scenario_prefix, field_resolution.
"""
import datetime as dt, itertools, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, d2, piexpr as P  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'e2e'))
import e2elib as E  # noqa: E402
import common  # noqa: E402

NUMERIC = ('NUMBER', 'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE', 'INTEGER_ZERO_OR_POSITIVE', 'PERCENTAGE', 'UNIT_INTERVAL')
DATES = ('DATE', 'DATETIME', 'AGE')
MAX_TRIALS = 20000


class Refused(Exception):
    pass


class Builder:
    def __init__(self, cfg):
        self.cfg = cfg
        self.md = E.after_metadata(cfg)          # proposed definitions: scenarios are made to be counted AFTER the fix
        self.prog = common.program_of(self.md, cfg.program_id)
        md = self.md
        self.DE = {x['id']: x for x in md['dataElements']}
        self.TEA = {x['id']: x for x in md['trackedEntityAttributes']}
        self.OPT = {x['id']: x for x in md['options']}
        self.OS = {s['id']: [self.OPT[o['id']] for o in s.get('options', []) if o['id'] in self.OPT] for s in md['optionSets']}
        self.STAGES = [s for s in md['programStages'] if (s.get('program') or {}).get('id', cfg.program_id) == cfg.program_id]
        self.STAGE_DES = {s['id']: {x['dataElement']['id'] for x in s.get('programStageDataElements', [])} for s in self.STAGES}
        self.MAPS = {m['id']: m for m in self.prog.get('categoryMappings', [])}
        self.CAT = {c['id']: c for c in md['categories']}
        self.CO = {c['id']: c for c in md['categoryOptions']}
        self.CC = {c['id']: c for c in md['categoryCombos']}
        self.ps, self.pe = cfg.period_bounds()
        self.lits = {}

    # ---------- name resolution ----------
    def _find(self, objs, ref, what):
        hits = [o for o in objs if ref in (o['id'], o.get('code'), o.get('name'), o.get('shortName'), o.get('formName'))]
        if len({h['id'] for h in hits}) != 1:
            raise Refused(f'{what} {ref!r}: {"not found" if not hits else "ambiguous"}')
        return hits[0]

    def stage(self, ref):
        return self._find(self.STAGES, ref, 'stage')['id']

    def de(self, ref, stage):
        return self._find([self.DE[i] for i in self.STAGE_DES[stage] if i in self.DE], ref, f'data element (stage {stage})')

    def tea(self, ref):
        pa = [a['trackedEntityAttribute']['id'] for a in self.prog.get('programTrackedEntityAttributes', [])]
        pool = [self.TEA[i] for i in pa if i in self.TEA] or list(self.TEA.values())
        return self._find(pool, ref, 'attribute')

    def value(self, obj, v):
        """Option-set values may be given by option name; DHIS2 stores the code."""
        v = '' if v is None else str(v)
        os_ = (obj.get('optionSet') or {}).get('id')
        if os_ and v:
            opts = self.OS.get(os_, [])
            if not any(o.get('code') == v for o in opts):
                m = [o for o in opts if o.get('name') == v]
                if len(m) == 1:
                    return m[0]['code']
                raise Refused(f'{obj.get("name")}: {v!r} is not a code of its option set')
        return v

    # ---------- typed context ----------
    def meta(self, node):
        return self.DE.get(node[2]) if node[0] == 'de' else self.TEA.get(node[1])

    def typed(self, node, v):
        if v is None or v == '':
            return None
        o = self.meta(node) or {}
        vt = o.get('valueType', 'TEXT')
        if vt in DATES:
            return P.to_date(v)
        if vt in NUMERIC and not o.get('optionSet'):
            return P.to_num(v)
        if vt in ('BOOLEAN', 'TRUE_ONLY'):
            return 1.0 if str(v).lower() == 'true' else 0.0
        return v

    def ctx(self, row, extra=None):
        extra = extra or {}

        def c(n):
            if n[0] == 'var':
                return {'analytics_period_start': self.ps, 'analytics_period_end': self.pe,
                        'event_date': P.to_date(row.get('event_date')), 'enrollment_date': P.to_date(row.get('enrolled')),
                        'incident_date': P.to_date(row.get('incident'))}.get(n[1])
            if n in extra:
                return self.typed(n, extra[n])
            return self.typed(n, self.get(row, n))
        return c

    # ---------- rows: where a PI reads its values ----------
    def rows(self, client, pi):
        """EVENT PI: one row per event of a stage the PI refers to (all events if none). ENROLLMENT PI: one row per
        client, reading the latest event of each stage (capped at the period end when the PI has an EVENT_DATE
        BEFORE_END boundary, behaviour C5)."""
        text = (pi.get('expression') or '') + ' ' + (pi.get('filter') or '')
        used = set(re.findall(r'#\{(\w+)\.\w+\}', text)) & set(self.STAGE_DES)
        if pi.get('analyticsType', 'EVENT') == 'EVENT':
            return [{'kind': 'event', 'client': client, 'events': {e['stage']: e}, 'event_date': e['date'],
                     'enrolled': client.get('enrolled'), 'incident': client.get('incident')}
                    for e in client['events'] if not used or e['stage'] in used]
        cap = any(b.get('boundaryTarget') == 'EVENT_DATE' and b.get('analyticsPeriodBoundaryType', '').startswith('BEFORE_END')
                  for b in pi.get('analyticsPeriodBoundaries') or [])
        latest = {}
        for e in sorted(client['events'], key=lambda e: e['date']):
            if not cap or P.to_date(e['date']) <= self.pe:
                latest[e['stage']] = e
        return [{'kind': 'enrollment', 'client': client, 'events': latest, 'enrolled': client.get('enrolled'),
                 'incident': client.get('incident')}]

    def get(self, row, n):
        if n[0] == 'tea':
            return row['client']['attributes'].get(n[1])
        e = row['events'].get(n[1])
        return e['values'].get(n[2]) if e else None

    def fillable(self, row, n):
        if n[0] == 'tea':
            return n[1] in self.TEA
        return n[1] in row['events'] and n[2] in self.STAGE_DES.get(n[1], ())

    def put(self, row, n, v):
        if n[0] == 'tea':
            row['client']['attributes'][n[1]] = v
        else:
            row['events'][n[1]]['values'][n[2]] = v

    # ---------- candidates ----------
    def literals(self, filters):
        out = {}
        for f in filters:
            for n in P.walk(f):
                if n[0] == 'bin' and n[1] in ('==', '!=', '<', '>', '<=', '>='):
                    for a, b in ((n[2], n[3]), (n[3], n[2])):
                        for leaf in P.walk(a):
                            if leaf[0] in ('de', 'tea') and b[0] in ('num', 'str'):
                                out.setdefault(leaf, set()).add(b[1])
                if n[0] == 'fn' and n[1] in ('contains', 'containsItems') and n[2] and n[2][0][0] in ('de', 'tea'):
                    out.setdefault(n[2][0], set()).update(a[1] for a in n[2][1:] if a[0] == 'str')
        return out

    def candidates(self, n, lits):
        o = self.meta(n) or {}
        vt = o.get('valueType', 'TEXT')
        L = lits.get(n, set())
        if o.get('optionSet'):
            return [x['code'] for x in self.OS.get(o['optionSet']['id'], [])] or sorted(map(str, L))
        if vt in ('BOOLEAN', 'TRUE_ONLY'):
            return ['true', 'false'] if vt == 'BOOLEAN' else ['true']
        if vt in DATES:
            out = [self.pe, self.ps, self.pe - dt.timedelta(days=1)]
            for x in L:
                if isinstance(x, float):  # an age/interval literal: dates that many years (and a bit) before the end
                    for y, d in ((int(x), 0), (int(x), 180), (int(x) + 1, -1), (int(x), -1)):
                        try:
                            out.append(self.pe.replace(year=self.pe.year - y) - dt.timedelta(days=d))
                        except ValueError:
                            pass
                elif P.to_date(x):
                    out += [P.to_date(x) + dt.timedelta(days=k) for k in (-1, 0, 1)]
            return [d.isoformat() for d in dict.fromkeys(out)]
        if vt in NUMERIC:
            res = self.cfg.resolution('#{%s.%s}' % (n[1], n[2]) if n[0] == 'de' else 'A{%s}' % n[1], vt)
            vals = {0.0, 1.0}
            for x in L:
                x = P.to_num(x)
                if x is not None:
                    vals |= {x, x - res, x + res, x + 0.5}
            vals = sorted(v for v in vals if v >= 0 or vt in ('NUMBER', 'INTEGER', 'INTEGER_NEGATIVE'))
            if vt.startswith('INTEGER'):
                vals = sorted({float(round(v)) for v in vals})
            return [str(int(v)) if v == int(v) else format(v, '.10g') for v in vals]
        return sorted(str(x) for x in L if isinstance(x, str)) or ['Test value']

    # ---------- placement ----------
    def hits(self, row, opts, extra=None):
        c = self.ctx(row, extra)
        return [o for o in opts if o['_ast'] is not None and P.ev(o['_ast'], c) is True]

    def place(self, row, cat, mapping, want):
        opts = [{**o, '_ast': P.parse(o['filter']) if (o.get('filter') or '').strip() else None} for o in mapping['optionMappings']]
        name = lambda o: (self.CO.get(o['optionId']) or {}).get('name', o['optionId'])
        h = self.hits(row, opts)
        if len(h) > 1:
            raise Refused(f'matches {len(h)} options of category {self.CAT[cat]["name"]!r}: {[name(o) for o in h]}')
        if len(h) == 1:
            if want and h[0]['optionId'] != want:
                raise Refused(f'is in option {name(h[0])!r} of {self.CAT[cat]["name"]!r}, not the chosen {name({"optionId": want})!r}')
            return
        lits = self.literals([o['_ast'] for o in opts if o['_ast'] is not None])
        leaves = list(dict.fromkeys(x for o in opts if o['_ast'] is not None for x in P.walk(o['_ast'])
                                    if x[0] in ('de', 'tea') and self.get(row, x) in (None, '')))
        free = [x for x in leaves if self.fillable(row, x)]
        pools = [self.candidates(x, lits) for x in free]
        for t in [o for o in opts if o['_ast'] is not None and (not want or o['optionId'] == want)]:
            if not free:
                break
            for combo in itertools.islice(itertools.product(*pools), MAX_TRIALS):
                extra = dict(zip(free, combo))
                hh = self.hits(row, opts, extra)
                if len(hh) == 1 and hh[0] is t:
                    # write only the fields the target option reads, unless the others are needed to rule options out
                    need = {k: v for k, v in extra.items() if k in {x for x in P.walk(t['_ast']) if x[0] in ('de', 'tea')}}
                    keep = need if [o is t for o in self.hits(row, opts, need)] == [True] else extra
                    for x, v in keep.items():
                        self.put(row, x, v)
                    return
        why = 'no field of the mapping can be set on this record (different stage?)' if not any(
            self.fillable(row, x) for o in opts if o['_ast'] for x in P.walk(o['_ast']) if x[0] in ('de', 'tea')) else 'no value makes exactly one option match'
        raise Refused(f'matches no option of category {self.CAT[cat]["name"]!r} and could not be placed: {why}')

    def categories(self, pi):
        out = []
        for k in ('categoryCombo', 'attributeCombo'):
            cc = self.CC.get((pi.get(k) or {}).get('id'))
            for c in (cc or {}).get('categories', []):
                cat = self.CAT.get(c['id'])
                if cat and cat.get('name') != 'default' and c['id'] not in out:
                    out.append(c['id'])
        return out

    def mapping_for(self, pi, cat):
        for mid in pi.get('categoryMappingIds') or []:
            m = self.MAPS.get(mid)
            if m and m.get('categoryId') == cat:
                return m
        return None

    def option_id(self, cat, ref):
        opts = [self.CO[o['id']] for o in self.CAT[cat].get('categoryOptions', []) if o['id'] in self.CO]
        return self._find(opts, ref, f'option of {self.CAT[cat]["name"]}')['id']

    def fill(self, sc, clients, pi):
        choose = {}
        for k, v in (sc.get('choose') or {}).items():
            c = common.resolve_category(self.md, k)
            if not c:
                raise Refused(f'choose: unknown category {k!r}')
            choose[c] = self.option_id(c, v)
        cats = self.categories(pi)
        for i, cl in enumerate(clients):
            for row in self.rows(cl, pi):
                for cat in cats:
                    m = self.mapping_for(pi, cat)
                    if not m:
                        continue  # analytics will fail with E8; that's the scenario's business
                    try:
                        self.place(row, cat, m, choose.get(cat))
                    except Refused as e:
                        f = (pi.get('filter') or '').strip()
                        counted = P.ev(P.parse(f), self.ctx(row)) if f else True
                        if counted is False:
                            continue  # a near-miss the PI does not count anyway
                        raise Refused(f'client {cl.get("label") or i + 1}' + (f', event {row["event_date"]}' if row['kind'] == 'event' else '') + f': {e}')

    # ---------- build ----------
    def client(self, raw):
        c = {'label': raw.get('label'), 'enrolled': raw.get('enrolled'), 'incident': raw.get('incident') or raw.get('enrolled'),
             'attributes': {}, 'events': []}
        for k, v in (raw.get('attributes') or {}).items():
            t = self.tea(k)
            c['attributes'][t['id']] = self.value(t, v)
        for e in raw.get('events') or []:
            st = self.stage(e['stage'])
            vals = {}
            for k, v in (e.get('values') or {}).items():
                d = self.de(k, st)
                vals[d['id']] = self.value(d, v)
            c['events'].append({'stage': st, 'date': e['date'], 'values': vals})
        return c

    def build(self, scenarios):
        cfg, checks, tes, events, truth, ous = self.cfg, [], [], [], [], []
        width = 11 - len(cfg.scenario_prefix)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,9}', cfg.scenario_prefix):
            sys.exit(f'org_units.scenario_prefix {cfg.scenario_prefix!r} must be 1-10 letters/digits starting with a letter')
        seen, errors = set(), []
        tracker = self.prog.get('programType', 'WITH_REGISTRATION') != 'WITHOUT_REGISTRATION'
        tet = cfg.tracked_entity_type
        for n, sc in enumerate(scenarios, 1):
            sid = sc['id']
            if sid in seen:
                sys.exit(f'duplicate scenario id {sid}')
            seen.add(sid)
            ou = cfg.scenario_prefix + str(n).zfill(width)
            try:
                clients = [self.client(c) for c in sc.get('clients', [])]
                for ex in sc.get('expect', []):
                    pi = common.resolve_pi(self.md, ex.get('item') or ex.get('pi'))
                    if not pi:
                        raise Refused(f'unknown PI {ex.get("item") or ex.get("pi")!r}')
                    if not ex.get('probe'):
                        self.fill(sc, clients, pi)
            except Refused as e:
                errors.append(f'scenario {sid}: {e}')
                continue
            ous.append({'id': ou, 'name': f'{cfg.scenario_prefix} {n:02d} {sid}'[:230], 'shortName': f'{cfg.scenario_prefix} {n:02d} {sid}'[:50],
                        'openingDate': '2000-01-01', 'parent': {'id': cfg.root}})
            for cl in clients:
                te, en = d2.uid(), d2.uid()
                evs = [{'event': d2.uid(), 'program': cfg.program_id, 'programStage': e['stage'], 'orgUnit': ou, 'occurredAt': e['date'],
                        'status': 'COMPLETED', 'dataValues': [{'dataElement': k, 'value': v} for k, v in e['values'].items() if v not in (None, '')]}
                       for e in cl['events']]
                if tracker:
                    tes.append({'trackedEntity': te, 'trackedEntityType': tet, 'orgUnit': ou,
                                'attributes': [{'attribute': k, 'value': v} for k, v in cl['attributes'].items() if v not in (None, '')],
                                'enrollments': [{'enrollment': en, 'program': cfg.program_id, 'orgUnit': ou, 'status': 'ACTIVE',
                                                 'enrolledAt': cl['enrolled'], 'occurredAt': cl['incident'], 'events': evs}]})
                else:
                    events += evs
                truth.append({'te': te if tracker else None, 'enrollment': en if tracker else None, 'ou': ou, 'enrolledAt': cl['enrolled'],
                              'attrs': cl['attributes'], 'witness_for': 'scenario:' + sid,
                              'events': [{'event': e['event'], 'stage': e['programStage'], 'date': e['occurredAt'],
                                          'values': {x['dataElement']: x['value'] for x in e['dataValues']}} for e in evs]})
            many = len(sc.get('expect', [])) > 1
            for ex in sc.get('expect', []):
                pi = common.resolve_pi(self.md, ex.get('item') or ex.get('pi'))
                checks.append({'key': f'{sid}:{common.pi_label(pi)}' if many else sid, 'scenario': sid, 'group': sc.get('group', ''),
                               'item': pi['id'], 'pi': pi['id'], 'code': common.pi_label(pi), 'name': pi.get('name'), 'title': sc.get('title', sid),
                               'note': ex.get('note') or sc.get('note', ''), 'ou': ou, 'pe': ex.get('pe') or sc.get('pe') or cfg.test_period,
                               'persons': len(clients), 'expectBefore': ex.get('before'), 'expectAfter': ex.get('after'),
                               'expectCellsAfter': ex.get('cellsAfter') or {}, 'probe': ex.get('probe')})
        return checks, ({'trackedEntities': tes} if tracker else {'events': events}), truth, {'organisationUnits': ous}, errors


def main():
    ap = config.arg_parser(__doc__.split('\n')[0])
    ap.add_argument('--scenarios', help='scenario file (default <work_dir>/scenarios.json)')
    args = ap.parse_args()
    cfg = config.load(args)
    path = args.scenarios or os.path.join(cfg.work_dir, 'scenarios.json')
    if not os.path.exists(path):
        sys.exit(f'no {path}: write the scenarios first (format: scripts/types/program-indicators/scenarios.example.json)')
    raw = E.jload(path)
    scenarios = raw['scenarios'] if isinstance(raw, dict) else raw
    checks, payload, truth, ous, errors = Builder(cfg).build(scenarios)
    for e in errors:
        print('REFUSED', e)
    if errors:
        sys.exit(f'{len(errors)} scenario(s) refused; nothing written')
    E.jdump(payload, cfg.out('data', 'scen-tracker.json'))
    E.jdump(truth, cfg.out('data', 'scen-truth.json'))
    E.jdump(ous, cfg.out('data', 'scen-ous.json'))
    E.jdump(checks, cfg.out('scenario-checks.json'))
    print(f'{len(scenarios)} scenarios, {len(checks)} checks, {len(truth)} clients, org units {ous["organisationUnits"][0]["id"] if ous["organisationUnits"] else "-"}..')


if __name__ == '__main__':
    main()
