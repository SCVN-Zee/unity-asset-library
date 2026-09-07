#!/usr/bin/env python3
"""Tests for conservative version cleanup. Stdlib only."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import cleanup_versions as cv  # noqa: E402


def record(path, version, date=None, pipeline=None, discriminators=None, size=10):
    parsed = cv.ia.parse(path)
    parsed.update(version=version, release_date=date, pipeline=pipeline,
                  discriminators=discriminators or [], size=size, mtime=1.0,
                  st_blocks=1)
    return parsed


class CleanupPlanTests(unittest.TestCase):
    def test_higher_version_removes_lower_version(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v1.0.unitypackage", "1.0"),
            record("Tools/Foo v2.0.unitypackage", "2.0"),
        ])
        self.assertEqual([r["rel_path"] for r in plan["removals"]], ["Tools/Foo v1.0.unitypackage"])
        self.assertEqual(plan["families"][0]["survivor"]["rel_path"], "Tools/Foo v2.0.unitypackage")

    def test_date_breaks_equal_version(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v1.0 (01 Jan 2024).unitypackage", "1.0", "2024-01-01"),
            record("Tools/Foo v1.0 (01 Jan 2025).unitypackage", "1.0", "2025-01-01"),
        ])
        self.assertEqual([r["rel_path"] for r in plan["removals"]], ["Tools/Foo v1.0 (01 Jan 2024).unitypackage"])

    def test_stable_version_beats_higher_prerelease(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v4.4.8b.unitypackage", "4.4.8b"),
            record("Tools/Foo v4.5.0-pre.unitypackage", "4.5.0-pre"),
        ])
        self.assertEqual([r["rel_path"] for r in plan["removals"]], ["Tools/Foo v4.5.0-pre.unitypackage"])

    def test_unparseable_family_is_protected(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v1.0.unitypackage", "1.0"),
            record("Tools/Foo.unitypackage", None),
        ])
        self.assertFalse(plan["removals"])
        self.assertEqual(plan["families"][0]["reason"], "unparseable-version")

    def test_exact_maximum_tie_is_protected(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v1.0 (01 Jan 2025).unitypackage", "1.0", "2025-01-01"),
            record("Archive/Foo v1.0 (01 Jan 2025).zip", "1.0", "2025-01-01"),
        ])
        self.assertFalse(plan["removals"])
        self.assertEqual(plan["families"][0]["reason"], "maximum-tie")
        self.assertIn("protected foo (maximum-tie)", cv.render_plan(plan))

    def test_pipeline_and_discriminator_variants_do_not_compete(self):
        plan = cv.plan_cleanup([
            record("Tools/Foo v1.0 (URP).unitypackage", "1.0", pipeline="URP"),
            record("Tools/Foo v2.0 (URP).unitypackage", "2.0", pipeline="URP"),
            record("Tools/Foo v1.0 (HDRP).unitypackage", "1.0", pipeline="HDRP"),
            record("Tools/Foo v1.0 (Source).unitypackage", "1.0", discriminators=["Source"]),
        ])
        self.assertEqual([r["rel_path"] for r in plan["removals"]], ["Tools/Foo v1.0 (URP).unitypackage"])

    def test_dry_run_never_unlinks_or_updates(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage"):
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
            with mock.patch.object(cv, "apply_cleanup") as apply, mock.patch.object(cv.ia, "main") as update:
                rc = cv.main(["--root", root])
            self.assertEqual(rc, 0)
            apply.assert_not_called()
            update.assert_not_called()
            self.assertTrue(os.path.exists(os.path.join(root, "Tools/Foo v1.0.unitypackage")))

    def test_apply_rejects_symlink_and_parent_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "Tools"))
            target = os.path.join(root, "outside.unitypackage")
            open(target, "wb").close()
            link = os.path.join(root, "Tools/Foo v1.0.unitypackage")
            os.symlink(target, link)
            with self.assertRaises(cv.SafetyError):
                cv.validate_candidate(root, "Tools/Foo v1.0.unitypackage", {})
            with self.assertRaises(cv.SafetyError):
                cv.validate_candidate(root, "../outside.unitypackage", {})

    def test_apply_rejects_same_size_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            names = ("Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage")
            scanned = []
            for name in names:
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(b"0123456789")
                scanned.append(record(name, name.split("v")[1].split(".")[0] + ".0", size=10))
            snapshot = cv.capture_snapshot(root, scanned)
            candidate = os.path.join(root, names[0])
            replacement = candidate + ".replacement"
            with open(replacement, "wb") as fh:
                fh.write(b"abcdefghij")
            os.replace(replacement, candidate)
            with self.assertRaises(cv.SafetyError):
                cv.preflight(root, scanned, snapshot)

    def test_apply_preserves_configured_output_handoff(self):
        with tempfile.TemporaryDirectory() as root:
            paths = ["Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage"]
            scanned = []
            for name in paths:
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
                scanned.append(record(name, name.split("v")[1].split(".")[0] + ".0", size=0))
            plan = cv.plan_cleanup(scanned)
            with mock.patch.object(cv.ia, "main", return_value=0) as update:
                self.assertEqual(cv.apply_cleanup(root, None, scanned, plan), 0)
            update.assert_called_once_with(["update"])
            self.assertFalse(os.path.exists(os.path.join(root, paths[0])))

    def test_apply_progress_counts_only_committed_removals(self):
        with tempfile.TemporaryDirectory() as root:
            paths = ["Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage"]
            scanned = []
            for name in paths:
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(b"x")
                scanned.append(record(name, name.split("v")[1].split(".")[0] + ".0", size=1))
            events = []
            with mock.patch.object(cv.ia, "main", return_value=0):
                self.assertEqual(cv.apply_cleanup(root, None, scanned, cv.plan_cleanup(scanned),
                                                  progress=events.append), 0)
            self.assertEqual(events[-1]["stage"], "complete")
            self.assertEqual(events[-1]["counts"]["removed"], 1)
    def test_state_lock_held_forwards_to_index_update(self):
        with tempfile.TemporaryDirectory() as root:
            paths = ["Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage"]
            scanned = []
            for name in paths:
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "wb").close()
                scanned.append(record(name, name.split("v")[1].split(".")[0] + ".0", size=0))
            with mock.patch.object(cv.ia, "main", return_value=0) as update:
                self.assertEqual(cv.apply_cleanup(root, None, scanned, cv.plan_cleanup(scanned),
                                                  state_lock_held=True), 0)
            update.assert_called_once_with(["update"], state_lock_held=True)

        with tempfile.TemporaryDirectory() as root:
            paths = ["Tools/Foo v1.0.unitypackage", "Tools/Foo v2.0.unitypackage"]
            scanned = []
            for name in paths:
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(b"x")
                scanned.append(record(name, name.split("v")[1].split(".")[0] + ".0", size=1))
            with mock.patch.object(cv.ia, "main", return_value=1):
                with self.assertRaises(cv.SafetyError) as ctx:
                    cv.apply_cleanup(root, None, scanned, cv.plan_cleanup(scanned))
            self.assertTrue(ctx.exception.result["applied"])
            self.assertFalse(ctx.exception.result["state_refreshed"])

if __name__ == "__main__":
    unittest.main()
