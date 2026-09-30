"""Transform a DHIS2 metadata file and write it back as one file or per-type files.

Transformations (all optional, they compose):
    --anonymize        strip PII/credentials, reduce user references to {id},
                       give user objects placeholder names so they stay valid
    --unshare          remove sharing / access fields
    --delocalize       remove translations
    --minimize         keep only schema owner+persisted fields (needs --schemas)
    --redact-ou-names  replace organisation unit names with 'OrgUnit <id>'
    --drop-coordinates remove organisation unit geometry

Output: --split-dir DIR writes DIR/<type>.json per type (default: split/ next
to the input); --out FILE writes a single transformed metadata file.

Examples:
    python transform_metadata.py ./export/metadata.json --schemas ./export/schemas.json \\
        --anonymize --minimize --unshare --delocalize --split-dir ./export/split

    python transform_metadata.py ./export/metadata.json --unshare --out ./clean.json

fetch_metadata.py imports this module and applies the same transformations
to what it downloads, after first excluding the matching fields server-side
(fields=:owner,!sharing,...) so removed data never leaves the server. The
local pass is still needed: server-side exclusion does not reach embedded
objects (e.g. legends inside legendSets) or user references inside collections.
"""
import argparse
import json
import os
import sys

# Property names treated as PII / credential material. Removed recursively by
# --anonymize and excluded server-side by fetch_metadata.py --anonymize.
# Do NOT add keys that legitimately exist on non-user objects — e.g. `url`
# (documents, externalMapLayers, organisationUnits, routes), `comment`, or
# `name` — the strip is recursive over every object and would corrupt them.
PII_FIELDS = {
    "username", "email", "phoneNumber", "whatsApp", "facebookMessenger",
    "skype", "telegram", "twitter", "firstName", "surname", "birthday",
    "nationality", "employer", "education", "gender", "jobTitle",
    "introduction", "languages", "interests", "avatar", "address",
    "lastLogin", "passwordLastUpdated", "password", "openId", "ldapId",
    "twoFA", "twoFactor", "twoFactorSecret", "twoFactorEnabled", "verifiedEmail",
    "emailVerificationToken", "welcomeMessage", "contactPerson",
    "accountExpiry", "credentials", "secret", "ldap",
    "previousPasswords", "idToken",
}

# Keys whose value is a user reference; reduced to {id} by --anonymize.
USER_REF_FIELDS = {"createdBy", "lastUpdatedBy", "user"}

# Server-stamped audit references. fetch_metadata.py excludes them server-side
# by default: they are re-stamped on import and carry the creator's real name
# and username on every object (`{id, code, name, displayName, username}`).
AUDIT_REF_FIELDS = {"createdBy", "lastUpdatedBy"}

SHARING_FIELDS = {
    "sharing", "userAccesses", "userGroupAccesses",
    "publicAccess", "externalAccess",
}

# Stripped by --minimize even though the schema marks them owner+persisted.
BOOKKEEPING_FIELDS = {
    "lastUpdated", "created", "lastUpdatedBy", "createdBy",
    "passwordLastUpdated", "lastLogin", "lastSynchronized",
}

TRANSLATION_FIELDS = {"translations"}

OU_COORDINATE_FIELDS = {"geometry", "coordinates", "featureType"}

# Derived/computed fields that are never owner+persisted on any type. --minimize
# strips them recursively so embedded objects without a schema of their own
# (DataDimensionItem, MapView on some versions) are cleaned too.
NOISE_FIELDS = {"displayName", "displayShortName", "displayFormName",
                "displayDescription", "access", "href", "favorite"}

# Shape of a user reference as the server renders it inside other objects.
USER_REF_SHAPE = {"id", "code", "name", "displayName", "username"}


class Options:
    """Which transformations to apply. Shared by the CLI and fetch_metadata.py."""

    def __init__(self, anonymize=False, minimize=False, unshare=False,
                 delocalize=False, redact_ou_names=False, drop_coordinates=False):
        self.anonymize = anonymize
        self.minimize = minimize
        self.unshare = unshare
        self.delocalize = delocalize
        self.redact_ou_names = redact_ou_names
        self.drop_coordinates = drop_coordinates

    @classmethod
    def from_args(cls, args):
        return cls(anonymize=args.anonymize, minimize=args.minimize,
                   unshare=args.unshare, delocalize=args.delocalize,
                   redact_ou_names=args.redact_ou_names,
                   drop_coordinates=args.drop_coordinates)

    def __bool__(self):
        return any(vars(self).values())

    @staticmethod
    def add_arguments(parser):
        parser.add_argument("--anonymize", action="store_true",
                            help="Strip PII/credential fields, reduce user references "
                                 "to {id}, give user objects placeholder names")
        parser.add_argument("--minimize", action="store_true",
                            help="Keep only schema owner+persisted fields (needs schemas)")
        parser.add_argument("--unshare", action="store_true",
                            help="Strip sharing/access fields")
        parser.add_argument("--delocalize", action="store_true",
                            help="Strip translations")
        parser.add_argument("--redact-ou-names", action="store_true",
                            help="Replace organisation unit names with 'OrgUnit <id>' "
                                 "(facility/school names can themselves be sensitive)")
        parser.add_argument("--drop-coordinates", action="store_true",
                            help="Remove geometry/coordinates from organisation units")


