#!/usr/bin/env python3
"""Phase 1 tests. Stdlib unittest — pytest is not installed in this environment.

Run:  python3 -m unittest discover -s tests -v
"""

import gzip
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import index_assets as ia  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config.json"), encoding="utf-8") as _fh:
    REAL_ROOT = json.load(_fh)["vault_root"]
# Recalibrated 2026-09-02 after superseded-version cleanup (26 archives removed).
# Bump after every intentional download or cleanup.
EXPECTED_FILE_COUNT = 854


def P(name, parent=""):
    """Parse a bare filename, optionally inside a parent folder."""
    return ia.parse(os.path.join(parent, name) if parent else name)


class TestScannerNeverOpens(unittest.TestCase):
    """The constraint the whole design exists to protect."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "sub"))
        for rel in ("A v1.0.unitypackage", os.path.join("sub", "B v2.0.unitypackage")):
            with open(os.path.join(self.tmp, rel), "w") as fh:
                fh.write("x")

    def test_scanner_never_opens_a_file(self):
        def explode(*a, **k):
            raise AssertionError("scanner opened a file — would hydrate OneDrive")

        with mock.patch("builtins.open", explode), \
             mock.patch.object(io, "open", explode), \
             mock.patch.object(tarfile, "open", explode), \
             mock.patch.object(zipfile, "ZipFile", explode), \
             mock.patch.object(gzip, "open", explode), \
             mock.patch.object(Path, "read_bytes", explode):
            result = ia.scan(self.tmp)

        self.assertEqual(len(result), 2)

    def test_scan_progress_is_unknown_until_final_and_observer_is_nonfatal(self):
        events = []
        result = ia.scan(self.tmp, progress=events.append)
        self.assertEqual(len(result), 2)
        self.assertIsNone(events[0]["total"])
        self.assertEqual((events[-1]["completed"], events[-1]["total"]), (2, 2))

        def explode(_event):
            raise RuntimeError("observer disconnected")
        self.assertEqual(len(ia.scan(self.tmp, progress=explode)), 2)

    def test_scan_records_metadata_only(self):
        rec = ia.scan(self.tmp)[0]
        for key in ("rel_path", "size", "mtime", "st_blocks", "format"):
            self.assertIn(key, rec)

    def test_no_name_based_directory_skipping_in_source(self):
        """'tools' and 'Tools' share an inode here; a name-based skip drops 191 archives.

        The exclusion list may be consulted to BUILD a set of resolved paths, but the
        pruning decision itself must compare resolved paths only.
        """
        src = Path(ia.__file__).read_text(encoding="utf-8")
        walk = src.split("def scan(", 1)[1].split("\ndef ", 1)[0]
        prune = walk.split("dirnames[:]", 1)[1].split("]", 1)[0]
        self.assertIn("realpath", prune)
        self.assertNotIn("EXCLUDED_DIR_NAMES", prune)
        self.assertNotRegex(prune, r'd\s*(==|!=|\bin\b)\s*[\'"(]')


class TestRealTree(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(os.path.join(REAL_ROOT, "3D")), "real library absent")
    def test_scan_of_real_root_finds_full_library(self):
        # 854 was the library size when this floor was written; downloads keep
        # arriving, so pin the floor, not the mutable live count.
        self.assertGreaterEqual(len(ia.scan(REAL_ROOT)), EXPECTED_FILE_COUNT)

    @unittest.skipUnless(os.path.isdir(os.path.join(REAL_ROOT, "Tools")), "real library absent")
    def test_tools_directory_is_not_skipped(self):
        paths = [r["rel_path"] for r in ia.scan(REAL_ROOT)]
        self.assertGreater(len([p for p in paths if p.startswith("Tools" + os.sep)]), 150)


class TestParse(unittest.TestCase):
    """Every case is a real filename from this library."""

    def test_version_extraction(self):
        cases = [
            ("Amplify Shader Editor v1.9.9.12.unitypackage", "1.9.9.12"),
            ("Bakery - GPU Lightmapper vv1.96.unitypackage", "1.96"),
            ("POLYGON City - Low Poly 3D Art by Synty 1.11.3.unitypackage", "1.11.3"),
            ("Pure Nature 2 Meadows v2.1 (01 Apr 2025).unitypackage", "2.1"),
            ("All In 1 Sprite Shader v4.25.unitypackage", "4.25"),
            ("Monsters Ultimate Pack 03 Cute Series v1.0.unitypackage", "1.0"),
            ("Particle Dynamic Magic 2 v2.5.3 Unity.unitypackage", "2.5.3"),
            ("Creepy Animatronic Anims (v1.0).unitypackage", "1.0"),
            ("Procedural Generation Grid (Beta) v1.6.6.2 (03 Dec 2024).unitypackage", "1.6.6.2"),
            ("Editor Console Pro v3.981 (14 Jan 2026).unitypackage", "3.981"),
            ("Retro Horror Template.unitypackage", None),
            ("Kenney Game Assets All-in-1.zip", None),
            ("Stylized Christmas Town (Unity 2020.3.26f1).unitypackage", None),
        ]
        for name, want in cases:
            with self.subTest(name=name):
                self.assertEqual(P(name)["version"], want)

    def test_title_extraction(self):
        cases = [
            ("Pure Nature 2 Meadows v2.1 (01 Apr 2025).unitypackage", "Pure Nature 2 Meadows"),
            ("All In 1 Sprite Shader v4.25.unitypackage", "All In 1 Sprite Shader"),
            ("Monsters Ultimate Pack 03 Cute Series v1.0.unitypackage",
             "Monsters Ultimate Pack 03 Cute Series"),
            ("Particle Dynamic Magic 2 v2.5.3 Unity.unitypackage", "Particle Dynamic Magic 2"),
            ("Amplify Shader Editor v1.9.9.12.unitypackage", "Amplify Shader Editor"),
        ]
        for name, want in cases:
            with self.subTest(name=name):
                self.assertEqual(P(name)["title"], want)

    def test_underscore_form(self):
        p = P("POLYGON_NatureBiomes_MeadowForest_Unity_2021_3_v1_9_2.unitypackage")
        self.assertEqual(p["version"], "1.9.2")
        self.assertEqual(p["editor_version"], "2021.3")

    def test_editor_version_is_never_a_package_version(self):
        p = P("Stylized Christmas Town (Unity 2020.3.26f1).unitypackage")
        self.assertIsNone(p["version"])
        self.assertIsNotNone(p["editor_version"])

    def test_prerelease_detected(self):
        p = P("Horse Animset Pro (Riding System) v4.5.0-pre (30 Jul 2025).unitypackage")
        self.assertEqual(p["version"], "4.5.0-pre")
        self.assertTrue(p["prerelease"])
        self.assertFalse(P("Horse Animset Pro Riding System v4.4.8b (23 Jan 2025).unitypackage")["prerelease"])

    def test_pipeline_only_in_variant_forms(self):
        self.assertEqual(P("ChineseAlley_URP_2021.3.6f1.unitypackage")["pipeline"], "URP")
        self.assertEqual(P("Stylized Solarpunk City_Builtin_2021.3.6f1.unitypackage")["pipeline"], "BUILTIN")
        self.assertEqual(
            P("Amusement Park - Low Poly Asset Pack by ithappy v1.1 (URP version).unitypackage")["pipeline"],
            "URP")
        # A bare pipeline word in a title is the product name, not a variant.
        for name in ("Beautify HDRP v7.1.1 (24 Dec 2024).unitypackage",
                     "Volumetric Lights 2 HDRP v6.8.5.unitypackage"):
            with self.subTest(name=name):
                p = P(name)
                self.assertIsNone(p["pipeline"])
                self.assertIn("HDRP", p["title"])

    def test_double_dot_typo_tolerated(self):
        p = P("Stylized Azure Hillside_URP_2021.3.6f1..unitypackage")
        self.assertEqual(p["pipeline"], "URP")

    def test_folder_supplies_missing_version(self):
        p = P("ChineseAlley_URP_2021.3.6f1.unitypackage", "3D/Chinese Alley Environment v1.0")
        self.assertEqual(p["version"], "1.0")
        self.assertEqual(p["folder_hint"], "Chinese Alley Environment v1.0")

    def test_discriminators_retained(self):
        for name, want in [
            ("GUI Pro - Simple Casual (PSD) v1.0.7 (27 Nov 2024).unitypackage", "PSD"),
            ("Universal Fighting Engine 2 (Source) v2.50.unitypackage", "Source"),
            ("GSpawn - Level Designer (PRO) v3.4.0.unitypackage", "PRO"),
        ]:
            with self.subTest(name=name):
                self.assertIn(want, P(name)["discriminators"])

    def test_leading_parenthetical_roles_differ(self):
        """(SE) is part of the store title; (Bug) is the user's own annotation."""
        self.assertIn("SE", P("(SE) Bark Howl Growl v2.0.unitypackage")["title"])
        self.assertNotIn("Bug", P("(Bug) Combat animations - Kung fu V1 v1.0.unitypackage")["title"])

    def test_release_date_both_orders(self):
        self.assertEqual(P("Shader Graph Baker v2025.3 (05 Jul 2025).unitypackage")["release_date"],
                         "2025-07-05")
        self.assertEqual(P("DOTween Pro v1.0.380 (Feb 2 2024).unitypackage")["release_date"],
                         "2024-02-02")

    def test_format_and_non_store(self):
        self.assertEqual(P("Kenney Game Assets All-in-1.zip")["format"], "zip")
        self.assertTrue(P("Kenney Game Assets All-in-1.zip")["non_store"])
        self.assertTrue(P("BattleSimulator.zip")["non_store"])
        self.assertTrue(P("SrRubfish_VFX_02.unitypackage")["non_store"])
        self.assertTrue(P("TwinDaggers_Animset v2.unitypackage", "3D/Animations/WM_Animset")["non_store"])
        self.assertFalse(P("Amplify Shader Editor v1.9.9.12.unitypackage")["non_store"])


