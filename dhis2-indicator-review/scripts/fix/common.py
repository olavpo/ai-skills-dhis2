"""Helpers shared by the fix-pattern modules of every type (types/<type>/patterns.py) and the core fix scripts.

Import it as a package module, never as a bare `common` (every type folder has its own common.py):

    sys.path.append(<scripts dir>)
    from fix.common import FixError, sub_once, ws_pattern, norm, uid_for
"""
import hashlib, re


class FixError(Exception):
    """A pattern does not apply cleanly; the item goes on the "not proposed" list with the message."""


def uid_for(seed):
    """Deterministic DHIS2-style UID (re-running the build gives the same ids)."""
    h = hashlib.sha256(seed.encode()).digest()
    a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    return a[h[0] % 52] + ''.join((a + '0123456789')[b % 62] for b in h[1:11])


def ws_pattern(old):
    """Regex for `old` that ignores whitespace differences."""
    pat = r'\s*'.join(re.escape(c) for c in old if not c.isspace())
    return (r'\s*' + pat) if old[:1].isspace() else pat


def sub_once(text, old, new, what):
    """Whitespace-insensitive literal replace; the pattern must occur exactly once."""
    out, n = re.subn(ws_pattern(old), lambda m: new, text or '')
    if n != 1:
        raise FixError(f'{what}: pattern matched {n} times: {old!r}')
    return out.strip()


def norm(s):
    """Normalised literal for matching: case, spaces and punctuation ignored."""
    return re.sub(r'[^0-9a-z]', '', (s or '').casefold())
