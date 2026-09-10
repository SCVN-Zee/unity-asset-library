"""Observable filesystem safety and queue boundaries for direct installation."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import import_packages as ip
import package_install as pi


def package(path, assets, extras=()):
    with tarfile.open(path, "w:gz") as archive:
        for guid, name, data in assets:
            meta = ("fileFormatVersion: 2\nguid: " + guid + "\n" + ("folderAsset: yes\n" if data is None else "")).encode()
            entries = [("pathname", name.encode()), ("asset.meta", meta)]
            if data is not None:
                entries.append(("asset", data))
            for leaf, content in entries:
                info = tarfile.TarInfo(guid + "/" + leaf)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
        for name, content, kind in extras:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(content)
            info.linkname = "../../outside"
            archive.addfile(info, io.BytesIO(content))


class ImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ual-import-test-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root, self.project = self.base / "library", self.base / "project"
        self.state = self.root / ".data"
        for folder in (self.state, self.project / "Assets", self.project / "ProjectSettings"):
            folder.mkdir(parents=True)
        (self.project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\n")
        self.rows = [{"asset_key": str(i), "file": f"Package {i} v1.2.unitypackage"} for i in range(3)]
        for i, row in enumerate(self.rows):
            package(self.root / row["file"], [(str(i + 1) * 32, f"Assets/File{i}.txt", f"content {i}".encode())])
        self.index()
        self.request = {"id": "job", "repo": str(self.base), "root": str(self.root), "project": str(self.project),
                        "packages": self.rows, "cancel_file": str(self.base / "cancel")}
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def index(self):
        (self.state / "assets.json").write_text(json.dumps({"assets": [
            {"asset_key": r["asset_key"], "versions": [{"file": r["file"]}]} for r in self.rows]}))

    def run_queue(self):
        return ip.run_import(ip.validate_request(self.request))

    def install(self, assets, overwrite=False, extras=()):
        path = self.base / "input.unitypackage"
        package(path, assets, extras)
        with tempfile.TemporaryDirectory(dir=self.base) as staging:
            return pi.install_package(str(self.project), str(path), overwrite, staging)

    def test_real_archives_install_in_order_without_editor_and_skip_identical(self):
        with patch.object(ip, "run_unity_json", side_effect=AssertionError("Editor must not be queried")):
            result = self.run_queue()
            first = (self.project / "Assets/File0.txt").stat()
            repeated = self.run_queue()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([r["status"] for r in result["results"]], ["installed"] * 3)
        self.assertEqual(repeated["status"], "completed", repeated)
        self.assertEqual((self.project / "Assets/File0.txt").stat().st_mtime_ns, first.st_mtime_ns)
        for i in range(3):
            self.assertEqual((self.project / f"Assets/File{i}.txt").read_bytes(), f"content {i}".encode())
            self.assertIn(str(i + 1) * 32, (self.project / f"Assets/File{i}.txt.meta").read_text())
        self.assertFalse((self.project / "Library").exists())
        self.assertEqual(list((self.project / pi.BACKUPS).iterdir()), [])

    def test_unity_gzip_extra_header_is_supported(self):
        archive = self.root / self.rows[0]["file"]
        data = bytearray(archive.read_bytes())
        data[3] |= 4  # RFC 1952 FEXTRA, as emitted by real Unity exports.
        extra = b"UT\x01\x00\x00"
        archive.write_bytes(bytes(data[:10]) + len(extra).to_bytes(2, "little") + extra + bytes(data[10:]))
        result = self.run_queue()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual((self.project / "Assets/File0.txt").read_bytes(), b"content 0")


    def test_legacy_pathname_line_trailer_preserves_folder_and_file(self):
        self.install([("a" * 32, "Assets/Legacy\n00", None), ("b" * 32, "Assets/Legacy/File\n00", b"payload")])
        self.assertTrue((self.project / "Assets/Legacy").is_dir())
        self.assertEqual((self.project / "Assets/Legacy/File").read_bytes(), b"payload")
        with self.assertRaises(pi.InstallError):
            self.install([("c" * 32, "Assets/Unsafe\n../outside", b"bad")])
        self.assertFalse((self.project / "Assets/Unsafe").exists())

    def test_changed_assets_require_consent_and_preserve_original_backups(self):
        guid = "a" * 32
        self.install([(guid, "Assets/A.txt", b"old")])
        before = (self.project / "Assets/A.txt.meta").read_bytes()
        with self.assertRaises(pi.InstallError):
            self.install([(guid, "Assets/A.txt", b"new")])
        self.assertEqual((self.project / "Assets/A.txt").read_bytes(), b"old")
        backup = Path(self.install([(guid, "Assets/A.txt", b"new")], overwrite=True))
        journal = json.loads((backup / "journal.json").read_text())
        self.assertEqual(journal["status"], "complete")
        item = next(op for op in journal["ops"] if op["rel"] == "Assets/A.txt")
        self.assertEqual((backup / item["backup"]).read_bytes(), b"old")
        self.assertEqual((self.project / "Assets/A.txt").read_bytes(), b"new")
        self.assertEqual((self.project / "Assets/A.txt.meta").read_bytes(), before)
        self.assertEqual(backup.parent, self.project / pi.BACKUPS)

    def test_unrelated_guid_conflicts_even_with_overwrite_and_preflight_is_read_only(self):
        self.install([("a" * 32, "Assets/Taken.txt", b"mine")])
        with self.assertRaises(pi.InstallError):
            self.install([("b" * 32, "Assets/New.txt", b"new"), ("c" * 32, "Assets/Taken.txt", b"other")], True)
        self.assertEqual((self.project / "Assets/Taken.txt").read_bytes(), b"mine")
        self.assertFalse((self.project / "Assets/New.txt").exists())
        self.assertFalse((self.project / "Assets/New.txt.meta").exists())

    def test_moved_folder_and_asset_guid_keep_project_paths_and_empty_folders(self):
        folder, file = "a" * 32, "b" * 32
        self.install([(folder, "Assets/Original", None), (file, "Assets/Original/A.txt", b"old")])
        (self.project / "Assets/Original").rename(self.project / "Assets/Moved")
        (self.project / "Assets/Original.meta").rename(self.project / "Assets/Moved.meta")
        (self.project / "Assets/Moved/A.txt").rename(self.project / "Assets/Elsewhere.txt")
        (self.project / "Assets/Moved/A.txt.meta").rename(self.project / "Assets/Elsewhere.txt.meta")
        self.install([(folder, "Assets/Original", None), (file, "Assets/Original/A.txt", b"updated"),
                      ("c" * 32, "Assets/Original/New.txt", b"new"), ("d" * 32, "Assets/Original/Empty", None)], True)
        self.assertFalse((self.project / "Assets/Original").exists())
        self.assertEqual((self.project / "Assets/Elsewhere.txt").read_bytes(), b"updated")
        self.assertEqual((self.project / "Assets/Moved/New.txt").read_bytes(), b"new")
        self.assertTrue((self.project / "Assets/Moved/Empty").is_dir())
        self.assertIn("d" * 32, (self.project / "Assets/Moved/Empty.meta").read_text())

    def test_rejects_malicious_members_paths_and_collisions_before_writing(self):
        a, b = "a" * 32, "b" * 32
        cases = [
            ([(a, "Assets/../escape", b"bad")], []),
            ([(a, "/tmp/escape", b"bad")], []),
            ([(a, "Assets/X", b"bad"), (b, "Assets/x", b"bad")], []),
            ([(a, "Assets/X", b"bad"), (b, "Assets/X/Child", b"bad")], []),
            ([(a, "Assets/X.meta", b"bad")], []),
            ([(a, "Assets/X", b"bad")], [("../../escape", b"", tarfile.REGTYPE)]),
            ([(a, "Assets/X", b"bad")], [(a + "/asset", b"duplicate", tarfile.REGTYPE)]),
            ([(a, "Assets/X", b"bad")], [(b + "/asset", b"", tarfile.SYMTYPE)]),
            ([(a, "Assets/X", b"bad")], [(b + "/asset", b"", tarfile.LNKTYPE)]),
            ([(a, "Assets/X", b"bad")], [(b + "/asset", b"", tarfile.FIFOTYPE)]),
        ]
        for assets, extras in cases:
            with self.subTest(assets=assets, extras=extras), self.assertRaises((pi.InstallError, OSError)):
                self.install(assets, True, extras)
            self.assertEqual(list((self.project / "Assets").iterdir()), [])

    def test_project_and_backup_symlinks_never_write_outside_project(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.project / "Assets/Linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((pi.InstallError, OSError)):
            self.install([("a" * 32, "Assets/Linked/X", b"bad")])
        (self.project / pi.BACKUPS).symlink_to(outside, target_is_directory=True)
        with self.assertRaises((pi.InstallError, OSError)):
            self.install([("b" * 32, "Assets/X", b"bad")])
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.project / "Assets/X").exists())

    def test_failure_rolls_back_own_files_without_losing_originals(self):
        self.install([("a" * 32, "Assets/A", b"old")])
        original = pi._put
        def fail(root, rel, source, expected, mode=0o644):
            if rel == "Assets/New/B":
                raise OSError("disk full")
            return original(root, rel, source, expected, mode)
        with patch.object(pi, "_put", side_effect=fail), self.assertRaises(pi.InstallError) as failure:
            self.install([("a" * 32, "Assets/A", b"changed"), ("b" * 32, "Assets/New/B", b"new")], True)
        self.assertEqual((self.project / "Assets/A").read_bytes(), b"old")
        self.assertFalse((self.project / "Assets/New").exists())
        journal = json.loads((Path(failure.exception.backup_path) / "journal.json").read_text())
        self.assertEqual(journal["status"], "rolled_back")
        self.install([("c" * 32, "Assets/After", b"allowed")])
        self.assertEqual((self.project / "Assets/After").read_bytes(), b"allowed")

    def test_concurrent_edit_is_not_rolled_back_and_unresolved_journal_blocks_retry(self):
        self.install([("a" * 32, "Assets/A", b"old")])
        original = pi._put
        def fail(root, rel, source, expected, mode=0o644):
            if rel == "Assets/B":
                (self.project / "Assets/A").write_bytes(b"user edit")
                raise OSError("interrupted")
            return original(root, rel, source, expected, mode)
        with patch.object(pi, "_put", side_effect=fail), self.assertRaises(pi.InstallError) as failure:
            self.install([("a" * 32, "Assets/A", b"changed"), ("b" * 32, "Assets/B", b"new")], True)
        self.assertEqual((self.project / "Assets/A").read_bytes(), b"user edit")
        journal = json.loads((Path(failure.exception.backup_path) / "journal.json").read_text())
        self.assertEqual(journal["status"], "needs_recovery")
        with self.assertRaises(pi.InstallError):
            self.install([("c" * 32, "Assets/After", b"blocked")])
        self.assertFalse((self.project / "Assets/After").exists())

    def test_rollback_preserves_concurrent_identical_file_we_never_committed(self):
        self.install([("a" * 32, "Assets/A", b"old")])
        link = os.link
        def race(source, destination, **kwargs):
            if destination == "B":
                (self.project / "Assets/B").write_bytes(b"new")
            return link(source, destination, **kwargs)
        with patch.object(pi.os, "link", side_effect=race), self.assertRaises(pi.InstallError):
            self.install([("a" * 32, "Assets/A", b"changed"), ("b" * 32, "Assets/B", b"new")], True)
        self.assertEqual((self.project / "Assets/A").read_bytes(), b"old")
        self.assertEqual((self.project / "Assets/B").read_bytes(), b"new")

    def test_failure_after_atomic_commit_still_restores_original(self):
        self.install([("a" * 32, "Assets/A", b"old")])
        fsync, failed = os.fsync, False
        def fail(fd):
            nonlocal failed
            if not failed and (self.project / "Assets/A").read_bytes() == b"changed":
                failed = True
                raise OSError("directory sync failed after rename")
            return fsync(fd)
        with patch.object(pi.os, "fsync", side_effect=fail), self.assertRaises(pi.InstallError):
            self.install([("a" * 32, "Assets/A", b"changed")], True)
        self.assertTrue(failed)
        self.assertEqual((self.project / "Assets/A").read_bytes(), b"old")

    def test_malformed_or_incomplete_recovery_journal_fails_closed(self):
        directory = self.project / pi.BACKUPS / "crashed"
        directory.mkdir(parents=True)
        for data in (None, "broken json", '{"status":"installing"}', "[]"):
            if data is not None:
                (directory / "journal.json").write_text(data)
            with self.subTest(data=data), self.assertRaises(pi.InstallError):
                self.install([("a" * 32, "Assets/A", b"blocked")])
            self.assertEqual(list((self.project / "Assets").iterdir()), [])

    def test_invalid_archive_stops_queue_at_package_boundary(self):
        (self.root / self.rows[1]["file"]).write_bytes(b"invalid")
        result = self.run_queue()
        self.assertEqual([r["status"] for r in result["results"]], ["installed", "failed", "cancelled"])
        self.assertFalse((self.project / "Assets/File2.txt").exists())

    def test_stop_after_current_even_if_next_package_is_ready(self):
        original = ip.install_package
        def stop(*args):
            result = original(*args)
            Path(self.request["cancel_file"]).touch()
            return result
        with patch.object(ip, "install_package", side_effect=stop):
            result = self.run_queue()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([r["status"] for r in result["results"]], ["installed", "cancelled", "cancelled"])
        self.assertFalse((self.project / "Assets/File1.txt").exists())

    def test_future_staging_failure_does_not_cancel_earlier_packages(self):
        real = ip.multiprocessing.Process
        class Failed:
            def __init__(self, result): self.result = result
            def start(self): Path(self.result).write_text(json.dumps({"ok": False, "error": "cloud failed"}))
            def join(self, timeout=None): pass
            def is_alive(self): return False
        def process(target, args, daemon):
            return Failed(args[2]) if "0002-" in args[1] else real(target=target, args=args, daemon=daemon)
        with patch.object(ip.multiprocessing, "Process", side_effect=process):
            result = self.run_queue()
        self.assertEqual([r["status"] for r in result["results"]], ["installed", "installed", "failed"])

    def test_bounded_staging_and_cancel_terminate_blocked_workers(self):
        self.rows += [{"asset_key": str(i), "file": f"{i}.unitypackage"} for i in range(3, 5)]
        for row in self.rows[3:]: (self.root / row["file"]).write_bytes(b"fixture")
        self.index()
        processes = []
        class Blocked:
            def __init__(self, target=None, args=(), daemon=None):
                self.args, self.alive = args, True
                processes.append(self)
            def start(self): pass
            def is_alive(self): return self.alive
            def join(self, timeout=None): pass
            def terminate(self): self.alive = False
            def kill(self): self.alive = False
        observed = []
        def cancel():
            deadline = time.monotonic() + 5
            while len(processes) < 3 and time.monotonic() < deadline: time.sleep(.01)
            time.sleep(.6)
            observed.append(len(processes))
            Path(self.request["cancel_file"]).touch()
        thread = threading.Thread(target=cancel)
        with patch.object(ip.multiprocessing, "Process", Blocked):
            thread.start()
            result = self.run_queue()
            thread.join()
        self.assertEqual(observed, [3])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(all(not p.alive for p in processes))
        self.assertTrue(all(not Path(p.args[1]).parent.exists() for p in processes))

    def test_stale_index_invalid_requests_and_locks_refuse_mutation(self):
        for value in (42, {**self.request, "mode": "closed"}, {**self.request, "overwrite": "yes"},
                      {**self.request, "cancel_file": False}, {**self.request, "packages": [None]}):
            with self.subTest(value=value), self.assertRaises(ip.RequestError): ip.validate_request(value)
        outside = self.base / "outside.unitypackage"
        outside.write_bytes(b"bad")
        (self.root / "escape.unitypackage").symlink_to(outside)
        for filename in ("../outside.unitypackage", "escape.unitypackage", str(outside)):
            with self.assertRaises(ip.RequestError):
                ip.validate_request({**self.request, "packages": [{"asset_key": "0", "file": filename}]})
        fields = ip.validate_request(self.request)
        with ip._per_project_lock(str(self.project)), self.assertRaises(ip.RequestError): ip.run_import(fields)
        with ip.cv._cleanup_lock(str(self.root)), self.assertRaises(ip.RequestError): ip.run_import(fields)
        with ip.ia.state_write_lock(str(self.state)), self.assertRaises(ip.ia.StateWriteBusy): ip.run_import(fields)
        (self.state / "assets.json").write_text('{"assets": []}')
        with self.assertRaises(ip.RequestError): ip.run_import(fields)
        self.assertEqual(list((self.project / "Assets").iterdir()), [])

    def test_stage_copy_rejects_short_copy_and_source_replacement(self):
        source, target, receipt = self.base / "src", self.base / "dst", self.base / "receipt"
        source.write_bytes(b"x" * 4096)
        read = os.read
        count = 0
        def short(fd, size):
            nonlocal count
            count += 1
            return read(fd, 2048) if count == 1 else b""
        with patch.object(ip.os, "read", side_effect=short): ip._stage_copy(str(source), str(target), str(receipt))
        self.assertFalse(json.loads(receipt.read_text())["ok"])
        self.assertFalse(target.exists())
        replaced = False
        def swap(fd, size):
            nonlocal replaced
            data = read(fd, size)
            if data and not replaced:
                other = self.base / "replacement"
                other.write_bytes(b"y" * 4096)
                other.replace(source)
                replaced = True
            return data
        with patch.object(ip.os, "read", side_effect=swap): ip._stage_copy(str(source), str(target), str(receipt))
        self.assertFalse(json.loads(receipt.read_text())["ok"])
        self.assertFalse(target.exists())
        ip._stage_copy(str(source), str(target), str(receipt))
        self.assertTrue(json.loads(receipt.read_text())["ok"])
        self.assertEqual(target.read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
