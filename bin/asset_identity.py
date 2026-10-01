"""Persistent library-local package identities without reading archive contents.

Initial families use Unicode-normalized titles, folder scope, discriminators and
pipeline. Category and version-only/product-version folders are not identity.
After discovery, keys survive local moves/renames through stat identities and
are retained for temporarily absent packages. File identities are local hints,
not portable content hashes: a move recreated by another device cannot prove
identity when both its path and title changed.
"""

import hashlib
import json
import os
import unicodedata


def family_hint(parsed):
    # Lazy import: index_assets uses this module while building the index.
    from index_assets import parse, normalize_title, FOLDER_CATEGORY
    title = normalize_title(parsed["title"])
    folders = os.path.dirname(parsed["rel_path"]).split(os.sep)
    while folders and folders[-1]:
        folder = parse(folders[-1] + ".unitypackage")
        folder_title = normalize_title(folder["title"])
        if folder.get("version") and folder_title in ("", title):
            folders.pop()
        else:
            break
    if folders and folders[0] in FOLDER_CATEGORY:
        folders.pop(0)
    scope = unicodedata.normalize("NFC", os.sep.join(folders)).casefold()
    return json.dumps([scope, title, parsed["discriminators"], parsed.get("pipeline")],
                      ensure_ascii=False, separators=(",", ":"))


def initial_key(parsed):
    return "asset-" + hashlib.sha256(family_hint(parsed).encode("utf-8")).hexdigest()


def load(state):
    path = os.path.join(state, "identities.json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except FileNotFoundError:
        return {"version": 1, "assets": {}}
    except (OSError, ValueError) as exc:
        raise ValueError("package identity registry is unreadable; refusing to rebuild") from exc
    if (not isinstance(value, dict) or value.get("version") != 1
            or not isinstance(value.get("assets"), dict)):
        raise ValueError("package identity registry is malformed; refusing to rebuild")
    for key, row in value["assets"].items():
        if (not isinstance(key, str) or not key or not isinstance(row, dict)
                or not isinstance(row.get("hints"), list)
                or not all(isinstance(hint, str) for hint in row["hints"])
                or not isinstance(row.get("files"), list)):
            raise ValueError("package identity registry contains an invalid family")
        for file in row["files"]:
            if (not isinstance(file, dict) or not isinstance(file.get("file"), str)
                    or not isinstance(file.get("size_bytes"), int)
                    or (file.get("file_identity") is not None and
                        (not isinstance(file["file_identity"], list)
                         or len(file["file_identity"]) != 3
                         or not all(isinstance(part, int) for part in file["file_identity"])) )):
                raise ValueError("package identity registry contains an invalid archive")
    return value


def assign(records, registry):
    """Return {file: key}; never merge two established identities on a title hint."""
    from index_assets import parse
    by_path, by_file, by_hint = {}, {}, {}
    for key, family in registry["assets"].items():
        for hint in family["hints"]:
            by_hint.setdefault(hint, set()).add(key)
        for file in family["files"]:
            parsed = parse(file["file"])
            variant = (tuple(parsed["discriminators"]), parsed.get("pipeline"))
            by_path.setdefault((file["file"], variant), set()).add(key)
            identity = file.get("file_identity")
            if identity:
                by_file.setdefault((tuple(identity), file["size_bytes"], variant), set()).add(key)
    buckets = {}
    for record in records:
        hint = family_hint(record)
        variant = (tuple(record["discriminators"]), record.get("pipeline"))
        matches = set()
        identity = record.get("file_identity")
        if identity:
            matches = by_file.get((tuple(identity), record["size"], variant), set())
        # In-place replacements retain product identity. A reused path belonging
        # to several historical families is not enough to select one.
        if len(matches) != 1:
            matches = by_path.get((record["rel_path"], variant), set())
        if len(matches) != 1:
            matches = by_hint.get(hint, set())
        key = next(iter(matches)) if len(matches) == 1 else None
        buckets.setdefault(hint, []).append((record, key))
    assignments = {}
    for hint, members in sorted(buckets.items()):
        known = {key for _, key in members if key is not None}
        for record, key in members:
            if key is None:
                if len(known) == 1:
                    key = next(iter(known))
                else:
                    key = initial_key(record)
                    if len(known) > 1 or key in registry["assets"]:
                        # An ambiguous historical hint must not alias an old family.
                        key = "asset-" + hashlib.sha256(
                            (hint + "\0" + record["rel_path"]).encode("utf-8")).hexdigest()
            assignments[record["rel_path"]] = key
    return assignments


def snapshot(data, registry):
    assets = {key: {"hints": list(row["hints"]), "files": list(row["files"])}
              for key, row in registry["assets"].items()}
    from index_assets import parse
    for asset in data["assets"]:
        row = assets.setdefault(asset["asset_key"], {"hints": [], "files": []})
        hints = set(row["hints"])
        # Retain historical paths so removed/reintroduced versions keep ownership.
        files = {file["file"]: file for file in row["files"]}
        for version in asset["versions"]:
            hints.add(family_hint(parse(version["file"])))
            files[version["file"]] = {key: version.get(key)
                                      for key in ("file", "size_bytes", "file_identity")}
        row["hints"] = sorted(hints)
        row["files"] = [files[path] for path in sorted(files)]
    return {"version": 1, "assets": assets}


def rekeys(previous, data):
    """Migrate legacy path-owned annotations; stable keys already own modern data."""
    if previous.get("identity_version") == 1:
        return {}
    by_path = {version["file"]: asset["asset_key"]
               for asset in data["assets"] for version in asset["versions"]}
    result = {}
    for asset in previous.get("assets", []):
        targets = sorted({by_path[version["file"]] for version in asset["versions"]
                          if version["file"] in by_path})
        if targets and targets != [asset["asset_key"]]:
            result[asset["asset_key"]] = targets
    return result


def migrate_keyed(mapping, changes, shared=False):
    result = dict(mapping)
    for old, targets in changes.items():
        if old not in mapping:
            continue
        value = result.pop(old, None)
        if value is None:
            continue
        if shared:
            for target in targets:
                result[target] = sorted(set(result.get(target, [])) | set(value))
        elif len(targets) == 1:
            result.setdefault(targets[0], value)
        # Cache/override ownership is unknowable after splitting an old group.
        # Original snapshots are retained by the journaled migration backup.
    return result
