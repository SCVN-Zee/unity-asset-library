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
import socket
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
        service._scan = lambda root, strict=True, progress=None: self._scan_rows()
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
        service._plan_organize = lambda root, data, scanned=None, progress=None: {
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
        service._apply_organize = lambda *a, **k: self.apply_organize_calls.append((a, k)) or {"moves_applied": 1}
        service._index_update = lambda scanned, progress=None: self.update_calls.append(1) or 0
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
        status, response_headers, raw = self.request("POST", path, body=body, headers=final, **kw)
        result = json.loads(raw) if raw else None
        if status == 202 and path in {"/api/resync/plan", "/api/resync/apply",
                                      "/api/cleanup/plan", "/api/cleanup/apply",
                                      "/api/organize/plan", "/api/organize/apply"}:
            job_id = result["job_id"]
            deadline = time.time() + 5
            while time.time() < deadline:
                _, _, action_raw = self.request("GET", f"/api/action/{job_id}")
                action = json.loads(action_raw)["action"]
                if action["status"] in ("completed", "failed"):
                    if action["status"] == "completed":
                        return 200, response_headers, json.dumps(action["result"]).encode("utf-8")
                    error = action.get("error_code") or "operation_failed"
                    body = {"error": "plan_changed" if error == "plan_changed" else error}
                    if action.get("result") and error == "plan_changed":
                        body.update(action["result"])
                        if "preview" in body:
                            body["plan"] = body.pop("preview")
                    return (409 if error in ("plan_changed", "busy", "stale_index") else 503,
                            response_headers, json.dumps(body).encode("utf-8"))
                time.sleep(0.005)
            raise AssertionError("action did not reach terminal status")
        return status, response_headers, raw

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
        self.assertEqual(body["service"], "unity-asset-library")
        self.assertTrue(body["csrf"])
        self.assertIsNone(body["job"])

    def test_assets_endpoint_returns_authoritative_index(self):
        status, headers, body = self.h.request("GET", "/api/assets")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        # User tags are authoritative and always present after the overlay.
        expected = {"assets": [{"asset_key": "k1", "name": "A", "tags": [], "tag_source": "user",
                                "versions": [{"file": "A.unitypackage", "size_bytes": 4096}]}]}
        self.assertEqual(json.loads(body), expected)

    def test_no_cors_header_anywhere(self):
        _, headers_get, _ = self.h.request("GET", "/api/state")
        status, headers_post, _ = self.h.post("/api/resync/plan", token="wrong")
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
        status, _, data = self.h.post("/api/resync/plan", token=self.h.token,
                                      headers={"Content-Type": "application/json",
                                               "Origin": "https://evil.example"})
        self.assertEqual(status, 403, data)
        self.assertIn(b"cross_origin", data)

    def test_non_json_post_rejected(self):
        status, _, data = self.h.request(
            "POST", "/api/resync/plan", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertIn(b"json_required", data)

    def test_unknown_keys_rejected(self):
        status, _, data = self.h.post("/api/resync/plan", {"csrf": self.h.token, "root": "/x"})
        self.assertEqual(status, 400)
        self.assertIn(b"unexpected_keys", data)

    def test_missing_or_wrong_token_is_403_with_zero_operations(self):
        for token in ("", "wrong"):
            status, _, data = self.h.post("/api/resync/plan", token=token)
            with self.subTest(token=token):
                self.assertEqual(status, 403)
                self.assertIn(b"bad_token", data)
        self.assertEqual(self.h.update_calls, [])

    def test_oversized_body_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=10)
        try:
            payload = b'{"csrf": "' + b"x" * (srv.MAX_BODY + 1) + b'"}'
            conn.request("POST", "/api/resync/plan", body=payload,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
            self.assertEqual(resp.status, 413)
            self.assertIn(b"body_too_large", data)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# resync and cleanup apply
    def test_action_start_returns_202_and_get_is_responsive_while_running(self):
        started = threading.Event()
        release = threading.Event()

        def blocked(progress=None):
            started.set()
            self.assertTrue(release.wait(5))
            if progress:
                progress({"stage": "done", "completed": 1, "total": 1,
                          "current_item": "a.unitypackage", "counts": {"done": 1}})
            return {"preview": {"kind": "resync"}, "plan_hash": "hash"}

        self.h.service.op_resync = blocked
        status, _, raw = self.h.request(
            "POST", "/api/resync/plan", body={"csrf": self.h.token},
            headers={"Content-Type": "application/json"})
        self.assertEqual(status, 202)
        job_id = json.loads(raw)["job_id"]
        self.assertTrue(started.wait(5))
        status, _, raw = self.h.request("GET", f"/api/action/{job_id}")
        self.assertEqual(status, 200)
        running = json.loads(raw)["action"]
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["kind"], "resync")
        status, _, raw = self.h.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["action"]["id"], job_id)
        release.set()
        self.assertTrue(_await(lambda: (self.h.service.op_action(job_id) or {}).get("status")
                               == "completed"))
        done = self.h.service.op_action(job_id)
        self.assertEqual(done["result"]["plan_hash"], "hash")
        self.assertEqual(done["counts"]["done"], 1)