def fields_exclusions(opts, strip_audit_refs=True):
    """`!field` terms for a DHIS2 `fields=` parameter matching these options.

    Appending these to `:owner` makes the server omit the data instead of
    sending it for local removal. Unknown field names are ignored per type,
    so the same list is safe on every endpoint.
    """
    excl = set()
    if strip_audit_refs:
        excl |= AUDIT_REF_FIELDS
    if opts.unshare:
        excl |= SHARING_FIELDS
    if opts.delocalize:
        excl |= TRANSLATION_FIELDS
    if opts.anonymize:
        excl |= PII_FIELDS
    if opts.drop_coordinates:
        excl |= OU_COORDINATE_FIELDS
    return [f"!{f}" for f in sorted(excl)]


# --- schemas -----------------------------------------------------------------

def is_schema_file(data):
    return isinstance(data, dict) and isinstance(data.get("schemas"), list)


def index_schemas(data):
    """(by_plural, by_klass) from a loaded schemas.json, or (None, None)."""
    if not is_schema_file(data):
        return None, None
    by_plural, by_klass = {}, {}
    for s in data["schemas"]:
        if s.get("plural"):
            by_plural[s["plural"]] = s
        if s.get("klass"):
            by_klass[s["klass"]] = s
    return by_plural, by_klass


def load_schemas(schemas_path):
    if not schemas_path or not os.path.exists(schemas_path):
        return None, None
    with open(schemas_path) as f:
        return index_schemas(json.load(f))


def schema_keep_keys(schema):
    """(keep_set, info_by_key): JSON keys kept by --minimize for one schema."""
    keep = {"id"}
    info = {}
    for p in schema.get("properties", []):
        if p.get("collection"):
            key = p.get("collectionName") or p.get("fieldName") or p.get("name")
        else:
            # JSON serialization uses `name`; `fieldName` is the Hibernate field
            # name, which sometimes differs (name=programRuleVariableSourceType,
            # fieldName=sourceType).
            key = p.get("name") or p.get("fieldName")
        if not key:
            continue
        info[key] = p
        if p.get("owner") and p.get("persisted") and key not in BOOKKEEPING_FIELDS:
            keep.add(key)
    return keep, info


def reduce_to_id(value):
    if isinstance(value, dict) and "id" in value:
        return {"id": value["id"]}
    return value


def minimize_object(obj, schema, by_klass):
    if not isinstance(obj, dict) or schema is None:
        return obj
    keep, info = schema_keep_keys(schema)
    out = {}
    for k, v in obj.items():
        if k not in keep:
            continue
        p = info.get(k, {})
        if p.get("propertyType") == "REFERENCE":
            out[k] = reduce_to_id(v)
        elif p.get("collection") and isinstance(v, list):
            embedded = p.get("embeddedObject")
            sub_schema = by_klass.get(p.get("itemKlass")) if (embedded and by_klass) else None
            if p.get("itemPropertyType") == "REFERENCE" and not embedded:
                out[k] = [reduce_to_id(x) for x in v]
            elif sub_schema is not None and schema_keep_keys(sub_schema)[0] - {"id"}:
                out[k] = [minimize_object(x, sub_schema, by_klass) for x in v]
            elif sub_schema is not None:
                # Sub-schema marks nothing owned+persisted (AttributeValue on
                # 2.40/2.41, EventRepetition): minimizing would leave {} per
                # item, so keep the items as they are.
                out[k] = v
            else:
                out[k] = v
        else:
            out[k] = v
    return out


# --- recursive passes ----------------------------------------------------------

def is_user_ref(value):
    """A serialized user reference: {id, code, name, displayName, username}.

    Other references render as {id} only, so a nested dict carrying displayName
    or username within that key set is a user. (username is missing when the
    fetch excluded it server-side, hence the displayName alternative.)
    """
    return (isinstance(value, dict) and "id" in value
            and set(value) <= USER_REF_SHAPE
            and ("username" in value or "displayName" in value))


