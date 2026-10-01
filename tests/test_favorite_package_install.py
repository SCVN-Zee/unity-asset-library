import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location("favorite_install", ROOT / "bin" / "install_favorite_packages.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class FavoriteInstallTests(unittest.TestCase):
    def test_conflict_preserves_manifest_and_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            (root / "Packages").mkdir()
            original = {"dependencies": {"com.unity.mathematics": "1.2.0", "com.example.other": "2"}, "scopedRegistries": [{"name": "private"}]}
            path = root / "Packages/manifest.json"
            path.write_text(json.dumps(original))
            request = {"id": "j", "project": str(root), "packages": [{"id": "x", "label": "Math", "kind": "registry", "source": "com.unity.mathematics", "version": "1.3.2"}]}
            with mock.patch.object(installer, "_editor_closed"), mock.patch.object(installer, "_editor", return_value="/Unity"), mock.patch.object(installer, "_run_helper") as helper:
                result = installer.run(request)
            self.assertEqual(result["results"][0]["status"], "failed")
            helper.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), original)

    def test_matching_dependency_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Assets").mkdir()
            (root / "ProjectSettings").mkdir()
            (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
            (root / "Packages").mkdir()
            (root / "Packages/manifest.json").write_text(json.dumps({"dependencies": {"com.unity.mathematics": "1.3.2"}}))
            request = {"id": "j", "project": str(root), "packages": [{"id": "x", "label": "Math", "kind": "registry", "source": "com.unity.mathematics", "version": "1.3.2"}]}
            with mock.patch.object(installer, "_editor_closed"), mock.patch.object(installer, "_editor", return_value="/Unity"), mock.patch.object(installer, "_run_helper") as helper:
                result = installer.run(request)
            self.assertEqual(result["results"][0]["status"], "skipped")
            helper.assert_not_called()

    def test_git_subfolder_metadata_uses_the_selected_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "Packages" / "Utilities"
            package.mkdir(parents=True)
            manifest = package / "package.json"
            manifest.write_text('{"name":"com.example.original","version":"1.0.0"}')
            def git(*args):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            git("init", "-b", "main")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "original")
            git("tag", "v1.0.0")
            manifest.write_text('{"name":"com.example.renamed","version":"2.0.0"}')
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "rename")
            row = {"id": "git", "label": "Utilities", "kind": "git",
                   "source": root.as_uri() + "?path=/Packages/Utilities#v1.0.0", "version": ""}
            self.assertEqual(installer._git_package_name(row), "com.example.original")

    def test_git_subfolder_traversal_is_rejected_before_reading_metadata(self):
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            installer._git_locator("https://example.invalid/package.git?path=/%2e%2e/other", "")

if __name__ == "__main__":
    unittest.main()