# ---------------------------------------------------------------------------

class TestResyncAndCleanup(ServerHarnessTestCase):

    def cleanup_preview(self):
        before = (len(self.h.update_calls), len(self.h.apply_cleanup_calls))
        status, _, result = self.post("/api/cleanup/plan")
        self.assertEqual((len(self.h.update_calls), len(self.h.apply_cleanup_calls)), before)
        self.assertEqual(status, 200, result)
        return result

    def test_resync_preview_and_apply_real_index_without_disk_deletion(self):
        service = self.h.service
        service._scan = ia.scan
        service._load_assets = service._load_assets_impl
        service._index_update = service._index_update_impl
        path = os.path.join(self.h.state, "assets.json")
        previous = {"assets": [{"versions": [
            {"file": "gone.unitypackage", "size_bytes": 9},
            {"file": "a.unitypackage", "size_bytes": 1}]}]}
        with open(path, "w") as fh:
            json.dump(previous, fh)
        with open(path, "rb") as fh:
            before = fh.read()
        status, _, plan = self.post("/api/resync/plan")
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["preview"]["additions"], [{"path": "b.unitypackage", "size_bytes": 5}])
        self.assertEqual(plan["preview"]["removals"], [{"path": "gone.unitypackage", "size_bytes": 9}])
        self.assertEqual(plan["preview"]["resized"], [{"path": "a.unitypackage", "size_bytes": 3, "previous_size_bytes": 1}])
        self.assertEqual(plan["preview"]["totals"], {"added": 1, "removed": 1, "resized": 1})
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)  # Closing the preview needs no write request.
        self.assertFalse(os.path.exists(os.path.join(self.h.state, "pending-enrichment.json")))
        with mock.patch.object(ia, "output_dir", return_value=self.h.static), \
                mock.patch.object(ia, "scan", side_effect=AssertionError("unreviewed rescan")):
            status, _, result = self.post("/api/resync/apply", {"plan_hash": plan["plan_hash"]})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["state_refreshed"])
        self.assertNotIn("preview", result)
        self.assertEqual(ia.manifest_of(service._load_assets()), {"a.unitypackage": 3, "b.unitypackage": 5})
        self.assertEqual(sorted(os.listdir(self.h.vault)), ["a.unitypackage", "b.unitypackage"])
        self.assertEqual(self.h.apply_cleanup_calls, [])
        _, _, unchanged = self.post("/api/resync/plan")
        self.assertEqual(unchanged["preview"]["totals"], {"added": 0, "removed": 0, "resized": 0})

    def test_resync_drift_requires_new_confirmation(self):
        _, _, plan = self.post("/api/resync/plan")
        self.h._make_archive("c.unitypackage", b"new")
        self.h.service._scan = ia.scan
        status, _, changed = self.post("/api/resync/apply", {"plan_hash": plan["plan_hash"]})
        self.assertEqual(status, 409, changed)
        self.assertEqual(changed["error"], "plan_changed")
        self.assertEqual(self.h.update_calls, [])
        self.assertIn("c.unitypackage", [r["path"] for r in changed["plan"]["additions"]])
        status, _, result = self.post("/api/resync/apply", {"plan_hash": changed["plan_hash"]})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.h.update_calls), 1)

    def test_resync_index_drift_and_first_index(self):
        _, _, plan = self.post("/api/resync/plan")
        self.h.service._load_assets = lambda: {"assets": []}
        status, _, result = self.post("/api/resync/apply", {"plan_hash": plan["plan_hash"]})
        self.assertEqual(status, 409, result)
        self.assertEqual(self.h.update_calls, [])
        self.h.service._load_assets = self.h.service._load_assets_impl
        _, _, first = self.post("/api/resync/plan")
        self.assertEqual(first["preview"]["totals"], {"added": 2, "removed": 0, "resized": 0})


    def test_resync_walk_error_is_clean_503_with_zero_writes(self):
        def boom(root, strict=True):
            raise OSError("walk failed")
        self.h.service._scan = boom
        status, _, body = self.post("/api/resync/plan")
        self.assertEqual(status, 503, body)
        self.assertEqual(self.h.update_calls, [])

    def test_cleanup_apply_happy_path_calls_engine_once(self):
        resync = self.cleanup_preview()
        status, _, body = self.post("/api/cleanup/apply",
                                    {"plan_hash": resync["plan_hash"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.h.apply_cleanup_calls[0][1]["lock_already_held"])
        self.assertTrue(self.h.apply_cleanup_calls[0][1]["state_lock_held"])
        self.assertEqual(len(self.h.apply_cleanup_calls), 1)

    def test_cleanup_apply_wrong_hash_is_409_with_replacement_plan(self):
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": "stale"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "plan_changed")
        self.assertEqual(body["plan"]["kind"], "cleanup")
        self.assertTrue(body["plan_hash"])
        self.assertEqual(self.h.apply_cleanup_calls, [])

    def test_cleanup_apply_inode_change_drifts_mtime_change_does_not(self):
        resync = self.cleanup_preview()
        h1 = resync["plan_hash"]
        # mtime-only churn: rewrite in place, same size
        self.h._make_archive("b.unitypackage", b"xxxxx")
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": h1})
        self.assertEqual(status, 200, body)
        # inode change: recreate at the same size
        os.remove(os.path.join(self.h.vault, "b.unitypackage"))
        self.h._make_archive("b.unitypackage", b"bbbbb")
        resync2 = self.cleanup_preview()
        self.assertNotEqual(resync2["plan_hash"], h1)
        status, _, body = self.post("/api/cleanup/apply", {"plan_hash": h1})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "plan_changed")
        self.assertEqual(len(self.h.apply_cleanup_calls), 1)

    def test_state_write_lock_contention_is_busy_409(self):
        fh = open(os.path.join(self.h.state, "state-write.lock"), "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            status, _, body = self.post("/api/resync/plan")
            self.assertEqual(status, 409)
            self.assertEqual(body["error"], "busy")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        status, _, _ = self.post("/api/resync/plan")
        self.assertEqual(status, 200)

    def test_engine_lock_contention_is_busy_409(self):
        name = hashlib.sha256(
            f"{cv._root_identity(self.h.vault)}".encode()).hexdigest()[:20]
        path = os.path.join(tempfile.gettempdir(), f"unity-asset-cleanup-{name}.lock")
        fh = open(path, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            status, _, body = self.post("/api/resync/plan")
            self.assertEqual(status, 409, body)
            self.assertEqual(body["error"], "busy")
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_manual_gate_hold_is_busy_409(self):
        with self.h.service._gate:
            status, _, body = self.post("/api/resync/plan")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "busy")
        status, _, _ = self.post("/api/resync/plan")
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
            service._index_update = lambda scanned: 0
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
        self.assertTrue(kwargs["state_lock_held"])
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
            return _FakeProc([
                "UL_PROGRESS " + json.dumps({"stage": "resolve", "completed": 0,
                                               "total": 2, "current_item": "a", "counts": {}}),
                "resolve: 2 assets to attempt",
                "[1/2] ok a",
                "UL_PROGRESS " + json.dumps({"stage": "resolve", "completed": 2,
                                               "total": 2, "current_item": "b",
                                               "counts": {"total": 2, "done": 2}}),
                "[2/2] ok b"], rc=0, release=release)
        if len(argv) > 3 and argv[3] == "enrich":
            return _FakeProc([
                "UL_PROGRESS " + json.dumps({"stage": "enrich", "completed": 2,
                                               "total": 2, "current_item": None,
                                               "counts": {"store_eligible": 2}}),
                "enrich: 2/2 store-eligible id-verified (100.0%)"], rc=0)
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
        self.assertEqual(snapshot["counts"], {"total": 2, "done": 2, "store_eligible": 2})
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
        status, _, _ = self.post("/api/resync/plan")
        self.assertEqual(status, 200)
        status, _, body2 = self.post("/api/enrich/start")
        self.assertEqual(status, 409)
        self.assertEqual(body2["error"], "busy")
        self.assertEqual(body2.get("job_id"), job_id)
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

    def test_cancel_exits_while_gate_or_state_lock_remains_held(self):
        service = self.h.service
        for held in ("gate", "state"):
            with self.subTest(held=held):
                calls = []
                service._runner = lambda argv: self._stage_proc(argv, calls)
                state_fh = None
                worker = None
                if held == "gate":
                    service._gate.acquire()
                else:
                    state_fh = open(os.path.join(self.h.state, "state-write.lock"), "a")
                    srv.fcntl.flock(state_fh.fileno(), srv.fcntl.LOCK_EX)
                try:
                    job_id = service.op_enrich_start()
                    worker = service._job_thread
                    self.assertTrue(_await(lambda: service.op_job(job_id)["stage"] == "waiting-for-lock"))
                    service.op_enrich_cancel()
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive(), "cancel must not wait for the external lock")
                    self.assertEqual(service.op_job(job_id)["status"], "cancelled")
                    self.assertEqual(len(calls), 1, "no merge/emit child may start after cancel")
                finally:
                    if held == "gate":
                        service._gate.release()
                    else:
                        srv.fcntl.flock(state_fh.fileno(), srv.fcntl.LOCK_UN)
                        state_fh.close()
                    if worker is not None:
                        worker.join(timeout=2)

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
        self.assertEqual(snapshot["stage"], "queued")

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
        status, _, _ = self.post("/api/resync/plan")
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
        status, _, rbody = self.post("/api/resync/plan")
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
    def test_prepare_restart_rebinds_same_port_with_open_client(self):
        with tempfile.TemporaryDirectory() as repo:
            state = os.path.join(repo, "state")
            root = os.path.join(repo, "vault")
            os.makedirs(state)
            os.makedirs(root)
            with open(os.path.join(root, "x.unitypackage"), "wb") as fh:
                fh.write(b"x")
            with open(os.path.join(repo, "config.json"), "w", encoding="utf-8") as fh:
                json.dump({"vault_root": root, "repo": repo, "output_dir": "."}, fh)
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            script = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "bin", "server.py")

            def start(identity):
                return subprocess.Popen(
                    [sys.executable, script, "--repo", repo, "--instance-id",
                     identity, "--port", str(port)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            first = start("restart-one")
            second = None
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                self.assertIn(str(port), first.stdout.readline())
                client.request("GET", "/api/state")
                state_body = json.loads(client.getresponse().read())
                req = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                req.request("POST", "/api/storage/prepare-restart",
                            body=json.dumps({"csrf": state_body["csrf"]}),
                            headers={"Content-Type": "application/json"})
                self.assertEqual(req.getresponse().status, 200)
                req.close()
                first.terminate()
                first.wait(timeout=10)
                second = start("restart-two")
                self.assertIn(str(port), second.stdout.readline())
                client.close()
                fresh = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                fresh.request("GET", "/api/state")
                self.assertEqual(json.loads(fresh.getresponse().read())["instance_id"],
                                 "restart-two")
                fresh.close()
            finally:
                client.close()
                for proc in (first, second):
                    if proc is not None and proc.stdout is not None:
                        proc.stdout.close()
                for proc in (first, second):
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=10)


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


class FavoritesTest(unittest.TestCase):
    """Persistent favorites: identity-keyed set API, restart/reindex survival,
    idempotence, and strict input handling."""

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.stop()

    def fav(self, asset_key, favorite, token=None):
        return self.h.post("/api/favorites", {"asset_key": asset_key, "favorite": favorite},
                           token=token or self.h.token)

    def get_favorites(self):
        status, _, raw = self.h.request("GET", "/api/favorites")
        return status, json.loads(raw)

    def test_set_get_remove_idempotent(self):
        self.assertEqual(self.get_favorites(), (200, {"favorites": []}))
        status, _, raw = self.fav("k1", True)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"favorites": ["k1"]})
        # Idempotent add and remove.
        status, _, raw = self.fav("k1", True)
        self.assertEqual((status, json.loads(raw)), (200, {"favorites": ["k1"]}))
        status, _, raw = self.fav("k1", False)
        self.assertEqual((status, json.loads(raw)), (200, {"favorites": []}))
        status, _, raw = self.fav("k1", False)
        self.assertEqual((status, json.loads(raw)), (200, {"favorites": []}))
        self.assertEqual(self.get_favorites(), (200, {"favorites": []}))

    def test_persist_across_restart_and_index_replacement(self):
        self.assertEqual(self.fav("k1", True)[0], 200)
        repo = os.path.dirname(self.h.vault)
        self.h.httpd.shutdown()
        self.h.httpd.server_close()
        self.h._thread.join(timeout=5)
        self.h.service.shutdown()
        # Fresh service over the same repo, and a replaced index with a
        # different asset list: favorites follow asset identity, not the index.
        ia.write_atomic(os.path.join(self.h.state, "assets.json"),
                        json.dumps({"assets": [{"asset_key": "k2", "name": "B",
                                                "versions": []}]}))
        second = srv.ViewerService(repo=repo,
                                   config={"vault_root": self.h.vault, "output_dir": self.h.static})
        httpd = srv._Server(("127.0.0.1", 0), srv.Handler, second)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", "/api/state")
            state = json.loads(conn.getresponse().read())
            conn.request("GET", "/api/favorites")
            self.assertEqual(json.loads(conn.getresponse().read()), {"favorites": ["k1"]})
            # The replaced index's key is settable; the old key stays stored.
            conn.request("POST", "/api/favorites",
                         body=json.dumps({"asset_key": "k2", "favorite": True, "csrf": state["csrf"]}),
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            body = json.loads(resp.read())
            self.assertEqual(resp.status, 200)
            self.assertEqual(body, {"favorites": ["k1", "k2"]})
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            second.shutdown()

    def test_unknown_asset_rejected(self):
        status, _, raw = self.fav("missing", True)
        self.assertEqual((status, json.loads(raw)), (404, {"error": "unknown_asset"}))
        self.assertEqual(self.get_favorites(), (200, {"favorites": []}))

    def test_bad_input_and_csrf(self):
        for payload in ({"asset_key": 7, "favorite": True},
                        {"asset_key": "k1", "favorite": "yes"},
                        {"asset_key": "", "favorite": True},
                        {"asset_key": None, "favorite": True}):
            self.assertEqual(self.fav(payload["asset_key"], payload["favorite"])[0], 400)
        self.assertEqual(self.fav("k1", True, token="wrong")[0], 403)
        # Extra/missing keys are rejected by the exact-key schema.
        status, _, raw = self.h.post("/api/favorites", {"asset_key": "k1"})
        self.assertEqual(status, 400)
        self.assertEqual(self.get_favorites(), (200, {"favorites": []}))

    def test_corrupt_store_surfaces_error_and_is_not_overwritten(self):
        path = os.path.join(self.h.state, "favorites.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        status, body = self.get_favorites()
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "operation_failed")
        status, _, raw = self.fav("k1", True)
        self.assertEqual(status, 500)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
