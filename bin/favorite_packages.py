"""User-owned UPM favorites in <library>/.data/package_favorites.json.

Independent of indexed asset stars; rescanning never changes these entries.
Callers hold the shared state-write lock when mutating.
"""
import json
import os
import re
from urllib.parse import urlsplit

import index_assets as ia

STORE_NAME = "package_favorites.json"


class StoreError(Exception):
    pass


def normalize_package(package):
    if not isinstance(package, dict) or set(package) != {"id", "label", "kind", "source", "version"}:
        raise ValueError("Each saved package requires id, label, kind, source and version")
    if any(not isinstance(value, str) or any(ord(c) < 32 for c in value)
           for value in package.values()):
        raise ValueError("Package fields must be text without control characters")
    result = {key: value.strip() for key, value in package.items()}
    if not result["id"] or len(result["id"]) > 128 or not result["label"]:
        raise ValueError("A package id and label are required")
    source, version = result["source"], result["version"]
    if result["kind"] == "registry":
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", source) or "." not in source:
            raise ValueError("Use a registry package name such as com.unity.mathematics")
        if version and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.+-]+)?", version):
            raise ValueError("Use an exact package version such as 1.3.2, or leave it empty")
    elif result["kind"] == "git":
        url = urlsplit(source)
        scp = re.match(r"[^/@\s]+@[^/:\s]+:.+", source)
        if (not source or any(c.isspace() for c in source)
                or not (scp or (url.scheme.removeprefix("git+") in ("https", "http", "ssh", "git", "file")
                               and (url.netloc or url.scheme == "file") and url.path))):
            raise ValueError("Use a Git URL accepted by Unity Package Manager")
        if version and ("#" in source or any(c.isspace() for c in version) or "#" in version):
            raise ValueError("Specify the Git ref in the URL or the ref field, not both")
    else:
        raise ValueError("Package kind must be registry or git")
    return result


def load(state):
    try:
        with open(os.path.join(state, STORE_NAME), encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {"packages": []}
    except (OSError, ValueError) as exc:
        raise StoreError(f"Saved packages are unreadable: {exc}") from exc
    try:
        if not isinstance(data, dict) or set(data) != {"packages"} or not isinstance(data["packages"], list):
            raise ValueError("Invalid saved-package store")
        packages = [normalize_package(item) for item in data["packages"]]
        if packages != data["packages"] or len({p["id"] for p in packages}) != len(packages):
            raise ValueError("Invalid or duplicate saved-package entries")
    except ValueError as exc:
        raise StoreError(f"Saved packages are malformed: {exc}") from exc
    return data


def mutate(state, action, package=None, package_id=None, ids=None):
    data = load(state)
    packages = data["packages"]
    if action == "save":
        package = normalize_package(package)
        identity = (package["kind"], package["source"], package["version"])
        if any(p["id"] != package["id"] and (p["kind"], p["source"], p["version"]) == identity for p in packages):
            raise ValueError("This package is already saved")
        existing = next((i for i, p in enumerate(packages) if p["id"] == package["id"]), None)
        if existing is None:
            packages.append(package)
        else:
            packages[existing] = package
    elif action == "remove":
        if not isinstance(package_id, str) or not package_id.strip():
            raise ValueError("A saved-package id is required")
        data["packages"] = [p for p in packages if p["id"] != package_id]
    elif action == "reorder":
        if (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
                or len(ids) != len(packages) or len(set(ids)) != len(ids)
                or set(ids) != {p["id"] for p in packages}):
            raise ValueError("Package order must include every saved package exactly once. Reload and try again.")
        by_id = {p["id"]: p for p in packages}
        data["packages"] = [by_id[item] for item in ids]
    else:
        raise ValueError("Unknown saved-package action")
    ia.write_atomic(os.path.join(state, STORE_NAME), json.dumps(data, indent=1))
    return data
