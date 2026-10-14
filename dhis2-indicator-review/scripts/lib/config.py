"""Run configuration shared by every script (core and type adapters).

A run is described by one JSON file (default ./pireview.json, or --config / $PIREVIEW_CONFIG; the variable keeps its
historical name for every type). Paths in it are relative to the config file. Credentials never go in the file: they
come from the environment (DHIS2_URL plus DHIS2_TOKEN, or DHIS2_USER and DHIS2_PASSWORD).

    {
      "type": "program-indicators",           # metadata type under review = a folder in scripts/types/ (the default)
      "title": "Child Programme",            # shown on the pages
      "metadata": "export/metadata.json",     # the export the review works on
      "work_dir": "out",                      # every script reads and writes here
      "test_period": "2026Q1",                # quarter the synthetic data is generated in
      "org_units": {"root": "TstRoot0001", "facilities": ["TstFacA0001", "TstFacB0001"],
                    "scenario_prefix": "TstScn"},
      "dhis2_version": "2.43.2",
      "exported": "2026-10-06",              # date of the production export
      "test_instance": true,                  # required before any script writes to the instance
      "seed": 42,                             # optional; random seed of the data generator
      "topics_extra": ["topics-extra.json"],  # optional; scope-specific review topics
      "fixes_file": "out/fixes-explicit.json",# optional; explicit fixes and group switches
      "notes": "notes.json"                   # optional; per fix group: impact on other consumers
    }

Type-specific keys (e.g. "program", "main_stage", "field_resolution" for program indicators) are documented in the
type's adapter (scripts/types/<type>/adapter.py). A type may extend this class (adapter.CONFIG): load() returns an
instance of it, so type scripts can use type-specific properties (e.g. cfg.program).
"""
import argparse, datetime as dt, json, os, sys

LIB = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(LIB)
DEFAULT_TYPE = 'program-indicators'


class Config:
    MD_KEYS = ('dataElements', 'optionSets', 'options', 'categories', 'categoryOptions', 'categoryCombos', 'dataSets')
    DEFAULT_TITLE = 'Indicators'

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.base = os.path.dirname(self.path)
        with open(self.path) as f:
            self.raw = json.load(f)
        r = self.raw
        self.type = r.get('type') or DEFAULT_TYPE
        self.title = r.get('title') or self.DEFAULT_TITLE
        self.metadata_path = self.resolve(r['metadata'])
        self.work_dir = self.resolve(r.get('work_dir', 'out'))
        os.makedirs(self.work_dir, exist_ok=True)
        self.test_period = r.get('test_period', f'{dt.date.today().year}Q1')
        ou = r.get('org_units', {})
        self.root = ou.get('root', 'TstRoot0001')
        self.facilities = ou.get('facilities', ['TstFacA0001', 'TstFacB0001'])
        self.scenario_prefix = ou.get('scenario_prefix', 'TstScn')
        self.dhis2_version = r.get('dhis2_version', '')
        self.exported = r.get('exported', '')
        self.test_instance = bool(r.get('test_instance', False))
        self.notes_path = self.resolve(r['notes']) if r.get('notes') else None
        self.seed = int(r.get('seed', 42))
        self.topics_extra = [self.resolve(x) for x in (r.get('topics_extra') or [])]
        self.fixes_file = self.resolve(r['fixes_file']) if r.get('fixes_file') else os.path.join(self.work_dir, 'fixes-explicit.json')
        self._md = None

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
            for k in self.MD_KEYS:
                self._md.setdefault(k, [])
        return self._md

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


def config_type(path):
    with open(path) as f:
        return json.load(f).get('type') or DEFAULT_TYPE


def load(args=None):
    """The run configuration, as an instance of its type's class (adapter.CONFIG) when the type defines one."""
    path = getattr(args, 'config', None) or os.environ.get('PIREVIEW_CONFIG', 'pireview.json')
    if not os.path.exists(path):
        sys.exit(f'no run configuration at {path}: copy scripts/pireview.example.json and edit it')
    import adapters  # noqa: E402  (same folder; imported here to keep config importable on its own)
    return adapters.config_class(config_type(path))(path)