class TestVersionOrdering(unittest.TestCase):
    def test_prerelease_never_outranks_stable(self):
        """Horse Animset Pro: 4.5.0-pre is numerically highest but must not win."""
        items = [("4.5.0-pre",), ("4.4.8b",), ("4.4.7",)]
        self.assertEqual(ia.pick_latest(items)[0], "4.4.8b")

    def test_mtime_is_never_a_version_signal(self):
        """AllSky: the higher semver carries the OLDER mtime. OneDrive rewrote it."""
        items = [{"version": "5.1.0", "mtime": 1732856300},
                 {"version": "5.2.0", "mtime": 1725377583}]
        got = ia.pick_latest(items, version_of=lambda i: i["version"])
        self.assertEqual(got["version"], "5.2.0")

    def test_numeric_not_lexical(self):
        for hi, lo in [("2.18.5", "2.16.0"), ("2.10", "2.9"), ("6.18.7", "6.18.4"),
                       ("2025.3", "2025.1"), ("1.9.9.12", "1.9.9.9")]:
            with self.subTest(hi=hi, lo=lo):
                self.assertGreater(ia.version_key(hi), ia.version_key(lo))

    def test_odd_versions_parse(self):
        for v in ("1.6.6.2", "3.981", "1.0.4.0", "2.11.0a", "1.1.1EA", "1.96"):
            with self.subTest(v=v):
                self.assertTrue(ia.version_key(v)[1])

    def test_unparseable_yields_none(self):
        self.assertIsNone(ia.pick_latest([{"version": None}], version_of=lambda i: i["version"]))


