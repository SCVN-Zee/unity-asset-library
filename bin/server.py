#!/usr/bin/env python3
"""Loopback viewer server for the Unity asset index.

Serves the React/Electron app's fixed JSON API:

    GET  /api/state          pending count, capability token, latest job summary
    POST /api/resync/plan    read-only index change preview
    POST /api/resync/apply   confirm index changes, then preview cleanup
    POST /api/cleanup/apply  confirm a cleanup preview by plan_hash
    POST /api/organize/plan  category-folder plan from the organize engine
    POST /api/organize/apply confirm an organize preview by plan_hash
    POST /api/enrich/start   pending-only resolve -> enrich -> emit background job
    POST /api/enrich/cancel  terminate the job's child process
    GET  /api/job/<id>       job status, stage, progress, bounded log tail

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
import subprocess
import sys
import tempfile
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402
import organize_versions as ov  # noqa: E402

SafetyError = cv.SafetyError

MAX_BODY = 16 * 1024
LOG_LINES = 200
DEFAULT_PORT = 8765

_TOTAL_RE = re.compile(r"resolve: (\d+) assets to attempt")
_STEP_RE = re.compile(r"\[(\d+)/(\d+)\]")


class Busy(Exception):
    """A mutation was refused because a gate-holding operation is active."""

    def __init__(self, detail, job_id=None):
        super().__init__(detail)
        self.job_id = job_id


class Drift(Exception):
    """The confirmed plan_hash no longer matches the current filesystem."""

    def __init__(self, plan, plan_hash_value):
        super().__init__("plan changed")
        self.plan = plan
        self.plan_hash = plan_hash_value


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

    def __init__(self, repo=None, config=None):
        self.repo = repo or ia.repo_dir()
        cfg = config if config is not None else ia.load_config()
        self.root = os.path.abspath(cfg["vault_root"])
        self.state = os.path.join(self.repo, "state")
        self.bin_dir = os.path.join(self.repo, "bin")
        self.resolve_py = os.path.join(self.bin_dir, "resolve_store.py")
        self.index_py = os.path.join(self.bin_dir, "index_assets.py")
        self.token = secrets.token_hex(32)

        self._gate = threading.Lock()
        self._gate_owner = None
        self._job_lock = threading.Lock()
        self._job = None
        self._child = None
        self._instance_lock_fh = None
        self._job_thread = None
        # seams (tests substitute these)
        self._scan = ia.scan
        self._plan_cleanup = cv.plan_cleanup
        self._capture_cleanup_snapshot = cv.capture_snapshot
        self._apply_cleanup = cv.apply_cleanup
        self._load_assets = self._load_assets_impl
        self._plan_organize = ov.plan_organize
        self._capture_organize_snapshot = ov.capture_snapshot
        self._apply_organize = ov.apply_organize
        self._index_update = self._index_update_impl
        self._runner = self._run_stage_process

    # -- state readers ------------------------------------------------------

    def _load_assets_impl(self):
        with open(os.path.join(self.state, "assets.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def _index_update_impl(self, scanned):
        return ia.main(["update", "--root", self.root, "--state", self.state],
                       state_lock_held=True, scanned=scanned)

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
    # -- plan shaping -------------------------------------------------------

    def cleanup_preview(self, scanned):
        plan = self._plan_cleanup(scanned)
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
        digest = plan_hash("cleanup", preview, self.root, scanned)
        return preview, digest

    def organize_preview(self, data, scanned):
        plan = self._plan_organize(self.root, data, scanned)
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
        digest = plan_hash("organize", preview, self.root, scanned,
                           extra={"destinations_free": destinations_free})
        return preview, digest, plan

    # -- operations ---------------------------------------------------------

    def _strict_scan(self):
        """Strict vault scan; walk errors surface as clean failures before any
        write happens."""
        return self._scan(self.root, strict=True)

    def op_state(self):
        with self._job_lock:
            job = self._job_snapshot(self._job)
        return {
            "service": "unity-asset-index",
            "api_version": 2,  # Preview/apply resync contract; bump on breaking API changes.
            "ready": True,
            "pending_enrichment": self._pending_count(),
            "job": job,
            "csrf": self.token,
        }

    def op_assets(self):
        return self._load_assets()

    def resync_preview(self, scanned):
        try:
            previous = ia.manifest_of(self._load_assets())
        except FileNotFoundError:
            previous = {}
        except ValueError as exc:
            raise SafetyError("The index is unreadable; resync cannot safely preview changes.") from exc
        current = {row["rel_path"]: row["size"] for row in scanned}
        # First sync previews all files, unlike the historical changelog baseline.
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
        return preview, plan_hash("resync", preview, self.root, scanned, extra=previous)

    def op_resync(self):
        """Preview index changes without changing the index or vault."""
        with self.mutation_gate("resync"), self.state_write_lock(), \
                self.engine_lock_attempt():
            preview, digest = self.resync_preview(self._strict_scan())
        return {"preview": preview, "plan_hash": digest}

    def op_resync_apply(self, plan_hash_value):
        with self.mutation_gate("resync-apply"), self.state_write_lock(), \
                self.engine_lock_attempt():
            scanned = self._strict_scan()
            preview, digest = self.resync_preview(scanned)
            if not hmac.compare_digest(digest, plan_hash_value):
                raise Drift(preview, digest)
            # Apply the validated scan, never a second unreviewed scan.
            update_result = self._index_update(scanned)
            if update_result != 0:
                raise SafetyError(f"index update exited {update_result}")
            try:
                preview, digest = self.cleanup_preview(self._strict_scan())
            except (OSError, SafetyError) as exc:
                return {"state_refreshed": True, "cleanup_error": str(exc)}
        return {"preview": preview, "plan_hash": digest, "state_refreshed": True}

    def op_cleanup_apply(self, plan_hash_value):
        with self.mutation_gate("cleanup-apply"), self.state_write_lock():
            with self.engine_lock_attempt():
                scanned = self._strict_scan()
                try:
                    preview, digest = self.cleanup_preview(scanned)
                except SafetyError as exc:
                    # An archive vanished or turned unreadable: the confirmed plan
                    # can no longer be verified — that is drift, not a server error.
                    raise Drift({"kind": "cleanup", "unavailable": str(exc)}, "") from exc
                if not hmac.compare_digest(digest, plan_hash_value):
                    raise Drift(preview, digest)
                snapshot = self._capture_cleanup_snapshot(self.root, scanned)
                plan = self._plan_cleanup(scanned)
                self._apply_cleanup(self.root, self.state, scanned, plan, False, snapshot,
                                    lock_already_held=True)
        return {"applied": True, "state_refreshed": True}

    def op_organize_plan(self):
        # Read-only planning is gate-free: it can never conflict with the
        # hours-long resolve stage or a running viewer request.
        scanned = self._strict_scan()
        data = self._load_assets()
        preview, digest, _ = self.organize_preview(data, scanned)
        return {"preview": preview, "plan_hash": digest}

    def op_organize_apply(self, plan_hash_value):
        with self.mutation_gate("organize-apply"), self.state_write_lock():
            with self.engine_lock_attempt():
                scanned = self._strict_scan()
                data = self._load_assets()
                try:
                    preview, digest, plan = self.organize_preview(data, scanned)
                except SafetyError as exc:
                    raise Drift({"kind": "organize", "unavailable": str(exc)}, "") from exc
                if not hmac.compare_digest(digest, plan_hash_value):
                    raise Drift(preview, digest)
                snapshot = self._capture_organize_snapshot(self.root, plan, scanned)
                report = self._apply_organize(self.root, self.state, plan, snapshot, False,
                                              lock_already_held=True)
        return {"applied": True, "report": report}

    # -- enrich job ---------------------------------------------------------

    def op_enrich_start(self):
        with self._job_lock:
            job = self._job
            if job and job["status"] in ("queued", "running"):
                raise Busy("an enrich job is already active", job_id=job["id"])
            job = {
                "id": uuid.uuid4().hex,
                "status": "queued",
                "stage": None,
                "counts": {},
                "remaining": None,
                "error": None,
                "log": collections.deque(maxlen=LOG_LINES),
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
            "id": job["id"],
            "status": job["status"],
            "stage": job["stage"],
            "counts": dict(job["counts"]),
            "remaining": job["remaining"],
            "error": job["error"],
            "log_tail": list(job["log"])[-20:],
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

    def _job_body(self, job):
        """resolve (gate-free) -> enrich (gate + state lock) -> emit (gate + state
        lock). Any nonzero stage fails the job and skips later stages. The
        watchdog finally guarantees a terminal status and lock release on every
        exit path, including a worker crash between stages."""
        stages = (
            ("resolve", [sys.executable, "-u", self.resolve_py,
                         "resolve", "--resume", "--pending"], False),
            ("enrich", [sys.executable, "-u", self.resolve_py, "enrich",
                         "--state-lock-held"], True),
            ("emit", [sys.executable, "-u", self.index_py, "emit",
                       "--state-lock-held"], True),
        )
        with self._job_lock:
            if job["status"] == "cancelled":
                return
            job["status"] = "running"
        gate_held = False
        state_fh = None
        try:
            for stage, argv, gated in stages:
                with self._job_lock:
                    if job["status"] == "cancelled":
                        return
                    job["stage"] = stage
                if gated:
                    # Blocking is safe in the worker: a concurrent Resync merely
                    # delays enrich/emit briefly; the reverse never happens.
                    self._gate.acquire()
                    self._gate_owner = f"job:{job['id']}:{stage}"
                    gate_held = True
                    state_fh = open(os.path.join(self.state, "state-write.lock"), "a")
                    fcntl.flock(state_fh.fileno(), fcntl.LOCK_EX)
                proc = None
                try:
                    # Keep the cancellation guard and the default Popen call under
                    # one job lock. A cancel request therefore either observes a
                    # queued stage, or observes its registered child.
                    with self._job_lock:
                        if job["status"] == "cancelled":
                            return
                        proc = self._runner(argv)
                    for line in proc.stdout:
                        line = line.rstrip("\n")
                        job["log"].append(line)
                        m = _TOTAL_RE.search(line)
                        if m:
                            job["counts"] = {"total": int(m.group(1)), "done": 0}
                        m = _STEP_RE.search(line)
                        if m and job["counts"]:
                            job["counts"]["done"] = int(m.group(1))
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
                        return
            with self._job_lock:
                if job["status"] == "cancelled":
                    return
                job["remaining"] = self._pending_count()
                job["status"] = "completed"
        except BaseException as exc:
            with self._job_lock:
                if job["status"] not in ("cancelled", "failed", "completed"):
                    job["status"] = "failed"
                    job["error"] = f"worker crashed: {exc}"
        finally:
            with self._job_lock:
                if job["status"] in ("queued", "running"):
                    job["status"] = "failed"
                    job["error"] = job["error"] or "worker exited without a terminal transition"
            if state_fh is not None:
                try:
                    fcntl.flock(state_fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                state_fh.close()
            if gate_held:
                self._gate_owner = None
                self._gate.release()
    # -- lifecycle ----------------------------------------------------------

    def shutdown(self):
        with self._job_lock:
            child = self._child
            job = self._job
            worker = self._job_thread
            if job and job["status"] in ("queued", "running"):
                job["status"] = "cancelled"
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        join = getattr(worker, "join", None)
        if worker is not None and worker is not threading.current_thread() and callable(join):
            join(timeout=15)
        if self._instance_lock_fh is not None:
            try:
                fcntl.flock(self._instance_lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._instance_lock_fh.close()
                self._instance_lock_fh = None


# ---------------------------------------------------------------------------



class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "UnityAssetIndex/1.0"
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

    def _read_json(self, schema):
        """Exact-key JSON body carrying the capability token. Every deviation
        fails before an operation is even named."""
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
        if not isinstance(body, dict) or set(body) != set(schema):
            self._reject(400, "unexpected_keys", expected=sorted(schema))
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
            "/api/resync/plan": ["csrf"],
            "/api/resync/apply": ["plan_hash", "csrf"],
            "/api/cleanup/apply": ["plan_hash", "csrf"],
            "/api/organize/plan": ["csrf"],
            "/api/organize/apply": ["plan_hash", "csrf"],
            "/api/enrich/start": ["csrf"],
            "/api/enrich/cancel": ["csrf"],
        }
        if path not in schema_by_path:
            self._send_json({"error": "unknown_route"}, 404)
            return
        body = self._read_json(schema_by_path[path])
        if body is None:
            return
        try:
            if path == "/api/resync/plan":
                self._send_json(service.op_resync())
            elif path == "/api/resync/apply":
                self._send_json(service.op_resync_apply(body["plan_hash"]))
            elif path == "/api/cleanup/apply":
                self._send_json(service.op_cleanup_apply(body["plan_hash"]))
            elif path == "/api/organize/plan":
                self._send_json(service.op_organize_plan())
            elif path == "/api/organize/apply":
                self._send_json(service.op_organize_apply(body["plan_hash"]))
            elif path == "/api/enrich/start":
                self._send_json({"job_id": service.op_enrich_start()}, 202)
            elif path == "/api/enrich/cancel":
                self._send_json(service.op_enrich_cancel())
        except Busy as exc:
            payload = {"error": "busy"}
            if exc.job_id:
                payload["job"] = exc.job_id
            self._send_json(payload, 409)
        except Drift as exc:
            self._send_json({"error": "plan_changed",
                             "plan": exc.plan, "plan_hash": exc.plan_hash}, 409)
        except ov.StaleIndex as exc:
            self._send_json({"error": "stale_index", "detail": str(exc)}, 409)
        except SafetyError as exc:
            self._send_json({"error": "operation_failed", "detail": str(exc)}, 500)
        except OSError as exc:
            self._send_json({"error": "operation_failed", "detail": str(exc)}, 503)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, service):
        super().__init__(address, handler)
        self.service = service

def serve(service, port):
    """Blocking serve loop. The lifetime instance lock is held across it."""
    service.acquire_instance_lock()
    try:
        httpd = _Server(("127.0.0.1", port), Handler, service)
    except OSError as exc:
        print(f"server: cannot bind 127.0.0.1:{port} ({exc})")
        service.shutdown()
        return 2
    url = f"http://127.0.0.1:{port}/"
    print(f"unity-asset-index API: {url}  (Ctrl-C to stop)")
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
    args = parser.parse_args(argv)
    service = ViewerService()
    try:
        return serve(service, args.port)
    except SafetyError as exc:
        print(f"server: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
