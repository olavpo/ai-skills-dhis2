"""Run configuration shared by every pireview script.

A run is described by one JSON file (default ./pireview.json, or --config / $PIREVIEW_CONFIG).
Paths in it are relative to the config file. Credentials never go in the file: they come from
the environment (DHIS2_URL plus DHIS2_TOKEN, or DHIS2_USER and DHIS2_PASSWORD).

    {
      "title": "Child Programme",            # shown on the pages
      "metadata": "export/metadata.json",     # dependency export of the program (+ dataSets for the dictionary)
      "program": "IpHINAT79UW",               # optional when the export holds one program
      "work_dir": "out",                      # every script reads and writes here
      "test_period": "2026Q1",                # quarter the synthetic data is generated in
      "org_units": {"root": "TstRoot0001", "facilities": ["TstFacA0001", "TstFacB0001"],
                    "scenario_prefix": "TstScn"},
      "main_stage": null,                     # optional; default: the stage most referenced by PIs
      "field_resolution": {"#{stage.de}": 0.001},   # optional; smallest step of a numeric field
      "dhis2_version": "2.43.2",
      "exported": "2026-10-06",              # date of the production export
      "test_instance": true,                  # required before any script writes to the instance
      "seed": 42,                             # optional; random seed of the data generator
      "active_name_pattern": null,            # optional regex: names meaning "status at period end"
      "topics_extra": ["topics-extra.json"],  # optional; program-specific review topics
      "fixes_file": "out/fixes-explicit.json",# optional; explicit fixes and group switches
      "notes": "notes.json"                   # optional; per fix group: impact on other consumers
    }
"""
import argparse, datetime as dt, json, os, sys
from collections import Counter

LIB = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(LIB)


class Config:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.base = os.path.dirname(self.path)
        with open(self.path) as f:
            self.raw = json.load(f)
        r = self.raw
        self.title = r.get('title', 'Program indicators')
        self.metadata_path = self.resolve(r['metadata'])
        self.work_dir = self.resolve(r.get('work_dir', 'out'))
        os.makedirs(self.work_dir, exist_ok=True)
        self.test_period = r.get('test_period', f'{dt.date.today().year}Q1')
        ou = r.get('org_units', {})
        self.root = ou.get('root', 'TstRoot0001')
        self.facilities = ou.get('facilities', ['TstFacA0001', 'TstFacB0001'])
        self.scenario_prefix = ou.get('scenario_prefix', 'TstScn')
        self.field_resolution = r.get('field_resolution', {})
        self.dhis2_version = r.get('dhis2_version', '')
        self.exported = r.get('exported', '')
        self.test_instance = bool(r.get('test_instance', False))
        self.notes_path = self.resolve(r['notes']) if r.get('notes') else None
        self.seed = int(r.get('seed', 42))
        self.active_name_pattern = r.get('active_name_pattern')  # regex for "status at period end" names; None = lint default
        self.topics_extra = [self.resolve(x) for x in (r.get('topics_extra') or [])]
        self.fixes_file = self.resolve(r['fixes_file']) if r.get('fixes_file') else os.path.join(self.work_dir, 'fixes-explicit.json')
        self._md = None
        self._program = r.get('program')
        self._main_stage = r.get('main_stage')

    def resolve(self, p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base, p))

    def out(self, *parts):
        """Path inside the work dir (parent folders created)."""
        p = os.path.join(self.work_dir, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    # --- metadata -----------------------------------------------------------------
    @property
    def md(self):
        if self._md is None:
            with open(self.metadata_path) as f:
                self._md = json.load(f)
            for k in ('dataElements', 'trackedEntityAttributes', 'programStages', 'programIndicators', 'optionSets',
                      'options', 'categories', 'categoryOptions', 'categoryCombos', 'dataSets', 'programs'):
                self._md.setdefault(k, [])
        return self._md

    @property
    def program(self):
        """The program object under review."""
        progs = self.md['programs']
        if self._program:
            for p in progs:
                if p['id'] == self._program:
                    p.setdefault('categoryMappings', [])
                    return p
            sys.exit(f'program {self._program} is not in {self.metadata_path}')
        if len(progs) != 1:
            sys.exit(f'{len(progs)} programs in the export: set "program" in {self.path}')
        progs[0].setdefault('categoryMappings', [])
        return progs[0]

    @property
    def program_id(self):
        return self.program['id']

    @property
    def tracked_entity_type(self):
        t = self.program.get('trackedEntityType')
        return t['id'] if t else None

    @property
    def pis(self):
        """Program indicators of the program under review."""
        pid = self.program_id
        return [p for p in self.md['programIndicators'] if (p.get('program') or {}).get('id', pid) == pid]

    @property
    def main_stage(self):
        if self._main_stage:
            return self._main_stage
        import re
        c = Counter()
        for p in self.pis:
            for t in (p.get('expression') or '', p.get('filter') or ''):
                c.update(re.findall(r'#\{(\w+)\.\w+\}', t))
        stages = {s['id'] for s in self.md['programStages']}
        for s, _ in c.most_common():
            if s in stages:
                return s
        return self.md['programStages'][0]['id'] if self.md['programStages'] else None

    def resolution(self, ref, value_type=None):
        """Smallest step of a numeric field: config, else from the value type."""
        if ref in self.field_resolution:
            return float(self.field_resolution[ref])
        return 1.0 if (value_type or '').startswith('INTEGER') else 0.001

    # --- periods (quarterly test period) ------------------------------------------
    def period_bounds(self, pe=None):
        y, q = quarter(pe or self.test_period)
        start = dt.date(y, 3 * q - 2, 1)
        end = shift_months(start, 3) - dt.timedelta(days=1)
        return start, end

    def require_test_instance(self):
        """Call before writing to the instance. Writes are only allowed on a declared test instance."""
        if not self.test_instance:
            sys.exit(f'refusing to write: set "test_instance": true in {self.path} only if {os.environ.get("DHIS2_URL")} '
                     'is a disposable test instance. Never run the data and fix steps against production.')


def quarter(pe):
    if len(pe) != 6 or pe[4] != 'Q':
        raise ValueError(f'test_period must be a quarter like 2026Q1, got {pe!r}')
    return int(pe[:4]), int(pe[5])


def shift_months(d, n):
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, 1)


def shift_quarter(pe, months):
    """Quarter that is `months` months after `pe` (months a multiple of 3)."""
    y, q = quarter(pe)
    k = (y * 12 + (q - 1) * 3) + months
    return f'{k // 12}Q{(k % 12) // 3 + 1}'


def arg_parser(description, **kw):
    """argparse with the shared --config option."""
    ap = argparse.ArgumentParser(description=description, **kw)
    ap.add_argument('--config', default=os.environ.get('PIREVIEW_CONFIG', 'pireview.json'),
                    help='run configuration (default ./pireview.json or $PIREVIEW_CONFIG)')
    return ap


def load(args=None):
    path = getattr(args, 'config', None) or os.environ.get('PIREVIEW_CONFIG', 'pireview.json')
    if not os.path.exists(path):
        sys.exit(f'no run configuration at {path}: copy scripts/pireview.example.json and edit it')
    return Config(path)
