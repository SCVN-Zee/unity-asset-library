#!/usr/bin/env python3
"""Storage configuration and crash-safe index rebuilds for the asset library."""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402
import resolve_store  # noqa: E402
from progress import json_progress  # noqa: E402

API_VERSION = 6
JOURNAL_NAME = "storage-journal.json"
JOURNAL_VERSION = 1
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_TXN_RE = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED = ("assets", "pending", "review", "meta", "csv", "config")


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


def _state(repo):
    return os.path.join(repo, "state")


def _config_path(repo):
    return os.path.join(repo, "config.json")


def _journal_path(repo):
    return os.path.join(_state(repo), JOURNAL_NAME)


def _canonical_root(path):
    if not isinstance(path, str) or not path.strip():
        raise StorageError("vault root must be a non-empty path")
    absolute = os.path.abspath(os.path.expanduser(path))
    try:
        st = os.lstat(absolute)
    except OSError as exc:
        raise StorageError(f"vault root is unavailable: {absolute}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise StorageError("vault root must be a real directory, not a symlink")
    return os.path.realpath(absolute)


def _reject_workspace_overlap(root, repo):
    # Generated config/state must never be scanned as part of the selected vault.
    try:
        if os.path.commonpath((root, repo)) == root:
            raise StorageError("vault root cannot contain the application workspace")
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


def _safe_output_rel(cfg):
    value = cfg.get("output_dir", ".")
    if not isinstance(value, str) or os.path.isabs(value):
        raise StorageError("output_dir must be a relative application path")
    normalized = os.path.normpath(value)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise StorageError("output_dir cannot escape the application workspace")
    return "." if normalized == "." else normalized


def _output_dir(repo, cfg):
    rel = _safe_output_rel(cfg)
    return os.path.join(repo, rel) if rel != "." else repo
def _validated_output_dir(repo, root, cfg):
    output = os.path.realpath(_output_dir(repo, cfg))
    repo = os.path.realpath(repo)
    root = os.path.realpath(root)
    try:
        if os.path.commonpath((repo, output)) != repo:
            raise StorageError("generated output must remain inside the application workspace")
        if os.path.commonpath((root, output)) == root:
            raise StorageError("generated output cannot be written inside the vault")
    except ValueError as exc:
        raise StorageError("generated output is on a different volume") from exc
    return output



def _identity(root):
    try:
        return list(cv._root_identity(root))
    except cv.SafetyError as exc:
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
        if os.path.exists(_journal_path(repo)):
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

def _journal_read(repo):
    path = _journal_path(repo)
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
    output_rel = journal.get("output_rel", ".")
    if not isinstance(output_rel, str) or os.path.isabs(output_rel):
        raise StorageError("storage recovery journal contains an unsafe output path")
    normalized = os.path.normpath(output_rel)
    if normalized == ".." or normalized.startswith(".." + os.sep):
        raise StorageError("storage recovery journal contains an unsafe output path")
    journal["output_rel"] = "." if normalized == "." else normalized
    return journal


def _paths(repo, txn, output_rel):
    state = _state(repo)
    output = os.path.join(repo, output_rel) if output_rel != "." else repo
    targets = {
        "assets": os.path.join(state, "assets.json"),
        "pending": os.path.join(state, "pending-enrichment.json"),
        "review": os.path.join(state, "review-queue.md"),
        "meta": os.path.join(state, "index-meta.json"),
        "csv": os.path.join(output, "assets.csv"),
        "config": _config_path(repo),
    }
    return {
        name: (target, os.path.join(os.path.dirname(target), f".storage-{kind}-{txn}-{name}"))
        for name, target in targets.items()
        for kind in ("stage", "backup")
    }


def _entry_paths(repo, txn, output_rel, name):
    if name not in _ALLOWED:
        raise StorageError("unsafe storage transaction entry")
    state = _state(repo)
    output = os.path.join(repo, output_rel) if output_rel != "." else repo
    output_real = os.path.realpath(output)
    repo_real = os.path.realpath(repo)
    try:
        if os.path.commonpath((repo_real, output_real)) != repo_real:
            raise StorageError("storage transaction output escapes the application workspace")
        cfg = _load_config(repo)
        if cfg is not None:
            configured_root = os.path.realpath(os.path.abspath(cfg["vault_root"]))
            if os.path.commonpath((configured_root, output_real)) == configured_root:
                raise StorageError("storage transaction output cannot be inside the vault")
    except ValueError as exc:
        raise StorageError("storage transaction output is on a different volume") from exc
    targets = {
        "assets": os.path.join(state, "assets.json"),
        "pending": os.path.join(state, "pending-enrichment.json"),
        "review": os.path.join(state, "review-queue.md"),
        "meta": os.path.join(state, "index-meta.json"),
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


def recover(repo):
    """Restore a prepared/applying transaction using only the fixed allowlist."""
    journal = _journal_read(repo)
    if journal is None:
        return False
    txn = journal["txn"]
    output_rel = journal["output_rel"]
    names = journal["entries"]
    committed = journal.get("status") == "committed"
    status = journal.get("status")
    if status not in ("prepared", "applying", "committed"):
        raise StorageError("storage recovery journal has an invalid status")
    if status == "prepared":
        for name in names:
            _, stage, _ = _entry_paths(repo, txn, output_rel, name)
            _remove(stage)
        _remove(_journal_path(repo))
        return True
    for name in reversed(names):
        target, stage, backup = _entry_paths(repo, txn, output_rel, name)
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
    _remove(_journal_path(repo))
    return True


def _state_payload(repo, *, path, ready, needs_setup, error, needs_index=False):
    return {"path": path, "ready": bool(ready), "needsSetup": bool(needs_setup),
            "needsIndex": bool(needs_index), "error": error}


def _write_json(path, value):
    ia.write_atomic(path, json.dumps(value, indent=1, sort_keys=True))


def status(repo=None):
    repo = _repo(repo)
    os.makedirs(_state(repo), exist_ok=True)
    # A live server owns this lock. It is safe to read its stable state, but
    # recovery must wait until the owner has exited.
    try:
        with _lock(os.path.join(_state(repo), "server-instance.lock"), blocking=False):
            with ia.state_write_lock(_state(repo), "storage status", blocking=True):
                recover(repo)
    except (StorageError, ia.StateWriteBusy) as exc:
        if isinstance(exc, StorageError) and "another backend instance" not in str(exc):
            return _state_payload(repo, path=None, ready=False, needs_setup=False, error=str(exc))
    try:
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
    ready, error = _valid_index(_state(repo), root, repo)
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
    state = _state(repo)
    os.makedirs(state, exist_ok=True)
    token = instance_id or secrets.token_hex(16)
    if not isinstance(token, str) or not _INSTANCE_RE.fullmatch(token):
        raise StorageError("instance id is invalid")
    selected = _canonical_root(root)
    _reject_workspace_overlap(selected, repo)
    output_rel = "."
    txn = uuid.uuid4().hex
    progress_cb = json_progress if progress else None
    with _lock(os.path.join(state, "server-instance.lock"), blocking=False):
        with ia.state_write_lock(state, "storage configure", blocking=True):
            recover(repo)
            old_cfg = _load_config(repo) or {}
            output_rel = _safe_output_rel(old_cfg)
            output_dir = _validated_output_dir(repo, selected, old_cfg)
            os.makedirs(output_dir, exist_ok=True)
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
            config = dict(old_cfg)
            config.setdefault("output_dir", ".")
            config.pop("instance_id", None)
            config.update({"vault_root": selected, "repo": repo,
                           "api_version": API_VERSION})
            stage_names = ["assets", "pending", "review", "meta", "csv", "config"]
            journal = {"version": JOURNAL_VERSION, "txn": txn, "status": "prepared",
                       "entries": stage_names, "output_rel": output_rel,
                       "backed_up": [], "existed": []}
            _write_json(_journal_path(repo), journal)
            try:
                for name in stage_names:
                    target, stage, _ = _entry_paths(repo, txn, output_rel, name)
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
                _write_json(_journal_path(repo), journal)
                for name in stage_names:
                    target, stage, backup = _entry_paths(repo, txn, output_rel, name)
                    if os.path.exists(target):
                        journal["existed"].append(name)
                        os.replace(target, backup)
                    journal["backed_up"].append(name)
                    _write_json(_journal_path(repo), journal)
                    os.replace(stage, target)
                journal["status"] = "committed"
                _write_json(_journal_path(repo), journal)
                recover(repo)
            except BaseException:
                try:
                    recover(repo)
                except BaseException:
                    pass
                raise
    return _state_payload(repo, path=selected, ready=True, needs_setup=False, error=None)


def recover_startup(repo=None):
    """Recover a crashed storage transaction while owning both stable locks."""
    repo = _repo(repo)
    state = _state(repo)
    os.makedirs(state, exist_ok=True)
    with _lock(os.path.join(state, "server-instance.lock"), blocking=False):
        with ia.state_write_lock(state, "storage startup", blocking=True):
            return recover(repo)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=None, help="application workspace")
    parser.add_argument("--instance-id", default=None, help="backend instance identity")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    configure_parser = sub.add_parser("configure")
    configure_parser.add_argument("--root", required=True)
    configure_parser.add_argument("--progress-json", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    repo = _repo(args.repo)
    try:
        if args.command == "status":
            print(json.dumps(status(repo), sort_keys=True))
            return 0
        outcome = configure(repo, args.root, args.instance_id, args.progress_json)
        print(json.dumps(outcome, sort_keys=True))
        return 0
    except (StorageError, OSError, ValueError, KeyError) as exc:
        print(json.dumps(_state_payload(repo, path=None, ready=False,
                                        needs_setup=False, error=str(exc)), sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
