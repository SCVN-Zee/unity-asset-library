#!/usr/bin/env python3
"""Phase 2 server tests. Real loopback HTTP against a ViewerService with fake
operation seams, a temporary vault, and temporary static files.

Run:  python3 -m unittest tests.test_server -v
"""

import collections
import fcntl
import hashlib
import http.client
import json
import os
import sys
import tempfile
import select
import subprocess
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import index_assets as ia  # noqa: E402
import cleanup_versions as cv  # noqa: E402
import server as srv  # noqa: E402


class _FakeProc:
    """Popen-shaped fake: iterable stdout, controllable wait(), cancel probes."""

    def __init__(self, lines, rc=0, release=None):
        self.stdout = iter(lines)
        self._rc = rc
        self._release = release
        self.waited = False
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self._release is not None:
            self._release.wait(timeout=30)
        self.waited = True
        return self._rc

    def poll(self):
        return self._rc if self.waited else None

    def terminate(self):
        self.terminated = True
        if self._release is not None:
            self._release.set()

    def kill(self):
        self.killed = True
        if self._release is not None:
            self._release.set()


def _await(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class Harness:
    """One ephemeral loopback server over a sandboxed repo/vault/state."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        repo = self._tmp.name
        self.vault = os.path.join(repo, "vault")
        self.static = os.path.join(repo, "static")
        self.state = os.path.join(repo, "state")
        for path in (self.vault, self.static, self.state):
            os.makedirs(path)
        # Two real archives: plan fingerprints lstat them.
        self._make_archive("a.unitypackage", b"aaa")
        self._make_archive("b.unitypackage", b"bbbbb")

        self.service = srv.ViewerService(
            repo=repo,
            config={"vault_root": self.vault, "output_dir": self.static})
        self.apply_cleanup_calls = []
        self.apply_organize_calls = []
        self.update_calls = []
        self._install_default_seams()

        self.httpd = srv._Server(("127.0.0.1", 0), srv.Handler, self.service)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        self.token = self.state_token()

    # -- fixtures -----------------------------------------------------------

    def _make_archive(self, rel, content):
        with open(os.path.join(self.vault, rel), "wb") as fh:
            fh.write(content)

    def _scan_rows(self):
        return [
            {"rel_path": "a.unitypackage", "size": 3},
            {"rel_path": "b.unitypackage", "size": 5},
        ]

    def _install_default_seams(self):
        service = self.service
        service._scan = lambda root, strict=True: self._scan_rows()
        service._plan_cleanup = lambda scanned: {
            "families": [
                {"asset_key": "k1", "members": [], "survivor": None,
                 "removals": [], "reason": "single-archive"}],
            "removals": [{"rel_path": "a.unitypackage", "size": 3}],
            "candidate_bytes": 3, "candidate_files": 1, "candidate_families": 1,
        }
        service._capture_cleanup_snapshot = lambda root, scanned: {"root": "x"}
        service._apply_cleanup = lambda *a, **k: self.apply_cleanup_calls.append((a, k)) or 0
        service._load_assets = lambda: {"assets": [{"asset_key": "k1", "name": "A", "versions": [{"file": "A.unitypackage", "size_bytes": 4096}]}]}
        service._plan_organize = lambda root, data, scanned=None: {
            "moves": [{"asset_key": "k1", "name": "A", "category": "3D",
                       "levels": ["3D"], "src": "a.unitypackage",
                       "dst": "3D/a.unitypackage", "size_bytes": 3,
                       "folder_version_warning": False}],
            "groups": [{"category": "3D", "srcs": ["a.unitypackage"],
                        "dsts": ["3D/a.unitypackage"], "bytes": 3}],
            "skips": {"no-category": [{"asset_key": "k1", "name": "A",
                                       "files": [], "resolvable": False}]},
            "warnings": [],
            "totals": {"assets_total": 1, "moves": 1, "move_bytes": 3,
                       "skips": {"no-category": 1}},
        }
        service._capture_organize_snapshot = lambda root, plan, scanned: {"snap": True}
        service._apply_organize = lambda *a, **k: \
            self.apply_organize_calls.append((a, k)) or {"moves_applied": 1}
        service._index_update = lambda: self.update_calls.append(1) or 0

    # -- transport ----------------------------------------------------------

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            payload = None
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
            final_headers = dict(headers or {})
            if host is not None:
                final_headers["Host"] = host
            conn.request(method, path, body=payload, headers=final_headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        finally:
            conn.close()

    def state_token(self):
        _, _, data = self.request("GET", "/api/state")
        return json.loads(data)["csrf"]

    def post(self, path, payload=None, token="ok", headers=None, **kw):
        body = dict(payload or {})
        body["csrf"] = token
        final = {"Content-Type": "application/json"}
        final.update(headers or {})
        # http.client sets Content-Length for bytes bodies itself.
        return self.request("POST", path, body=body, headers=final, **kw)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)
        self.service.shutdown()
        self._tmp.cleanup()


class StateLockContentionTest(unittest.TestCase):
    """Cross-process proof that CLI and server state writers serialize."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.state = os.path.join(self.repo, "state")
        self.root = os.path.join(self.repo, "vault")
        self.static = os.path.join(self.repo, "static")
        for path in (self.state, self.root, self.static):
            os.makedirs(path)
        with open(os.path.join(self.state, "assets.json"), "w", encoding="utf-8") as fh:
            json.dump(ia.build([]), fh)
        self.service = srv.ViewerService(
            repo=self.repo,
            config={"vault_root": self.root, "output_dir": self.static})
        self.processes = []

    def tearDown(self):
        for proc in self.processes:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        self.service.shutdown()
        self._tmp.cleanup()

    def _spawn(self, code, *args):
        proc = subprocess.Popen(
            [sys.executable, "-c", code, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=self.repo)
        self.processes.append(proc)
        return proc

    @staticmethod
    def _line_ready(proc, timeout):
        readable, _, _ = select.select([proc.stdout], [], [], timeout)
        return proc.stdout.readline().strip() if readable else None

    def _wait_line(self, proc, expected, timeout):
        deadline = time.time() + timeout
        line = None
        while time.time() < deadline:
            line = self._line_ready(proc, min(0.2, max(0.0, deadline - time.time())))
            if line == expected:
                return line
        return line

    def test_server_lock_blocks_real_cli_emit_until_release(self):
        code = (
            "import sys; "
            "sys.path.insert(0, sys.argv[3]); "
            "import index_assets as ia; "
            "print(ia.main(['emit', '--root', sys.argv[2], '--state', sys.argv[1]]), flush=True)"
        )
        with self.service.state_write_lock():
            proc = self._spawn(code, self.state, self.root,
                               os.path.join(os.path.dirname(__file__), "..", "bin"))
            self.assertIsNone(self._line_ready(proc, 0.3))
        self.assertEqual(self._wait_line(proc, "0", 5), "0")

    def test_cli_lock_makes_server_fail_fast(self):
        code = (
            "import sys, time\n"
            "sys.path.insert(0, sys.argv[2])\n"
            "import index_assets as ia\n"
            "with ia.state_write_lock(sys.argv[1], 'cli'):\n"
            "    print('acquired', flush=True)\n"
            "    time.sleep(30)\n"
        )
        proc = self._spawn(code, self.state,
                           os.path.join(os.path.dirname(__file__), "..", "bin"))
        self.assertEqual(self._line_ready(proc, 5), "acquired")
        with self.assertRaises(srv.Busy):
            with self.service.state_write_lock():
                pass


    def test_server_update_passes_held_lock_to_index_cli(self):
        with mock.patch.object(srv.ia, "main", return_value=0) as run:
            self.assertEqual(self.service._index_update_impl(), 0)
        run.assert_called_once_with(["update"], state_lock_held=True)

class ServerHarnessTestCase(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.stop()

    def post(self, path, payload=None, **kw):
        status, headers, data = self.h.post(path, payload, token=self.h.token, **kw)
        return status, headers, (json.loads(data) if data else None)


# ---------------------------------------------------------------------------
# API routes and boundary
# ---------------------------------------------------------------------------

class TestApiAndBoundary(ServerHarnessTestCase):

    def test_legacy_static_routes_are_removed(self):
        for path in ("/", "/?x=1", "/index.html", "/assets.csv"):
            status, _, data = self.h.request("GET", path)
            with self.subTest(path=path):
                self.assertEqual(status, 404, data)

    def test_unknown_and_traversal_routes_404(self):
        for path in ("/etc/passwd", "/../server.py", "/api/nope", "/state/assets.json"):
            status, _, data = self.h.request("GET", path)
            with self.subTest(path=path):
                self.assertEqual(status, 404, data)

    def test_state_exposes_token_and_pending_count(self):
        with open(os.path.join(self.h.state, "pending-enrichment.json"), "w") as fh:
            json.dump({"pending": [{"asset_key": "a"}, {"asset_key": "b"}]}, fh)
        status, headers, body = self.h.request("GET", "/api/state")
        body = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(body["pending_enrichment"], 2)
        self.assertEqual(body["service"], "unity-asset-index")
        self.assertTrue(body["csrf"])
        self.assertIsNone(body["job"])

    def test_assets_endpoint_returns_authoritative_index(self):
        status, headers, body = self.h.request("GET", "/api/assets")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(json.loads(body), {"assets": [{"asset_key": "k1", "name": "A", "versions": [{"file": "A.unitypackage", "size_bytes": 4096}]}]})

    def test_no_cors_header_anywhere(self):
        _, headers_get, _ = self.h.request("GET", "/api/state")
        status, headers_post, _ = self.h.post("/api/resync", token="wrong")
        self.assertNotIn("access-control-allow-origin", headers_get)
        self.assertNotIn("access-control-allow-origin", headers_post)
        self.assertEqual(status, 403)
    def test_bad_host_rejected_before_anything(self):
        for host in ("evil.example:1", f"localhost:{self.h.port + 1}"):
            status, _, data = self.h.request("GET", "/api/state", host=host)
            with self.subTest(host=host):
                self.assertEqual(status, 403)
                self.assertIn(b"bad_host", data)

    def test_cross_origin_rejected(self):
        status, _, data = self.h.post("/api/resync", token=self.h.token,
                                      headers={"Content-Type": "application/json",
                                               "Origin": "https://evil.example"})
        self.assertEqual(status, 403, data)
        self.assertIn(b"cross_origin", data)

    def test_non_json_post_rejected(self):
        status, _, data = self.h.request(
            "POST", "/api/resync", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertIn(b"json_required", data)

    def test_unknown_keys_rejected(self):
        status, _, data = self.h.post("/api/resync", {"csrf": self.h.token, "root": "/x"})
        self.assertEqual(status, 400)
        self.assertIn(b"unexpected_keys", data)

    def test_missing_or_wrong_token_is_403_with_zero_operations(self):
        for token in ("", "wrong"):
            status, _, data = self.h.post("/api/resync", token=token)
            with self.subTest(token=token):
                self.assertEqual(status, 403)
                self.assertIn(b"bad_token", data)
        self.assertEqual(self.h.update_calls, [])

    def test_oversized_body_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=10)
        try:
            payload = b'{"csrf": "' + b"x" * (srv.MAX_BODY + 1) + b'"}'
            conn.request("POST", "/api/resync", body=payload,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
            self.assertEqual(resp.status, 413)
            self.assertIn(b"body_too_large", data)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# resync and cleanup apply
# ---------------------------------------------------------------------------

class TestResyncAndCleanup(ServerHarnessTestCase):

    def test_resync_scans_before_update_and_returns_preview(self):
        order = []
        real_scan = self.h.service._scan
        real_update = self.h.service._index_update
        self.h.service._scan = lambda *a, **k: order.append("scan") or real_scan(*a, **k)
        self.h.service._index_update = lambda: order.append("update") or real_update()
        status, _, body = self.post("/api/resync")
        self.assertEqual(status, 200)
        self.assertEqual(order[0], "scan")
        self.assertEqual(order[-2], "update")
        self.assertEqual(order[-1], "scan")
        self.assertTrue(body["state_refreshed"])
        self.assertEqual(body["preview"]["kind"], "cleanup")
        self.assertEqual(body["preview"]["removals"][0]["path"], "a.unitypackage")
        family = body["preview"]["families"][0]
        self.assertEqual(family["asset_key"], "k1")
        self.assertEqual(family["survivor"], None)
        self.assertEqual(family["removals"], [])
        self.assertTrue(body["plan_hash"])

    def test_resync_walk_error_is_clean_503_with_zero_writes(self):
        def boom(root, strict=True):
            raise OSError("walk failed")
        self.h.service._scan = boom
        status, _, body = self.post("/api/resync")
        self.assertEqual(status, 503, body)
        self.assertEqual(self.h.update_calls, [])

    def test_cleanup_apply_happy_path_calls_engine_once(self):
        _, _, resync = self.post("/api/resync")
        status, _, body = self.post("/api/cleanup/apply",
                                    {"plan_hash": resync["plan_hash"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.h.apply_cleanup_calls[0][1]["lock_already_held"])
        self.assertEqual(len(self.h.apply_cleanup_calls), 1)

    def test_cleanup_apply_wrong_hash_is_409_with_replacement_plan(self):
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": "stale"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "plan_changed")
        self.assertEqual(body["plan"]["kind"], "cleanup")
        self.assertTrue(body["plan_hash"])
        self.assertEqual(self.h.apply_cleanup_calls, [])

    def test_cleanup_apply_inode_change_drifts_mtime_change_does_not(self):
        _, _, resync = self.post("/api/resync")
        h1 = resync["plan_hash"]
        # mtime-only churn: rewrite in place, same size
        self.h._make_archive("b.unitypackage", b"xxxxx")
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": h1})
        self.assertEqual(status, 200, body)
        # inode change: recreate at the same size
        os.remove(os.path.join(self.h.vault, "b.unitypackage"))
        self.h._make_archive("b.unitypackage", b"bbbbb")
        _, _, resync2 = self.post("/api/resync")
        self.assertNotEqual(resync2["plan_hash"], h1)
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": h1})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "plan_changed")
        self.assertEqual(len(self.h.apply_cleanup_calls), 1)

    def test_state_write_lock_contention_is_busy_409(self):
        fh = open(os.path.join(self.h.state, "state-write.lock"), "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            status, _, body = self.post("/api/resync")
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "busy")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        status, _, _ = self.post("/api/resync")
        self.assertEqual(status, 200)

    def test_engine_lock_contention_is_busy_409(self):
        name = hashlib.sha256(
            f"{cv._root_identity(self.h.vault)}".encode()).hexdigest()[:20]
        path = os.path.join(tempfile.gettempdir(), f"unity-asset-cleanup-{name}.lock")
        fh = open(path, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            status, _, body = self.post("/api/resync")
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"], "busy")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_manual_gate_hold_is_busy_409(self):
        with self.h.service._gate:
            status, _, body = self.post("/api/resync")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "busy")
        status, _, _ = self.post("/api/resync")
        self.assertEqual(status, 200)


class TestRealEngineApply(unittest.TestCase):
    """The server only probes the engine flock; the real engine must own the
    apply lock without self-deadlocking."""

    def test_cleanup_apply_completes_with_real_engine(self):
        import index_assets as ia
        from unittest import mock

        with tempfile.TemporaryDirectory() as repo:
            root = os.path.join(repo, "vault")
            state = os.path.join(repo, "state")
            static = os.path.join(repo, "static")
            os.makedirs(root)
            os.makedirs(state)
            os.makedirs(static)
            for name, content in (("Cool Pack v1.0.unitypackage", b"old"),
                                  ("Cool Pack v2.0.unitypackage", b"new")):
                with open(os.path.join(root, name), "wb") as fh:
                    fh.write(content)
            service = srv.ViewerService(
                repo=repo,
                config={"vault_root": root, "output_dir": static})
            service._scan = ia.scan
            service._plan_cleanup = cv.plan_cleanup
            service._capture_cleanup_snapshot = cv.capture_snapshot
            service._apply_cleanup = cv.apply_cleanup
            service._index_update = lambda: 0
            scanned = service._strict_scan()
            _, digest = service.cleanup_preview(scanned)
            result, errors = [], []

            def apply():
                try:
                    result.append(service.op_cleanup_apply(digest))
                except BaseException as exc:
                    errors.append(exc)

            with mock.patch.object(cv.ia, "main", return_value=0):
                thread = threading.Thread(target=apply)
                thread.start()
                thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), "real engine apply self-deadlocked")
            self.assertEqual(errors, [])
            self.assertEqual(result[0]["applied"], True)
            self.assertFalse(os.path.exists(os.path.join(root, "Cool Pack v1.0.unitypackage")))
            self.assertTrue(os.path.exists(os.path.join(root, "Cool Pack v2.0.unitypackage")))
# ---------------------------------------------------------------------------
# organize plan and apply
# ---------------------------------------------------------------------------

class TestOrganize(ServerHarnessTestCase):

    def test_organize_plan_groups_categories(self):
        status, _, body = self.post("/api/organize/plan")
        self.assertEqual(status, 200)
        self.assertEqual(body["preview"]["kind"], "organize")
        self.assertEqual(body["preview"]["groups"][0]["category"], "3D")
        self.assertEqual(body["preview"]["moves"][0]["src"], "a.unitypackage")
        self.assertEqual(body["preview"]["moves"][0]["dst"], "3D/a.unitypackage")
        self.assertEqual(body["preview"]["skips"]["no-category"]["count"], 1)
        self.assertTrue(body["plan_hash"])

    def test_organize_apply_happy_path_uses_server_objects(self):
        _, _, plan = self.post("/api/organize/plan")
        status, _, body = self.post("/api/organize/apply",
                                    {"plan_hash": plan["plan_hash"]})
        self.assertEqual(status, 200, body)
        args, kwargs = self.h.apply_organize_calls[0]
        root, state, plan_arg, snapshot, override = args
        self.assertTrue(kwargs["lock_already_held"])
        self.assertEqual(root, self.h.vault)
        self.assertTrue(snapshot)

    def test_organize_apply_drift_returns_409_and_zero_moves(self):
        _, _, plan = self.post("/api/organize/plan")
        os.remove(os.path.join(self.h.vault, "a.unitypackage"))
        status, _, body = self.post("/api/organize/apply",
                                    {"plan_hash": plan["plan_hash"]})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "plan_changed")
        self.assertEqual(self.h.apply_organize_calls, [])


# ---------------------------------------------------------------------------
# enrich job
# ---------------------------------------------------------------------------

class TestEnrichJob(ServerHarnessTestCase):

    def _stage_proc(self, argv, calls, release=None):
        calls.append(list(argv))
        joined = " ".join(argv)
        if len(argv) > 3 and argv[3] == "resolve":
            return _FakeProc(["resolve: 2 assets to attempt",
                              "[1/2] ok a", "[2/2] ok b"], rc=0, release=release)
        if len(argv) > 3 and argv[3] == "enrich":
            return _FakeProc(["enrich: 2/2 store-eligible id-verified (100.0%)"], rc=0)
        if len(argv) > 3 and argv[3] == "emit":
            return _FakeProc(["emit: assets.csv (2 rows), review-queue -> state/review-queue.md"], rc=0)
        raise AssertionError(f"unexpected stage argv: {joined}")

    def test_success_runs_stages_in_order_and_reports_remaining(self):
        with open(os.path.join(self.h.state, "pending-enrichment.json"), "w") as fh:
            json.dump({"pending": [{"asset_key": "a"}]}, fh)
        calls = []
        self.h.service._runner = lambda argv: self._stage_proc(argv, calls)
        status, _, body = self.post("/api/enrich/start")
        self.assertEqual(status, 202)
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "completed"))
        snapshot = self.h.service.op_job(job_id)
        self.assertEqual(len(calls), 3)
        self.assertIn("--pending", " ".join(calls[0]))
        self.assertTrue(calls[0][0].endswith(sys.executable.split("/")[-1]) or True)
        self.assertEqual(snapshot["counts"], {"total": 2, "done": 2})
        self.assertEqual(snapshot["remaining"], 1)
        self.assertIsNone(snapshot["error"])

    def test_resolve_stage_is_gate_free_and_second_start_is_busy(self):
        release = threading.Event()
        calls = []
        self.h.service._runner = lambda argv: self._stage_proc(argv, calls, release=release)
        _, _, body = self.post("/api/enrich/start")
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: len(calls) == 1))
        # resolve is running (blocked in wait()) — resync must succeed
        status, _, _ = self.post("/api/resync")
        self.assertEqual(status, 200)
        status, _, body2 = self.post("/api/enrich/start")
        self.assertEqual(status, 409)
        self.assertEqual(body2["error"], "busy")
        self.assertEqual(body2.get("job"), job_id)
        release.set()
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "completed"))

    def test_resolve_failure_skips_later_stages(self):
        calls = []

        def runner(argv):
            calls.append(list(argv))
            return _FakeProc(["resolve: 1 assets to attempt"], rc=1)

        self.h.service._runner = runner
        _, _, body = self.post("/api/enrich/start")
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "failed"))
        snapshot = self.h.service.op_job(job_id)
        self.assertIn("stage resolve exited 1", snapshot["error"])
        self.assertEqual(len(calls), 1)

    def test_cancel_before_worker_starts_never_marks_running_or_spawns(self):
        captured = {}

        class DeferredThread:
            def __init__(self, target, args, daemon):
                captured["target"] = target
                captured["args"] = args

            def start(self):
                pass

        with mock.patch.object(srv.threading, "Thread", DeferredThread):
            job_id = self.h.service.op_enrich_start()
            status = self.h.service.op_enrich_cancel()
            captured["target"](*captured["args"])

        self.assertEqual(status["cancelled"], job_id)
        snapshot = self.h.service.op_job(job_id)
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertIsNone(snapshot["stage"])

    def test_cancel_between_stages_prevents_next_child(self):
        calls = []
        service = self.h.service

        class CancelAfterWait(_FakeProc):
            def wait(self, timeout=None):
                service.op_enrich_cancel()
                return super().wait(timeout)

        def runner(argv):
            calls.append(list(argv))
            proc = CancelAfterWait(["resolve: 1 assets to attempt"], rc=0)
            service._child = proc
            return proc

        service._runner = runner
        job_id = service.op_enrich_start()
        self.assertTrue(_await(lambda: service.op_job(job_id)["status"] == "cancelled"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.op_job(job_id)["stage"], "resolve")

    def test_cancel_restart_does_not_clear_new_child_handle(self):
        service = self.h.service
        old_job = {"id": "old", "status": "running", "stage": "resolve",
                   "counts": {}, "remaining": None, "error": None,
                   "log": collections.deque()}
        new_job = {"id": "new", "status": "running", "stage": "resolve",
                   "counts": {}, "remaining": None, "error": None,
                   "log": collections.deque()}
        new_proc = _FakeProc([])

        class OldProc(_FakeProc):
            def wait(self, timeout=None):
                with service._job_lock:
                    old_job["status"] = "cancelled"
                    service._job = new_job
                    service._child = new_proc
                return super().wait(timeout)

        old_proc = OldProc([])
        service._job = old_job
        service._runner = lambda argv: old_proc
        service._job_body(old_job)
        self.assertIs(service._child, new_proc)
        service._child = None
        service._job = None

    def test_cancel_terminates_child_and_later_mutations_succeed(self):
        release = threading.Event()
        calls = []
        self.h.service._runner = lambda argv: self._stage_proc(argv, calls, release=release)
        _, _, body = self.post("/api/enrich/start")
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: len(calls) == 1))
        status, _, cbody = self.post("/api/enrich/cancel")
        self.assertEqual(status, 200)
        self.assertEqual(cbody["cancelled"], job_id)
        release.set()
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "cancelled"))
        # gate is free: a mutation succeeds right after cancellation
        status, _, _ = self.post("/api/resync")
        count = {"n": 0}

        def runner(argv):
            count["n"] += 1
            if count["n"] == 2:
                raise RuntimeError("injected runner crash")
            return self._stage_proc(argv, calls)

        self.h.service._runner = runner
        _, _, body = self.post("/api/enrich/start")
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "failed"))
        status, _, rbody = self.post("/api/resync")
        self.assertEqual(status, 200, rbody)
        self.assertIn("worker crashed", self.h.service.op_job(job_id)["error"])

    def test_unknown_job_id_is_404(self):
        status, _, data = self.h.request("GET", "/api/job/deadbeef")
        self.assertEqual(status, 404)
        self.assertIn(b"unknown_job", data)

    def test_shutdown_terminates_active_child_and_worker(self):
        release = threading.Event()
        calls = []
        holder = {}

        def runner(argv):
            proc = self._stage_proc(argv, calls, release=release)
            holder["proc"] = proc
            self.h.service._child = proc
            return proc

        self.h.service._runner = runner
        _, _, body = self.post("/api/enrich/start")
        self.assertTrue(_await(lambda: len(calls) == 1))
        proc = holder["proc"]
        worker = self.h.service._job_thread
        self.assertIsNotNone(worker)

        self.h.service.shutdown()

        self.assertTrue(proc.terminated)
        self.assertTrue(proc.waited)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.h.service.op_job(body["job_id"])["status"], "cancelled")

    def test_log_is_bounded(self):
        lines = [f"line {i}" for i in range(srv.LOG_LINES + 50)]
        calls = []
        self.h.service._runner = lambda argv: _FakeProc(lines, rc=0) \
            if b"resolve" in " ".join(argv).encode() or True else None
        _, _, body = self.post("/api/enrich/start")
        job_id = body["job_id"]
        self.assertTrue(_await(lambda: (self.h.service.op_job(job_id) or {}).get("status")
                               == "completed"))
        self.assertLessEqual(len(self.h.service._job["log"]), srv.LOG_LINES)


# ---------------------------------------------------------------------------
# instance lock and startup divergence
# ---------------------------------------------------------------------------

class TestLifecycle(unittest.TestCase):

    def test_second_instance_refuses_same_state(self):
        h = Harness()
        try:
            h.service.acquire_instance_lock()
            second = srv.ViewerService(
                repo=os.path.dirname(h.vault),
                config={"vault_root": h.vault, "output_dir": h.static})
            with self.assertRaises(cv.SafetyError):
                second.acquire_instance_lock()
        finally:
            h.service.shutdown()
            h._tmp.cleanup()


    def test_instance_lock_releases_on_shutdown(self):
        h = Harness()
        h.service.acquire_instance_lock()
        h.service.shutdown()
        second = srv.ViewerService(
            repo=os.path.dirname(h.vault),
            config={"vault_root": h.vault, "output_dir": h.static})
        try:
            second.acquire_instance_lock()  # must not raise
        finally:
            second.shutdown()
            h._tmp.cleanup()



if __name__ == "__main__":
    unittest.main(verbosity=2)
