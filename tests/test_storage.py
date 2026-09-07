#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import index_assets as ia  # noqa: E402
import server  # noqa: E402
import storage  # noqa: E402


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.state = os.path.join(self.repo, "state")
        os.makedirs(self.state)
        self.root_a = os.path.join(self.tmp.name, "vault-a")
        self.root_b = os.path.join(self.tmp.name, "vault-b")
        os.mkdir(self.root_a)
        os.mkdir(self.root_b)
        with open(os.path.join(self.root_a, "A v1.0.unitypackage"), "wb") as fh:
            fh.write(b"a")
        with open(os.path.join(self.root_b, "B v1.0.unitypackage"), "wb") as fh:
            fh.write(b"b")
        ia.write_atomic(os.path.join(self.state, "cache.json"), json.dumps({
            "resolved": {"a/a": {"status": "resolved", "id": "1"}},
            "misses": {},
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_configure_builds_index_and_keeps_metadata_cache(self):
        result = storage.configure(self.repo, self.root_a, "instance-test-1")
        self.assertTrue(result["ready"])
        with open(os.path.join(self.state, "assets.json"), encoding="utf-8") as fh:
            assets = json.load(fh)
        self.assertEqual([v["file"] for a in assets["assets"] for v in a["versions"]],
                         ["A v1.0.unitypackage"])
        with open(os.path.join(self.state, "cache.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["resolved"]["a/a"]["id"], "1")
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_a))
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["output_dir"], ".")

    def test_staged_artifact_failure_preserves_previous_state(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            old_config = fh.read()
        with mock.patch.object(storage.ia, "emit_csv", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                storage.configure(self.repo, self.root_b, "instance-test-2")
        with open(os.path.join(self.repo, "config.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), old_config)
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_a))
    def test_recovery_does_not_delete_unstarted_targets(self):
        assets = os.path.join(self.state, "assets.json")
        config = os.path.join(self.repo, "config.json")
        with open(assets, "w", encoding="utf-8") as fh:
            fh.write("old-assets")
        txn = "a" * 32
        _, _, backup = storage._entry_paths(self.repo, txn, ".", "assets")
        os.replace(assets, backup)
        _, stage_config, _ = storage._entry_paths(self.repo, txn, ".", "config")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"vault_root": self.root_a, "output_dir": "."}, fh)
        with open(stage_config, "w", encoding="utf-8") as fh:
            fh.write("new-config")
        storage._write_json(storage._journal_path(self.repo), {
            "version": storage.JOURNAL_VERSION, "txn": txn, "status": "applying",
            "entries": ["assets", "config"], "output_rel": ".",
            "backed_up": ["assets"], "existed": ["assets"],
        })
        self.assertTrue(storage.recover(self.repo))
        with open(assets, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "old-assets")
        with open(config, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["vault_root"], self.root_a)


    def test_external_server_lock_blocks_root_change(self):
        service = server.ViewerService(repo=self.repo,
                                       config={"vault_root": self.root_a, "output_dir": "."},
                                       instance_id="instance-test-1")
        service.acquire_instance_lock()
        try:
            with self.assertRaises(storage.StorageError):
                storage.configure(self.repo, self.root_b, "instance-test-2")
        finally:
            service.shutdown()

    def test_root_switch_replaces_assets_and_pending_queue(self):
        storage.configure(self.repo, self.root_a, "instance-test-1")
        storage.configure(self.repo, self.root_b, "instance-test-2")
        with open(os.path.join(self.state, "assets.json"), encoding="utf-8") as fh:
            assets = json.load(fh)
        files = [v["file"] for a in assets["assets"] for v in a["versions"]]
        self.assertEqual(files, ["B v1.0.unitypackage"])
        self.assertEqual(storage.status(self.repo)["path"], os.path.realpath(self.root_b))

    def test_prepare_restart_rejects_active_admission(self):
        service = server.ViewerService(repo=self.repo,
                                       config={"vault_root": self.root_a, "output_dir": "."},
                                       instance_id="instance-test-1")
        with service._job_lock:
            service._action = {"status": "running", "id": "active"}
        with self.assertRaises(server.Busy):
            service.prepare_restart()
        with service._job_lock:
            service._action = None
        self.assertTrue(service.prepare_restart()["prepared"])
        with self.assertRaises(server.Busy):
            service.op_enrich_start()
        service.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
