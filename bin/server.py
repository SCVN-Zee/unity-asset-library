#!/usr/bin/env python3
"""Loopback viewer server for the Unity asset library.

Serves the React/Electron app's fixed JSON API:

    GET  /api/state          pending count, capability token, latest action and job
    GET  /api/action/<id>    action stage, progress, result and terminal error
    GET  /api/assets         index with authoritative tags and live archive availability
    GET  /api/tags           lowercase tag catalog and package assignments
    POST /api/tags           create, rename, delete, assign or remove a user tag
    POST /api/resync/plan    read-only index change preview
    POST /api/resync/apply   confirm index changes only
    POST /api/cleanup/plan   preview old-version disk cleanup
    POST /api/cleanup/apply  confirm a cleanup preview by plan_hash
    POST /api/organize/plan  category-folder plan from the organize engine
    POST /api/organize/apply confirm an organize preview by plan_hash
    POST /api/enrich/start   pending-only resolve -> enrich -> emit background job
    POST /api/enrich/cancel  terminate the job's child process
    GET  /api/job/<id>       job status, stage, progress, bounded log tail

The six plan/apply routes return HTTP 202 {job_id}; poll /api/action/<id>.
API version 6 requires the matching desktop bridge and user tag endpoints. Execution-time lock
contention is a failed action (error_code=busy); admission contention is HTTP 409.

Safety shape: loopback bind only; per-process capability token on every mutating
POST; non-blocking mutation gate (busy 409, never queue destructive work);
plan_hash binds the user's confirmation to a fresh filesystem snapshot; client
JSON never becomes filesystem input. Stdlib only.

Usage:
    python3 bin/server.py [--port 8765]
"""

import argparse
import contextlib
import collections
import fcntl
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402
import organize_versions as ov  # noqa: E402
import storage  # noqa: E402
import user_tags  # noqa: E402


SafetyError = cv.SafetyError

MAX_BODY = 16 * 1024
LOG_LINES = 200
DEFAULT_PORT = 8765

PROGRESS_PREFIX = "UL_PROGRESS "


class Busy(Exception):
    """A mutation was refused because a gate-holding operation is active."""

    def __init__(self, detail, job_id=None):
        super().__init__(detail)
        self.job_id = job_id


class Drift(Exception):
    """The confirmed plan_hash no longer matches the current filesystem."""

    def __init__(self, plan, plan_hash_value):
        super().__init__(plan_hash_value)
        self.plan = plan
        self.plan_hash = plan_hash_value


class UnknownAsset(Exception):
    """A mutation named an asset_key that is not in the current index."""

    def __init__(self, asset_key):
        super().__init__(asset_key)
        self.asset_key = asset_key


class LibraryMismatch(Exception):
    """A request named a different library than the one that is open."""