class TestGrouping(unittest.TestCase):
    """The safety invariant: no `duplicate` verdict across distinct products."""

    FALSE_FAMILIES = [
        ["All In 1 3D-Shader v1.61", "All In 1 Springs Toolkit v1.45",
         "All In 1 Sprite Lighting v2.25", "All In 1 Sprite Shader v4.25",
         "All In 1 Vfx Toolkit v2.1"],
        ["Pure Nature 2 Meadows v2.1", "Pure Nature 2 Mountains v2.1",
         "Pure Nature 2 Islands v2.1", "Pure Nature v1.2"],
        ["Monsters Ultimate Pack 01 Cute Series v1.0",
         "Monsters Ultimate Pack 03 Cute Series v1.0"],
        ["Epic Toon VFX 2 v1.3", "Epic Toon VFX 3 v1.0"],
        ["GUI Pro - Simple Casual (PSD) v1.0.7", "GUI Pro - Simple Casual v1.0.7"],
    ]

    def test_no_duplicate_verdict_across_distinct_products(self):
        """Adversarial: force an identical size so only identity can save us."""
        for family in self.FALSE_FAMILIES:
            with self.subTest(family=family[0]):
                entries = []
                for f in family:
                    p = P(f + ".unitypackage")
                    p["size"] = 123_456_789  # same size for every member
                    entries.append(p)
                for g in ia.group(entries):
                    self.assertNotEqual(g["verdict"], "duplicate",
                                        f"false duplicate: {family} -> {g}")

    def test_psd_sibling_never_shares_identity(self):
        a = ia.asset_key(P("GUI Pro - Simple Casual (PSD) v1.0.7.unitypackage"))
        b = ia.asset_key(P("GUI Pro - Simple Casual v1.0.7.unitypackage"))
        self.assertNotEqual(a, b)

    def test_same_size_same_folder_has_no_winner(self):
        """66 Creatures: both copies in 3D/, so no path heuristic applies."""
        e = []
        for n in ("66 Creatures Super Mega (Pack) v1.0 (10 Nov 2024)",
                  "66 Creatures Super Mega Pack"):
            p = P(n + ".unitypackage", "3D")
            p["size"] = 4_162_660_084
            e.append(p)
        groups = ia.group(e)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["reason"], "same-size-same-folder")
        self.assertEqual(groups[0]["identity_evidence"], "size+name, unhashed")

    def test_size_coincidence_is_not_a_duplicate(self):
        """Two unrelated 4096-byte broken downloads."""
        e = []
        for n, d in (("Particle Dynamic Magic 2 v2.5.3 Unity", ""),
                     ("Prefab Brush - Easy Object Placement Tool Level Designer v1.3.3", "Tools")):
            p = P(n + ".unitypackage", d)
            p["size"] = 4096
            e.append(p)
        g = ia.group(e)[0]
        self.assertEqual(g["verdict"], "distinct")
        self.assertEqual(g["reason"], "size-coincidence")

    def test_cross_folder_same_version_is_duplicate(self):
        e = []
        for d in ("", "Audio"):
            p = P("Pro Sound Collection v1.3.unitypackage", d)
            p["size"] = 2_121_358_051
            e.append(p)
        g = ia.group(e)[0]
        self.assertEqual(g["verdict"], "duplicate")
        self.assertEqual(g["reason"], "same-size-same-version-different-folder")

    def test_verdicts_never_prescribe_an_action(self):
        allowed = {"duplicate", "variant", "distinct"}
        e = []
        for d in ("", "Audio"):
            p = P("Pro Sound Collection v1.3.unitypackage", d)
            p["size"] = 10
            e.append(p)
        for g in ia.group(e):
            self.assertIn(g["verdict"], allowed)
            self.assertNotIn("quarantine", str(g).lower())
            self.assertNotIn("delete", str(g).lower())


