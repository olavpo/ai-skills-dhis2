"""Helpers shared by the aggregate-indicator type scripts (types/indicators/*.py).

Shared options (add_common(ap)):
  --metadata FILE [FILE ...]  overlays applied on top of the export named in the config, in order: objects replaced or
                              added by id, e.g. {"indicators": [...], "dataElements": [...]} (a proposed payload such
                              as proposed-indicators.json / proposed-dataElements.json, or a full export)
  --items IDS (alias --pis)   restrict to these indicators: a JSON file with a list of uids (or {"ids": [...]}), or a
                              comma-separated list of uids/codes
  --tag TAG                   suffix for output files: analytics-<tag>.json, compare-<tag>.json, syntax-<tag>.json

Scope (config key "indicators"): a list of indicator uids/codes, or the uid of an indicator group in the export
(indicatorGroups), or absent = every indicator in the export.

The core CLI helpers (lib/cli.py) are re-exported here; this module adds the aggregate overlay, the scope and Meta.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, '..', '..', 'lib'))
if LIB not in sys.path:
    sys.path.insert(0, LIB)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import config  # noqa: E402,F401
from cli import parser, add_common, load, select, tagged, read_json, write_json  # noqa: E402,F401

NUMERIC = {'NUMBER', 'INTEGER', 'INTEGER_POSITIVE', 'INTEGER_NEGATIVE', 'INTEGER_ZERO_OR_POSITIVE', 'PERCENTAGE',
           'UNIT_INTERVAL', 'BOOLEAN', 'TRUE_ONLY'}
# period types, shortest first
PERIOD_ORDER = ['Daily', 'Weekly', 'WeeklyWednesday', 'WeeklyThursday', 'WeeklySaturday', 'WeeklySunday', 'BiWeekly',
                'Monthly', 'BiMonthly', 'Quarterly', 'QuarterlyNov', 'SixMonthly', 'SixMonthlyApril', 'SixMonthlyNov',
                'Yearly', 'FinancialApril', 'FinancialJuly', 'FinancialOct', 'FinancialNov']


def apply_overlay(cfg, ov, path):
    """One --metadata overlay (parsed JSON) applied to cfg.md (called through adapter.apply_overlay). Objects are
    replaced or added by id; a partial object (e.g. a data element with only id + aggregationType) is merged."""
    if isinstance(ov, list):
        sys.exit(f'{path}: JSON Patch overlays are for program indicators; aggregate overlays are {{"<type>": [objects]}}')
    md = cfg.md
    for key, objs in ov.items():
        if not isinstance(objs, list) or not all(isinstance(o, dict) for o in objs):
            continue
        cur = md.setdefault(key, [])
        idx = {o.get('id'): i for i, o in enumerate(cur)}
        for o in objs:
            if o.get('id') in idx:
                merged = dict(cur[idx[o['id']]])
                merged.update(o)
                cur[idx[o['id']]] = merged
            else:
                cur.append(o)
    cfg._meta = None


def scope(cfg):
    """Indicators under review (config "indicators": list of uids/codes, an indicator group uid, or all)."""
    inds = cfg.md['indicators']
    want = cfg.raw.get('indicators')
    if not want:
        return inds
    if isinstance(want, str):
        g = [x for x in cfg.md['indicatorGroups'] if x['id'] == want]
        if not g:
            sys.exit(f'"indicators": {want!r} is not an indicator group in the export: give a list of uids/codes')
        want = [x['id'] for x in g[0].get('indicators', [])]
    want = set(want)
    return [i for i in inds if i['id'] in want or i.get('code') in want]


# ------------------------------------------------------------------------------ metadata view
class Meta:
    """Lookups over an aggregate export."""

    def __init__(self, md):
        self.md = md
        by = lambda k: {o['id']: o for o in md.get(k, [])}
        self.ind, self.it, self.de, self.ds = by('indicators'), by('indicatorTypes'), by('dataElements'), by('dataSets')
        self.cc, self.cat, self.co, self.coc = by('categoryCombos'), by('categories'), by('categoryOptions'), by('categoryOptionCombos')
        self.const, self.oug, self.ig = by('constants'), by('organisationUnitGroups'), by('indicatorGroups')
        self.cc_cocs = {}
        for c in md.get('categoryOptionCombos', []):
            self.cc_cocs.setdefault((c.get('categoryCombo') or {}).get('id'), []).append(c['id'])
        self.de_ds = {}
        for ds in md.get('dataSets', []):
            for e in ds.get('dataSetElements', []):
                self.de_ds.setdefault(e['dataElement']['id'], []).append(ds['id'])
        self.default_cc = next((c['id'] for c in md.get('categoryCombos', []) if c.get('name') == 'default'), None)
        self.default_coc = next((c['id'] for c in md.get('categoryOptionCombos', []) if c.get('name') == 'default'), None)

    def name(self, kind, uid):
        o = getattr(self, kind).get(uid)
        return (o.get('displayFormName') or o.get('formName') or o.get('name')) if o else uid

    def de_combo(self, de, ds=None):
        """Category combo the DE's values are stored in (the data set's override when the data set has one)."""
        if ds:
            for e in self.ds.get(ds, {}).get('dataSetElements', []):
                if e['dataElement']['id'] == de and e.get('categoryCombo'):
                    return e['categoryCombo']['id']
        return ((self.de.get(de) or {}).get('categoryCombo') or {}).get('id') or self.default_cc

    def de_combos(self, de):
        """Every combo a DE is captured with: its own and any data-set override."""
        out = [self.de_combo(de)]
        for ds in self.de_ds.get(de, []):
            c = self.de_combo(de, ds)
            if c not in out:
                out.append(c)
        return out

    def de_cocs(self, de):
        """COCs of the DE's combos (as exported: the COC's own categoryCombo reference)."""
        out = []
        for c in self.de_combos(de):
            out += [x for x in self.cc_cocs.get(c, []) if x not in out]
        return out

    def coc_options(self, coc):
        return frozenset(o['id'] for o in (self.coc.get(coc) or {}).get('categoryOptions', []))

    def combo_option_sets(self, cc):
        return [{o['id'] for o in self.cat.get(c['id'], {}).get('categoryOptions', [])} for c in self.cc.get(cc, {}).get('categories', [])]

    def coc_fits_combo(self, coc, cc):
        """Do the COC's options form one option per category of the combo?"""
        opts = self.coc_options(coc)
        sets = self.combo_option_sets(cc)
        if not sets:
            return False
        return len(opts) == len(sets) and all(len(opts & s) == 1 for s in sets)

    def ds_period_type(self, ds):
        return self.ds.get(ds, {}).get('periodType') or 'Monthly'

    def de_period_type(self, de):
        """Period type data is entered in: the shortest of the DE's data sets (Monthly when in none)."""
        pts = [self.ds_period_type(d) for d in self.de_ds.get(de, [])]
        pts = [p for p in pts if p in PERIOD_ORDER] or ['Monthly']
        return min(pts, key=PERIOD_ORDER.index)

    def de_dataset(self, de):
        """The data set that defines the DE's period type (and attribute combo)."""
        ds = self.de_ds.get(de, [])
        if not ds:
            return None
        pt = self.de_period_type(de)
        return next(d for d in ds if self.ds_period_type(d) == pt)

    def ds_aocs(self, ds):
        cc = ((self.ds.get(ds) or {}).get('categoryCombo') or {}).get('id') or self.default_cc
        return self.cc_cocs.get(cc) or ([self.default_coc] if self.default_coc else [])

    def factor(self, ind):
        it = self.it.get((ind.get('indicatorType') or {}).get('id')) or {}
        f = it.get('factor')
        return float(f) if f is not None else None


REF_RX = re.compile(r'#\{([A-Za-z]\w{10})(?:\.([A-Za-z]\w{10}|\*))?(?:\.([A-Za-z]\w{10}|\*))?\}')
