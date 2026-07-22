import os
import json
import sys
import argparse

# Property names treated as PII and stripped recursively when --anonymize is set.
# Do NOT add keys here that legitimately exist on non-user objects — e.g. `url`
# (documents, externalMapLayers, organisationUnits, routes) or `comment` — the
# strip is recursive over every object and would corrupt them.
PII_FIELDS = {
    "username", "email", "phoneNumber", "whatsApp", "facebookMessenger",
    "skype", "telegram", "twitter", "firstName", "surname", "birthday",
    "nationality", "employer", "education", "gender", "jobTitle",
    "introduction", "languages", "interests", "avatar", "address",
    "lastLogin", "passwordLastUpdated", "password", "openId", "ldapId",
    "twoFA", "twoFactor", "twoFactorSecret", "verifiedEmail",
    "emailVerificationToken", "welcomeMessage", "contactPerson",
    "accountExpiry", "credentials", "secret", "ldap",
    "previousPasswords", "idToken",
}

# Embedded user-reference fields reduced to {id} when --anonymize is set.
USER_REF_FIELDS = {"createdBy", "lastUpdatedBy", "user"}

# Keys removed recursively when --unshare is set.
SHARING_FIELDS = {
    "sharing", "userAccesses", "userGroupAccesses",
    "publicAccess", "externalAccess",
}

# Bookkeeping fields stripped by --minimize even though owner=true/persisted=true.
BOOKKEEPING_FIELDS = {
    "lastUpdated", "created", "lastUpdatedBy", "createdBy",
    "passwordLastUpdated", "lastLogin", "lastSynchronized",
}

# Keys removed recursively when --delocalize is set.
TRANSLATION_FIELDS = {"translations"}


def is_schema_file(data):
    return "schemas" in data and isinstance(data.get("schemas"), list)


def load_schemas(schemas_path):
    if not schemas_path or not os.path.exists(schemas_path):
        return None, None
    with open(schemas_path) as f:
        data = json.load(f)
    if not is_schema_file(data):
        return None, None
    by_plural, by_klass = {}, {}
    for s in data["schemas"]:
        if s.get("plural"):
            by_plural[s["plural"]] = s
        if s.get("klass"):
            by_klass[s["klass"]] = s
    return by_plural, by_klass


def schema_keep_keys(schema):
    """Return (keep_set, info_by_key) — JSON keys kept by --minimize."""
    keep = {"id"}
    info = {}
    for p in schema.get("properties", []):
        if p.get("collection"):
            key = p.get("collectionName") or p.get("fieldName") or p.get("name")
        else:
            # JSON serialization uses `name`; `fieldName` is the Hibernate field name
            # which sometimes differs (e.g. name=programRuleVariableSourceType,
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
        ptype = p.get("propertyType")
        if ptype == "REFERENCE":
            out[k] = reduce_to_id(v)
        elif p.get("collection"):
            item_klass = p.get("itemKlass")
            item_ptype = p.get("itemPropertyType")
            embedded = p.get("embeddedObject")
            sub_schema = by_klass.get(item_klass) if (embedded and by_klass) else None
            if isinstance(v, list):
                if item_ptype == "REFERENCE" and not embedded:
                    out[k] = [reduce_to_id(x) for x in v]
                elif sub_schema is not None:
                    out[k] = [minimize_object(x, sub_schema, by_klass) for x in v]
                else:
                    out[k] = v
            else:
                out[k] = v
        else:
            out[k] = v
    return out


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
                continue
            out[k] = anonymize(v)
        return out
    return value


def anonymize_user(user):
    """Replace PII on user objects with placeholders so the object stays valid."""
    if not isinstance(user, dict):
        return user
    out = anonymize(user)
    uid = out.get("id", "anon")
    out["firstName"] = "User"
    out["surname"] = uid
    out["username"] = f"user_{uid}"
    if "name" in user or "displayName" in user:
        out["name"] = f"User {uid}"
        out["displayName"] = f"User {uid}"
    return out


def redact_org_unit(ou, redact_names, drop_coords):
    """Org units can themselves be sensitive (e.g. school names, GPS points)."""
    if not isinstance(ou, dict):
        return ou
    out = dict(ou)
    if redact_names:
        uid = out.get("id", "anon")
        label = f"OrgUnit {uid}"
        out["name"] = label
        out["shortName"] = label[:50]
        for k in ("displayName", "displayShortName", "description", "comment"):
            out.pop(k, None)
    if drop_coords:
        for k in ("geometry", "coordinates", "featureType"):
            out.pop(k, None)
    return out