class TestIntegrityTiers(unittest.TestCase):
    def test_byte_literal_thresholds(self):
        self.assertEqual(ia.BROKEN_MAX_BYTES, 10_000)
        self.assertEqual(ia.SUSPICIOUS_MAX_BYTES, 100_000)
        self.assertEqual(ia.integrity_tier(4096), "broken")
        self.assertEqual(ia.integrity_tier(77_642), "suspicious")
        self.assertIsNone(ia.integrity_tier(100_095))  # just over the line
        self.assertEqual(ia.integrity_tier(77_642), "suspicious")
        self.assertIsNone(ia.integrity_tier(50_000_000))





class TestAtomicWrite(unittest.TestCase):
    def test_replaces_atomically_and_leaves_no_tmp(self):
        d = tempfile.mkdtemp()
        target = os.path.join(d, "a.json")
        ia.write_atomic(target, '{"a":1}')
        self.assertEqual(Path(target).read_text(), '{"a":1}')
        ia.write_atomic(target, '{"a":2}')
        self.assertEqual(Path(target).read_text(), '{"a":2}')
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])

    def test_failed_write_cleans_up(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(TypeError):
            ia.write_atomic(os.path.join(d, "b.json"), None)
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class TestBuildDeterminism(unittest.TestCase):
    def test_two_builds_agree_ignoring_volatile_fields(self):
        scanned = [
            {"rel_path": "Tools/A v1.0.unitypackage", "size": 10, "mtime": 1,
             "st_blocks": 0, "format": "unitypackage"},
            {"rel_path": "Tools/A v1.1.unitypackage", "size": 20, "mtime": 2,
             "st_blocks": 8, "format": "unitypackage"},
        ]
        import copy
        a = ia.build(copy.deepcopy(scanned))
        b = ia.build(copy.deepcopy([dict(s, mtime=s["mtime"] + 999, st_blocks=99)
                                    for s in scanned]))
        a.pop("generated"), b.pop("generated")
        self.assertEqual(a, b, "build must not depend on mtime or st_blocks")

    def test_versions_sorted_newest_first_and_latest_flagged(self):
        scanned = [
            {"rel_path": "T/A v1.0.unitypackage", "size": 1, "mtime": 9, "st_blocks": 0,
             "format": "unitypackage"},
            {"rel_path": "T/A v2.0.unitypackage", "size": 2, "mtime": 1, "st_blocks": 0,
             "format": "unitypackage"},
        ]
        asset = ia.build(scanned)["assets"][0]
        self.assertEqual(asset["versions"][0]["version"], "2.0")
        self.assertTrue(asset["versions"][0]["latest"])
        self.assertFalse(asset["versions"][1]["latest"])




class TestReviewFindingFixes(unittest.TestCase):
    """Regressions for defects found in code review after the first implementation."""

    def test_literal_period_after_v_parses(self):
        """M2: 'Shift - Complete Sci-Fi UI v.2.0.11' swallowed its version into the title."""
        p = P("Shift - Complete Sci-Fi UI v.2.0.11.unitypackage")
        self.assertEqual(p["version"], "2.0.11")
        self.assertNotIn("2.0.11", p["title"])

    def test_pipeline_pair_is_variant_not_distinct(self):
        """M1: the 'variant' verdict was dead code — asset_key folds in pipeline, so a
        URP/HDRP pair reached the distinct branch first and read as unrelated."""
        e = []
        for pipe in ("URP", "HDRP"):
            p = P(f"ChineseAlley_{pipe}_2021.3.6f1.unitypackage", "3D/Chinese Alley Environment v1.0")
            p["size"] = 555_000_000
            e.append(p)
        g = ia.group(e)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["verdict"], "variant")
        self.assertEqual(g[0]["reason"], "pipeline-variants")
        self.assertEqual(g[0]["redundant_bytes"], 0, "variants are never redundant")

    def test_different_versions_are_review_not_duplicate(self):
        """H1: AllSky 5.1.0/5.2.0 are two real releases. Calling them 'duplicate' and
        totalling 5.95 GB 'redundant' invites deleting a deliberately-kept release."""
        e = []
        for ver, d in (("5.1.0", "VFX"), ("5.2.0", "2D")):
            p = P(f"AllSky - 220 Sky Skybox Set v{ver}.unitypackage", d)
            p["size"] = 5_948_381_557
            e.append(p)
        g = ia.group(e)[0]
        self.assertEqual(g["verdict"], "review")
        self.assertEqual(g["reason"], "same-size-different-version")
        self.assertEqual(g["redundant_bytes"], 0)

    def test_psd_sibling_stays_distinct_via_base_key(self):
        """base_key excludes pipeline but KEEPS discriminators, so PSD never pairs."""
        a = ia.base_key(P("GUI Pro - Simple Casual (PSD) v1.0.7.unitypackage"))
        b = ia.base_key(P("GUI Pro - Simple Casual v1.0.7.unitypackage"))
        self.assertNotEqual(a, b)









