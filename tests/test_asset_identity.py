"""Behavioral coverage for identity ownership and reconstructible pending work."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import cleanup_versions as cv
import index_assets as ia
import storage
import user_tags


class IdentityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "library"
        self.root.mkdir()
        self.state = self.root / ".data"
        self.state.mkdir()

    def archive(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"local package fixture")
        return path

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value), encoding="utf-8")

    def read(self, name):
        return json.loads((self.state / name).read_text(encoding="utf-8"))

    def update(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return ia.main(["update", "--root", str(self.root), "--state", str(self.state)])

    def assets(self):
        return self.read("assets.json")["assets"]

    def annotate(self, key):
        user_tags.mutate(str(self.state), {"action": "create", "tag": "keep"})
        user_tags.mutate(str(self.state), {"action": "assign", "tag": "keep", "asset_key": key})
        self.write("favorites.json", {"favorites": [key]})

    def seed_legacy(self, paths):
        for path in paths:
            self.archive(path)
        data = ia.build(ia.scan(str(self.root)))
        versions = [v for a in data["assets"] for v in a["versions"]]
        asset = data["assets"][0]
        asset.update(asset_key="water", versions=versions, tags=["keep"])
        data.update(assets=[asset], asset_count=1)
        data.pop("identity_version")
        self.write("assets.json", data)
        self.write("user_tags.json", {"tags": ["keep"], "assignments": {"water": ["keep"]}})
        self.write("favorites.json", {"favorites": ["water"]})
        self.write("cache.json", {"resolved": {"water": {
            "status": "resolved", "id": "123", "score": 1.0,
            "legacy": {"publisher": "Publisher", "name": "Water"}}}, "misses": {}})
        self.write("overrides.json", {"water": {"author": "Manual author"}})
        self.write("pending-enrichment.json", {"pending": [{
            "asset_key": "water", "first_seen": "2024-01-01"}]})

    def test_publishers_and_unicode_products_never_share_ownership(self):
        for path in ("Publisher A/Water v1.0.unitypackage",
                     "Publisher B/Water v2.0.unitypackage",
                     "猫 v1.0.unitypackage", "犬 v1.0.unitypackage"):
            self.archive(path)
        self.assertEqual(self.update(), 0)
        assets = self.assets()
        by_file = {v["file"]: a["asset_key"] for a in assets for v in a["versions"]}
        self.assertNotEqual(by_file["Publisher A/Water v1.0.unitypackage"],
                            by_file["Publisher B/Water v2.0.unitypackage"])
        self.assertNotEqual(by_file["猫 v1.0.unitypackage"], by_file["犬 v1.0.unitypackage"])
        self.assertEqual({a["local_name"] for a in assets}, {"Water", "猫", "犬"})

    def test_rename_move_new_version_and_temporary_absence_preserve_annotations(self):
        source = self.archive("Publisher/Water v1.0.unitypackage")
        self.assertEqual(self.update(), 0)
        key = self.assets()[0]["asset_key"]
        self.annotate(key)
        renamed = self.root / "Tools/New Publisher/Water Deluxe v1.0.unitypackage"
        renamed.parent.mkdir(parents=True)
        source.rename(renamed)
        self.assertEqual(self.update(), 0)
        self.archive("Tools/New Publisher/Water Deluxe v2.0.unitypackage")
        self.assertEqual(self.update(), 0)
        self.assertEqual([a["asset_key"] for a in self.assets()], [key])
        self.assertEqual({v["version"] for v in self.assets()[0]["versions"]}, {"1.0", "2.0"})
        self.assertEqual(self.assets()[0]["tags"], ["keep"])
        self.assertEqual(self.read("favorites.json"), {"favorites": [key]})
        holding = Path(self.temp.name) / "holding"
        holding.mkdir()
        for path in renamed.parent.glob("*.unitypackage"):
            path.rename(holding / path.name)
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.assets(), [])
        (holding / renamed.name).rename(renamed)
        self.assertEqual(self.update(), 0)
        self.assertEqual(self.assets()[0]["asset_key"], key)
        self.assertEqual(self.assets()[0]["tags"], ["keep"])

    def test_same_named_established_products_remain_distinct_after_colocation(self):
        first = self.archive("Publisher A/Water v1.0.unitypackage")
        second = self.archive("Publisher B/Water v2.0.unitypackage")
        self.assertEqual(self.update(), 0)
        original = {a["versions"][0]["file"]: a["asset_key"] for a in self.assets()}
        self.annotate(original["Publisher A/Water v1.0.unitypackage"])
        destination = self.root / "Tools"
        destination.mkdir()
        first.rename(destination / first.name)
        second.rename(destination / second.name)
        self.assertEqual(self.update(), 0)
        self.assertEqual({a["asset_key"] for a in self.assets()}, set(original.values()))
        tagged = [a["asset_key"] for a in self.assets() if a["tags"]]
        self.assertEqual(tagged, [original["Publisher A/Water v1.0.unitypackage"]])
        plan = cv.plan_cleanup(ia.scan(str(self.root)), ia.asset_identity.load(str(self.state)))
        self.assertEqual(plan["removals"], [])
        self.assertEqual(self.read("assets.json")["duplicate_groups"][0]["verdict"], "distinct")

    def test_legacy_split_preserves_annotations_but_does_not_guess_store_ownership(self):
        self.seed_legacy(["Publisher A/Water v1.0.unitypackage",
                          "Publisher B/Water v2.0.unitypackage"])
        original = {name: self.read(name) for name in
                    ("assets.json", "user_tags.json", "favorites.json", "cache.json", "overrides.json")}
        self.assertEqual(self.update(), 0)
        keys = {a["asset_key"] for a in self.assets()}
        self.assertEqual(len(keys), 2)
        self.assertNotIn("water", keys)
        self.assertEqual(self.read("user_tags.json")["assignments"], {k: ["keep"] for k in keys})
        self.assertEqual(set(self.read("favorites.json")["favorites"]), keys)
        self.assertEqual(self.read("cache.json")["resolved"], {})
        self.assertEqual(self.read("overrides.json"), {})
        self.assertTrue(all(not a["resolution"]["id_verified"] for a in self.assets()))
        backup = self.read("identity-migration-backup.json")
        self.assertEqual(backup["cache"], original["cache.json"])
        self.assertEqual(backup["tags"], original["user_tags.json"])
        self.assertEqual({r["first_seen"] for r in self.read("pending-enrichment.json")["pending"]},
                         {"2024-01-01"})
        self.assertEqual(self.update(), 0)
        self.assertEqual({a["asset_key"] for a in self.assets()}, keys)
        self.assertEqual(self.read("identity-migration-backup.json"), backup)

    def test_unambiguous_legacy_cache_and_override_follow_new_identity(self):
        self.seed_legacy(["Water v1.0.unitypackage"])
        self.assertEqual(self.update(), 0)
        asset = self.assets()[0]
        self.assertTrue(asset["resolution"]["id_verified"])
        self.assertEqual(asset["store_id"], "123")
        self.assertEqual(asset["author"], "Manual author")
        self.assertEqual(set(self.read("cache.json")["resolved"]), {asset["asset_key"]})
        self.assertEqual(self.read("pending-enrichment.json")["pending"], [])

    def test_interrupted_identity_cutover_restores_index_and_annotations(self):
        self.seed_legacy(["Publisher A/Water v1.0.unitypackage",
                          "Publisher B/Water v2.0.unitypackage"])
        names = ("assets.json", "user_tags.json", "favorites.json", "cache.json", "overrides.json")
        original = {name: (self.state / name).read_bytes() for name in names}
        replace = os.replace
        def fail(source, target):
            if ".storage-stage-" in str(source) and str(source).endswith("-tags"):
                raise OSError("injected disk failure")
            return replace(source, target)
        with mock.patch.object(storage.os, "replace", side_effect=fail):
            self.assertEqual(self.update(), 1)
        self.assertEqual({name: (self.state / name).read_bytes() for name in names}, original)
        self.assertFalse((self.state / "identities.json").exists())
        self.assertEqual(self.update(), 0)
        self.assertEqual(len(self.assets()), 2)

    def test_queue_write_failure_and_corruption_do_not_forget_unresolved_assets(self):
        self.archive("Water v1.0.unitypackage")
        self.assertEqual(self.update(), 0)
        self.archive("Forest v1.0.unitypackage")
        import pending_queue
        with mock.patch.object(pending_queue, "reconcile", side_effect=OSError("queue write failure")):
            self.assertEqual(self.update(), 1)
        self.assertEqual(self.update(), 0)
        keys = {a["asset_key"] for a in self.assets()}
        self.assertEqual({r["asset_key"] for r in self.read("pending-enrichment.json")["pending"]}, keys)
        queue = self.state / "pending-enrichment.json"
        queue.write_bytes(b"{ corrupted queue")
        run = subprocess.run([sys.executable, str(BIN / "resolve_store.py"), "export-titles",
                              "--pending", "--state", str(self.state)],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual({r["asset_key"] for r in self.read("search-worklist.json")}, keys)
        self.assertEqual(Path(str(queue) + ".corrupt").read_bytes(), b"{ corrupted queue")
        (self.state / "cache.json").unlink(missing_ok=True)
        queue.unlink()
        self.assertEqual(self.update(), 0)
        self.assertEqual({r["asset_key"] for r in self.read("pending-enrichment.json")["pending"]}, keys)


    def test_stat_matching_cannot_merge_source_and_plain_variants(self):
        source = self.archive("Tools/Foo (Source) v1.0.unitypackage")
        self.assertEqual(self.update(), 0)
        source_key = self.assets()[0]["asset_key"]
        self.annotate(source_key)
        source.rename(self.root / "Tools/Foo v1.0.unitypackage")
        self.archive("Tools/Foo (Source) v2.0.unitypackage")
        self.assertEqual(self.update(), 0)
        by_variant = {tuple(a["discriminators"]): a for a in self.assets()}
        self.assertEqual(set(by_variant), {(), ("Source",)})
        self.assertNotEqual(by_variant[()]["asset_key"], by_variant[("Source",)]["asset_key"])
        self.assertEqual(by_variant[("Source",)]["asset_key"], source_key)
        self.assertEqual(by_variant[("Source",)]["tags"], ["keep"])
        self.assertEqual(by_variant[()]["tags"], [])


    def test_path_swap_preserves_identity_owned_tags_favorite_and_cache(self):
        first = self.archive("Publisher A/Water v1.0.unitypackage")
        second = self.archive("Publisher B/Forest v1.0.unitypackage")
        self.assertEqual(self.update(), 0)
        keys = {a["local_name"]: a["asset_key"] for a in self.assets()}
        assignments = {keys["Water"]: ["water"], keys["Forest"]: ["forest"]}
        self.write("user_tags.json", {"tags": ["forest", "water"], "assignments": assignments})
        self.write("favorites.json", {"favorites": [keys["Water"]]})
        cache = {"resolved": {keys["Water"]: {"status": "resolved", "id": "123"},
                              keys["Forest"]: {"status": "resolved", "id": "456"}}, "misses": {}}
        self.write("cache.json", cache)
        temporary = self.root / "swapping.unitypackage"
        first.rename(temporary)
        second.rename(first)
        temporary.rename(second)
        self.assertEqual(self.update(), 0)
        by_file = {v["file"]: a for a in self.assets() for v in a["versions"]}
        moved_water = by_file["Publisher B/Forest v1.0.unitypackage"]
        moved_forest = by_file["Publisher A/Water v1.0.unitypackage"]
        self.assertEqual(moved_water["asset_key"], keys["Water"])
        self.assertEqual(moved_forest["asset_key"], keys["Forest"])
        self.assertEqual(moved_water["tags"], ["water"])
        self.assertEqual(moved_forest["tags"], ["forest"])
        self.assertEqual(self.read("user_tags.json")["assignments"], assignments)
        self.assertEqual(self.read("favorites.json"), {"favorites": [keys["Water"]]})
        self.assertEqual(self.read("cache.json"), cache)
        self.assertEqual(moved_water["store_id"], "123")
        self.assertEqual(moved_forest["store_id"], "456")


if __name__ == "__main__":
    unittest.main()
