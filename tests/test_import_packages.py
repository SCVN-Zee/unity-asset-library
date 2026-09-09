"""Import safety contracts; real Editor imports are exercised separately."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import import_packages as ip


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
        self.rows = [{"asset_key": name, "file": name + ".unitypackage"} for name in ("first", "second", "third")]
        for row in self.rows:
            (self.root / row["file"]).write_bytes(b"archive fixture")
        (self.state / "assets.json").write_text(json.dumps({"assets": [{"asset_key": row["asset_key"], "versions": [{"file": row["file"]}]} for row in self.rows]}))
        self.request = {"id": "test-job", "repo": str(self.base), "root": str(self.root), "project": str(self.project),
                        "mode": "closed", "packages": self.rows, "cancel_file": str(self.base / "cancel")}
        self.running = []
        def discovery(args):
            if args[:2] == ["projects", "list"]:
                return []
            if args[:2] == ["editors", "running"]:
                return {"instances": self.running}
            if args == ["editors", "--installed", "--json"]:
                return [{"version": "2022.3.16f1"}]
            raise AssertionError(args)
        self.discovery = patch.object(ip, "run_unity_json", side_effect=discovery)
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def run_queue(self):
        return ip.run_import(ip.validate_request(self.request))

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

    def test_first_failure_preserves_unstarted_packages(self):
        with patch.object(ip, "run_closed_import", side_effect=[(True, None), (False, "invalid archive")]) as run:
            result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "failed", "pending"])
        self.assertEqual(result["results"][1]["error"], "invalid archive")
        self.assertEqual(run.call_count, 2)

    def test_stop_waits_for_current_package_and_skips_rest(self):
        def importing(project, package):
            Path(self.request["cancel_file"]).touch()
            return True, None
        with patch.object(ip, "run_closed_import", side_effect=importing):
            result = self.run_queue()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([row["status"] for row in result["results"]], ["imported", "cancelled", "cancelled"])

    def test_stale_index_and_conflicting_locks_refuse_import(self):
        fields = ip.validate_request(self.request)
        with ip._per_project_lock(str(self.project)), self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        with ip.cv._cleanup_lock(str(self.root)), self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        with ip.ia.state_write_lock(str(self.state)), self.assertRaises(ip.ia.StateWriteBusy):
            ip.run_import(fields)
        (self.state / "assets.json").write_text('{"assets": []}')
        with patch.object(ip, "run_closed_import") as run, self.assertRaises(ip.RequestError):
            ip.run_import(fields)
        run.assert_not_called()

    def test_open_editor_cannot_be_imported_in_closed_mode(self):
        self.running = [{"projectPath": str(self.project), "pid": os.getpid(), "unityVersion": "2022.3.16f1"}]
        with patch.object(ip, "run_closed_import") as run:
            result = self.run_queue()
        self.assertEqual(result["status"], "failed")
        run.assert_not_called()

    def test_live_receipt_does_not_release_locks_with_an_outstanding_claim(self):
        self.live()
        steps = []
        def editor_tick(seconds):
            with self.assertRaises(ip.RequestError):
                ip._per_project_lock(str(self.project))
            request = json.loads((self.mailbox / "request.json").read_text())
            self.assertEqual(request["editor_pid"], os.getpid())
            steps.append(request["id"])
            if len(steps) == 1:
                (self.mailbox / "done.json").write_text(json.dumps({"id": request["id"], "status": "imported"}))
                Path(self.request["cancel_file"]).touch()
            else:
                (self.mailbox / "request.json").unlink()
        with patch.object(ip.time, "sleep", side_effect=editor_tick):
            result = self.run_queue()
        self.assertEqual(len(steps), 2, "Receipt alone is not a safe package boundary")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["results"][0]["status"], "imported")

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