# ---------------------------------------------------------------------------
# Phase 1 — change tracking: diff, classify, changelog, queue
# ---------------------------------------------------------------------------

class TestDiffManifest(unittest.TestCase):
    """Diff keys on (path, size). mtime is deliberately unused: OneDrive rewrites
    timestamps, so AllSky ships identical sizes with the higher version carrying the
    OLDER mtime. Any mtime sensitivity invents changes that did not happen."""

    def test_mtime_only_change_yields_no_diff(self):
        prev = {"A v1.0.unitypackage": 1000, "B v2.0.unitypackage": 2000}
        now = dict(prev)  # sizes identical; mtime cannot enter by construction
        d = ia.diff_manifest(prev, now)
        self.assertEqual((d["added"], d["removed"], d["resized"]), ([], [], []))

    def test_diff_body_never_mentions_mtime(self):
        """Structural guard so a future edit cannot quietly reintroduce it."""
        src = Path(ia.__file__).read_text(encoding="utf-8")
        body = src.split("def diff_manifest(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("mtime", body)

    def test_added_only(self):
        d = ia.diff_manifest({"a": 1}, {"a": 1, "b": 2})
        self.assertEqual(d["added"], ["b"])
        self.assertEqual((d["removed"], d["resized"]), ([], []))

    def test_removed_only(self):
        d = ia.diff_manifest({"a": 1, "b": 2}, {"a": 1})
        self.assertEqual(d["removed"], ["b"])
        self.assertEqual((d["added"], d["resized"]), ([], []))

    def test_resized_reports_old_and_new(self):
        d = ia.diff_manifest({"a": 100}, {"a": 250})
        self.assertEqual(d["resized"], [("a", 100, 250)])
        self.assertEqual((d["added"], d["removed"]), ([], []))

    def test_rename_is_one_added_and_one_removed(self):
        d = ia.diff_manifest({"old.unitypackage": 5}, {"new.unitypackage": 5})
        self.assertEqual(d["added"], ["new.unitypackage"])
        self.assertEqual(d["removed"], ["old.unitypackage"])

    def test_empty_previous_state_is_a_baseline_not_861_additions(self):
        d = ia.diff_manifest({}, {"a": 1, "b": 2})
        self.assertTrue(d["is_baseline"])
        self.assertEqual((d["added"], d["removed"], d["resized"]), ([], [], []))

    def test_ordering_is_deterministic(self):
        prev, now = {"z": 1}, {"m": 1, "a": 1, "z": 1}
        self.assertEqual(ia.diff_manifest(prev, now)["added"],
                         ia.diff_manifest(prev, now)["added"])
        self.assertEqual(ia.diff_manifest(prev, now)["added"], ["a", "m"])

    def test_manifest_of_reads_every_version_file(self):
        data = {"assets": [{"versions": [{"file": "a", "size_bytes": 1},
                                         {"file": "b", "size_bytes": 2}]},
                           {"versions": [{"file": "c", "size_bytes": 3}]}]}
        self.assertEqual(ia.manifest_of(data), {"a": 1, "b": 2, "c": 3})


class TestClassifyAdded(unittest.TestCase):
    """A new VERSION of a known asset reuses its cached resolution offline. A genuinely
    NEW asset needs a web search, which needs a session — so the two must not be
    conflated, or every added file would look like it needs a lookup."""

    CACHE = {
        "dungen": {"status": "resolved", "id": "15682"},
        # pipeline is part of asset_key, so a URP file keys as "<title>/urp"
        "moderncity/urp": {"status": "unverified", "candidates_rejected": []},
    }

    def test_new_version_of_known_asset_is_not_queued(self):
        """The common 'downloaded v2.19 over v2.18' case must not burn a search."""
        got = ia.classify_added(["Tools/DunGen v2.19.0.unitypackage"], self.CACHE)
        self.assertEqual(got["new_assets"], [])
        self.assertEqual(len(got["new_versions"]), 1)
        self.assertEqual(got["new_versions"][0]["asset_key"], "dungen")

    def test_genuinely_new_asset_is_queued(self):
        got = ia.classify_added(["Tools/Totally New Thing v1.0.unitypackage"], self.CACHE)
        self.assertEqual(got["new_versions"], [])
        self.assertEqual(len(got["new_assets"]), 1)
        self.assertFalse(got["new_assets"][0]["previously_unresolved"])

    def test_previously_failed_asset_is_queued_but_flagged(self):
        """Re-queuing silently would imply a fresh lookup will succeed. It already failed."""
        got = ia.classify_added(["3D/ModernCity_URP_2021.3.6f1.unitypackage"], self.CACHE)
        self.assertEqual(len(got["new_assets"]), 1)
        self.assertTrue(got["new_assets"][0]["previously_unresolved"])

    def test_empty_input(self):
        got = ia.classify_added([], self.CACHE)
        self.assertEqual((got["new_assets"], got["new_versions"]), ([], []))


class TestChangelog(unittest.TestCase):
    def _diff(self):
        return {"added": ["New Thing v1.0.unitypackage"], "removed": ["Old.unitypackage"],
                "resized": [("Changed.unitypackage", 100, 200)], "is_baseline": False}

    def _classified(self):
        return {"new_assets": [{"asset_key": "new-thing", "title": "New Thing",
                                "path": "New Thing v1.0.unitypackage",
                                "previously_unresolved": False}],
                "new_versions": []}

    def test_entry_counts_match_sections(self):
        e = ia.format_changelog_entry(self._diff(), self._classified(), "2026-08-02 14:31")
        self.assertIn("+1 −1 ~1", e)
        self.assertIn("NEW ASSETS (1)", e)
        self.assertIn("REMOVED (1)", e)

    def test_resized_shows_the_size_delta(self):
        e = ia.format_changelog_entry(self._diff(), self._classified(), "t")
        self.assertIn("100", e)
        self.assertIn("200", e)

    def test_empty_diff_produces_no_entry(self):
        empty = {"added": [], "removed": [], "resized": [], "is_baseline": False}
        self.assertIsNone(ia.format_changelog_entry(
            empty, {"new_assets": [], "new_versions": []}, "t"))

    def test_baseline_produces_no_entry(self):
        base = {"added": [], "removed": [], "resized": [], "is_baseline": True}
        self.assertIsNone(ia.format_changelog_entry(
            base, {"new_assets": [], "new_versions": []}, "t"))

    def test_append_prepends_newest_first_and_keeps_history(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "CHANGES.md")
        for label in ("first", "second", "third"):
            ia.append_changelog(p, f"## {label}\n\nbody\n")
        text = Path(p).read_text(encoding="utf-8")
        self.assertLess(text.index("## third"), text.index("## second"))
        self.assertLess(text.index("## second"), text.index("## first"))
        self.assertEqual(text.count("# Change Log"), 1)

    def test_append_leaves_no_tmp_file(self):
        import tempfile
        d = tempfile.mkdtemp()
        ia.append_changelog(os.path.join(d, "CHANGES.md"), "## x\n")
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class TestUpdateVerb(unittest.TestCase):
    """End-to-end on a temp tree. Never touches the real library."""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, ".index"))
        self._write("Tools/Thing v1.0.unitypackage", 500)

    def _write(self, rel, size):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"\0" * size)

    def _run(self):
        return ia.main(["update", "--root", self.root,
                        "--state", os.path.join(self.root, ".index")])

    def _changes(self):
        p = os.path.join(self.root, ".index", "CHANGES.md")
        return Path(p).read_text(encoding="utf-8") if os.path.exists(p) else ""

    def test_first_run_is_a_baseline_with_no_changelog(self):
        self._run()
        self.assertNotIn("## ", self._changes())

    def test_update_twice_appends_one_entry(self):
        """Same idempotency class as the local_name bug that corrupted 213 records."""
        self._run()                                   # baseline
        self._write("Tools/Extra v1.0.unitypackage", 600)
        self._run()                                   # +1
        first = self._changes().count("\n## ")
        self._run()                                   # no change
        self.assertEqual(self._changes().count("\n## "), first)
        self.assertEqual(first, 1)

    def test_added_file_is_queued(self):
        self._run()
        self._write("Tools/Brand New Pack v2.0.unitypackage", 700)
        self._run()
        q = json.loads(Path(os.path.join(self.root, ".index",
                                         "pending-enrichment.json")).read_text())
        self.assertTrue(any("brand" in e["asset_key"] for e in q["pending"]))

    def test_update_progress_separates_index_diff_from_emission_rows(self):
        self._run()
        os.remove(os.path.join(self.root, "Tools/Thing v1.0.unitypackage"))
        events = []
        self.assertEqual(ia.main(["update", "--root", self.root,
                                  "--state", os.path.join(self.root, ".index")],
                                 progress=events.append), 0)
        compare = [e for e in events if e["stage"] == "compare" and e["completed"] == 1][-1]
        self.assertEqual(compare["counts"]["index_removed"], 1)
        self.assertNotIn("removed", compare["counts"])
        emitted = [e for e in events if e["stage"] == "emit"][-1]
        self.assertEqual(emitted["counts"]["emitted_rows"], 0)
        self.assertNotIn("emitted", emitted["counts"])

    def test_removed_file_is_logged(self):
        self._run()
        os.remove(os.path.join(self.root, "Tools/Thing v1.0.unitypackage"))
        self._run()
        self.assertIn("REMOVED", self._changes())

    def test_update_never_opens_an_archive(self):
        """update writes text files, so assert no ARCHIVE path is ever opened."""
        self._write("Tools/Another v1.0.unitypackage", 100)
        opened = []
        real_open = open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", spy), \
             mock.patch.object(tarfile, "open", self._boom), \
             mock.patch.object(zipfile, "ZipFile", self._boom), \
             mock.patch.object(gzip, "open", self._boom):
            self._run()
        archives = [p for p in opened if p.endswith((".unitypackage", ".zip"))]
        self.assertEqual(archives, [], f"opened archives: {archives}")

    @staticmethod
    def _boom(*a, **k):
        raise AssertionError("update opened an archive — would hydrate OneDrive")


