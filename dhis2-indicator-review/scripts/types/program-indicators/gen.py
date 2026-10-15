"""Generate synthetic tracker data for the engine check: one witness person per PI, noise people, out-of-period controls.

    python3 scripts/types/program-indicators/gen.py [--config pireview.json] [--metadata OVERLAY ...] [--pis IDS] [--seed N]
                                    [--noise 400] [--controls 60] [--prefix PREFIX] [--edge-share 0.3]

What is generated (all dates inside cfg.test_period unless a filter asks otherwise):
  * witness: for every PI one person whose values satisfy the PI filter (sat.py), with free expression fields
    filled (so SUM/MAX PIs have values) and ~25% with a second event of the same stage 3 days apart (latest-event
    and distinct-person counting);
  * disaggregation fields are derived from the program's categoryMappings, never named: for each mapped category
    of the PI the generator usually satisfies one random option's filter (record lands in a cell), and sometimes
    (--edge-share) takes values on and around every band edge at cfg.resolution() (numeric fields) or any option code,
    so gaps and overlaps between bands get exercised. A program without categoryMappings works the same, minus this;
  * noise: --noise people with values for every referenced field on the main stage (cfg.main_stage) and, with
    probability 0.4, on each other stage;
  * controls: --controls copies of witnesses shifted 3 months before or after the test period (must not count).

Output (in work_dir, names prefixed with --prefix, e.g. --prefix fix- for witness data of corrected filters):
  tracker.json   payload for POST /api/tracker (trackedEntities with nested enrollments/events; for an event
                 program without registration: {"events": [...]}); import it with import_data.py
  truth.json     the same records as the evaluator reads them (format in references/data-formats.md)
  unsat.json     [[piUid, reason], ...] for filters the generator could not satisfy (read them by hand)
The same --seed gives the same UIDs: after deleting imported data (tracker DELETE is a soft delete), use a new seed.

Config keys: metadata, program, work_dir, test_period, org_units.facilities, main_stage, field_resolution,
optional "seed" (default 42).
"""
import copy, datetime as dt, json, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
import config, piexpr as P, sat  # noqa: E401
import common


def main():
    ap = common.add_common(common.parser(__doc__))
    ap.add_argument('--seed', type=int, help='random seed (default: config "seed" or 42)')
    ap.add_argument('--noise', type=int, default=400, help='number of noise people (default 400)')
    ap.add_argument('--controls', type=int, default=60, help='number of out-of-period controls (default 60)')
    ap.add_argument('--prefix', default='', help='prefix for the output file names')
    ap.add_argument('--edge-share', type=float, default=0.3, help='share of records with band-edge disaggregation values')
    args = ap.parse_args()
    cfg = common.load(args)
    seed = args.seed if args.seed is not None else cfg.seed
    g = Gen(cfg, seed, args.edge_share)
    pis = common.select_pis(cfg, args.items)
    for p in pis:
        g.witness(p)
    for _ in range(args.noise):
        g.noise()
    g.controls(args.controls)
    if g.event_program:
        payload = {'events': [e for t in g.tes for e in t['_events']]}
    else:
        payload = {'trackedEntities': [{k: v for k, v in t.items() if k != '_events'} for t in g.tes]}
    files = {n: common.tagged(cfg, args.prefix + n, args.tag) for n in ('tracker.json', 'truth.json', 'unsat.json')}
    with open(files['tracker.json'], 'w') as f:
        json.dump(payload, f)
    with open(files['truth.json'], 'w') as f:
        json.dump(g.truth, f)
    common.write_json(files['unsat.json'], g.unsat)
    n_ev = sum(len(t['events']) for t in g.truth)
    print(f"{len(g.truth)} people ({len(pis)} witnesses, {args.noise} noise, {args.controls} controls); {n_ev} events; "
          f"{len(g.unsat)} filters the generator could not satisfy; seed {seed}")
    print('disaggregation fields from categoryMappings:', len(g.disagg_leaves),
          '(numeric with band edges:', sum(1 for l in g.disagg_leaves if g.S.edge_values(l)), ')')
    print('->', ', '.join(files.values()))


