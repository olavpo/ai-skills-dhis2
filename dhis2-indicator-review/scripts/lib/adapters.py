"""Type adapters: everything the core needs to know about one metadata type lives in scripts/types/<type>/.

    import adapters
    A = adapters.load(cfg)                      # the adapter module of cfg.type (types/<type>/adapter.py)
    path = adapters.type_script(cfg, 'gen.py')  # a type script, or exit 2 when the type has none
    adapters.out_name(A, 'review.html')         # 'pi-review.html': output file names carry the type's PREFIX

The adapter interface (constants and functions) is described in references/data-formats.md ("Type adapters") and
implemented for program indicators in types/program-indicators/adapter.py. The type folder is put first on sys.path,
so an adapter imports its sibling modules by name (including its own `common`; core modules never import a bare
`common`, and fix/common.py is imported as `fix.common`).
"""
import importlib.util, os, sys

LIB = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(LIB)
TYPES = os.path.join(SCRIPTS, 'types')
_CACHE = {}


def known_types():
    return sorted(d for d in os.listdir(TYPES) if os.path.exists(os.path.join(TYPES, d, 'adapter.py')))


def type_dir(typ):
    d = os.path.join(TYPES, typ)
    if not os.path.exists(os.path.join(d, 'adapter.py')):
        sys.exit(f'unknown type {typ!r} (config "type"); known: {", ".join(known_types())}')
    return d


def load_type(typ):
    if typ not in _CACHE:
        d = type_dir(typ)
        if d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
        spec = importlib.util.spec_from_file_location('adapter_' + typ.replace('-', '_'), os.path.join(d, 'adapter.py'))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _CACHE[typ] = mod
    return _CACHE[typ]


def load(cfg):
    """The adapter module for a Config (or a type name)."""
    return load_type(cfg if isinstance(cfg, str) else cfg.type)


def config_class(typ):
    import config
    return getattr(load_type(typ), 'CONFIG', config.Config)


def type_script(cfg, name, required=True):
    """Path of scripts/types/<type>/<name>; exits 2 with a clear message when the type doesn't have it."""
    typ = cfg if isinstance(cfg, str) else cfg.type
    p = os.path.join(type_dir(typ), name)
    if not os.path.exists(p):
        if not required:
            return None
        print(f'type {typ!r} has no {name}: this step is not supported for {typ}', file=sys.stderr)
        sys.exit(2)
    return p


def need(A, name):
    """An optional adapter function; exits 2 when the type doesn't provide it."""
    fn = getattr(A, name, None)
    if fn is None:
        print(f'type {A.TYPE!r} does not support this step (its adapter has no {name}())', file=sys.stderr)
        sys.exit(2)
    return fn


def out_name(A, name):
    """Output file name with the type prefix: 'review.html' -> 'pi-review.html', 'VALIDATION.md' -> 'PI-VALIDATION.md'."""
    p = A.PREFIX.upper() if name[:1].isupper() else A.PREFIX
    return f'{p}-{name}'


def labels(A):
    return dict(A.LABELS)