class TestUpdatePreservesEnrichment(unittest.TestCase):
    """build() regenerates entries from disk with empty store metadata. update writes
    assets.json, so skipping the enrichment merge silently wipes every resolved asset
    from the index — observed live as id-verified dropping 717 -> 0."""

    def test_update_reapplies_cached_enrichment(self):
        import tempfile, subprocess
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".index"))
        p = os.path.join(root, "Tools", "DunGen v2.18.5.unitypackage")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"\0" * 100)

        key = ia.asset_key(ia.parse("Tools/DunGen v2.18.5.unitypackage"))
        cache = {"resolved": {key: {
            "status": "resolved", "id": "15682", "score": 1.0,
            "legacy": {"publisher": "Aegon Games Ltd", "name": "DunGen"},
            "detail": {"author": "Aegon Games Ltd", "name": "DunGen",
                       "category_path": "Tools/Utilities",
                       "category_levels": ["Tools", "Utilities"],
                       "store": "https://assetstore.unity.com/packages/tools/utilities/dungen-15682"}}}}
        ia.write_atomic(os.path.join(root, ".index", "cache.json"),
                        json.dumps(cache))

        ia.main(["update", "--root", root, "--state", os.path.join(root, ".index")])
        data = json.loads(Path(os.path.join(root, ".index", "assets.json")).read_text())
        asset = data["assets"][0]
        self.assertTrue(asset["resolution"].get("id_verified"),
                        "update wiped the cached enrichment")
        self.assertEqual(asset["author"], "Aegon Games Ltd")
        self.assertEqual(asset["category"]["path"], "Tools/Utilities")
        self.assertEqual(asset["local_name"], "DunGen")