class Gen:
    def __init__(self, cfg, seed, edge_share):
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.PS, self.PE = cfg.period_bounds()
        self.prog = cfg.program
        self.S = sat.Solver(cfg.md, self.prog, (self.PS, self.PE), rng=self.rng, resolution=cfg.resolution)
        self.edge_share = edge_share
        self.ous = list(cfg.facilities) or [cfg.root]
        self.main = cfg.main_stage
        self.stages = {s['id']: s for s in cfg.md['programStages'] if s['id'] in self.S.stage_des and s['id'] in {x['id'] for x in cfg.program.get('programStages', [])}}
        self.event_program = self.prog.get('programType') == 'WITHOUT_REGISTRATION'
        # disaggregation: parsed option filters per mapping, and the fields they read
        self.map_opts, self.disagg_leaves = {}, []
        for mid, m in self.S.maps.items():
            opts = []
            for om in m.get('optionMappings', []):
                try:
                    opts.append((om['optionId'], P.parse(om['filter'])))
                except (P.ParseError, KeyError, TypeError):
                    continue
                for l in P.leaves(opts[-1][1], dates=False):
                    if l not in self.disagg_leaves:
                        self.disagg_leaves.append(l)
            self.map_opts[mid] = opts
        self.ref_des = [n for n in self.S.lits if n[0] == 'de']
        self.ref_teas = [n for n in self.S.lits if n[0] == 'tea']
        self.tes, self.truth, self.unsat = [], [], []
        self.n_witness = 0

    # ---------------------------------------------------------------- values
    def rand_value(self, leaf):
        ev = self.S.edge_values(leaf)
        if ev and self.rng.random() < 0.7:
            return self.rng.choice(ev)
        c = [x for x in self.S.candidates(leaf) if x is not None]
        return self.rng.choice(c) if c else None

    def fill_disagg(self, asg, mids, keep=None):
        """Give the fields of these mappings values. keep: parsed PI filter that must stay true."""
        mids = [m for m in mids if m in self.map_opts]
        self.rng.shuffle(mids)
        for mid in mids:
            opts = [o for o in self.map_opts[mid]]
            if opts and self.rng.random() >= self.edge_share:
                self.rng.shuffle(opts)
                for _, f in opts[:4]:                      # try to land in one random option
                    trial = sat.Assign(asg)
                    if self.S.satisfy(f, True, trial) and self.S.is_(f, trial, True) and (keep is None or self.S.is_(keep, trial, True)):
                        asg.clear(); asg.update(trial)
                        break
            for _, f in opts:                              # anything still free: edge / random values
                for l in P.leaves(f, dates=False):
                    if l not in asg:
                        trial = sat.Assign(asg)
                        trial[l] = self.rand_value(l)
                        if keep is None or self.S.is_(keep, trial, True):
                            asg[l] = trial[l]

    # ---------------------------------------------------------------- records
    def rand_date(self, lo=None, hi=None):
        lo, hi = lo or self.PS, hi or self.PE
        return lo + dt.timedelta(days=self.rng.randint(0, max(0, (hi - lo).days)))

    def person(self, asg, witness_for, extra_event=False, spread_dates=False):
        """One person with one event per stage that has values in asg (the main stage when none).
        spread_dates: each stage gets its own random date (noise); otherwise all share the event date."""
        S = self.S
        edate = P.to_date(asg.get(('var', 'event_date'))) or self.rand_date()
        stages = {n[1] for n in asg if n[0] == 'de' and n[1] in self.stages and asg[n] is not None}
        if asg.get(('var', 'program_stage_id')) in self.stages:     # filter on V{program_stage_id}
            stages.add(asg[('var', 'program_stage_id')])
        stages = stages or {self.main}
        enrolled = P.to_date(asg.get(('var', 'enrollment_date'))) or self.rand_date(hi=min(edate, self.PE))
        by_stage = []
        for st in sorted(s for s in stages if s):
            vals = {n[2]: S.fmt(n, v) for n, v in asg.items() if n[0] == 'de' and n[1] == st and v is not None}
            date = edate if st == self.main or not spread_dates else self.rand_date()
            by_stage.append((st, date, vals))
            if extra_event and self.stages.get(st, {}).get('repeatable', True):
                d2_ = date - dt.timedelta(days=3) if date > self.PS + dt.timedelta(days=5) else date + dt.timedelta(days=3)
                by_stage.append((st, d2_, dict(vals)))
        attrs = {n[1]: S.fmt(n, v) for n, v in asg.items() if n[0] == 'tea' and v is not None}
        self.emit(by_stage, attrs, enrolled, witness_for)

    def emit(self, by_stage, attrs, enrolled, witness_for):
        S = self.S
        ou = self.rng.choice(self.ous)
        te_uid, en_uid = S.uid(), S.uid()
        evs = [{'event': S.uid(), 'programStage': st, 'program': self.prog['id'], 'orgUnit': ou,
                'occurredAt': date.isoformat(), 'status': 'COMPLETED',
                'dataValues': [{'dataElement': k, 'value': v} for k, v in vals.items() if v is not None]}
               for st, date, vals in by_stage]
        if self.event_program:
            for e in evs:   # one record per event: te/enrollment = event uid so distinct counts work
                self.tes.append({'_events': [e]})
                self.truth.append({'te': e['event'], 'enrollment': e['event'], 'ou': ou, 'enrolledAt': e['occurredAt'],
                                   'attrs': {}, 'events': [self.truth_event(e)], 'witness_for': witness_for})
            return
        self.tes.append({'trackedEntity': te_uid, 'trackedEntityType': self.cfg.tracked_entity_type, 'orgUnit': ou,
                         'attributes': [{'attribute': k, 'value': v} for k, v in attrs.items() if v is not None],
                         'enrollments': [{'enrollment': en_uid, 'program': self.prog['id'], 'orgUnit': ou, 'status': 'ACTIVE',
                                          'enrolledAt': enrolled.isoformat(), 'occurredAt': enrolled.isoformat(), 'events': evs}],
                         '_events': evs})
        self.truth.append({'te': te_uid, 'enrollment': en_uid, 'ou': ou, 'enrolledAt': enrolled.isoformat(), 'attrs': attrs,
                           'events': [self.truth_event(e) for e in evs], 'witness_for': witness_for})

    @staticmethod
    def truth_event(e):
        return {'event': e['event'], 'stage': e['programStage'], 'date': e['occurredAt'],
                'values': {x['dataElement']: x['value'] for x in e['dataValues']}}

    # ---------------------------------------------------------------- the three kinds
    def witness(self, p):
        S = self.S
        self.n_witness += 1
        try:
            f = P.parse((p.get('filter') or '').strip() or 'true')
        except P.ParseError as e:
            self.unsat.append([p['id'], f'parse: {e}'])
            f = None
        asg = S.witness(p.get('filter')) if f is not None else None   # value expression leaves filled below
        if f is not None and asg is None:
            self.unsat.append([p['id'], 'generator could not satisfy the filter'])
        asg = asg or sat.Assign()
        satisfied = f is not None and S.is_(f, asg, True)
        try:                                              # value expressions need values (without breaking the filter)
            for l in P.leaves(P.parse(p.get('expression') or '1')):
                c = [x for x in S.candidates(l) if x is not None]
                if l not in asg and c:
                    trial = sat.Assign(asg); trial[l] = c[0]
                    if not satisfied or S.is_(f, trial, True):
                        asg[l] = c[0]
        except P.ParseError:
            pass
        self.fill_disagg(asg, [m for m in p.get('categoryMappingIds') or [] if m], keep=f if satisfied else None)
        self.person(asg, p['id'], extra_event=self.rng.random() < 0.25)

    def noise(self):
        S = self.S
        asg = sat.Assign()
        stages = {self.main} | {s for s in self.stages if s != self.main and self.rng.random() < 0.4}
        self.fill_disagg(asg, list(self.map_opts))
        for n in self.ref_des + self.ref_teas:            # ~15% of referenced fields left blank
            if n not in asg and (n[0] == 'tea' or n[1] in stages):
                asg[n] = self.rand_value(n) if self.rng.random() < 0.85 else None
        self.person(asg, None, extra_event=self.rng.random() < 0.3, spread_dates=True)

    def controls(self, n):
        src = [t for t in self.truth[:self.n_witness]]
        for i in range(min(n, len(src) * 2) if src else 0):
            s = self.rng.choice(src)
            months = -3 if i % 2 == 0 else 3
            sh = lambda d: P.add_months(P.to_date(d), months)
            by_stage = [(e['stage'], sh(e['date']), dict(e['values'])) for e in s['events']]
            self.emit(by_stage, dict(s['attrs']), sh(s['enrolledAt']), 'control:' + (s['witness_for'] or ''))


if __name__ == '__main__':
    main()