# ---------------------------------------------------------------------------
# request-bound plan fingerprints
# ---------------------------------------------------------------------------

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def plan_hash(kind, preview, root, scanned, extra=None):
    """Bind a confirmation to the material it was shown: discriminator, every
    previewed row/summary/total, root identity, the full path manifest and its
    (dev, ino, size) identities. mtime_ns is deliberately excluded — OneDrive
    rewrites it routinely, and mtime-only churn must never produce a 409."""
    identities = sorted(
        (row["rel_path"], row["size"], *identity[:3])
        for row in scanned
        for identity in (cv.validate_candidate(root, row["rel_path"], {}),))
    material = {
        "kind": kind,
        "preview": preview,
        "root": cv._root_identity(root),
        "manifest": identities,
        "extra": extra or {},
    }
    return hashlib.sha256(canonical(material).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class ViewerService:
    """Per-server-process state: paths, capability token, mutation gate, latest
    job, lock seams. Handlers never own locks or jobs."""

    def __init__(self, repo=None, config=None, instance_id=None):
        self.repo = os.path.realpath(os.path.abspath(repo or ia.repo_dir()))
        cfg = config if config is not None else storage._load_config(self.repo)
        if not isinstance(cfg, dict):
            raise SafetyError("backend storage configuration is missing")
        configured_repo = cfg.get("repo")
        if configured_repo and os.path.realpath(os.path.abspath(configured_repo)) != self.repo:
            raise SafetyError("backend repository identity does not match configuration")
        self.root = os.path.abspath(cfg["vault_root"])
        self.instance_id = str(instance_id or secrets.token_hex(16))
        # All persistent user-side data lives in the vault's .data directory.
        self.state = storage._ensure_data_dir(self.root)
        self.bin_dir = os.path.join(self.repo, "bin")
        self.resolve_py = os.path.join(self.bin_dir, "resolve_store.py")
        self.index_py = os.path.join(self.bin_dir, "index_assets.py")
        self.token = secrets.token_hex(32)

        self._gate = threading.Lock()
        self._gate_owner = None
        self._job_lock = threading.Lock()
        self._action = None
        self._shutting_down = False
        self._admissions_closed = False
        self._job = None
        self._child = None
        self._instance_lock_fh = None
        self._job_thread = None
        self._action_thread = None
        self._scan = ia.scan
        self._plan_cleanup = cv.plan_cleanup
        self._capture_cleanup_snapshot = cv.capture_snapshot
        self._apply_cleanup = cv.apply_cleanup
        self._load_assets = self._load_assets_impl
        self._plan_organize = ov.plan_organize
        self._capture_organize_snapshot = ov.capture_snapshot
        self._apply_organize = ov.apply_organize
        self._index_update = self._index_update_impl
        try:
            with ia.state_write_lock(self.state, "storage migration", blocking=True):
                storage.ensure_migrated(self.repo, cfg)
        except storage.StorageError as exc:
            raise SafetyError(str(exc)) from exc
        self._runner = self._run_stage_process
        self._migrate_user_tags()

    def _migrate_user_tags(self):
        """One-time harvest of legacy asset tags, before anything serves or
        rescans. CLI-only runs migrate inside index_assets; this covers a
        server started against a never-migrated state dir."""
        try:
            user_tags.migrate(self.state)
        except user_tags.TagStoreError as exc:
            raise SafetyError(str(exc)) from exc

    def _load_assets_impl(self):
        with open(os.path.join(self.state, "assets.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _index_update_impl(self, scanned, progress=None):
        return ia.main(["update", "--root", self.root, "--state", self.state],
                       state_lock_held=True, scanned=scanned, progress=progress)
    def _pending_count(self):
        path = os.path.join(self.state, "pending-enrichment.json")
        if not os.path.exists(path):
            return 0
        try:
            with open(path, encoding="utf-8") as fh:
                return len(json.load(fh).get("pending", []))
        except (ValueError, OSError):
            return 0

    # -- locks --------------------------------------------------------------

    @staticmethod
    def _flock_nb(path):
        fh = open(path, "a")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return None
        return fh

    def acquire_instance_lock(self):
        """Lifetime single-instance enforcement, distinct from state-write.lock."""
        fh = self._flock_nb(os.path.join(self.state, "server-instance.lock"))
        if fh is None:
            raise SafetyError("another server instance is already running")
        self._instance_lock_fh = fh

    @contextlib.contextmanager
    def mutation_gate(self, owner):
        """Non-blocking, non-reentrant serialization of every mutation."""
        if not self._gate.acquire(blocking=False):
            with self._job_lock:
                job = self._job
            active = job if (job and job["status"] in ("queued", "running")) else None
            raise Busy("another operation holds the mutation gate",
                       job_id=active["id"] if active else None)
        self._gate_owner = owner
        try:
            yield
        finally:
            self._gate_owner = None
            self._gate.release()

    @contextlib.contextmanager
    def state_write_lock(self):
        """Use the shared non-blocking lock for HTTP mutations."""
        try:
            with ia.state_write_lock(self.state, "server", blocking=False):
                yield
        except ia.StateWriteBusy as exc:
            raise Busy(str(exc)) from exc

    @contextlib.contextmanager
    def engine_lock_attempt(self):
        """Hold the engine flock across planning and apply.

        Probing and releasing would leave a TOCTOU window before the core's normal
        blocking flock acquisition, so callers keep this context until completion.
        """
        # The filename predates the Unity Asset Library rename; it is a stable
        # cross-version safety key, so old and new backends share one lock.
        name = hashlib.sha256(f"{cv._root_identity(self.root)}".encode()).hexdigest()[:20]
        path = os.path.join(tempfile.gettempdir(), f"unity-asset-cleanup-{name}.lock")
        fh = self._flock_nb(path)
        if fh is None:
            raise Busy("an external cleanup/organize run holds the engine lock")
        try:
            yield fh
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
    def _bound_plan_hash(self, kind, preview, scanned, extra=None):
        """Bind confirmations to this backend session, not only filesystem state."""
        bound = dict(extra or {})
        bound["session_id"] = self.token
        bound["instance_id"] = self.instance_id
        return plan_hash(kind, preview, self.root, scanned, extra=bound)

    # -- plan shaping -------------------------------------------------------

    def cleanup_preview(self, scanned, progress=None):
        if progress:
            progress({"stage": "prepare", "completed": 0, "total": 1,
                      "current_item": None, "counts": {}})
        plan = self._plan_cleanup(scanned)
        if progress:
            progress({"stage": "prepare", "completed": 1, "total": 1,
                      "current_item": None, "counts": {"families": len(plan["families"])}})
        def version_view(version):
            if not version:
                return None
            return {"version": version.get("version"),
                    "release_date": version.get("release_date"),
                    "path": version.get("rel_path"),
                    "size_bytes": version.get("size")}

        families = []
        for family in plan["families"]:
            families.append({
                "asset_key": family["asset_key"],
                "reason": family["reason"],
                "survivor": version_view(family.get("survivor")),
                "removals": [version_view(v) for v in family["removals"]],
            })
        protected = collections.Counter(
            f["reason"] for f in plan["families"] if not f["removals"])
        preview = {
            "kind": "cleanup",
            "families": families,
            "removals": [{"path": r["rel_path"], "size_bytes": r["size"]}
                         for r in plan["removals"]],
            "protected": dict(sorted(protected.items())),
            "totals": {"files": plan["candidate_files"],
                       "bytes": plan["candidate_bytes"],
                       "families": plan["candidate_families"]},
        }
        digest = self._bound_plan_hash("cleanup", preview, scanned)
        return preview, digest

    def organize_preview(self, data, scanned, progress=None):
        plan = self._plan_organize(self.root, data, scanned, progress=progress)
        preview = {
            "kind": "organize",
            "groups": plan["groups"],
            "moves": plan["moves"],
            "warnings": plan["warnings"],
            "skips": {key: {"count": len(items), "items": items}
                      for key, items in plan["skips"].items()},
            "totals": plan["totals"],
        }
        destinations_free = sorted(
            (m["src"], m["dst"],
             not os.path.lexists(ov._contained_path(self.root, m["dst"])))
            for m in plan["moves"])
        digest = self._bound_plan_hash(
            "organize", preview, scanned,
            extra={"destinations_free": destinations_free})
        return preview, digest, plan

    # -- operations ---------------------------------------------------------

    def _strict_scan(self, progress=None):
        """Strict vault scan; walk errors surface before any write."""
        return self._scan(self.root, strict=True, progress=progress)

    def op_state(self):
        with self._job_lock:
            job = self._job_snapshot(self._job)
            action = self._action_snapshot(self._action)
        return {
            "service": "unity-asset-library",
            "api_version": storage.API_VERSION,
            "ready": True,
            "vault_root": os.path.realpath(self.root),
            "repo": self.repo,
            "instance_id": self.instance_id,
            "pending_enrichment": self._pending_count(),
            "job": job,
            "action": action,
            "csrf": self.token,
        }


    # -- favorites ----------------------------------------------------------
    # -- preferences --------------------------------------------------------

    PREFERENCE_KEYS = ("theme", "sort", "view", "filters")
    PREFERENCE_OPTIONAL_KEYS = ("action",)

    def _preferences_path(self):
        return os.path.join(self.state, "preferences.json")

    def op_preferences(self):
        """Stored preferences object; missing file is an empty object."""
        try:
            with open(self._preferences_path(), encoding="utf-8") as fh:
                prefs = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise SafetyError(f"preferences file is unreadable: {exc}") from exc
        if not isinstance(prefs, dict):
            raise SafetyError("preferences file is invalid")
        return prefs

    def op_set_preferences(self, preferences, library_root, only_if_missing):
        """Atomically replace .data/preferences.json. `only_if_missing` never
        overwrites an existing file (used by the legacy migration seed)."""
        if (not isinstance(library_root, str) or not library_root
                or os.path.realpath(os.path.abspath(library_root)) != os.path.realpath(self.root)):
            raise LibraryMismatch("library_root does not match the open library")
        self._validate_preferences(preferences)
        path = self._preferences_path()
        try:
            with self.state_write_lock():
                if only_if_missing and os.path.exists(path):
                    return self.op_preferences()
                ia.write_atomic(path, json.dumps(preferences, indent=1, sort_keys=True))
        except ia.StateWriteBusy as exc:
            raise Busy(str(exc)) from exc
        except OSError as exc:
            raise SafetyError(f"preferences are not writable: {exc}") from exc
        return preferences

    def _validate_preferences(self, preferences):
        if not isinstance(preferences, dict):
            raise ValueError("preferences must be an object")
        allowed = set(self.PREFERENCE_KEYS) | set(self.PREFERENCE_OPTIONAL_KEYS)
        if not set(self.PREFERENCE_KEYS) <= set(preferences) or not set(preferences) <= allowed:
            raise ValueError(f"preferences keys must include {sorted(self.PREFERENCE_KEYS)} "
                             f"and only {sorted(allowed)}")
        for key in ("theme", "sort", "view"):
            if not isinstance(preferences[key], str):
                raise ValueError(f"preferences.{key} must be a string")
        action = preferences.get("action")
        if action is not None:
            if not isinstance(action, dict) or not set(action) <= {"id", "kind", "phase"} \
                    or not all(isinstance(v, str) for v in action.values()):
                raise ValueError("preferences.action must be null or an object with "
                                 "string id/kind/phase")
        if not isinstance(preferences["filters"], dict):
            raise ValueError("preferences.filters must be an object")

    # -- user tags ------------------------------------------------------------

    def op_assets(self):
        """Overlay authoritative tags and current metadata-only availability."""
        try:
            data = user_tags.overlay(self._load_assets(), self.state)
        except user_tags.TagStoreError as exc:
            raise SafetyError(str(exc)) from exc
        root = os.path.realpath(self.root)
        for asset in data.get("assets", []):
            for version in asset.get("versions", []):
                version["availability"] = self._archive_availability(root, version.get("file"))
        return data

    @staticmethod
    def _archive_availability(root, relative):
        # Never open an archive here: reading a placeholder downloads it.
        if not isinstance(relative, str) or not relative or "\0" in relative or os.path.isabs(relative):
            return "unknown"
        try:
            path = os.path.realpath(os.path.join(root, relative))
            if not path.startswith(root + os.sep):
                return "unknown"
            info = os.stat(path)
            if not stat.S_ISREG(info.st_mode):
                return "missing"
            if sys.platform == "darwin":
                # Python 3.12 lacks the named Darwin flag (sys/stat.h).
                dataless = getattr(stat, "SF_DATALESS", 0x40000000)
                return "cloud_only" if info.st_flags & dataless else "local"
            return "unknown"
        except FileNotFoundError:
            return "missing"
        except OSError:
            return "unknown"

    def op_tags(self):
        try:
            return user_tags.load(self.state)
        except user_tags.TagStoreError as exc:
            raise SafetyError(str(exc)) from exc

    def op_mutate_tags(self, change):
        fields = {"create": {"action", "tag"}, "delete": {"action", "tag"},
                  "rename": {"action", "tag", "new_tag"},
                  "assign": {"action", "tag", "asset_key"},
                  "remove": {"action", "tag", "asset_key"}}
        action = change.get("action")
        if not isinstance(action, str) or action not in fields or set(change) - {"csrf"} != fields[action]:
            raise ValueError("invalid tag change")
        try:
            user_tags.normalize(change.get("tag"))
            if action == "rename":
                user_tags.normalize(change.get("new_tag"))
        except user_tags.TagStoreError as exc:
            raise ValueError(str(exc)) from exc
        if action in ("assign", "remove") and (not isinstance(change["asset_key"], str) or not change["asset_key"].strip()):
            raise ValueError("asset_key required")
        try:
            with self.state_write_lock():
                if action in ("assign", "remove"):
                    keys = {a["asset_key"] for a in self._load_assets()["assets"]}
                    if change["asset_key"] not in keys:
                        raise UnknownAsset(change["asset_key"])
                return user_tags.mutate(self.state, change, lock_already_held=True)
        except user_tags.TagStoreError as exc:
            raise SafetyError(str(exc)) from exc

    # -- favorites ----------------------------------------------------------
    def _favorites_path(self):
        return os.path.join(self.state, "favorites.json")

    def _read_favorites(self):
        """Stored asset_key list. A missing file is empty; a corrupt one is an
        error, never silently replaced."""
        try:
            with open(self._favorites_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return []
        except (ValueError, UnicodeDecodeError) as exc:
            raise SafetyError("the favorites store is unreadable; fix or remove the library’s .data/favorites.json") from exc
        if (not isinstance(data, dict) or set(data) != {"favorites"}
                or not isinstance(data["favorites"], list)
                or not all(isinstance(key, str) and key for key in data["favorites"])):
            raise SafetyError("the favorites store is malformed")
        return sorted(set(data["favorites"]))

    def op_favorites(self):
        return {"favorites": self._read_favorites()}

    def op_set_favorite(self, asset_key, favorite):
        """Atomic, idempotent set keyed on asset identity, under the shared
        state write lock so CLI index writers cannot interleave."""
        try:
            keys = {a["asset_key"] for a in self._load_assets()["assets"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError("the asset index is unreadable") from exc
        if asset_key not in keys:
            raise UnknownAsset(asset_key)
        with self.state_write_lock():
            current = set(self._read_favorites())
            if favorite:
                current.add(asset_key)
            else:
                current.discard(asset_key)
            result = sorted(current)
            ia.write_atomic(self._favorites_path(),
                            canonical({"favorites": result}))
        return {"favorites": result}

    def resync_preview(self, scanned, progress=None):
        if progress:
            progress({"stage": "compare", "completed": 0, "total": len(scanned),
                      "current_item": None, "counts": {}})
        try:
            previous = ia.manifest_of(self._load_assets())
        except FileNotFoundError:
            previous = {}
        except ValueError as exc:
            raise SafetyError("The index is unreadable; resync cannot safely preview changes.") from exc
        current = {row["rel_path"]: row["size"] for row in scanned}
        diff = ia.diff_manifest(previous, current, had_previous=True)
        preview = {
            "kind": "resync",
            "additions": [{"path": p, "size_bytes": current[p]} for p in diff["added"]],
            "removals": [{"path": p, "size_bytes": previous[p]} for p in diff["removed"]],
            "resized": [{"path": p, "size_bytes": new, "previous_size_bytes": old}
                        for p, old, new in diff["resized"]],
            "totals": {"added": len(diff["added"]), "removed": len(diff["removed"]),
                       "resized": len(diff["resized"])},
        }
        if progress:
            progress({"stage": "compare", "completed": len(scanned), "total": len(scanned),
                      "current_item": None,
                      "counts": {"index_" + key: value for key, value in preview["totals"].items()}})
        return preview, self._bound_plan_hash("resync", preview, scanned, extra=previous)

    def op_resync(self, progress=None):
        """Preview index changes without changing the index or vault."""
        with self.mutation_gate("resync"), self.state_write_lock(), self.engine_lock_attempt():
            preview, digest = self.resync_preview(self._strict_scan(progress=progress), progress=progress)
        return {"preview": preview, "plan_hash": digest}

    def op_resync_apply(self, plan_hash_value, progress=None):
        with self.mutation_gate("resync-apply"), self.state_write_lock(), self.engine_lock_attempt():
            scanned = self._strict_scan(progress=progress)
            preview, digest = self.resync_preview(scanned, progress=progress)
            if not hmac.compare_digest(digest, plan_hash_value):
                raise Drift(preview, digest)
            update_result = self._index_update(scanned, progress=progress)
            if update_result != 0:
                raise SafetyError(f"index update exited {update_result}")
        return {"state_refreshed": True}

    def op_cleanup_plan(self, progress=None):
        with self.mutation_gate("cleanup-plan"), self.state_write_lock(), self.engine_lock_attempt():
            preview, digest = self.cleanup_preview(self._strict_scan(progress=progress), progress=progress)
        return {"preview": preview, "plan_hash": digest}

    def op_cleanup_apply(self, plan_hash_value, progress=None):
        with self.mutation_gate("cleanup-apply"), self.state_write_lock():
            with self.engine_lock_attempt():
                scanned = self._strict_scan(progress=progress)
                try:
                    preview, digest = self.cleanup_preview(scanned, progress=progress)
                except SafetyError as exc:
                    raise Drift({"kind": "cleanup", "unavailable": str(exc)}, "") from exc
                if not hmac.compare_digest(digest, plan_hash_value):
                    raise Drift(preview, digest)
                snapshot = self._capture_cleanup_snapshot(self.root, scanned)
                plan = self._plan_cleanup(scanned)
                self._apply_cleanup(self.root, self.state, scanned, plan, False, snapshot,
                                    lock_already_held=True, progress=progress,
                                    state_lock_held=True)
        return {"applied": True, "state_refreshed": True}

    def op_organize_plan(self, progress=None):
        scanned = self._strict_scan(progress=progress)
        data = self._load_assets()
        preview, digest, _ = self.organize_preview(data, scanned, progress=progress)
        return {"preview": preview, "plan_hash": digest}

    def op_organize_apply(self, plan_hash_value, progress=None):
        with self.mutation_gate("organize-apply"), self.state_write_lock():
            with self.engine_lock_attempt():
                scanned = self._strict_scan(progress=progress)
                data = self._load_assets()
                try:
                    preview, digest, plan = self.organize_preview(data, scanned, progress=progress)
                except SafetyError as exc:
                    raise Drift({"kind": "organize", "unavailable": str(exc)}, "") from exc
                if not hmac.compare_digest(digest, plan_hash_value):
                    raise Drift(preview, digest)
                snapshot = self._capture_organize_snapshot(self.root, plan, scanned)
                report = self._apply_organize(self.root, self.state, plan, snapshot, False,
                                              lock_already_held=True, progress=progress,
                                              state_lock_held=True)
        return {"applied": True, "report": report}

    # -- action jobs --------------------------------------------------------

    def start_action(self, kind, phase, plan_hash_value=None):
        if (kind, phase) not in {
                ("resync", "plan"), ("resync", "apply"),
                ("cleanup", "plan"), ("cleanup", "apply"),
                ("organize", "plan"), ("organize", "apply")}:
            raise ValueError("unknown action")
        with self._job_lock:
            if self._shutting_down or self._admissions_closed:
                raise Busy("server is shutting down")
            active = self._action
            if active and active["status"] in ("queued", "running"):
                raise Busy("an action is already active", job_id=active["id"])
            action = {
                "id": uuid.uuid4().hex, "kind": kind, "phase": phase,
                "status": "queued", "stage": "queued", "completed": 0,
                "total": None, "current_item": None, "counts": {},
                "started_at": time.time(), "finished_at": None,
                "result": None, "error": None, "error_code": None,
            }
            self._action = action
            thread = threading.Thread(
                target=self._action_body,
                args=(action, plan_hash_value), daemon=True,
                name=f"action-{action['id']}")
            self._action_thread = thread
            thread.start()
            return action["id"]

    def _publish_action_progress(self, action, event):
        if not isinstance(event, dict):
            return
        stage = event.get("stage")
        completed = event.get("completed", 0)
        total = event.get("total")
        current = event.get("current_item")
        counts = event.get("counts", {})
        if not isinstance(stage, str) or not stage:
            return
        if not isinstance(completed, int) or completed < 0:
            return
        if total is not None and (not isinstance(total, int) or total < 0 or completed > total):
            return
        if current is not None and not isinstance(current, str):
            return
        if not isinstance(counts, dict):
            return
        with self._job_lock:
            if action["status"] not in ("queued", "running"):
                return
            action["stage"] = stage
            action["completed"] = completed
            action["total"] = total
            action["current_item"] = current
            action["counts"].update({k: v for k, v in counts.items()
                                      if isinstance(k, str) and isinstance(v, (int, float))})

    def _finish_action(self, action, status, result=None, error=None, error_code=None):
        with self._job_lock:
            if action["status"] in ("completed", "failed"):
                return
            action["status"] = status
            action["result"] = result
            action["error"] = error
            action["error_code"] = error_code
            action["finished_at"] = time.time()
            action["current_item"] = None

    def _action_body(self, action, plan_hash_value):
        with self._job_lock:
            if action["status"] != "queued":
                return
            action["status"] = "running"
            action["stage"] = "starting"
        progress = lambda event: self._publish_action_progress(action, event)
        try:
            if action["kind"] == "resync":
                result = (self.op_resync(progress=progress) if action["phase"] == "plan"
                          else self.op_resync_apply(plan_hash_value, progress=progress))
            elif action["kind"] == "cleanup":
                result = (self.op_cleanup_plan(progress=progress) if action["phase"] == "plan"
                          else self.op_cleanup_apply(plan_hash_value, progress=progress))
            else:
                result = (self.op_organize_plan(progress=progress) if action["phase"] == "plan"
                          else self.op_organize_apply(plan_hash_value, progress=progress))
        except Busy as exc:
            self._finish_action(action, "failed", error=str(exc), error_code="busy")
        except Drift as exc:
            self._finish_action(action, "failed",
                                result={"preview": exc.plan, "plan_hash": exc.plan_hash},
                                error="plan changed", error_code="plan_changed")
        except ov.StaleIndex as exc:
            self._finish_action(action, "failed", error=str(exc), error_code="stale_index")
        except SafetyError as exc:
            self._finish_action(action, "failed", result=getattr(exc, "result", None),
                                error=str(exc), error_code="safety_error")
        except OSError as exc:
            self._finish_action(action, "failed", error=str(exc), error_code="os_error")
        except BaseException as exc:
            self._finish_action(action, "failed", error=str(exc), error_code="worker_error")
        else:
            self._finish_action(action, "completed", result=result)

    @staticmethod
    def _action_snapshot(action):
        if not action:
            return None
        return {"id": action["id"], "kind": action["kind"], "phase": action["phase"],
                "status": action["status"], "stage": action["stage"],
                "completed": action["completed"], "total": action["total"],
                "current_item": action["current_item"], "counts": dict(action["counts"]),
                "started_at": action["started_at"], "finished_at": action["finished_at"],
                "result": action["result"], "error": action["error"],
                "error_code": action["error_code"]}

    def op_action(self, action_id):
        with self._job_lock:
            if not self._action or self._action["id"] != action_id:
                return None
            return self._action_snapshot(self._action)


    # -- enrich job ---------------------------------------------------------

    def op_enrich_start(self):
        with self._job_lock:
            if self._shutting_down or self._admissions_closed:
                raise Busy("server is shutting down")
            job = self._job
            if job and job["status"] in ("queued", "running"):
                raise Busy("an enrich job is already active", job_id=job["id"])
            job = {
                "id": uuid.uuid4().hex, "kind": "enrich", "phase": "run",
                "status": "queued", "stage": "queued", "completed": 0,
                "total": None, "current_item": None, "counts": {},
                "started_at": time.time(), "finished_at": None,
                "remaining": None, "result": None, "error": None,
                "error_code": None, "log": collections.deque(maxlen=LOG_LINES),
            }
            self._job = job
            thread = threading.Thread(target=self._job_body, args=(job,), daemon=True)
            self._job_thread = thread
            thread.start()
        return job["id"]

    def op_enrich_cancel(self):
        with self._job_lock:
            job = self._job
            if not job or job["status"] not in ("queued", "running"):
                raise SafetyError("no active enrich job")
            child = self._child
            job["status"] = "cancelled"
            job["error"] = "cancelled by user"
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return {"cancelled": job["id"]}

    def op_job(self, job_id):
        with self._job_lock:
            job = self._job
            if not job or job["id"] != job_id:
                return None
            return self._job_snapshot(job)

    @staticmethod
    def _job_snapshot(job):
        if not job:
            return None
        return {
            "id": job["id"], "kind": job.get("kind", "enrich"),
            "phase": job.get("phase", "run"), "status": job["status"],
            "stage": job["stage"], "completed": job.get("completed", 0),
            "total": job.get("total"), "current_item": job.get("current_item"),
            "counts": dict(job["counts"]), "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"), "remaining": job["remaining"],
            "result": job.get("result"), "error": job["error"],
            "error_code": job.get("error_code"), "log_tail": list(job["log"])[-20:],
        }

    def _run_stage_process(self, argv):
        """Spawn one fixed argv and register it for cancellation.

        The caller holds _job_lock while invoking this method. That makes the
        cancellation check and child registration one atomic boundary from the
        cancel endpoint's perspective.
        """
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=self.repo)
        self._child = proc
        return proc

    def _publish_job_progress(self, job, event):
        if not isinstance(event, dict):
            return
        stage = event.get("stage")
        completed = event.get("completed", 0)
        total = event.get("total")
        current = event.get("current_item")
        counts = event.get("counts", {})
        if (not isinstance(stage, str) or not stage or not isinstance(completed, int)
                or completed < 0 or (total is not None and
                (not isinstance(total, int) or total < 0 or completed > total))
                or (current is not None and not isinstance(current, str))
                or not isinstance(counts, dict)):
            return
        with self._job_lock:
            if job["status"] not in ("queued", "running"):
                return
            job["stage"] = stage
            job["completed"] = completed
            job["total"] = total
            job["current_item"] = current
            job["counts"].update({k: v for k, v in counts.items()
                                   if isinstance(k, str) and isinstance(v, (int, float))})

    def _job_body(self, job):
        stages = (
            ("resolve", [sys.executable, "-u", self.resolve_py, "resolve", "--resume",
                         "--pending", "--progress-json", "--state", self.state], False),
            ("enrich", [sys.executable, "-u", self.resolve_py, "enrich", "--state-lock-held",
                         "--progress-json", "--state", self.state], True),
            ("emit", [sys.executable, "-u", self.index_py, "emit", "--state-lock-held",
                       "--progress-json", "--root", self.root, "--state", self.state], True),
        )
        with self._job_lock:
            if job["status"] == "cancelled":
                return
            job["status"] = "running"
            job["started_at"] = job.get("started_at") or time.time()
        gate_held = False
        state_fh = None
        try:
            for stage, argv, gated in stages:
                with self._job_lock:
                    if job["status"] == "cancelled":
                        return
                    job["stage"] = stage
                    job["completed"] = 0
                    job["total"] = None
                    job["current_item"] = None
                if gated:
                    with self._job_lock:
                        if job["status"] == "cancelled":
                            return
                        job["stage"] = "waiting-for-lock"
                    while not self._gate.acquire(timeout=0.1):
                        with self._job_lock:
                            if job["status"] == "cancelled":
                                return
                    self._gate_owner = f"job:{job['id']}:{stage}"
                    gate_held = True
                    state_fh = open(os.path.join(self.state, "state-write.lock"), "a")
                    while True:
                        with self._job_lock:
                            if job["status"] == "cancelled":
                                return
                        try:
                            fcntl.flock(state_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            time.sleep(0.1)
                    with self._job_lock:
                        if job["status"] == "cancelled":
                            return
                        job["stage"] = stage
                proc = None
                try:
                    with self._job_lock:
                        if job["status"] == "cancelled":
                            return
                        proc = self._runner(argv)
                    for line in proc.stdout:
                        line = line.rstrip("\n")
                        if line.startswith(PROGRESS_PREFIX):
                            try:
                                event = json.loads(line[len(PROGRESS_PREFIX):])
                            except (ValueError, TypeError):
                                continue
                            self._publish_job_progress(job, event)
                        else:
                            with self._job_lock:
                                job["log"].append(line)
                    rc = proc.wait()
                finally:
                    with self._job_lock:
                        if proc is not None and self._child is proc:
                            self._child = None
                    if state_fh is not None:
                        try:
                            fcntl.flock(state_fh.fileno(), fcntl.LOCK_UN)
                        finally:
                            state_fh.close()
                            state_fh = None
                    if gate_held:
                        gate_held = False
                        self._gate_owner = None
                        self._gate.release()
                with self._job_lock:
                    if job["status"] == "cancelled":
                        return
                    if rc != 0:
                        job["status"] = "failed"
                        job["error"] = f"stage {stage} exited {rc}"
                        job["error_code"] = "stage_failed"
                        job["finished_at"] = time.time()
                        return
            with self._job_lock:
                if job["status"] == "cancelled":
                    return
                job["remaining"] = self._pending_count()
                job["status"] = "completed"
                job["finished_at"] = time.time()
        except BaseException as exc:
            with self._job_lock:
                if job["status"] not in ("cancelled", "failed", "completed"):
                    job["status"] = "failed"
                    job["error"] = f"worker crashed: {exc}"
                    job["error_code"] = "worker_error"
                    job["finished_at"] = time.time()
        finally:
            if state_fh is not None:
                try:
                    fcntl.flock(state_fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                state_fh.close()
            if gate_held:
                self._gate_owner = None
                self._gate.release()

    def prepare_restart(self):
        """Atomically close new admissions once all owned work has drained."""
        with self._job_lock:
            active_job = self._job and self._job.get("status") in ("queued", "running")
            active_action = self._action and self._action.get("status") in ("queued", "running")
            if active_job or active_action:
                active = self._job if active_job else self._action
                raise Busy("backend has an active operation",
                           job_id=active.get("id") if active else None)
            self._admissions_closed = True
            return {"prepared": True, "instance_id": self.instance_id}


    def shutdown(self):
        with self._job_lock:
            self._shutting_down = True
            child = self._child
            job = self._job
            worker = self._job_thread
            action_worker = self._action_thread
            if job and job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        # The action owns the mutation gate; release the instance lock only
        # after it has drained, so a new server cannot overlap its writes.
        for thread in (worker, action_worker):
            join = getattr(thread, "join", None)
            if thread is not None and thread is not threading.current_thread() and callable(join):
                join(timeout=None if thread is action_worker else 15)
        if self._instance_lock_fh is not None:
            try:
                fcntl.flock(self._instance_lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._instance_lock_fh.close()
                self._instance_lock_fh = None


# ---------------------------------------------------------------------------



class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "UnityAssetLibrary/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def service(self):
        return self.server.service

    def _port(self):
        return self.server.server_address[1]

    def _send_json(self, obj, status=200):
        body = canonical(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:
            # The request body may not have been drained; keep-alive would
            # poison the connection for the next request.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)


    def log_message(self, fmt, *args):
        pass

    def _reject(self, status, error, **extra):
        self._send_json({"error": error, **extra}, status)

    def _check_boundary(self):
        """Loopback Host, same-origin Origin. Runs before anything else."""
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self._port()}", f"localhost:{self._port()}"}
        if host not in allowed:
            self._reject(403, "bad_host")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {
                f"http://127.0.0.1:{self._port()}",
                f"http://localhost:{self._port()}"}:
            self._reject(403, "cross_origin")
            return False
        return True

    def _read_json(self, schema, optional=()):
        """Exact-key JSON body carrying the capability token. Every deviation
        fails before an operation is even named. `optional` keys may appear;
        keys outside schema+optional never may."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._reject(415, "json_required")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reject(400, "missing_length")
            return None
        if length < 0 or length > MAX_BODY:
            self._reject(413, "body_too_large")
            return None
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._reject(400, "malformed_json")
            return None
        allowed = set(schema) | set(optional)
        if not isinstance(body, dict) or not set(body) <= allowed or not set(schema) <= set(body):
            self._reject(400, "unexpected_keys", expected=sorted(allowed))
            return None
        if not hmac.compare_digest(str(body.get("csrf", "")), self.service.token):
            self._reject(403, "bad_token")
            return None
        return body
    # -- routing ------------------------------------------------------------

    def do_GET(self):
        if not self._check_boundary():
            return
        path = self.path.split("?", 1)[0]
        service = self.service
        try:
            if path == "/api/state":
                self._send_json(service.op_state())
            elif path == "/api/assets":
                self._send_json(service.op_assets())
            elif path == "/api/tags":
                self._send_json(service.op_tags())
            elif path == "/api/favorites":
                self._send_json(service.op_favorites())
            elif path == "/api/preferences":
                self._send_json(service.op_preferences())
            elif path.startswith("/api/action/"):
                snapshot = service.op_action(path[len("/api/action/"):])
                if snapshot is None:
                    self._send_json({"error": "unknown_action"}, 404)
                else:
                    self._send_json({"action": snapshot})
            elif path.startswith("/api/job/"):
                snapshot = service.op_job(path[len("/api/job/"):])
                if snapshot is None:
                    self._send_json({"error": "unknown_job"}, 404)
                else:
                    self._send_json({"job": snapshot})
            else:
                self._send_json({"error": "unknown_route"}, 404)
        except SafetyError as exc:
            self._send_json({"error": "operation_failed", "detail": str(exc)}, 500)

    def do_POST(self):
        if not self._check_boundary():
            return
        path = self.path.split("?", 1)[0]
        service = self.service
        schema_by_path = {
            "/api/resync/plan": (["csrf"], ()),
            "/api/resync/apply": (["plan_hash", "csrf"], ()),
            "/api/cleanup/plan": (["csrf"], ()),
            "/api/cleanup/apply": (["plan_hash", "csrf"], ()),
            "/api/organize/plan": (["csrf"], ()),
            "/api/organize/apply": (["plan_hash", "csrf"], ()),
            "/api/enrich/start": (["csrf"], ()),
            "/api/enrich/cancel": (["csrf"], ()),
            "/api/storage/prepare-restart": (["csrf"], ()),
            "/api/favorites": (["asset_key", "csrf", "favorite"], ()),
            "/api/tags": (["action", "csrf", "tag"], ("asset_key", "new_tag")),
            "/api/preferences": (["csrf", "preferences", "library_root"],
                                 ("only_if_missing",)),
        }
        if path not in schema_by_path:
            self._send_json({"error": "unknown_route"}, 404)
            return
        required, optional = schema_by_path[path]
        body = self._read_json(required, optional)
        if body is None:
            return
        try:
            action_paths = {
                "/api/resync/plan": ("resync", "plan"),
                "/api/resync/apply": ("resync", "apply"),
                "/api/cleanup/plan": ("cleanup", "plan"),
                "/api/cleanup/apply": ("cleanup", "apply"),
                "/api/organize/plan": ("organize", "plan"),
                "/api/organize/apply": ("organize", "apply"),
            }
            if path in action_paths:
                kind, phase = action_paths[path]
                plan_hash_value = body.get("plan_hash")
                self._send_json({"job_id": service.start_action(kind, phase, plan_hash_value)}, 202)
            elif path == "/api/enrich/start":
                self._send_json({"job_id": service.op_enrich_start()}, 202)
            elif path == "/api/enrich/cancel":
                self._send_json(service.op_enrich_cancel())
            elif path == "/api/storage/prepare-restart":
                self._send_json(service.prepare_restart())
            elif path == "/api/favorites":
                asset_key = body.get("asset_key")
                favorite = body.get("favorite")
                if not isinstance(asset_key, str) or not asset_key or not isinstance(favorite, bool):
                    self._reject(400, "bad_request")
                else:
                    self._send_json(service.op_set_favorite(asset_key, favorite))
            elif path == "/api/preferences":
                only_if_missing = body.get("only_if_missing", False)
                if not isinstance(only_if_missing, bool):
                    self._reject(400, "bad_request")
                else:
                    self._send_json(service.op_set_preferences(
                        body.get("preferences"), body.get("library_root"),
                        only_if_missing))
            elif path == "/api/tags":
                self._send_json(service.op_mutate_tags(body))
        except UnknownAsset:
            self._send_json({"error": "unknown_asset"}, 404)
        except ValueError:
            self._reject(400, "bad_request")
        except LibraryMismatch as exc:
            self._send_json({"error": "library_mismatch", "detail": str(exc)}, 409)
        except Busy as exc:
            payload = {"error": "busy"}
            if exc.job_id:
                payload["job_id"] = exc.job_id
            self._send_json(payload, 409)
        except Drift as exc:
            self._send_json({"error": "plan_changed",
                             "plan": exc.plan, "plan_hash": exc.plan_hash}, 409)
        except ov.StaleIndex as exc:
            self._send_json({"error": "stale_index", "detail": str(exc)}, 409)
        except SafetyError as exc:
            self._send_json({"error": "operation_failed", "detail": str(exc),
                             "result": getattr(exc, "result", None)}, 500)
        except OSError as exc:
            self._send_json({"error": "operation_failed", "detail": str(exc)}, 503)



class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, service):
        super().__init__(address, handler)
        self.service = service

def serve(service, port):
    """Blocking serve loop. The lifetime instance lock is held across it."""
    service.acquire_instance_lock()
    try:
        cfg = storage._load_config(service.repo)
        if (not isinstance(cfg, dict)
                or os.path.realpath(os.path.abspath(cfg["vault_root"])) != os.path.realpath(service.root)):
            raise SafetyError("storage configuration changed during server startup")
    except BaseException:
        service.shutdown()
        raise
    try:
        httpd = _Server(("127.0.0.1", port), Handler, service)
    except OSError as exc:
        print(f"server: cannot bind 127.0.0.1:{port} ({exc})")
        service.shutdown()
        return 2
    url = f"http://127.0.0.1:{port}/"
    print(f"unity-asset-library API: {url}  (Ctrl-C to stop)", flush=True)
    stopping = threading.Event()
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def request_shutdown(_signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        # shutdown() must run outside serve_forever()'s thread.
        threading.Thread(target=httpd.shutdown, name="server-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserver: stopping")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        httpd.server_close()
        service.shutdown()
    return 0

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"loopback port (default {DEFAULT_PORT})")
    parser.add_argument("--repo", default=None, help="application workspace")
    parser.add_argument("--instance-id", default=None, help="backend instance identity")
    args = parser.parse_args(argv)
    repo = os.path.realpath(os.path.abspath(args.repo or ia.repo_dir()))
    try:
        storage.recover_startup(repo)
        service = ViewerService(repo=repo, instance_id=args.instance_id)
        return serve(service, args.port)
    except SafetyError as exc:
        print(f"server: {exc}")
        return 2
    except storage.StorageError as exc:
        print(f"server: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