class TestReviewFindingFixesPhase1(unittest.TestCase):
    """Regressions for the seven findings from the post-implementation review."""

    def _tree(self):
        import tempfile
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, ".index"))
        p = os.path.join(root, "Tools", "DunGen v2.18.5.unitypackage")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(b"\0" * 100)
        key = ia.asset_key(ia.parse("Tools/DunGen v2.18.5.unitypackage"))
        ia.write_atomic(os.path.join(root, ".index", "cache.json"), json.dumps(
            {"resolved": {key: {
                "status": "resolved", "id": "15682", "score": 1.0,
                "legacy": {"publisher": "Aegon Games Ltd", "name": "DunGen"},
                "detail": {"author": "Aegon Games Ltd", "name": "DunGen",
                           "category_path": "Tools/Utilities",
                           "category_levels": ["Tools", "Utilities"],
                           "store": "https://assetstore.unity.com/packages/x/y-15682"}}}}))
        return root

    def _verified(self, root):
        d = json.loads(Path(os.path.join(root, ".index", "assets.json")).read_text())
        return d["assets"][0]["resolution"].get("id_verified")

    def test_scan_combined_with_update_does_not_wipe_enrichment(self):
        """CRITICAL. The update block wrote merged data, then a standalone `scan` step
        rebuilt from disk with empty metadata and overwrote it — reachable via the very
        natural `scan emit update`, silently reproducing the 717-to-0 wipe."""
        for steps in (["update"], ["scan", "update"], ["update", "scan"],
                      ["scan", "emit", "update"]):
            with self.subTest(steps=steps):
                root = self._tree()
                ia.main(steps + ["--root", root, "--state", os.path.join(root, ".index")])
                self.assertTrue(self._verified(root),
                                f"enrichment wiped by steps={steps}")

    def test_changelog_survives_a_header_text_change(self):
        """HIGH. Slicing by len(CHANGELOG_HEADER) destroyed all prior history the moment
        the header wording changed — defeating the point of an append-only log."""
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "CHANGES.md")
        Path(p).write_text("# Change Log\n\nA shorter older header.\n\n"
                           "## 2026-01-01 10:00 — +1 −0 ~0\n\n- `A.unitypackage`\n",
                           encoding="utf-8")
        ia.append_changelog(p, "## 2026-02-02 11:00 — +1 −0 ~0\n\n- `B.unitypackage`\n")
        text = Path(p).read_text(encoding="utf-8")
        self.assertIn("A.unitypackage", text, "prior history destroyed")
        self.assertIn("B.unitypackage", text)
        self.assertLess(text.index("2026-02-02"), text.index("2026-01-01"))

    def test_changelog_handles_a_file_with_no_header(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "CHANGES.md")
        Path(p).write_text("## 2026-01-01 10:00 — +1 −0 ~0\n\n- `A.unitypackage`\n",
                           encoding="utf-8")
        ia.append_changelog(p, "## new\n")
        self.assertIn("A.unitypackage", Path(p).read_text(encoding="utf-8"))

    def test_corrupt_pending_queue_is_preserved_not_silently_dropped(self):
        """HIGH. Losing the queue silently is the exact failure the accumulate-with-
        first_seen design exists to prevent."""
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "pending-enrichment.json")
        Path(p).write_text("{ this is not json", encoding="utf-8")
        ia.write_pending_queue(p, [{"asset_key": "new-one", "title": "New One",
                                    "previously_unresolved": False}])
        self.assertTrue(os.path.exists(p + ".corrupt"), "bad queue was not preserved")
        self.assertIn("new-one", Path(p).read_text(encoding="utf-8"))

    def test_corrupt_index_is_preserved_when_tags_cannot_be_migrated(self):
        root = self._tree()
        index = os.path.join(root, ".index", "assets.json")
        ia.write_atomic(index, "{ truncated")
        self.assertEqual(ia.main(["update", "--root", root,
                                  "--state", os.path.join(root, ".index")]), 1)
        self.assertEqual(Path(index).read_text(), "{ truncated")

    def test_baseline_distinguishes_missing_from_legitimately_empty(self):
        """LOW. An existing-but-empty previous state is not a baseline."""
        self.assertTrue(ia.diff_manifest({}, {"a": 1})["is_baseline"])
        d = ia.diff_manifest({}, {"a": 1}, had_previous=True)
        self.assertFalse(d["is_baseline"])
        self.assertEqual(d["added"], ["a"])

    def test_unittest_main_guard_is_the_last_statement(self):
        """HIGH. The guard sat mid-file, so direct invocation reported OK on 37 tests
        while silently skipping 8 later classes — including the 717-to-0 regression."""
        for f in ("test_index_assets.py", "test_resolve_store.py"):
            with self.subTest(file=f):
                p = Path(__file__).parent / f
                lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
                self.assertIn("__main__", lines[-2],
                              f"{f}: guard is not at the end — later classes are skipped")






