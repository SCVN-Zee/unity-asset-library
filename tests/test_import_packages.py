"""Import safety contracts; real Editor imports are exercised separately."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import import_packages as ip


class FakeProc:
    def __init__(self, returncode=0):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ual-import-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "library"
        self.project = self.base / "Unity project"
        self.state = self.root / ".data"
        for directory in (self.root, self.state, self.project / "Assets", self.project / "ProjectSettings"):
            directory.mkdir(parents=True, exist_ok=True)
        (self.project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.16f1\n")
        self.editor = self.base / "Editor.app/Contents/MacOS/Unity"
        self.editor.parent.mkdir(parents=True)
        self.editor.touch()
        self.editor.chmod(0o755)
        self.rows = [{"asset_key": name, "file": name + ".unitypackage"} for name in ("first", "second", "third")]
        for row in self.rows:
            (self.root / row["file"]).write_bytes(b"archive fixture " + row["asset_key"].encode() * 8)
        (self.state / "assets.json").write_text(json.dumps({"assets": [{"asset_key": row["asset_key"], "versions": [{"file": row["file"]}]} for row in self.rows]}))
        self.request = {"id": "test-job", "repo": str(self.base), "root": str(self.root), "project": str(self.project),
                        "mode": "closed", "packages": self.rows, "cancel_file": str(self.base / "cancel")}
        self.running = []
        self.popen_calls = []
        def discovery(args):
            if args[:2] == ["projects", "list"]:
                return []
            if args[:2] == ["editors", "running"]:
                return {"instances": self.running}
            if args == ["editors", "--installed", "--json"]:
                return [{"version": "2022.3.16f1", "location": str(self.base / "Editor.app")}]
            raise AssertionError(args)
        self.discovery = patch.object(ip, "run_unity_json", side_effect=discovery)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def run_queue(self):
        return ip.run_import(ip.validate_request(self.request))

    def batch_json(self):
        return json.loads((self.project / "Library/UALImport/batch.json").read_text())

    def write_receipts(self, receipts):
        receipts_dir = self.project / "Library/UALImport/batch-receipts"
        for index, receipt in receipts.items():
            if receipt["status"] == "imported":
                staged = Path(self.batch_json()["packages"][index]["package"])
                deadline = time.monotonic() + 10
                while not staged.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(staged.read_bytes(), (self.root / self.request["packages"][index]["file"]).read_bytes())
            (receipts_dir / ("%d.json" % index)).write_text(json.dumps(receipt))

    def fake_unity(self, action=None):
        """Simulate one closed Editor session: imports may write receipts."""
        def popen(command, **kwargs):
            self.popen_calls.append(command)
            if action:
                action()
            return FakeProc(0)
        return patch.object(ip.subprocess, "Popen", side_effect=popen)

    def live(self):
        ip.install_bridge(str(self.project))
        self.running = [{"projectPath": str(self.project), "pid": os.getpid(), "unityVersion": "2022.3.16f1"}]
        self.request["mode"] = "live"
        self.mailbox = self.project / "Library/UALImport"
        self.mailbox.mkdir(parents=True)
        self.heartbeat = {"pid": os.getpid(), "bridge_version": "1", "status": "ready", "updated": datetime.now(timezone.utc).isoformat()}
        (self.mailbox / "heartbeat.json").write_text(json.dumps(self.heartbeat))

    def test_rejects_malformed_requests_and_archive_escape(self):
        for request in (42, [], {**self.request, "root": 5}, {**self.request, "cancel_file": False}, {**self.request, "packages": [None]}):
            with self.subTest(request=request), self.assertRaises(ip.RequestError):
                ip.validate_request(request)
        outside = self.base / "outside.unitypackage"
        outside.write_bytes(b"must not import")
        (self.root / "escape.unitypackage").symlink_to(outside)
        for file in ("../outside.unitypackage", "escape.unitypackage", str(outside)):
            with self.subTest(file=file), self.assertRaises(ip.RequestError):
                ip.validate_request({**self.request, "packages": [{"asset_key": "first", "file": file}]})

    def test_closed_batch_imports_from_one_session_with_staged_copies(self):
        with self.fake_unity(lambda: self.write_receipts(
                {i: {"index": i, "status": "imported", "error": None} for i in range(3)})):
            result = self.run_queue()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([row["status"] for row in result["results"]], ["imported"] * 3)
        self.assertEqual(len(self.popen_calls), 1, "one Unity session per batch")
        self.assertIn("-executeMethod", self.popen_calls[0])
        batch = self.batch_json()
        staged_dir = os.path.dirname(batch["packages"][0]["package"])
        self.assertNotIn(str(self.root), staged_dir, "staging must live in OS temp, not the synced library")
        for row in result["results"]:
            self.assertGreater(row["bytes_total"], 0)
            self.assertEqual(row["bytes_completed"], row["bytes_total"])
        self.assertFalse(os.path.exists(staged_dir), "staging is cleaned after the batch")

    def test_first_failure_stops_ordered_imports(self):
        with self.fake_unity(lambda: self.write_receipts(
                {0: {"index": 0, "status": "imported", "error": None},
                 1: {"index": 1, "status": "failed", "error": "invalid archive"}})):
            result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "failed", "cancelled"])
        self.assertEqual(result["results"][1]["error"], "invalid archive")
        self.assertEqual(len(self.popen_calls), 1)

    def test_stop_waits_for_current_package_and_skips_rest(self):
        def importing():
            Path(self.request["cancel_file"]).touch()
            self.write_receipts({0: {"index": 0, "status": "imported", "error": None}})
        with self.fake_unity(importing):
            result = self.run_queue()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "cancelled", "cancelled"])

    def test_window_never_stages_more_than_three_ahead(self):
        self.request["packages"] = [{"asset_key": "p%d" % n, "file": "p%d.unitypackage" % n} for n in range(5)]
        (self.state / "assets.json").write_text(json.dumps({"assets": [
            {"asset_key": row["asset_key"], "versions": [{"file": row["file"]}]} for row in self.request["packages"]]}))
        for row in self.request["packages"]:
            (self.root / row["file"]).write_bytes(b"fixture")
        spawned = []

        class HangingProcess:
            def __init__(self, target=None, args=(), daemon=None):
                self.args = args
            def start(self):
                spawned.append(self)
            def is_alive(self):
                return True
            def join(self, timeout=None):
                pass
            def terminate(self):
                pass
            def kill(self):
                pass
        window_filled = threading.Event()
        def popen(command, **kwargs):
            # The session only starts once the window holds STAGE_WINDOW packages;
            # give the scheduler passes time to overfill if the bound were broken.
            self.assertTrue(window_filled.wait(10), "window never filled")
            time.sleep(0.7)
            self.assertLessEqual(len(spawned), ip.STAGE_WINDOW)
            return FakeProc(0)
        def fill():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(spawned) < ip.STAGE_WINDOW:
                time.sleep(0.02)
            window_filled.set()
        thread = threading.Thread(target=fill)
        thread.start()
        try:
            with patch.object(ip.multiprocessing, "Process", HangingProcess):
                with patch.object(ip.subprocess, "Popen", side_effect=popen):
                    self.run_queue()
        finally:
            thread.join()
        self.assertEqual(len(spawned), ip.STAGE_WINDOW, "exactly the window is staged, nothing more")

    def test_future_preparation_failure_stops_the_batch(self):
        def popen(command, **kwargs):
            # The future package's staging failure arrives FIRST, while the earlier
            # imports are still unresolved: ordered adoption must wait its turn.
            self.write_receipts({2: {"index": 2, "status": "failed", "error": "cloud read failed"}})
            time.sleep(1.2)
            self.write_receipts({0: {"index": 0, "status": "imported", "error": None}})
            time.sleep(0.2)
            self.write_receipts({1: {"index": 1, "status": "imported", "error": None}})
            return FakeProc(0)
        real_process = ip.multiprocessing.Process
        class FailedProcess:
            def __init__(self, result):
                self.result = result
            def start(self):
                Path(self.result).write_text(json.dumps({"ok": False, "error": "cloud read failed", "bytes_total": None}))
            def join(self, timeout=None):
                pass
            def is_alive(self):
                return False
        def process(target, args, daemon):
            if "0002-" in os.path.basename(args[1]):
                return FailedProcess(args[2])
            return real_process(target=target, args=args, daemon=daemon)
        with patch.object(ip.multiprocessing, "Process", side_effect=process):
            with patch.object(ip.subprocess, "Popen", side_effect=popen):
                result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "imported", "failed"])
        self.assertEqual(result["results"][2]["error"], "cloud read failed")

    def test_dead_copy_worker_fails_its_package(self):
        class DeadProcess:
            def __init__(self, target=None, args=(), daemon=None):
                self.args = args
            def start(self):
                pass
            def is_alive(self):
                return False
            def join(self, timeout=None):
                pass
        def await_failure():
            # Unity consumes the staging receipt before exiting; an immediate
            # fake exit races the scheduler and tests Editor death instead.
            receipt = self.project / "Library/UALImport/batch-receipts/0.json"
            deadline = time.monotonic() + 10
            while not receipt.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(receipt.exists(), "dead worker did not produce a failure receipt")
        with patch.object(ip.multiprocessing, "Process", DeadProcess):
            with self.fake_unity(await_failure):
                result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        self.assertIn("copy worker died", result["results"][0]["error"])
        self.assertEqual(len(self.popen_calls), 1, "the session still runs so the runner adopts the failure")

    def test_cancel_terminates_blocked_reads_and_cleans_staging(self):
        class BlockedProcess:
            def __init__(self, target=None, args=(), daemon=None):
                self.args = args
                self.started = threading.Event()
            def start(self):
                self.started.set()
            def is_alive(self):
                return True
            def join(self, timeout=None):
                pass
            def terminate(self):
                pass
            def kill(self):
                pass
        processes = []
        def factory(target=None, args=(), daemon=None):
            proc = BlockedProcess(target=target, args=args, daemon=daemon)
            processes.append(proc)
            return proc
        def popen(command, **kwargs):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not processes:
                time.sleep(0.02)
            processes[0].started.wait(5)
            Path(self.request["cancel_file"]).touch()
            return FakeProc(0)
        staging_dir = {}
        original = ip._Preparation.staged_path
        def staged_path(self, i):
            path = original(self, i)
            staging_dir["path"] = os.path.dirname(path)
            return path
        with patch.object(ip.multiprocessing, "Process", factory):
            with patch.object(ip.subprocess, "Popen", side_effect=popen):
                with patch.object(ip._Preparation, "staged_path", staged_path):
                    result = self.run_queue()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([row["status"] for row in result["results"]], ["cancelled"] * 3)
        self.assertTrue(staging_dir.get("path"))
        self.assertFalse(os.path.exists(staging_dir["path"]), "partial staging is cleaned after cancellation")

    def test_stage_copy_rejects_short_copy_and_source_change(self):
        src = self.base / "src.unitypackage"
        src.write_bytes(b"x" * 4096)
        dest = self.base / "dest.unitypackage"
        real_read = os.read
        state = {"calls": 0}
        def half_read(fd, size):
            state["calls"] += 1
            return real_read(fd, 2048) if state["calls"] == 1 else b""
        with patch.object(ip.os, "read", half_read):
            ip._stage_copy(str(src), str(dest), str(self.base / "r1.json"))
        receipt = json.loads((self.base / "r1.json").read_text())
        self.assertFalse(receipt["ok"])
        self.assertIn("short copy", receipt["error"])
        self.assertFalse(dest.exists())
        before = src.stat()
        replaced = False
        def swap_source(fd, size):
            nonlocal replaced
            data = real_read(fd, size)
            if data and not replaced:
                replacement = self.base / "replacement"
                replacement.write_bytes(b"y" * before.st_size)
                os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                os.replace(replacement, src)
                replaced = True
            return data
        with patch.object(ip.os, "read", swap_source):
            ip._stage_copy(str(src), str(dest), str(self.base / "r2.json"))
        receipt = json.loads((self.base / "r2.json").read_text())
        self.assertFalse(receipt["ok"])
        self.assertIn("source changed", receipt["error"])
        self.assertFalse(dest.exists())
        ip._stage_copy(str(src), str(dest), str(self.base / "r3.json"))
        receipt = json.loads((self.base / "r3.json").read_text())
        self.assertTrue(receipt["ok"], receipt)
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), src.read_bytes())

    def test_runner_cleanup_spares_foreign_files(self):
        dest_dir = self.project / "Assets" / ip.RUNNER_SUBDIR
        dest_dir.mkdir(parents=True)
        (dest_dir / ip.RUNNER_NAME).write_text("stale")
        (dest_dir / "UserData.cs").write_text("user script")
        folder_meta = Path(str(dest_dir) + ".meta")
        folder_meta.write_text("guid: user-folder")
        ip._remove_runner(str(self.project))
        self.assertFalse((dest_dir / ip.RUNNER_NAME).exists())
        self.assertTrue((dest_dir / "UserData.cs").exists(), "only owned templates are removed")
        self.assertEqual(folder_meta.read_text(), "guid: user-folder")
        (dest_dir / "UserData.cs").unlink()
        ip._remove_runner(str(self.project))
        self.assertFalse(dest_dir.exists())

    def test_partial_runner_deployment_leaves_no_owned_files(self):
        write_atomic = ip.ia.write_atomic
        def fail_assembly(path, content):
            if path.endswith(ip.RUNNER_ASMDEF):
                raise OSError("disk full")
            return write_atomic(path, content)
        with patch.object(ip.ia, "write_atomic", side_effect=fail_assembly):
            with self.assertRaises(OSError):
                ip._deploy_runner(str(self.project))
        self.assertFalse((self.project / "Assets" / ip.RUNNER_SUBDIR).exists())

    def test_stale_index_and_conflicting_locks_refuse_import(self):
        fields = ip.validate_request(self.request)
        with ip._per_project_lock(str(self.project)), self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        with ip.cv._cleanup_lock(str(self.root)), self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        with ip.ia.state_write_lock(str(self.state)), self.assertRaises(ip.ia.StateWriteBusy):
            ip.run_import(fields)
        (self.state / "assets.json").write_text('{"assets": []}')
        with patch.object(ip, "run_closed_batch") as run, self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        run.assert_not_called()

    def test_open_editor_cannot_be_imported_in_closed_mode(self):
        self.running = [{"projectPath": str(self.project), "pid": os.getpid(), "unityVersion": "2022.3.16f1"}]
        with patch.object(ip, "run_closed_batch") as run:
            result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        run.assert_not_called()

    def test_live_receipt_does_not_release_locks_with_an_outstanding_claim(self):
        self.live()
        steps = []
        seen_packages = []
        await_ready = ip._Preparation.await_ready
        def all_ready(prep, index):
            self.assertEqual(await_ready(prep, 2), "ready")
            return await_ready(prep, index)
        def editor_tick(seconds):
            with self.assertRaises(ip.RequestError):
                ip._per_project_lock(str(self.project))
            request = json.loads((self.mailbox / "request.json").read_text())
            self.assertEqual(request["editor_pid"], os.getpid())
            self.assertTrue(request["package"].endswith(".unitypackage"))
            self.assertNotIn(str(self.root), request["package"], "live imports use the local staged copy")
            seen_packages.append(request["package"])
            steps.append(request["id"])
            if len(steps) == 1:
                (self.mailbox / "done.json").write_text(json.dumps({"id": request["id"], "status": "imported"}))
                Path(self.request["cancel_file"]).touch()
            else:
                (self.mailbox / "request.json").unlink()
        with patch.object(ip._Preparation, "await_ready", all_ready), patch.object(ip.time, "sleep", side_effect=editor_tick):
            result = self.run_queue()
        self.assertEqual(len(steps), 2, "Receipt alone is not a safe package boundary")
        self.assertFalse(os.path.exists(seen_packages[0]), "consumed staged copies are removed")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["results"][0]["status"], "imported")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "cancelled", "cancelled"])

    def test_dead_editor_cleans_only_its_own_pending_request(self):
        self.live()
        (self.mailbox / "request.json").write_text('{"id":"ours","editor_pid":123}')
        (self.mailbox / "active.json").write_text('{"id":"someone-else"}')
        with patch.object(ip, "_pid_alive", return_value=False):
            result = ip._wait_live_done(str(self.project), "ours", 123)
        self.assertEqual(result["status"], "failed")
        self.assertFalse((self.mailbox / "request.json").exists())
        self.assertEqual(json.loads((self.mailbox / "active.json").read_text())["id"], "someone-else")

    def test_readiness_requires_current_idle_editor_and_fresh_heartbeat(self):
        self.live()
        self.assertTrue(ip.bridge_ready(str(self.project)))
        stale = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        for change in ({"status": "busy"}, {"updated": stale}, {"pid": os.getpid() + 1}, {"updated": "garbage"}):
            (self.mailbox / "heartbeat.json").write_text(json.dumps({**self.heartbeat, **change}))
            with self.subTest(change=change):
                self.assertFalse(ip.bridge_ready(str(self.project)))

    def test_install_does_not_overwrite_unowned_files(self):
        destination = self.project / "Assets" / ip.BRIDGE_SUBDIR
        destination.mkdir()
        source = destination / ip.BRIDGE_NAME
        source.write_text("unrelated user script")
        with self.assertRaises(ip.RequestError):
            ip.install_bridge(str(self.project))
        self.assertEqual(source.read_text(), "unrelated user script")
        self.assertFalse((destination / ip.BRIDGE_ASMDEF).exists())

    def test_malformed_cli_request_returns_one_terminal_json_line(self):
        manifest = self.base / "request.json"
        manifest.write_text("42")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(ip.main(["run", "--request", str(manifest)]), 2)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
