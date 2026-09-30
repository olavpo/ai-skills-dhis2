"""Deterministic DHIS2 metadata building blocks for a prototype generator.

Every UID is derived from a key, so re-running the generator produces identical
metadata, re-imports update in place, and other scripts (loaders, flow
generators, verification) can refer to any object as uid("de:C_STATUS")
without lookups.

    from d2gen import Gen, RADIO
    g = Gen(prefix="DEMO", salt="demo-poc")          # salt keeps UIDs distinct per project
    g.option_set("SEX", "Sex", [("M", "Male"), ("F", "Female")])
    g.data_element("C_SEX", "Child sex", "TEXT", option_set_code="SEX")
    g.write("metadata/01_base.json")

Codes get the prefix (DEMO_C_SEX); names do not, because names show in Capture
lists, headers and videos. Import the result with
POST /api/metadata?importStrategy=CREATE_AND_UPDATE&atomicMode=ALL&identifier=UID.
Metadata import never deletes: delete objects the generator stopped emitting.
"""
import hashlib
import json
import os

ALNUM = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
LETTERS = ALNUM[10:]

# public sharing strings: metadata read-write + data read-write / metadata read-write only
PUBLIC_RW_DATA = "rwrw----"
PUBLIC_RW_META = "rw------"


def render(mobile=None, desktop=None):
    r = {}
    if mobile:
        r["MOBILE"] = {"type": mobile}
    if desktop:
        r["DESKTOP"] = {"type": desktop}
    return r


RADIO = render("VERTICAL_RADIOBUTTONS", "VERTICAL_RADIOBUTTONS")
HRADIO = render("HORIZONTAL_RADIOBUTTONS", "HORIZONTAL_RADIOBUTTONS")
DROPDOWN = render("DEFAULT", "DEFAULT")


def sharing(public=PUBLIC_RW_META):
    return {"public": public, "external": False, "users": {}, "userGroups": {}}


class Gen:
    def __init__(self, prefix, salt=None):
        self.prefix = prefix
        self.salt = (salt or prefix.lower()) + ":"
        self.objs = {}

    # ------------------------------------------------------------ ids
    def uid(self, key):
        """11-character DHIS2 UID (letter first) from a stable key."""
        n = int.from_bytes(hashlib.sha256((self.salt + key).encode()).digest(), "big")
        out = [LETTERS[n % 52]]
        n //= 52
        for _ in range(10):
            out.append(ALNUM[n % 62])
            n //= 62
        return "".join(out)

    def ref(self, key):
        return {"id": self.uid(key)}

    def code(self, code):
        return f"{self.prefix}_{code}"

    # ------------------------------------------------------------ bundle
    def add(self, typ, obj):
        """Add or replace an object of a metadata type (later wins)."""
        self.objs.setdefault(typ, {})[obj["id"]] = obj
        return obj

    def get(self, typ, key):
        return self.objs[typ][self.uid(key)]

    def to_json(self):
        return {t: list(v.values()) for t, v in sorted(self.objs.items())}

    def write(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_json(), f, indent=1, ensure_ascii=False)
        return path

    def clear(self):
        self.objs = {}

    # ------------------------------------------------------------ builders
    def option_set(self, code, name, options, value_type="TEXT"):
        """options: list of (code, name). Option codes stay unprefixed (they are data)."""
        opts = []
        for i, (oc, on) in enumerate(options):
            o = self.add("options", {"id": self.uid(f"opt:{code}:{oc}"), "code": oc, "name": on,
                                     "sortOrder": i + 1, "optionSet": self.ref(f"os:{code}")})
            opts.append({"id": o["id"]})
        self.add("optionSets", {"id": self.uid(f"os:{code}"), "code": self.code(code), "name": name,
                                "valueType": value_type, "options": opts, "sharing": sharing()})
        return self.uid(f"os:{code}")

    def data_element(self, code, name, value_type, short=None, form=None, option_set_code=None,
                     description=None, agg="NONE", domain="TRACKER"):
        de = {"id": self.uid(f"de:{code}"), "code": self.code(code), "name": name,
              "shortName": (short or name)[:50], "formName": form or name,
              "valueType": value_type, "domainType": domain, "aggregationType": agg,
              "zeroIsSignificant": value_type.startswith("INTEGER"), "sharing": sharing()}
        if option_set_code:
            de["optionSet"] = self.ref(f"os:{option_set_code}")
        if description:
            de["description"] = description
        self.add("dataElements", de)
        return de["id"]

    def attribute(self, code, name, value_type, short=None, form=None, option_set_code=None,
                  unique=False, generated=False, pattern=None, description=None, inherit=False):
        """pattern: text pattern for generated values. ORG_UNIT_CODE(...) takes one character
        per dot: ORG_UNIT_CODE(...) is the first three characters only."""
        tea = {"id": self.uid(f"tea:{code}"), "code": self.code(code), "name": name,
               "shortName": (short or name)[:50], "formName": form or name,
               "valueType": value_type, "aggregationType": "NONE", "unique": unique,
               "generated": generated, "orgunitScope": False, "inherit": inherit,
               "confidential": False, "sharing": sharing()}
        if option_set_code:
            tea["optionSet"] = self.ref(f"os:{option_set_code}")
        if pattern:
            tea["pattern"] = pattern
        if description:
            tea["description"] = description
        self.add("trackedEntityAttributes", tea)
        return tea["id"]


if __name__ == "__main__":
    g = Gen("DEMO")
    assert g.uid("de:X") == Gen("DEMO").uid("de:X"), "UIDs must be deterministic"
    assert len(g.uid("de:X")) == 11 and g.uid("de:X")[0].isalpha()
    g.option_set("SEX", "Sex", [("M", "Male"), ("F", "Female")])
    g.data_element("C_SEX", "Child sex", "TEXT", option_set_code="SEX")
    print(json.dumps(g.to_json(), indent=1)[:400])
