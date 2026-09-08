#!/usr/bin/env python3
"""Persistent user-owned tag store.

Tags are user data, not derived index metadata: a rescan must never rewrite
them and enrichment must never override them. They live in their own file
(state/user_tags.json) shaped exactly like the desktop bridge contract:

    {"tags": [...], "assignments": {asset_key: [tag, ...]}}

Every tag is normalized (trimmed, lowercased) at every boundary. A corrupt
store is an error, never silently replaced — same fail-closed rule as
favorites. asset_key identity means tags follow an asset across versions and
survive the asset being temporarily absent from the vault.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_ia = None


def _index_assets():
    """Lazy: index_assets imports this module at its own top level."""
    global _ia
    if _ia is None:
        import index_assets
        _ia = index_assets
    return _ia

STORE_NAME = "user_tags.json"
ACTIONS = ("create", "rename", "delete", "assign", "remove")


class TagStoreError(Exception):
    """The tag store is unreadable or malformed; fail closed."""


def normalize(tag):
    """Trim + lowercase. Anything that normalizes to nothing is invalid."""
    if not isinstance(tag, str):
        raise TagStoreError("tag must be a string")
    norm = tag.strip().lower()
    if not norm:
        raise TagStoreError("tag must not be empty")
    return norm


def store_path(state_dir):
    return os.path.join(state_dir, STORE_NAME)


def _validate(data):
    if (not isinstance(data, dict) or set(data) != {"tags", "assignments"}
            or not isinstance(data["tags"], list)
            or not isinstance(data["assignments"], dict)):
        raise TagStoreError("the user tag store is malformed")
    tags = data["tags"]
    if any(normalize(tag) != tag for tag in tags) or len(set(tags)) != len(tags):
        raise TagStoreError("the user tag store contains non-normalized or duplicate tags")
    known = set(tags)
    for key, assigned in data["assignments"].items():
        if (not isinstance(key, str) or not key or not isinstance(assigned, list)
                or not all(isinstance(tag, str) and tag in known for tag in assigned)
                or len(set(assigned)) != len(assigned)):
            raise TagStoreError("the user tag store contains invalid assignments")
    return data


def load(state_dir):
    """Missing file is an empty store; a corrupt file is an error."""
    path = store_path(state_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"tags": [], "assignments": {}}
    except (ValueError, UnicodeDecodeError) as exc:
        raise TagStoreError("the user tag store is unreadable; "
                            f"fix or remove state/{STORE_NAME}") from exc
    return _validate(data)


def _snapshot(tags, assignments):
    return {"tags": sorted(tags),
            "assignments": {k: sorted(v) for k, v in sorted(assignments.items())
                            if v}}


def migrate(state_dir, lock_already_held=False):
    """One-time harvest of every existing asset tag (generated or manual)
    into the user store. Must run BEFORE a scan overwrites assets.json, so
    CLI-only rescans preserve tags without the UI ever loading.

    The store file's existence is the migration marker: once it exists this
    is a no-op, so later rescans never re-import or reset user edits.
    """
    path = store_path(state_dir)
    with _index_assets().state_write_lock(state_dir, "user_tags", blocking=True,
                                         already_held=lock_already_held):
        if os.path.exists(path):
            load(state_dir)
            return False
        assets_json = os.path.join(state_dir, "assets.json")
        try:
            with open(assets_json, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {"assets": []}
        except (ValueError, UnicodeDecodeError) as exc:
            raise TagStoreError("cannot migrate tags: asset index is unreadable") from exc
        if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
            raise TagStoreError("cannot migrate tags: asset index is malformed")
        tags, assignments = set(), {}
        for asset in data["assets"]:
            if not isinstance(asset, dict) or not isinstance(asset.get("asset_key"), str) or not asset["asset_key"]:
                raise TagStoreError("cannot migrate tags: invalid asset identity")
            old_tags = asset.get("tags") or []
            if not isinstance(old_tags, list):
                raise TagStoreError("cannot migrate tags: invalid asset tags")
            normalized = {normalize(tag) for tag in old_tags}
            assignments.setdefault(asset["asset_key"], set()).update(normalized)
            tags.update(normalized)
        _index_assets().write_atomic(path, json.dumps(_snapshot(tags, assignments), indent=1, sort_keys=True))
    return True


def overlay(data, state_dir):
    """User tags are authoritative: replace each asset's derived/enriched
    tags with its assignment (empty when unassigned)."""
    assignments = load(state_dir)["assignments"]
    for asset in data.get("assets", []):
        asset["tags"] = sorted(assignments.get(asset.get("asset_key"), []))
        asset["tag_source"] = "user"
    return data


def mutate(state_dir, change, lock_already_held=False):
    """Apply one change and return the new snapshot. Input shape is validated
    by the caller; store corruption raises TagStoreError with nothing written.
    """
    action = change.get("action")
    if action not in ACTIONS:
        raise TagStoreError(f"unknown tag action {action!r}")
    tag = normalize(change.get("tag"))
    with _index_assets().state_write_lock(state_dir, "user_tags", blocking=True,
                             already_held=lock_already_held):
        store = load(state_dir)
        tags = set(store["tags"])
        assignments = {k: set(v) for k, v in store["assignments"].items()}
        if action == "create":
            tags.add(tag)
        elif action == "rename":
            # Renaming onto an existing tag merges: the union of both
            # assignments lands on the surviving name.
            new_tag = normalize(change.get("new_tag"))
            if tag not in tags:
                raise TagStoreError("tag no longer exists; refresh before renaming")
            if new_tag != tag:
                for assigned in assignments.values():
                    if tag in assigned:
                        assigned.discard(tag)
                        assigned.add(new_tag)
                tags.discard(tag)
                tags.add(new_tag)
        elif action == "delete":
            tags.discard(tag)
            for assigned in assignments.values():
                assigned.discard(tag)
        else:
            key = change.get("asset_key")
            if not isinstance(key, str) or not key:
                raise TagStoreError("asset_key required for assign/remove")
            if action == "assign":
                tags.add(tag)
                assignments.setdefault(key, set()).add(tag)
            else:
                assignments.get(key, set()).discard(tag)
        result = _snapshot(tags, assignments)
        _index_assets().write_atomic(store_path(state_dir),
                        json.dumps(result, indent=1, sort_keys=True))
    return result
