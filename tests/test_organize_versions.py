#!/usr/bin/env python3
"""Tests for breadcrumb organize engine. Stdlib only."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402
import organize_versions as ov  # noqa: E402


def make_file(root, rel, size=0):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"0" * size)
    return path


def version(file, size=0, hint=None, ver="1.0"):
    return {"version": ver, "file": file, "size_bytes": size,
            "folder_hint": hint, "latest": False}


def asset(key, name, levels, versions, source="store", non_store=False, verified=True):
    return {
        "asset_key": key,
        "name": name,
        "local_name": name,
        "category": {"path": "/".join(levels), "levels": list(levels), "source": source},
        "non_store": non_store,
        "resolution": {"method": "web-search" if verified else None, "id_verified": verified},
        "versions": list(versions),
    }


def data_of(*assets):
    return {"generated": "t", "file_count": sum(len(a["versions"]) for a in assets),
            "asset_count": len(assets), "assets": list(assets)}


SRC = "Adventures/Publisher/Cool Pack v1.0.unitypackage"
DST = "3D/Props/Weapons/Cool Pack v1.0.unitypackage"


def simple_asset(file=SRC, levels=("3D", "Props", "Weapons"), hint=None, size=0):
    return asset("k1", "Cool Pack", levels, [version(file, size, hint)])


class PlannerTests(unittest.TestCase):
    def plan_for(self, root, *assets, scanned=None):
        return ov.plan_organize(root, data_of(*assets), scanned=scanned)

    def test_full_breadcrumb_destination_with_flattening(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = self.plan_for(root, simple_asset())
            self.assertEqual([m["dst"] for m in plan["moves"]], [DST])
            self.assertEqual(plan["groups"][0]["category"], "3D/Props/Weapons")
            self.assertEqual(plan["totals"]["moves"], 1)

    def test_basename_and_extension_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            src = "Adventures/Pub/Old Pack v2.0.zip"
            make_file(root, src)
            plan = self.plan_for(root, simple_asset(file=src))
            self.assertEqual(os.path.basename(plan["moves"][0]["dst"]), "Old Pack v2.0.zip")

    def test_non_store_and_missing_category_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            a1 = asset("k1", "WM Thing", (), [version("WM_Animset/Thing.unitypackage")],
                       non_store=True)
            a2 = asset("k2", "No Cat", (), [version("Tools/No Cat.unitypackage")],
                       source="folder", verified=True)
            a3 = asset("k3", "Never Resolved", (), [version("Tools/NR.unitypackage")],
                       source="folder", verified=False)
            for a in (a1, a2, a3):
                make_file(root, a["versions"][0]["file"])
            plan = self.plan_for(root, a1, a2, a3)
            self.assertEqual(plan["moves"], [])
            self.assertEqual([s["asset_key"] for s in plan["skips"]["non-store"]], ["k1"])
            by_key = {s["asset_key"]: s for s in plan["skips"]["no-category"]}
            self.assertFalse(by_key["k2"]["resolvable"])
            self.assertTrue(by_key["k3"]["resolvable"])

    def test_already_correct_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, DST)
            plan = self.plan_for(root, simple_asset(file=DST))
            self.assertEqual(plan["moves"], [])
            self.assertEqual([s["asset_key"] for s in plan["skips"]["already-correct"]], ["k1"])

    def test_existing_destination_collision(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            make_file(root, DST, size=5)
            # The occupant is an indexed asset too, already sitting at its
            # destination (already-correct); k1 would land on the same path.
            occupant = asset("k2", "Occupant", ("3D", "Props", "Weapons"),
                             [version(DST, size=5)])
            plan = self.plan_for(root, simple_asset(), occupant)
            self.assertEqual(plan["moves"], [])
            skip = plan["skips"]["collision"][0]
            self.assertEqual(skip["dst"], DST)
            self.assertEqual(skip["occupants"], [DST])
            self.assertEqual(skip["candidates"], [SRC])
            self.assertEqual(len(plan["skips"]["already-correct"]), 1)

    def test_duplicate_planned_destination_aliases_collide(self):
        with tempfile.TemporaryDirectory() as root:
            nfc = "Tools/\u00c1pack v1.0.unitypackage"         # NFC form of Á
            # NFD spelling of the same visible basename (A + combining acute).
            nfd = "Tools/Other/A\u0301pack v1.0.unitypackage"
            make_file(root, nfc)
            make_file(root, nfd)
            a1 = asset("k1", "A", ("3D",), [version(nfc)])
            a2 = asset("k2", "A", ("3D",), [version(nfd)])
            plan = self.plan_for(root, a1, a2)
            self.assertEqual(plan["moves"], [])
            skip = plan["skips"]["collision"][0]
            self.assertEqual(skip["candidates"], sorted([nfc, nfd]))

    def test_unsafe_category_rejects_plan(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            for bad in (("../escape",), ("3D/Props",), ("_quarantine",), ("", "3D"), ("3D", "..")):
                with self.assertRaises(cv.SafetyError):
                    self.plan_for(root, simple_asset(levels=bad))

    def test_source_under_excluded_directory_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            src = "_Quarantine/x v1.0.unitypackage"
            make_file(root, src)
            with self.assertRaises(cv.SafetyError):
                self.plan_for(root, simple_asset(file=src))

    def test_folder_hint_warns_but_never_renames(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = self.plan_for(root, simple_asset(hint="Adventures/Publisher v1.0"))
            self.assertEqual([m["dst"] for m in plan["moves"]], [DST])
            self.assertTrue(plan["moves"][0]["folder_version_warning"])
            self.assertEqual(len(plan["warnings"]), 1)

    def test_deterministic_under_reversed_input(self):
        with tempfile.TemporaryDirectory() as root:
            files = ["Tools/A v1.0.unitypackage", "Tools/B v1.0.unitypackage"]
            for f in files:
                make_file(root, f)
            a1 = asset("k1", "A", ("3D",), [version(files[0]), version("Tools/A v0.9.unitypackage")])
            a2 = asset("k2", "B", ("3D",), [version(files[1])])
            make_file(root, "Tools/A v0.9.unitypackage")
            forward = self.plan_for(root, a1, a2)
            a1r = dict(a1, versions=list(reversed(a1["versions"])))
            backward = self.plan_for(root, a2, a1r)
            self.assertEqual([m["src"] for m in forward["moves"]],
                             [m["src"] for m in backward["moves"]])
            self.assertEqual(forward["groups"], backward["groups"])

    def test_blocked_ancestor_skip_not_abort(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            make_file(root, "3D")  # stray file where a category dir must live
            plan = self.plan_for(root, simple_asset())
            self.assertEqual(plan["moves"], [])
            self.assertEqual(plan["skips"]["blocked-ancestor"][0]["path"], "3D")

    def test_symlinked_ancestor_skip(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            outside = os.path.join(root, "elsewhere")
            os.makedirs(outside)
            os.symlink(outside, os.path.join(root, "3D"))
            plan = self.plan_for(root, simple_asset())
            self.assertEqual(plan["moves"], [])
            self.assertEqual(plan["skips"]["blocked-ancestor"][0]["path"], "3D")

    def test_stale_manifest_aborts(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC, size=3)
            with self.assertRaises(cv.SafetyError):
                self.plan_for(root, simple_asset(size=4))


class SnapshotTests(unittest.TestCase):
    def test_symlink_source_rejected(self):
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as out:
            outside = os.path.join(out, "outside.unitypackage")
            make_file(out, "outside.unitypackage")
            os.makedirs(os.path.join(root, "Adventures", "Publisher"))
            os.symlink(outside, os.path.join(root, SRC))
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            with self.assertRaises(cv.SafetyError):
                ov.capture_snapshot(root, plan, scanned)


class ApplyTests(unittest.TestCase):
    def apply_simple(self, root, *assets, pre=None):
        plan = ov.plan_organize(root, data_of(*assets))
        scanned = ia.scan(root, strict=True)
        snapshot = ov.capture_snapshot(root, plan, scanned)
        if pre:
            pre()
        return ov.apply_organize(root, None, plan, snapshot)

    def test_dry_run_cli_mutates_nothing(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            make_file(root, SRC)
            with open(os.path.join(state, "assets.json"), "w", encoding="utf-8") as fh:
                json.dump(data_of(simple_asset()), fh)
            with mock.patch.object(ov.ia, "main") as update:
                rc = ov.main(["--root", root, "--state", state])
            self.assertEqual(rc, 0)
            update.assert_not_called()
            self.assertTrue(os.path.exists(os.path.join(root, SRC)))
            self.assertFalse(os.path.exists(os.path.join(root, "3D")))

    def test_apply_success_moves_and_cleans_only_empty_parents(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            make_file(root, "Adventures/Publisher/Keep v1.0.unitypackage")
            make_file(root, "Solo/Last v1.0.unitypackage")
            keeper = asset("k2", "Keep", (), [version("Adventures/Publisher/Keep v1.0.unitypackage")],
                           source="folder")
            mover2 = asset("k3", "Last", ("Audio",), [version("Solo/Last v1.0.unitypackage")])
            with mock.patch.object(ov.ia, "main", return_value=0) as update:
                report = self.apply_simple(root, simple_asset(), keeper, mover2)
            update.assert_called_once_with(["update"])
            self.assertTrue(os.path.exists(os.path.join(root, DST)))
            self.assertFalse(os.path.exists(os.path.join(root, SRC)))
            # Still-occupied parent survives; fully emptied parent chain removed.
            self.assertTrue(os.path.exists(os.path.join(root, "Adventures/Publisher")))
            self.assertFalse(os.path.exists(os.path.join(root, "Solo")))
            self.assertEqual(report["moves_applied"], 2)

    def test_same_size_replacement_aborts_before_moves(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC, size=10)
            plan = ov.plan_organize(root, data_of(simple_asset(size=10)))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            replacement = os.path.join(root, "replacement.bin")
            with open(replacement, "wb") as fh:
                fh.write(b"abcdefghij")
            os.replace(replacement, os.path.join(root, SRC))
            with self.assertRaises(cv.SafetyError):
                ov.apply_organize(root, None, plan, snapshot)
            self.assertTrue(os.path.exists(os.path.join(root, SRC)))

    def test_destination_appearing_aborts_with_zero_moves(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            make_file(root, DST, size=2)
            with self.assertRaises(cv.SafetyError):
                ov.apply_organize(root, None, plan, snapshot)
            self.assertTrue(os.path.exists(os.path.join(root, SRC)))

    def test_mtime_only_change_does_not_abort(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            os.utime(os.path.join(root, SRC), (1000, 2000000000))
            with mock.patch.object(ov.ia, "main", return_value=0):
                report = ov.apply_organize(root, None, plan, snapshot)
            self.assertEqual(report["moves_applied"], 1)

    def test_second_move_failure_reverses_first(self):
        with tempfile.TemporaryDirectory() as root:
            src_a = "Old/A v1.0.unitypackage"
            src_b = "Old/B v1.0.unitypackage"
            make_file(root, src_a)
            make_file(root, src_b)
            assets = (asset("k1", "A", ("3D",), [version(src_a)]),
                      asset("k2", "B", ("3D",), [version(src_b)]))
            plan = ov.plan_organize(root, data_of(*assets))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_link = os.link

            def flaky_link(src, dst, *a, **k):
                if dst.endswith("B v1.0.unitypackage"):
                    raise OSError("injected failure")
                return real_link(src, dst, *a, **k)

            with mock.patch.object(ov.os, "link", flaky_link), \
                 mock.patch.object(ov.ia, "main", return_value=0):
                with self.assertRaises(cv.SafetyError):
                    ov.apply_organize(root, None, plan, snapshot)
            self.assertTrue(os.path.exists(os.path.join(root, src_a)))
            self.assertFalse(os.path.exists(os.path.join(root, "3D/A v1.0.unitypackage")))
            self.assertTrue(os.path.exists(os.path.join(root, src_b)))

    def test_unlink_failure_after_link_rolls_back_new_link(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_unlink = os.unlink

            def flaky_unlink(path, *a, **k):
                if path.endswith(SRC) and "3D/" not in path:
                    raise OSError("injected unlink failure")
                return real_unlink(path, *a, **k)

            with mock.patch.object(ov.os, "unlink", flaky_unlink), \
                 mock.patch.object(ov.ia, "main", return_value=0) as update:
                with self.assertRaises(cv.SafetyError):
                    ov.apply_organize(root, None, plan, snapshot)
            # The committed destination link was dropped; the original is intact.
            self.assertTrue(os.path.exists(os.path.join(root, SRC)))
            self.assertFalse(os.path.exists(os.path.join(root, DST)))
            update.assert_called_once()

    def test_rollback_incomplete_reports_residual_and_reconciles(self):
        with tempfile.TemporaryDirectory() as root:
            src_a = "Old/A v1.0.unitypackage"
            src_b = "Old/B v1.0.unitypackage"
            make_file(root, src_a)
            make_file(root, src_b)
            assets = (asset("k1", "A", ("3D",), [version(src_a)]),
                      asset("k2", "B", ("3D",), [version(src_b)]))
            plan = ov.plan_organize(root, data_of(*assets))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_link = os.link
            real_unlink = os.unlink

            def flaky_link(src, dst, *a, **k):
                if dst.endswith("B v1.0.unitypackage"):
                    raise OSError("injected failure")
                return real_link(src, dst, *a, **k)

            def flaky_unlink(path, *a, **k):
                if path.endswith("3D/A v1.0.unitypackage"):
                    raise OSError("rollback blocked")
                return real_unlink(path, *a, **k)

            with mock.patch.object(ov.os, "link", flaky_link), \
                 mock.patch.object(ov.os, "unlink", flaky_unlink), \
                 mock.patch.object(ov.ia, "main", return_value=0) as update:
                with self.assertRaises(cv.SafetyError) as ctx:
                    ov.apply_organize(root, None, plan, snapshot)
            self.assertIn("3D/A v1.0.unitypackage", str(ctx.exception))
            update.assert_called_once()  # reconciliation against actual disk
            self.assertTrue(os.path.exists(os.path.join(root, "3D/A v1.0.unitypackage")))

    def test_fileexists_race_skips_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as root:
            src_a = "Old/A v1.0.unitypackage"
            src_b = "Old/B v1.0.unitypackage"
            make_file(root, src_a)
            make_file(root, src_b)
            assets = (asset("k1", "A", ("3D",), [version(src_a)]),
                      asset("k2", "B", ("3D",), [version(src_b)]))
            plan = ov.plan_organize(root, data_of(*assets))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_link = os.link

            def racing_link(src, dst, *a, **k):
                if dst.endswith("B v1.0.unitypackage"):
                    make_file(root, "3D/B v1.0.unitypackage", size=7)
                return real_link(src, dst, *a, **k)

            with mock.patch.object(ov.os, "link", racing_link), \
                 mock.patch.object(ov.ia, "main", return_value=0) as update:
                report = ov.apply_organize(root, None, plan, snapshot)
            update.assert_called_once_with(["update"])
            self.assertEqual(report["moves_applied"], 1)
            self.assertEqual([s["dst"] for s in report["skipped_collisions"]],
                             ["3D/B v1.0.unitypackage"])
            with open(os.path.join(root, "3D/B v1.0.unitypackage"), "rb") as fh:
                self.assertEqual(fh.read(), b"0000000")
            self.assertTrue(os.path.exists(os.path.join(root, src_b)))

    def test_target_parent_device_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            os.makedirs(os.path.join(root, "3D"))  # nearest existing dst ancestor
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_stat = os.stat

            def foreign_stat(path, *a, **k):
                st = real_stat(path, *a, **k)
                # _contained_path realpaths root (/var -> /private/var), so the
                # walk's paths differ textually from the fixture root.
                if os.path.realpath(path) == os.path.realpath(os.path.join(root, "3D")):
                    return mock.Mock(st_dev=st.st_dev + 1)
                return st

            with mock.patch.object(ov, "_stat", foreign_stat):
                with self.assertRaises(cv.SafetyError):
                    ov.apply_organize(root, None, plan, snapshot)
            self.assertTrue(os.path.exists(os.path.join(root, SRC)))

    def test_update_failure_after_moves_reports_moves_applied(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            with mock.patch.object(ov.ia, "main", side_effect=RuntimeError("disk full")):
                with self.assertRaises(cv.SafetyError) as ctx:
                    ov.apply_organize(root, None, plan, snapshot)
            self.assertIn("applied 1 moves", str(ctx.exception))
            self.assertTrue(os.path.exists(os.path.join(root, DST)))
            self.assertFalse(os.path.exists(os.path.join(root, SRC)))

    def test_no_hydration_guard(self):
        with tempfile.TemporaryDirectory() as root:
            make_file(root, SRC)
            plan = ov.plan_organize(root, data_of(simple_asset()))
            scanned = ia.scan(root, strict=True)
            snapshot = ov.capture_snapshot(root, plan, scanned)
            real_open = open

            def no_archive_open(file, *a, **k):
                if str(file).endswith(ia.ARCHIVE_EXTS):
                    raise AssertionError("archive hydration attempted")
                return real_open(file, *a, **k)

            with mock.patch("builtins.open", no_archive_open), \
                 mock.patch.object(ov.ia, "main", return_value=0):
                report = ov.apply_organize(root, None, plan, snapshot)
            self.assertEqual(report["moves_applied"], 1)


if __name__ == "__main__":
    unittest.main()