def unshare(value):
    if isinstance(value, list):
        return [unshare(v) for v in value]
    if isinstance(value, dict):
        return {k: unshare(v) for k, v in value.items() if k not in SHARING_FIELDS}
    return value


def delocalize(value):
    if isinstance(value, list):
        return [delocalize(v) for v in value]
    if isinstance(value, dict):
        return {k: delocalize(v) for k, v in value.items() if k not in TRANSLATION_FIELDS}
    return value


def transform_metadata(data, by_plural, by_klass,
                       do_anonymize, do_minimize, do_unshare, do_delocalize,
                       redact_ou_names=False, drop_coordinates=False):
    out = {}
    for key, value in data.items():
        if not isinstance(value, list):
            out[key] = value
            continue
        items = value
        if do_minimize and by_plural and key in by_plural:
            schema = by_plural[key]
            items = [minimize_object(it, schema, by_klass) for it in items]
        if do_unshare:
            items = [unshare(it) for it in items]
        if do_delocalize:
            items = [delocalize(it) for it in items]
        if do_anonymize:
            if key == "users":
                items = [anonymize_user(it) for it in items]
            else:
                items = [anonymize(it) for it in items]
        if key == "organisationUnits" and (redact_ou_names or drop_coordinates):
            items = [redact_org_unit(it, redact_ou_names, drop_coordinates)
                     for it in items]
        out[key] = items
    return out


def split_metadata(data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    for key, value in data.items():
        if isinstance(value, list):
            output_file = os.path.join(output_dir, f"{key}.json")
            with open(output_file, "w") as f:
                json.dump(value, f, indent=2)
            print(f"Saved: {output_file}")


def split_schemas(data, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    for schema in data["schemas"]:
        plural = schema.get("plural")
        if plural:
            output_file = os.path.join(output_dir, f"{plural}.json")
            with open(output_file, "w") as f:
                json.dump([schema], f, indent=2)
            print(f"Saved: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Split and transform DHIS2 metadata or schema files."
    )
    parser.add_argument("input_file", help="DHIS2 metadata or schemas JSON file")
    parser.add_argument("--output-dir", default=None,
                        help='Output directory (defaults to "metadata" or "schemas")')
    parser.add_argument("--schemas", default=None,
                        help="Path to schemas.json (used by --minimize). "
                             "Defaults to schemas.json next to the input file.")
    parser.add_argument("--anonymize", action="store_true",
                        help="Strip user/PII fields from all objects")
    parser.add_argument("--minimize", action="store_true",
                        help="Strip non-essential fields using the schema")
    parser.add_argument("--unshare", action="store_true",
                        help="Strip sharing/access fields from all objects")
    parser.add_argument("--delocalize", action="store_true",
                        help="Strip translations from all objects")
    parser.add_argument("--redact-ou-names", action="store_true",
                        help="Replace organisation unit names with 'OrgUnit <id>' "
                             "(facility/school names can themselves be sensitive)")
    parser.add_argument("--drop-coordinates", action="store_true",
                        help="Remove geometry/coordinates from organisation units")
    args = parser.parse_args()

    with open(args.input_file) as f:
        data = json.load(f)

    any_transform = (args.anonymize or args.minimize or args.unshare
                     or args.delocalize or args.redact_ou_names
                     or args.drop_coordinates)

    if is_schema_file(data):
        if any_transform:
            print("Note: transformations are ignored for schema input files.",
                  file=sys.stderr)
        split_schemas(data, args.output_dir or "schemas")
        return

    by_plural, by_klass = None, None
    if args.minimize:
        schemas_path = args.schemas or os.path.join(
            os.path.dirname(os.path.abspath(args.input_file)), "schemas.json"
        )
        by_plural, by_klass = load_schemas(schemas_path)
        if by_plural is None:
            print(f"Warning: --minimize requires schemas; none found at "
                  f"{schemas_path}. Skipping minimize.", file=sys.stderr)

    if any_transform:
        data = transform_metadata(
            data, by_plural, by_klass,
            args.anonymize, args.minimize, args.unshare, args.delocalize,
            redact_ou_names=args.redact_ou_names,
            drop_coordinates=args.drop_coordinates,
        )

    split_metadata(data, args.output_dir or "metadata")


if __name__ == "__main__":
    main()
