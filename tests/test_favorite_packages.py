"""Saved UPM favorites survive reload independently of indexed asset stars."""
import json
import os
import unittest

from tests.test_server import Harness
import favorite_packages


class FavoritePackagesTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.stop)
        self.package = {"id": "math", "label": "Mathematics", "kind": "registry",
                        "source": "com.unity.mathematics", "version": "1.3.2"}

    def post(self, path, body):
        status, _, raw = self.h.post(path, body, token=self.h.token)
        return status, json.loads(raw)

    def test_edit_and_remove_survive_reload_without_changing_asset_stars(self):
        self.assertEqual(self.post("/api/favorites", {"asset_key": "k1", "favorite": True})[0], 200)
        self.assertEqual(self.post("/api/package-favorites/save", {"package": self.package})[0], 200)
        edited = {**self.package, "label": "Math utilities", "version": "1.2.6"}
        self.assertEqual(self.post("/api/package-favorites/save", {"package": edited}),
                         (200, {"packages": [edited]}))
        # Load from disk again, not a service-owned in-memory copy.
        self.assertEqual(favorite_packages.load(self.h.state), {"packages": [edited]})
        status, _, raw = self.h.request("GET", "/api/package-favorites")
        self.assertEqual((status, json.loads(raw)), (200, {"packages": [edited]}))
        self.assertEqual(self.post("/api/package-favorites/remove", {"id": "math"}),
                         (200, {"packages": []}))
        self.assertEqual(self.h.service.op_favorites(), {"favorites": ["k1"]})

    def test_duplicate_and_corrupt_store_never_overwrite_saved_data(self):
        self.post("/api/package-favorites/save", {"package": self.package})
        path = os.path.join(self.h.state, favorite_packages.STORE_NAME)
        with open(path, "rb") as stream:
            original = stream.read()
        duplicate = {**self.package, "id": "duplicate"}
        self.assertEqual(self.post("/api/package-favorites/save", {"package": duplicate})[0], 400)
        with open(path, "rb") as stream:
            self.assertEqual(stream.read(), original)
        with open(path, "w") as stream:
            stream.write("{broken")
        self.assertEqual(self.post("/api/package-favorites/remove", {"id": "math"})[0], 500)
        with open(path) as stream:
            self.assertEqual(stream.read(), "{broken")
    def test_reorder_survives_reload_and_preserves_package_details(self):
        second = {**self.package, "id": "tools", "label": "Tools", "source": "com.example.tools"}
        third = {**self.package, "id": "extra", "label": "Extra", "source": "com.example.extra"}
        for item in (self.package, second, third):
            self.post("/api/package-favorites/save", {"package": item})
        self.assertEqual(self.post("/api/package-favorites/reorder", {"ids": ["extra", "math", "tools"]}),
                         (200, {"packages": [third, self.package, second]}))
        self.assertEqual(favorite_packages.load(self.h.state), {"packages": [third, self.package, second]})
        edited = {**self.package, "label": "Edited"}
        self.post("/api/package-favorites/save", {"package": edited})
        self.assertEqual(favorite_packages.load(self.h.state), {"packages": [third, edited, second]})

    def test_invalid_or_stale_reorder_cannot_drop_or_duplicate_saved_packages(self):
        second = {**self.package, "id": "tools", "source": "com.example.tools"}
        for item in (self.package, second):
            self.post("/api/package-favorites/save", {"package": item})
        for ids in (["math"], ["math", "math"], ["math", "unknown"], ["math", {}], "math", None):
            with self.subTest(ids=ids):
                self.assertEqual(self.post("/api/package-favorites/reorder", {"ids": ids})[0], 400)
                self.assertEqual(favorite_packages.load(self.h.state), {"packages": [self.package, second]})


if __name__ == "__main__":
    unittest.main()