class TestPendingEnrichmentFlag(unittest.TestCase):
    """Annotating the pending queue keeps state output current."""

    def _data(self, key="foo"):
        return {"generated": "t", "file_count": 1, "asset_count": 1,
                "duplicate_groups": [], "assets": [{
                    "asset_key": key, "name": "Foo", "local_name": "Foo", "author": None,
                    "category": {"path": None, "levels": [], "source": "folder"},
                    "tags": [], "tag_source": "keyword", "thumbnail": None, "store": None,
                    "store_id": None, "non_store": False, "discriminators": [],
                    "flags": [], "versions": [{
                        "version": "1.0", "prerelease": False, "file": "Foo.unitypackage",
                        "size_bytes": 1, "release_date": None, "editor_version": None,
                        "pipeline": None, "folder_hint": None, "integrity": None,
                        "duplicate": None, "latest": True}],
                    "resolution": {"method": None, "id_verified": False}}]}

    def _queue(self, d, keys):
        p = os.path.join(d, "pending-enrichment.json")
        ia.write_atomic(p, json.dumps({"generated": "t", "pending": [
            {"asset_key": k, "title": k, "first_seen": "2026-08-02",
             "previously_unresolved": False} for k in keys]}))
        return p

    def test_queued_asset_gets_the_flag(self):
        import tempfile
        d = tempfile.mkdtemp()
        got = ia.annotate_pending(self._data("foo"), self._queue(d, ["foo"]))
        self.assertTrue(got["assets"][0]["pending_enrichment"])

    def test_unqueued_asset_does_not(self):
        import tempfile
        d = tempfile.mkdtemp()
        got = ia.annotate_pending(self._data("bar"), self._queue(d, ["foo"]))
        self.assertFalse(got["assets"][0].get("pending_enrichment"))

    def test_missing_queue_is_a_noop(self):
        got = ia.annotate_pending(self._data(), "/nonexistent/queue.json")
        self.assertFalse(got["assets"][0].get("pending_enrichment"))

    def test_malformed_queue_warns_but_does_not_break_emit(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "q.json")
        Path(p).write_text("{ not json", encoding="utf-8")
        got = ia.annotate_pending(self._data(), p)   # must not raise
        self.assertFalse(got["assets"][0].get("pending_enrichment"))



# ---------------------------------------------------------------------------
# viewer redesign, phase 1 — design system foundation
# ---------------------------------------------------------------------------















# ---------------------------------------------------------------------------
# viewer redesign, phase 2 — thumbnails remote-only
# ---------------------------------------------------------------------------







class TestOutputDirRouting(unittest.TestCase):
    """config output_dir relocates generated CSV output relative to the repo."""

    def test_output_dir_resolves_relative_to_repo(self):
        root = tempfile.mkdtemp()
        state = os.path.join(root, "state")
        os.makedirs(state)
        vault = os.path.join(root, "vault")
        os.makedirs(os.path.join(vault, "Tools"))
        with open(os.path.join(vault, "Tools", "Thing v1.0.unitypackage"), "wb") as fh:
            fh.write(b"\0" * 500)
        ia.main(["scan", "--root", vault, "--state", state])

        fake_repo = tempfile.mkdtemp()
        with mock.patch.object(ia, "load_config",
                               return_value={"vault_root": vault,
                                             "output_dir": "gallery"}), \
             mock.patch.object(ia, "repo_dir", return_value=fake_repo):
            ia.main(["emit", "--state", state])

        self.assertTrue(os.path.exists(os.path.join(fake_repo, "gallery", "assets.csv")))
        self.assertTrue(os.path.exists(os.path.join(state, "review-queue.md")))
        self.assertFalse(os.path.exists(os.path.join(fake_repo, "gallery", "index.html")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