def anonymize(value):
    if isinstance(value, list):
        return [anonymize(v) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in PII_FIELDS:
                continue
            if k in USER_REF_FIELDS and isinstance(v, dict) and "id" in v:
                out[k] = {"id": v["id"]}
            elif is_user_ref(v):
                # e.g. userGroups.users[] — reference rendered with the user's name
                out[k] = {"id": v["id"]}
            elif isinstance(v, list) and any(is_user_ref(x) for x in v):
                out[k] = [reduce_to_id(x) if is_user_ref(x) else anonymize(x) for x in v]
            else:
                out[k] = anonymize(v)
        return out
    return value


def anonymize_user(user):
    """Replace PII on a top-level user object with placeholders so it stays valid."""
    if not isinstance(user, dict):
        return user
    out = anonymize(user)
    uid = out.get("id", "anon")
    out["firstName"] = "User"
    out["surname"] = uid
    out["username"] = f"user_{uid}"
    for k in ("name", "displayName"):
        if k in user:
            out[k] = f"User {uid}"
    return out


def redact_org_unit(ou, redact_names, drop_coords):
    if not isinstance(ou, dict):
        return ou
    out = dict(ou)
    if redact_names:
        label = f"OrgUnit {out.get('id', 'anon')}"
        out["name"] = label
        out["shortName"] = label[:50]
        for k in ("displayName", "displayShortName", "description", "comment"):
            out.pop(k, None)
    if drop_coords:
        for k in OU_COORDINATE_FIELDS:
            out.pop(k, None)
    return out


def strip_keys(value, keys):
    if isinstance(value, list):
        return [strip_keys(v, keys) for v in value]
    if isinstance(value, dict):
        return {k: strip_keys(v, keys) for k, v in value.items() if k not in keys}
    return value


def transform(data, opts, by_plural=None, by_klass=None):
    """Apply `opts` to a metadata dict ({plural: [objects]}); returns a new dict."""
    if not opts:
        return data
    out = {}
    for key, value in data.items():
        if not isinstance(value, list):
            if opts.anonymize and key == "system" and isinstance(value, dict):
                # server id, revision and export date identify the instance
                value = {k: v for k, v in value.items() if k == "version"}
            out[key] = value
            continue
        items = value
        if opts.minimize and by_plural and key in by_plural:
            items = [minimize_object(it, by_plural[key], by_klass) for it in items]
            items = strip_keys(items, NOISE_FIELDS)
        if opts.unshare:
            items = strip_keys(items, SHARING_FIELDS)
        if opts.delocalize:
            items = strip_keys(items, TRANSLATION_FIELDS)
        if opts.anonymize:
            items = [anonymize_user(it) for it in items] if key == "users" \
                else anonymize(items)
            items = strip_keys(items, {"href"})  # carries the server URL
        if key == "organisationUnits" and (opts.redact_ou_names or opts.drop_coordinates):
            items = [redact_org_unit(it, opts.redact_ou_names, opts.drop_coordinates)
                     for it in items]
        out[key] = items
    return out


# --- output --------------------------------------------------------------------

def write_split(data, out_dir, quiet=False):
    """Write one <plural>.json per type; returns the list of files written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for key, value in data.items():
        if isinstance(value, list):
            path = os.path.join(out_dir, f"{key}.json")
            with open(path, "w") as f:
                json.dump(value, f, indent=2)
            written.append(path)
            if not quiet:
                print(f"Saved: {path} ({len(value)})")
    return written


def write_split_schemas(data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for schema in data["schemas"]:
        if schema.get("plural"):
            path = os.path.join(out_dir, f"{schema['plural']}.json")
            with open(path, "w") as f:
                json.dump([schema], f, indent=2)
    print(f"Saved: {len(data['schemas'])} schema files to {out_dir}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_file", help="DHIS2 metadata (or schemas) JSON file")
    p.add_argument("--schemas", default=None,
                   help="schemas.json for --minimize (default: next to the input file)")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--split-dir", default=None,
                     help="Write <type>.json per type into this directory "
                          "(default: 'split' next to the input file)")
    out.add_argument("--out", default=None, help="Write a single transformed file instead")
    Options.add_arguments(p)
    args = p.parse_args()
    opts = Options.from_args(args)

    with open(args.input_file) as f:
        data = json.load(f)
    in_dir = os.path.dirname(os.path.abspath(args.input_file))

    if is_schema_file(data):
        if opts:
            print("Note: transformations are ignored for schema files.", file=sys.stderr)
        write_split_schemas(data, args.split_dir or os.path.join(in_dir, "schemas"))
        return

    by_plural, by_klass = None, None
    if opts.minimize:
        schemas_path = args.schemas or os.path.join(in_dir, "schemas.json")
        by_plural, by_klass = load_schemas(schemas_path)
        if by_plural is None:
            sys.exit(f"--minimize needs schemas.json; none found at {schemas_path}. "
                     "Fetch it (fetch_metadata.py --schemas) or pass --schemas.")

    data = transform(data, opts, by_plural, by_klass)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(data, f)
        print(f"Saved: {args.out}")
    else:
        write_split(data, args.split_dir or os.path.join(in_dir, "split"))


if __name__ == "__main__":
    main()
