#!/usr/bin/env python3
"""Storage configuration and crash-safe index rebuilds for the asset library.

All persistent user-side data lives in the chosen library at <vault>/.data
(flat state JSON, reports, recovery journal, locks, preferences). The repo's
config.json is only the agreed exception: the last-opened-library pointer.
Legacy repo state/ is copied
into .data once (gated by a durable completion marker), never overwritten,
and never deleted.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402
import resolve_store  # noqa: E402
from progress import json_progress  # noqa: E402

API_VERSION = 7
JOURNAL_NAME = "storage-journal.json"
JOURNAL_VERSION = 1
DATA_DIR_NAME = ".data"
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_TXN_RE = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED = ("assets", "pending", "review", "meta", "csv", "config")

# Existing destination files win; migration keeps the old files as a recovery backup.
_LEGACY_MIGRATABLE = ("assets.json", "cache.json", "overrides.json", "user_tags.json",
                      "pending-enrichment.json", "review-queue.md", "index-meta.json",
                      "favorites.json", "preferences.json", "search-worklist.json", "CHANGES.md")
MIGRATION_MARKER = "legacy-migrated.json"


class StorageError(RuntimeError):
    """A user-actionable storage operation failure."""


@contextlib.contextmanager
def _lock(path, blocking=True):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(path, "a", encoding="utf-8")
    acquired = False
    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fh.fileno(), flags)
        except BlockingIOError as exc:
            raise StorageError("another backend instance is running") from exc
        acquired = True
        yield fh
    finally:
        if acquired:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _repo(value=None):
    return os.path.realpath(os.path.abspath(value or ia.repo_dir()))


def _legacy_state(repo):
    return os.path.join(repo, "state")


def data_dir(root):
    """All persistent user-side data for one library lives here."""
    return os.path.join(root, DATA_DIR_NAME)


def _ensure_data_dir(root):
    target = data_dir(root)
    # A symlinked .data could silently relocate every user-side file outside
    # the library; only a real directory is accepted.
    if os.path.islink(target) or (os.path.exists(target) and not os.path.isdir(target)):
        raise StorageError(f"library data directory must be a real directory: {target}")
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        raise StorageError(f"library data directory is not writable: {target}") from exc
    return target


def _config_path(repo):
    return os.path.join(repo, "config.json")


def _journal_path(state):
    return os.path.join(state, JOURNAL_NAME)


def _canonical_root(path):
    if not isinstance(path, str) or not path.strip():
        raise StorageError("vault root is missing")
    absolute = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(absolute):
        raise StorageError(f"vault root is not a directory: {absolute}")
    return os.path.realpath(absolute)


def _reject_workspace_overlap(root, repo):
    # The application workspace must never be scanned as part of the vault.
    try:
        if os.path.commonpath((root, repo)) == root:
            raise StorageError("application workspace is inside the vault root")
    except ValueError as exc:
        raise StorageError("vault root and application workspace are on different volumes") from exc


def _load_config(repo):
    path = _config_path(repo)
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StorageError("saved storage configuration is unreadable") from exc
    if not isinstance(value, dict):
        raise StorageError("saved storage configuration is invalid")
    root = value.get("vault_root")
    if not isinstance(root, str) or not root:
        raise StorageError("saved storage configuration has no vault root")
    return value


def _write_pointer(repo, root):
    """Repo config.json is only the agreed exception: the last-opened
    library path, nothing else."""
    _write_json(_config_path(repo), {"vault_root": root})


def _identity(root):
    try:
        return list(cv._root_identity(root))
    except Exception as exc:
        raise StorageError(str(exc)) from exc


def _valid_index(state, root, repo):
    assets = os.path.join(state, "assets.json")
    if not os.path.isfile(assets):
        return False, "index is not built yet"
    try:
        with open(assets, encoding="utf-8") as fh:
            value = json.load(fh)
        if not isinstance(value, dict) or not isinstance(value.get("assets"), list):
            return False, "index is unreadable"
    except (OSError, ValueError):
        return False, "index is unreadable"
    metadata = os.path.join(state, "index-meta.json")
    if not os.path.exists(metadata):
        # Indexes made before storage binding are safe to adopt once, but never
        # silently adopt an index when a recovery journal is present.
        if os.path.exists(_journal_path(state)):
            return False, "index binding is incomplete"
        return True, None
    try:
        with open(metadata, encoding="utf-8") as fh:
            meta = json.load(fh)
        expected = _identity(root)
        if os.path.realpath(meta.get("vault_root", "")) != root:
            return False, "index belongs to a different vault root"
        if list(meta.get("root_identity", [])) != expected:
            return False, "vault root identity changed; rebuild required"
        if os.path.realpath(meta.get("repo", "")) != repo:
            return False, "index belongs to a different application workspace"
    except (OSError, ValueError, TypeError, StorageError):
        return False, "index binding is unreadable"
    return True, None


def _journal_read(state, legacy_output=False):
    path = _journal_path(state)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            journal = json.load(fh)
    except (OSError, ValueError) as exc:
        raise StorageError("storage recovery journal is unreadable") from exc
    if not isinstance(journal, dict) or journal.get("version") != JOURNAL_VERSION:
        raise StorageError("storage recovery journal has an unsupported format")
    txn = journal.get("txn")
    names = journal.get("entries")
    if not isinstance(txn, str) or not _TXN_RE.fullmatch(txn) or not isinstance(names, list):
        raise StorageError("storage recovery journal is invalid")
    if any(name not in _ALLOWED for name in names) or len(set(names)) != len(names):
        raise StorageError("storage recovery journal contains an unsafe file")
    backed_up = journal.get("backed_up", [])
    existed = journal.get("existed", [])
    if (not isinstance(backed_up, list) or any(name not in _ALLOWED for name in backed_up)
            or not isinstance(existed, list) or any(name not in _ALLOWED for name in existed)):
        raise StorageError("storage recovery journal contains unsafe progress")
    journal["backed_up"] = backed_up
    journal["existed"] = existed
    if legacy_output:
        # Legacy journals targeted the repo output dir for csv and the repo
        # config.json itself; recovery must restore those original paths.
        output_rel = journal.get("output_rel", ".")
        if not isinstance(output_rel, str) or os.path.isabs(output_rel):
            raise StorageError("storage recovery journal contains an unsafe output path")
        normalized = os.path.normpath(output_rel)
        if normalized == ".." or normalized.startswith(".." + os.sep):
            raise StorageError("storage recovery journal contains an unsafe output path")
        journal["output_rel"] = "." if normalized == "." else normalized
    return journal


def _targets(state):
    return {
        "assets": os.path.join(state, "assets.json"),
        "pending": os.path.join(state, "pending-enrichment.json"),
        "review": os.path.join(state, "review-queue.md"),
        "meta": os.path.join(state, "index-meta.json"),
        "csv": os.path.join(state, "assets.csv"),
        "config": os.path.join(state, "config.json"),
    }


def _entry_paths(state, txn, name):
    if name not in _ALLOWED:
        raise StorageError("unsafe storage transaction entry")
    target = _targets(state)[name]
    return (target,
            os.path.join(state, f".storage-stage-{txn}-{name}"),
            os.path.join(state, f".storage-backup-{txn}-{name}"))


def _legacy_output_dir(repo, output_rel):
    repo = _repo(repo)
    if not isinstance(output_rel, str) or os.path.isabs(output_rel):
        raise StorageError("unsafe legacy output path")
    output = os.path.realpath(os.path.join(repo, output_rel))
    if os.path.commonpath((repo, output)) != repo:
        raise StorageError("legacy output escapes the application workspace")
    return output


def _legacy_entry_paths(repo, txn, name, output_rel):
    """Recover the old journal using its original target-adjacent staging files."""
    if name not in _ALLOWED:
        raise StorageError("unsafe storage transaction entry")
    legacy = _legacy_state(repo)
    output = _legacy_output_dir(repo, output_rel)
    targets = {
        "assets": os.path.join(legacy, "assets.json"),
        "pending": os.path.join(legacy, "pending-enrichment.json"),
        "review": os.path.join(legacy, "review-queue.md"),
        "meta": os.path.join(legacy, "index-meta.json"),
        "csv": os.path.join(output, "assets.csv"),
        "config": _config_path(repo),
    }
    target = targets[name]
    return (target,
            os.path.join(os.path.dirname(target), f".storage-stage-{txn}-{name}"),
            os.path.join(os.path.dirname(target), f".storage-backup-{txn}-{name}"))


def _remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def recover(repo, state=None):
    """Restore a prepared/applying transaction using only the fixed allowlist.

    A journal found in the legacy repo state/ targets the legacy layout (csv
    in the repo output dir, config at the repo root); a journal in .data
    targets the relocated layout.
    """
    repo = _repo(repo)
    legacy = _legacy_state(repo)
    if state is None:
        state = legacy
    legacy_mode = os.path.realpath(state) == os.path.realpath(legacy)
    journal = _journal_read(state, legacy_output=legacy_mode)
    if journal is None:
        return False
    txn = journal["txn"]
    names = journal["entries"]
    committed = journal.get("status") == "committed"
    status = journal.get("status")
    if status not in ("prepared", "applying", "committed"):
        raise StorageError("storage recovery journal has an invalid status")

    def entry(name):
        if legacy_mode:
            return _legacy_entry_paths(repo, txn, name, journal.get("output_rel", "."))
        return _entry_paths(state, txn, name)

    if status == "prepared":
        for name in names:
            _, stage, _ = entry(name)
            _remove(stage)
        _remove(_journal_path(state))
        return True
    for name in reversed(names):
        target, stage, backup = entry(name)
        if committed:
            _remove(backup)
            _remove(stage)
            continue
        if os.path.exists(backup):
            _remove(target)
            os.replace(backup, target)
        elif name in journal.get("backed_up", []) and name not in journal.get("existed", []):
            _remove(target)
        _remove(stage)
    _remove(_journal_path(state))
    return True


def _legacy_owner_root(legacy):
    """The library a legacy repo state/ belongs to, from its index binding."""
    try:
        with open(os.path.join(legacy, "index-meta.json"), encoding="utf-8") as fh:
            bound = json.load(fh).get("vault_root")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(bound, str) or not bound:
        return None
    return os.path.realpath(bound)


def ensure_migrated(repo, cfg):
    """Copy legacy repo state/ into <vault>/.data once.

    Only runs when the legacy state's index binding matches the library being
    opened (no cross-library bleed). A durable completion marker in .data ends
    migration permanently, so a destination file deleted afterwards is never
    resurrected. Existing destination files always win; legacy files are never
    deleted.
    """
    legacy = _legacy_state(repo)
    if not os.path.isdir(legacy):
        return False
    root = _canonical_root(cfg["vault_root"])
    state = _ensure_data_dir(root)
    marker = os.path.join(state, MIGRATION_MARKER)
    if os.path.exists(marker):
        return False
    with _lock(os.path.join(legacy, "server-instance.lock"), blocking=False), ia.state_write_lock(legacy, "legacy migration", blocking=True):
        recover(repo, state=legacy)
        copied = []
        if _legacy_owner_root(legacy) == root:
            sources = [(name, os.path.join(legacy, name)) for name in _LEGACY_MIGRATABLE]
            old_cfg = _load_config(repo) or cfg
            output = _legacy_output_dir(repo, old_cfg.get("output_dir", "."))
            sources.append(("assets.csv", os.path.join(output, "assets.csv")))
            for name, src in sources:
                dst = os.path.join(state, name)
                if not os.path.isfile(src) or os.path.lexists(dst):
                    continue
                try:
                    with open(src, encoding="utf-8") as fh:
                        payload = fh.read()
                except OSError as exc:
                    raise StorageError(f"legacy state file is unreadable: {src}") from exc
                try:
                    ia.write_atomic(dst, payload)
                except OSError as exc:
                    raise StorageError(f"library data directory is not writable: {state}") from exc
                copied.append(name)
        _write_json(marker, {"complete": True, "copied": copied})
    return bool(copied)


def recover_startup(repo=None):
    """Recover a crashed storage transaction for the last-opened library."""
    repo = _repo(repo)
    legacy = _legacy_state(repo)
    if os.path.exists(_journal_path(legacy)):
        with _lock(os.path.join(legacy, "server-instance.lock"), blocking=False):
            with ia.state_write_lock(legacy, "legacy storage recover", blocking=True):
                recover(repo, state=legacy)
    try:
        cfg = _load_config(repo)
    except StorageError:
        cfg = None
    state = _legacy_state(repo)
    if cfg is not None:
        try:
            root = _canonical_root(cfg["vault_root"])
            _reject_workspace_overlap(root, repo)
            state = _ensure_data_dir(root)
        except StorageError:
            state = _legacy_state(repo)
    if os.path.exists(_journal_path(state)):
        with _lock(os.path.join(state, "server-instance.lock"), blocking=False), ia.state_write_lock(state, "storage recover", blocking=True):
            recover(repo, state=state)
    return True


def _state_payload(repo, *, path, ready, needs_setup, error, needs_index=False):
    return {"path": path, "ready": bool(ready), "needsSetup": bool(needs_setup),
            "needsIndex": bool(needs_index), "error": error}


def _write_json(path, value):
    ia.write_atomic(path, json.dumps(value, indent=1, sort_keys=True))


def status(repo=None):
    repo = _repo(repo)
    try:
        recover_startup(repo)
        cfg = _load_config(repo)
    except StorageError as exc:
        return _state_payload(repo, path=None, ready=False, needs_setup=False, error=str(exc))
    if cfg is None:
        return _state_payload(repo, path=None, ready=False, needs_setup=True, error=None)
    try:
        root = _canonical_root(cfg["vault_root"])
        _reject_workspace_overlap(root, repo)
    except StorageError as exc:
        raw = cfg.get("vault_root") if isinstance(cfg, dict) else None
        return _state_payload(repo, path=raw, ready=False, needs_setup=False, error=str(exc))
    state = _ensure_data_dir(root)
    # A live server owns this lock. It is safe to read its stable state, but
    # recovery must wait until the owner has exited.
    try:
        with _lock(os.path.join(state, "server-instance.lock"), blocking=False):
            with ia.state_write_lock(state, "storage status", blocking=True):
                recover(repo, state=state)
                ensure_migrated(repo, cfg)
    except (StorageError, ia.StateWriteBusy) as exc:
        if isinstance(exc, StorageError) and "another backend instance" not in str(exc):
            return _state_payload(repo, path=root, ready=False, needs_setup=False, error=str(exc))
    # Once migration/configuration succeeds the repo config is reduced to
    # the last-opened pointer, dropping any legacy keys.
    fresh = _load_config(repo)
    if fresh is not None and set(fresh) != {"vault_root"}:
        _write_pointer(repo, root)
    ready, error = _valid_index(state, root, repo)
    needs_index = not ready and error == "index is not built yet"
    if needs_index:
        error = None
    return _state_payload(repo, path=root, ready=ready, needs_setup=False,
                          error=error, needs_index=needs_index)


def _pending_for(data, cache):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    resolved = cache.get("resolved", {}) if isinstance(cache, dict) else {}
    for asset in data.get("assets", []):
        if asset.get("non_store") or (resolved.get(asset.get("asset_key"), {}).get("status") == "resolved"):
            continue
        rows.append({"asset_key": asset["asset_key"], "title": asset["name"],
                     "first_seen": today,
                     "previously_unresolved": bool(resolved.get(asset.get("asset_key")))})
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pending": sorted(rows, key=lambda row: row["asset_key"])}


def _read_cache(state):
    path = os.path.join(state, "cache.json")
    if not os.path.exists(path):
        return {"resolved": {}, "misses": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError) as exc:
        raise StorageError("metadata cache is unreadable; refusing to replace it") from exc
    if not isinstance(cache, dict) or not isinstance(cache.get("resolved", {}), dict):
        raise StorageError("metadata cache is invalid; refusing to replace it")
    return cache


def configure(repo=None, root=None, instance_id=None, progress=False):
    repo = _repo(repo)
    recover_startup(repo)
    token = instance_id or secrets.token_hex(16)
    if not isinstance(token, str) or not _INSTANCE_RE.fullmatch(token):
        raise StorageError("instance id is invalid")
    selected = _canonical_root(root)
    _reject_workspace_overlap(selected, repo)
    state = _ensure_data_dir(selected)
    txn = uuid.uuid4().hex
    progress_cb = json_progress if progress else None
    with _lock(os.path.join(state, "server-instance.lock"), blocking=False):
        with ia.state_write_lock(state, "storage configure", blocking=True):
            recover(repo, state=state)
            # Legacy repo state only ever migrates into the library its
            # index binding names; switching libraries never bleeds data.
            ensure_migrated(repo, {"vault_root": selected})
            before = _identity(selected)
            scanned = ia.scan(selected, strict=True, progress=progress_cb)
            if _identity(selected) != before:
                raise StorageError("vault root identity changed during scan")
            data = ia.build(scanned, progress=progress_cb)
            cache = _read_cache(state)
            overrides_path = os.path.join(state, "overrides.json")
            overrides = {}
            if os.path.exists(overrides_path):
                try:
                    with open(overrides_path, encoding="utf-8") as fh:
                        overrides = json.load(fh)
                except (OSError, ValueError) as exc:
                    raise StorageError("metadata overrides are unreadable") from exc
            data = resolve_store.merge(data, cache, overrides)
            try:
                ia.user_tags.migrate(state, lock_already_held=True)
                data = ia.user_tags.overlay(data, state)
            except ia.user_tags.TagStoreError as exc:
                raise StorageError(str(exc)) from exc
            pending = _pending_for(data, cache)
            metadata = {"api_version": API_VERSION, "vault_root": selected,
                        "repo": repo, "root_identity": before}
            config = {"vault_root": selected, "repo": repo, "api_version": API_VERSION}
            stage_names = ["assets", "pending", "review", "meta", "csv", "config"]
            journal = {"version": JOURNAL_VERSION, "txn": txn, "status": "prepared",
                       "entries": stage_names,
                       "backed_up": [], "existed": []}
            _write_json(_journal_path(state), journal)
            try:
                for name in stage_names:
                    target, stage, _ = _entry_paths(state, txn, name)
                    os.makedirs(os.path.dirname(stage), exist_ok=True)
                    if name == "assets":
                        _write_json(stage, data)
                    elif name == "pending":
                        _write_json(stage, pending)
                    elif name == "meta":
                        _write_json(stage, metadata)
                    elif name == "review":
                        ia.emit_review_queue(data, stage)
                    elif name == "csv":
                        ia.emit_csv(data, stage)
                    else:
                        _write_json(stage, config)
                journal["status"] = "applying"
                _write_json(_journal_path(state), journal)
                for name in stage_names:
                    target, stage, backup = _entry_paths(state, txn, name)
                    if os.path.exists(target):
                        journal["existed"].append(name)
                        os.replace(target, backup)
                    journal["backed_up"].append(name)
                    _write_json(_journal_path(state), journal)
                    os.replace(stage, target)
                journal["status"] = "committed"
                _write_json(_journal_path(state), journal)
                recover(repo, state=state)
                # The repo config.json pointer is written only after the
                # journaled commit succeeded.
                _write_pointer(repo, selected)
            except BaseException:
                try:
                    recover(repo, state=state)
                except BaseException:
                    pass
                raise
    return _state_payload(repo, path=selected, ready=True, needs_setup=False, error=None)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("steps", nargs="+", choices=["status", "configure"])
    parser.add_argument("--repo", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--root", default=None,
                        help="vault root for configure (default: last-opened pointer)")
    parser.add_argument("--instance-id", dest="instance_id", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--progress-json", dest="progress_json", action="store_true",
                        help="emit machine-readable progress events")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if "configure" in args.steps:
            if not args.root:
                raise StorageError("configure requires --root")
            outcome = configure(repo=args.repo, root=args.root,
                                instance_id=args.instance_id,
                                progress=args.progress_json)
        else:
            outcome = status(repo=args.repo)
    except StorageError as exc:
        outcome = {"path": None, "ready": False, "needsSetup": False,
                   "needsIndex": False, "error": str(exc)}
    # One JSON line on stdout: the Electron host parses single-line outcomes.
    print(json.dumps(outcome))
    return 0 if outcome.get("ready") or outcome.get("needsSetup") else 1


if __name__ == "__main__":
    raise SystemExit(main())
