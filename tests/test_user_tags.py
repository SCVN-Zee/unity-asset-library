"""Run with python3 -m unittest tests.test_user_tags."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_server import Harness
import index_assets as ia
import resolve_store
import storage
import user_tags


class UserTagsTest(unittest.TestCase):
    def test_cli_migration_and_user_edits_survive_scan_enrichment_and_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "repo"
            state = repo / "state"
            vault = Path(folder) / "vault"
            state.mkdir(parents=True)
            vault.mkdir()
            (vault / "Forest v1.0.unitypackage").write_bytes(b"package")
            data = ia.build(ia.scan(str(vault)))
            key = data["assets"][0]["asset_key"]
            data["assets"][0]["tags"] = [" NATURE ", "Nature", "audio"]
            (state / "assets.json").write_text(json.dumps(data))
            args = ["update", "--root", str(vault), "--state", str(state)]
            self.assertEqual(ia.main(args), 0)
            self.assertEqual(user_tags.load(state)["assignments"][key], ["audio", "nature"])
            user_tags.mutate(state, {"action": "rename", "tag": "NATURE", "new_tag": " AUDIO "})
            user_tags.mutate(state, {"action": "assign", "tag": " My Game ", "asset_key": key})
            user_tags.mutate(state, {"action": "delete", "tag": "audio"})
            self.assertEqual(ia.main(args), 0)
            self.assertEqual(json.loads((state / "assets.json").read_text())["assets"][0]["tags"], ["my game"])
            (state / "overrides.json").write_text(json.dumps({key: {"tags": ["resurrected"]}}))
            with mock.patch.object(ia, "state_dir", return_value=str(state)):
                self.assertEqual(resolve_store.main(["enrich"]), 0)
            self.assertEqual(json.loads((state / "assets.json").read_text())["assets"][0]["tags"], ["my game"])
            storage.configure(str(repo), str(vault))
            self.assertEqual(json.loads((state / "assets.json").read_text())["assets"][0]["tags"], ["my game"])
            # Temporarily missing packages retain assignments; new versions recover them.
            (vault / "Forest v1.0.unitypackage").unlink()
            self.assertEqual(ia.main(args), 0)
            self.assertEqual(user_tags.load(state)["assignments"][key], ["my game"])
            (vault / "Forest v2.0.unitypackage").write_bytes(b"new package")
            self.assertEqual(ia.main(args), 0)
            self.assertEqual(json.loads((state / "assets.json").read_text())["assets"][0]["tags"], ["my game"])

    def test_corrupt_store_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as state:
            path = Path(user_tags.store_path(state))
            for broken in ['{broken', json.dumps({"tags": ["UPPER"], "assignments": {}})]:
                path.write_text(broken)
                with self.assertRaises(user_tags.TagStoreError):
                    user_tags.mutate(state, {"action": "create", "tag": "test"})
                self.assertEqual(path.read_text(), broken)

    def test_http_mutations_validate_and_preserve_assignments(self):
        h = Harness()
        try:
            def post(change, token=None):
                status, _, body = h.post('/api/tags', change, token=h.token if token is None else token)
                return status, json.loads(body)
            self.assertEqual(post({"action": "create", "tag": "ui"}, "wrong")[0], 403)
            for change in [{"action": "assign", "tag": "ui"}, {"action": "create", "tag": " "}, {"action": "create", "tag": "ui", "asset_key": "k1"}, {"action": [], "tag": "ui"}]:
                self.assertEqual(post(change)[0], 400)
            self.assertEqual(post({"action": "assign", "tag": "ui", "asset_key": "missing"})[0], 404)
            status, result = post({"action": "assign", "tag": " UI ", "asset_key": "k1"})
            self.assertEqual(status, 200)
            self.assertEqual(result, {"tags": ["ui"], "assignments": {"k1": ["ui"]}})
            self.assertEqual(post({"action": "create", "tag": "Ui"})[1], result)
            post({"action": "rename", "tag": "ui", "new_tag": " Menu "})
            self.assertEqual(h.service.op_assets()["assets"][0]["tags"], ["menu"])
            post({"action": "remove", "tag": "menu", "asset_key": "k1"})
            self.assertEqual(h.service.op_assets()["assets"][0]["tags"], [])
            self.assertEqual(post({"action": "delete", "tag": "menu"})[1], {"tags": [], "assignments": {}})
        finally:
            h.stop()


if __name__ == '__main__':
    unittest.main()
