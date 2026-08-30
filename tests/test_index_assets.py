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
# Recalibrated 2026-08-30 from the live library during the repo split (was 861 on
# 2026-07-30; 8 downloads arrived since). Bump after every intentional download.
EXPECTED_FILE_COUNT = 869


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
    def test_scan_of_real_root_finds_861(self):
        self.assertEqual(len(ia.scan(REAL_ROOT)), EXPECTED_FILE_COUNT)

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


class TestHtmlEscaping(unittest.TestCase):
    """The predecessor escaped the literal '</script>'; two other forms still injected."""

    PAYLOADS = ["</script>", "</ScRiPt >", "</script/>", "<!--", "javascript:",
                " ", "</SCRIPT\t>", "<img src=x>"]

    def _injects(self, html):
        from html.parser import HTMLParser

        class Probe(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth = 0
                self.escaped = 0

            def handle_starttag(self, tag, attrs):
                if tag == "script":
                    self.depth = 1
                elif self.depth == 0 and tag in ("img", "svg", "iframe"):
                    self.escaped += 1

            def handle_endtag(self, tag):
                if tag == "script":
                    self.depth = 0

        p = Probe()
        p.feed(html)
        return p.escaped

    def test_all_payloads_contained_in_every_string_field(self):
        for payload in self.PAYLOADS:
            for field in ("name", "author", "category", "tag", "store", "thumb", "override"):
                with self.subTest(payload=payload, field=field):
                    data = self._make(field, payload + "<img src=x>")
                    html = ia.HTML_TEMPLATE.replace("__DATA__", ia.safe_json(data))
                    self.assertEqual(self._injects(html), 0,
                                     f"{field} escaped the data block via {payload!r}")

    def _make(self, field, evil):
        a = {"asset_key": "k", "name": "n", "author": None,
             "category": {"path": "Tools", "levels": ["Tools"], "source": "folder"},
             "tags": ["shader"], "tag_source": "keyword", "thumbnail": None, "store": None,
             "store_id": None, "non_store": False, "discriminators": [], "flags": [],
             "versions": [{"version": "1.0", "prerelease": False, "file": "a.unitypackage",
                           "size_bytes": 1, "release_date": None, "editor_version": None,
                           "pipeline": None, "folder_hint": None, "integrity": None,
                           "duplicate": None, "latest": True}],
             "resolution": {"method": None, "id_verified": False}}
        if field == "name":
            a["name"] = evil
        elif field == "author":
            a["author"] = evil
        elif field == "category":
            a["category"] = {"path": evil, "levels": [evil], "source": "store"}
        elif field == "tag":
            a["tags"] = [evil]
        elif field == "store":
            a["store"] = evil
        elif field == "thumb":
            a["thumbnail"] = {"local": evil, "remote": evil}
        elif field == "override":
            a["custom_note"] = evil
        return {"generated": "x", "file_count": 1, "asset_count": 1,
                "duplicate_groups": [], "assets": [a]}

    def test_safe_json_escapes_angle_brackets(self):
        out = ia.safe_json({"x": "</script><!-- "})
        for ch in ("<", ">"):
            self.assertNotIn(ch, out)
        self.assertIn("\\u003c", out)
        self.assertIn("\\u2028", out)

    def test_no_external_resource_loading(self):
        """Amended when thumbnails went remote-only. Images now load from exactly one
        CDN host: 303 MB of OneDrive-synced local mirrors were traded for a hard
        dependency on that host being up. There is no local fallback, so CDN rot blanks
        every image permanently -- mirror_thumbnail in resolve_store.py is retained as
        the documented way back.

        The blanket 'nothing external' assertion this replaces still passed after that
        change, because src is assigned via setAttribute and never appears as src="http
        in the template. Passing by accident is worse than failing, so the posture is
        stated explicitly here instead."""
        html = ia.HTML_TEMPLATE
        self.assertNotIn('src="http', html)
        self.assertNotIn("@import", html)
        self.assertNotRegex(html, r'<link[^>]+href="http')
        csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html)
        self.assertIsNotNone(csp, "CSP meta tag missing")
        directives = {}
        for part in csp.group(1).split(";"):
            if part.strip():
                name, _, value = part.strip().partition(" ")
                directives[name] = value.strip()
        self.assertEqual(directives.get("default-src"), "'none'")
        self.assertEqual(directives.get("img-src"),
                         "https://assetstorev1-prd-cdn.unity3d.com data:")
        # Script and style stay inline-only; no host may creep into either.
        for key in ("script-src", "style-src"):
            with self.subTest(directive=key):
                self.assertEqual(directives.get(key), "'unsafe-inline'")

    def test_rendering_uses_dom_apis_not_string_html(self):
        html = ia.HTML_TEMPLATE
        self.assertIn("textContent", html)
        self.assertNotIn(".innerHTML", html)


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

    def test_facet_counts_deduplicate_flags(self):
        """M5: a duplicate group is one asset with 2 flagged versions, so undeduped
        counting reported 20 duplicates where there were 10."""
        js = ia.HTML_TEMPLATE.split("function facetCounts()", 1)[1].split("function ", 1)[0]
        self.assertIn("indexOf(x)===i", js)


class TestCategoryHierarchyAndStoreFields(unittest.TestCase):
    """The viewer must carry all store category levels and a store-URL slot, and must
    show an explicit pending state for both before enrichment fills them in."""

    def _asset(self, key, levels, store):
        return {"asset_key": key, "name": key, "author": "A",
                "category": {"path": "/".join(levels) or None, "levels": levels,
                             "source": "store" if levels else "folder"},
                "tags": [], "tag_source": "keyword", "thumbnail": None, "store": store,
                "store_id": "1", "non_store": False, "discriminators": [], "flags": [],
                "versions": [{"version": "1.0", "prerelease": False,
                              "file": key + ".unitypackage", "size_bytes": 1,
                              "release_date": None, "editor_version": None,
                              "pipeline": None, "folder_hint": None, "integrity": None,
                              "duplicate": None, "latest": True}],
                "resolution": {"method": "web-search", "id_verified": True}}

    def test_indent_rule_exists_for_every_depth_the_store_produces(self):
        """Unity paths reach 4 levels (3D/Characters/Humanoids/Fantasy); a missing
        .lvlN rule renders that depth flat and the hierarchy reads wrong."""
        css = ia.HTML_TEMPLATE
        for depth in range(1, 5):
            with self.subTest(depth=depth):
                self.assertIn(f".facet.lvl{depth}{{", css.replace(" ", ""))

    def test_viewer_renders_all_levels_not_just_level_one(self):
        js = ia.HTML_TEMPLATE
        self.assertIn("categoryTree", js)
        self.assertIn("levels", js)
        # Selection must match by path PREFIX so a level-1 pick includes its children.
        self.assertIn("inCategory", js)
        self.assertIn('indexOf(prefix+"/")', js)

    def test_store_url_has_a_slot_in_both_views(self):
        js = ia.HTML_TEMPLATE
        self.assertIn("Store URL", js)          # list view column
        self.assertIn("copy store URL", js)     # card action
        self.assertIn("store link pending", js) # explicit empty state
        self.assertIn("category pending", js)   # explicit empty state

    def test_deep_and_missing_categories_both_survive_emit(self):
        import tempfile, re, json as _json
        data = {"generated": "t", "file_count": 3, "asset_count": 3,
                "duplicate_groups": [], "assets": [
                    self._asset("deep", ["3D", "Characters", "Humanoids", "Fantasy"],
                                "https://assetstore.unity.com/packages/a/b/c/d-1"),
                    self._asset("two", ["Tools", "Visual Scripting"],
                                "https://assetstore.unity.com/packages/tools/vs/x-2"),
                    self._asset("none", [], None)]}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        ia.emit_html(data, out)
        payload = _json.loads(re.search(r'id="data">(.*?)</script>',
                                        Path(out).read_text(encoding="utf-8"), re.S).group(1))
        levels = [len(a["category"]["levels"]) for a in payload["assets"]]
        self.assertEqual(sorted(levels), [0, 2, 4])
        self.assertEqual(sum(1 for a in payload["assets"] if a["store"]), 2)


class TestBothNamesSurfaceInViewer(unittest.TestCase):
    def test_viewer_shows_store_name_and_on_disk_name(self):
        js = ia.HTML_TEMPLATE
        self.assertIn("Name (store)", js)
        self.assertIn("Name (on disk)", js)
        self.assertIn("on disk: ", js)
        # both names searchable — the user recognises files by filename
        self.assertIn("a.local_name", js)

    def test_local_name_field_always_present_in_entries(self):
        scanned = [{"rel_path": "T/A v1.0.unitypackage", "size": 1, "mtime": 1,
                    "st_blocks": 0, "format": "unitypackage"}]
        self.assertIn("local_name", ia.build(scanned)["assets"][0])

    def test_csv_carries_both_names(self):
        import tempfile, csv as _csv
        scanned = [{"rel_path": "T/A v1.0.unitypackage", "size": 1, "mtime": 1,
                    "st_blocks": 0, "format": "unitypackage"}]
        p = os.path.join(tempfile.mkdtemp(), "a.csv")
        ia.emit_csv(ia.build(scanned), p)
        with open(p, encoding="utf-8") as fh:
            cols = next(_csv.reader(fh))
        self.assertIn("name", cols)
        self.assertIn("local_name", cols)


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

    def test_malformed_assets_json_degrades_instead_of_crashing(self):
        """MEDIUM. Two of three sibling reads degraded gracefully; this one raised."""
        root = self._tree()
        ia.write_atomic(os.path.join(root, ".index", "assets.json"), "{ truncated")
        self.assertEqual(ia.main(["update", "--root", root,
                                  "--state", os.path.join(root, ".index")]), 0)
        self.assertTrue(self._verified(root))

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



class TestStalenessStamp(unittest.TestCase):
    """Manual-only triggering means a stale index looks identical to a current one.
    The stamp is the entire mitigation for the chosen trigger design."""

    def test_viewer_renders_the_generated_timestamp(self):
        js = ia.HTML_TEMPLATE
        self.assertIn('id="generated"', js)
        self.assertIn("DATA.generated", js)

    def test_viewer_marks_a_stale_index(self):
        """Age must be visible, not just the raw timestamp."""
        js = ia.HTML_TEMPLATE
        self.assertIn("stale", js)

    def test_stamp_does_not_break_build_determinism(self):
        """TestBuildDeterminism pops `generated` before comparing; nothing else may
        depend on it."""
        scanned = [{"rel_path": "T/A v1.0.unitypackage", "size": 1, "mtime": 1,
                    "st_blocks": 0, "format": "unitypackage"}]
        import copy
        a, b = ia.build(copy.deepcopy(scanned)), ia.build(copy.deepcopy(scanned))
        a.pop("generated"), b.pop("generated")
        self.assertEqual(a, b)


class TestPendingEnrichmentFlag(unittest.TestCase):
    """The queue is written after build(), so annotating in build() would land the flag
    one run late. emit reads the queue directly instead."""

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

    def test_flag_reaches_the_viewer_facet(self):
        js = ia.HTML_TEMPLATE
        self.assertIn("pending_enrichment", js)
        flags = js.split("function flagsOf", 1)[1].split("return f;}", 1)[0]
        self.assertIn("pending_enrichment", flags,
                      "flag must be collected by flagsOf to appear in the facet sidebar")


# ---------------------------------------------------------------------------
# viewer redesign, phase 1 — design system foundation
# ---------------------------------------------------------------------------

COLOR_TOKENS = ("--bg", "--surface", "--sunk", "--ink", "--ink-2", "--ink-3",
                "--line", "--accent", "--warn")
RADIUS_TOKENS = ("--r", "--r-sm")
DARK_OPEN = "@media(prefers-color-scheme:dark){:root{"


def _light_tokens():
    """The first :root body. The dark block nests its own :root inside the media
    query, so first-occurrence slicing is what separates them."""
    return ia.HTML_TEMPLATE.split(":root{", 1)[1].split("}", 1)[0]


def _dark_tokens():
    return ia.HTML_TEMPLATE.split(DARK_OPEN, 1)[1].split("}}", 1)[0]


class TestDesignTokens(unittest.TestCase):
    """One token set, two schemes. A token defined in only one scheme is a
    half-themed control that looks right until the system toggle flips."""

    def test_both_schemes_define_every_token(self):
        light, dark = _light_tokens(), _dark_tokens()
        for tok in COLOR_TOKENS:
            with self.subTest(token=tok):
                self.assertIn(tok + ":", light, "missing from the light scheme")
                self.assertIn(tok + ":", dark, "missing from the dark scheme")
        # Radius carries no colour, so it is defined once and never re-declared.
        for tok in RADIUS_TOKENS:
            with self.subTest(token=tok):
                self.assertIn(tok + ":", light)
                self.assertNotIn(tok + ":", dark,
                                 "radius must not be scheme-dependent")

    def test_no_pure_black_or_white_in_dark_scheme(self):
        dark = _dark_tokens().lower()
        for bad in ("#000", "#fff"):
            with self.subTest(value=bad):
                self.assertNotIn(bad, dark)

    def test_single_radius_scale(self):
        """The four ad-hoc radii this replaced (3/4/5/9px) are the failure mode. `0` is
        allowed because it is the absence of a radius, not a competing value: the detail
        panel squares off when it pins to the viewport edge below 1100px."""
        found = set(re.findall(r"border-radius:([^;}]+)", ia.HTML_TEMPLATE))
        self.assertTrue(found, "no border-radius declarations at all")
        self.assertEqual(found - {"var(--r)", "var(--r-sm)", "50%", "0"}, set(),
                         "hardcoded radius outside the scale")

    def test_tabular_numerals_present(self):
        self.assertGreaterEqual(ia.HTML_TEMPLATE.count("tabular-nums"), 4,
                                "counts and sizes jitter without tabular figures")

    def test_no_em_dash_anywhere_in_template(self):
        """Matched by codepoint, never by pasting the glyph, so the assertion
        survives an editor that helpfully substitutes one. Guards every later
        phase, not just this one."""
        for cp, name in (("\u2014", "em dash"), ("\u2013", "en dash")):
            with self.subTest(char=name):
                self.assertNotIn(cp, ia.HTML_TEMPLATE, f"{name} in a shipped UI string")


# ---------------------------------------------------------------------------
# viewer redesign, phase 2 — thumbnails remote-only
# ---------------------------------------------------------------------------

def _fn_body(name):
    """One JS function out of the template, sliced to its own matching closing brace.

    The obvious version splits on the next `\\nfunction `, which silently swallows any
    comment block sitting between this function and the next one, and runs to end of
    file for the last function in the template. Both were live here: a comment
    containing another function's declaration text already broke that split once.
    """
    src = ia.HTML_TEMPLATE
    start = src.index("function " + name)
    depth, i = 0, src.index("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unbalanced braces after " + name)


class TestRemoteThumbnails(unittest.TestCase):
    """Every one of the 717 thumbnails has a remote URL, so nothing loses an image.
    What is lost is offline: with no network all 835 cards render imageless, which is
    why the placeholder is a designed state rather than a patched edge case."""

    CDN = "https://assetstorev1-prd-cdn.unity3d.com"

    def test_card_reads_remote_not_local(self):
        html = ia.HTML_TEMPLATE
        self.assertIn("thumbnail.remote", html)
        self.assertNotIn("thumbnail.local", html)

    def test_csp_allows_exactly_the_cdn_host(self):
        html = ia.HTML_TEMPLATE
        self.assertIn(f"img-src {self.CDN} data:", html)
        self.assertNotIn("img-src 'self'", html)

    def test_images_are_lazy_and_sized(self):
        """Without width/height the browser has no intrinsic ratio before the bytes
        arrive and every card jumps as the grid fills."""
        card = _fn_body("card(a)")
        for attr, value in (("loading", "lazy"), ("decoding", "async")):
            with self.subTest(attribute=attr):
                self.assertIn(f'"{attr}","{value}"', card.replace(", ", ","))
        for attr in ("width", "height"):
            with self.subTest(attribute=attr):
                self.assertRegex(card, r'"%s"\s*,\s*"\d+"' % attr)

    def test_placeholder_uses_category_not_no_image(self):
        self.assertNotIn("no image", ia.HTML_TEMPLATE)
        self.assertIn("levels", _fn_body("thumbFallback(a)"))

    def test_image_failure_falls_back_to_placeholder(self):
        """Registered as a listener, not an inline onerror attribute: an onerror in
        static markup is exactly what TestHtmlEscaping exists to keep out."""
        card = _fn_body("card(a)")
        self.assertIn('addEventListener("error"', card.replace(", ", ","))
        self.assertIn("thumbFallback", card)

    def test_no_local_fallback_path(self):
        """Remote or placeholder, nothing between. A local fallback would put 'self'
        back in the CSP and make the airplane-mode check ambiguous."""
        card = _fn_body("card(a)")
        self.assertNotIn(".local", card.replace("a.local_name", ""))


# ---------------------------------------------------------------------------
# viewer redesign, phase 3 — shell recompose and detail panel
# ---------------------------------------------------------------------------

class TestCardAndDetailSplit(unittest.TestCase):
    """The card carried 8 text rows and 4 buttons, so a filtered grid of 835 held
    ~3,104 tab stops. Everything but four elements moves to a detail column."""

    def test_card_has_one_focusable(self):
        body = _fn_body("card(a)")
        made = re.findall(r'(?:el|document\.createElement)\(\s*"(button|a)"', body)
        self.assertEqual(made, ["button"],
                         "the card is one button; nothing interactive nests inside it")

    def test_actions_live_in_the_detail_panel(self):
        card, detail = _fn_body("card(a)"), _fn_body("detail(a)")
        for action in ("copy path", "copy store URL", "store page"):
            with self.subTest(action=action):
                self.assertIn(action, detail)
                self.assertNotIn(action, card)

    def test_detail_panel_element_exists(self):
        self.assertIn('id="detail"', ia.HTML_TEMPLATE)

    def test_card_name_is_clamped(self):
        """Unity store names run long enough to push the whole grid out of alignment;
        '100+ Stylized Historical Textures - Medieval, Egyptian, Roman & More' is real."""
        css = ia.HTML_TEMPLATE
        self.assertIn("text-wrap:balance", css)
        self.assertIn("line-clamp:2", css)

    def test_escape_closes_the_panel(self):
        js = ia.HTML_TEMPLATE.replace(", ", ",")
        self.assertIn('addEventListener("keydown"', js)
        key = js.split('addEventListener("keydown"', 1)[1]
        self.assertIn('e.key==="Escape"', key)
        self.assertIn("closePanel()", key)

    def test_closing_restores_focus_to_whatever_opened_the_panel(self):
        """#out precedes #detail in DOM order, so the panel is ~835 tab stops from the
        card that opened it; the open path focuses it and the close path hands focus
        back. Without the second half, closing drops focus to <body>."""
        self.assertIn(".focus()", _fn_body("openDetail(a, node)"))
        close = _fn_body("closePanel()")
        self.assertIn("origin", close)
        self.assertIn(".focus()", close)

    def test_verbatim_strings_survive_the_move(self):
        """These are matched character-for-character by four older tests. Paraphrasing
        one while moving it to the panel breaks them; fix the copy, never the test."""
        js = ia.HTML_TEMPLATE
        for s in ("copy store URL", "store link pending", "category pending",
                  "Store URL", "on disk: ", "Name (store)", "Name (on disk)",
                  "a.local_name"):
            with self.subTest(string=s):
                self.assertIn(s, js)


# ---------------------------------------------------------------------------
# viewer redesign, phase 4 — render performance
# ---------------------------------------------------------------------------

def _listener(element_id, event):
    """One listener body, sliced from the init block by the element it binds to."""
    anchor = 'document.getElementById("%s").addEventListener("%s",' % (element_id, event)
    tail = ia.HTML_TEMPLATE.split(anchor, 1)[1]
    for stop in ('\ndocument.getElementById(', '\nrender();'):
        tail = tail.split(stop, 1)[0]
    return tail


class TestRenderPerformance(unittest.TestCase):
    """These confirm the mechanism is wired, not that it is fast. Throughput is a
    manual check: there is no headless browser in this stack and adding one would
    contradict the zero-dependency offline design."""

    def test_search_input_is_debounced(self):
        body = _listener("q", "input")
        self.assertIn("setTimeout", body)
        self.assertIn("clearTimeout", body)

    def test_no_scroll_event_listener(self):
        self.assertNotRegex(ia.HTML_TEMPLATE, r'addEventListener\(\s*"scroll"')

    def test_chunked_append_uses_intersection_observer(self):
        js = ia.HTML_TEMPLATE
        self.assertIn("IntersectionObserver", js)
        self.assertRegex(js, r"\bCHUNK\s*=\s*\d+")

    def test_observer_is_disconnected_between_renders(self):
        """An observer per render, never disconnected, is a leak that climbs with
        every keystroke."""
        self.assertIn("disconnect(", ia.HTML_TEMPLATE)

    def test_superseded_observer_cannot_append_into_a_dead_grid(self):
        """disconnect() unregisters targets but does not drop entries already queued,
        so a superseded render's observer still gets one delivery. It closes over the
        old grid while reading module-scope cursor/view/obs, so unguarded it appended
        the new list into a detached node, skipped a chunk of results, and disconnected
        the live observer. The generation token is the whole fix."""
        body = _fn_body("render()")
        self.assertIn("var mine=++gen", body)
        cb = body.split("new IntersectionObserver", 1)[1]
        self.assertIn("if(mine!==gen)return;", cb)
        # and the guard must come before anything that mutates shared state
        self.assertLess(cb.index("if(mine!==gen)return;"), cb.index("appendChunk("))

    def test_sort_and_view_are_not_debounced(self):
        """Discrete actions. 120ms of nothing after a click reads as lag, not smooth."""
        for element_id, event in (("sort", "change"), ("view", "click")):
            with self.subTest(element=element_id):
                body = _listener(element_id, event)
                self.assertIn("render()", body)
                self.assertNotIn("setTimeout", body)


# ---------------------------------------------------------------------------
# viewer redesign, phase 5 — facets, sort, keyboard
# ---------------------------------------------------------------------------

class TestFacetRefinement(unittest.TestCase):
    def test_facet_counts_signature_is_unchanged(self):
        """test_facet_counts_deduplicate_flags splits the template on this literal.
        A parameter makes that split raise IndexError, so the older test errors rather
        than fails and reads as a broken suite instead of a broken change."""
        self.assertIn("function facetCounts()", ia.HTML_TEMPLATE)

    def test_counts_read_the_filtered_list(self):
        """Library-wide totals were the defect: selecting 3D left every tag count
        exactly where it was."""
        body = _fn_body("facetCounts()")
        self.assertNotIn("assets.forEach", body)
        self.assertNotIn("assets.filter", body)
        self.assertIn("facetPool", body)

    def test_family_excludes_own_selection(self):
        """Counting a family under its own selections drops every sibling to zero, and
        the selection can then never be widened without first being undone."""
        js = ia.HTML_TEMPLATE
        self.assertRegex(js, r"function facetPool\(family\)")
        self.assertRegex(js, r"function matches\(a, q, except\)")
        body = _fn_body("facetCounts()")
        for family in ("tag", "author", "flag"):
            with self.subTest(family=family):
                self.assertIn('facetPool("%s")' % family, body)


class TestSortOptions(unittest.TestCase):
    def test_rating_sort_exists(self):
        js = ia.HTML_TEMPLATE
        self.assertIn('<option value="rating">', js)
        self.assertIn('sortBy==="rating"', js)

    def test_date_sort_puts_nulls_last(self):
        """release_date is populated on 214 of 861 versions. The old comparator
        collapsed the other 75% to "" and sorted them into one indistinguishable block
        at whichever end that happened to land."""
        body = _fn_body("render()")
        self.assertNotIn('release_date||""', body)
        date = body.split('sortBy==="date"', 1)[1]
        self.assertIn("if(!dx)return 1;", date)
        self.assertIn("if(!dy)return -1;", date)

    def test_table_headers_are_bound(self):
        body = _fn_body("table(list)")
        self.assertIn('addEventListener("click"', body.replace(", ", ","))
        self.assertIn("SORTABLE", body)

    def test_unsortable_headers_lose_the_pointer_cursor(self):
        """cursor:pointer on every th with nothing bound is a lie the UI told on every
        hover of nine columns."""
        css = ia.HTML_TEMPLATE
        self.assertNotIn("cursor:pointer", re.search(r"\nth\{([^}]*)\}", css).group(1))
        self.assertIn("th.sortable{", css.replace(" ", ""))


class TestKeyboard(unittest.TestCase):
    def test_slash_focuses_search(self):
        key = ia.HTML_TEMPLATE.split('addEventListener("keydown"', 1)[1]
        self.assertIn('e.key==="/"', key)
        self.assertIn('getElementById("q").focus()', key.replace(", ", ","))
        # Otherwise the key is swallowed while typing a path into the search box.
        self.assertIn("activeElement", key)
        self.assertIn("typing", key)

    def test_escape_is_bound(self):
        self.assertIn('e.key==="Escape"', ia.HTML_TEMPLATE)

    def test_table_rows_are_arrow_navigable(self):
        key = ia.HTML_TEMPLATE.split('addEventListener("keydown"', 1)[1]
        for k, call in (('"ArrowDown"', "moveRow("), ('"ArrowUp"', "moveRow(-1)"),
                        ('"Enter"', "openDetail(")):
            with self.subTest(key=k):
                self.assertIn(k, key)
                self.assertIn(call, key)

    def test_rows_open_the_panel_on_click(self):
        """Phase 3 moved every action into the panel. Without a row handler a mouse
        user in list view can reach none of them."""
        body = _fn_body("table(list)")
        self.assertIn('addEventListener("click"', body.replace(", ", ","))
        self.assertIn("openDetail(", body)

    def test_row_state_is_painted_from_state_not_assigned_in_place(self):
        """Two separate concerns share the row: a keyboard cursor and the asset open in
        the panel. Painting both from (rowIdx, selected) is what stops them drifting."""
        body = _fn_body("paintRows(scroll)")
        self.assertIn("rowIdx", body)
        self.assertIn("selected", body)
        self.assertIn('"cur"', body)
        self.assertIn('"pick"', body)
        self.assertIn("paintRows(false)", _fn_body("closePanel()"))


# ---------------------------------------------------------------------------
# viewer redesign, phase 6 — decommission the local thumbnail cache
# ---------------------------------------------------------------------------

class TestThumbnailLocalRemoved(unittest.TestCase):
    """708 files and 290 MiB of OneDrive-synced mirrors, unread since phase 2. The
    references go before the bytes, so no intermediate state points at a missing file."""

    def test_template_references_no_local_thumbs_path(self):
        self.assertNotIn(".index/thumbs", ia.HTML_TEMPLATE)

    def test_no_module_still_names_the_thumbs_directory(self):
        """A default argument pointing at a deleted directory is the kind of residue
        that reads as intent to a later maintainer."""
        for mod in ("index_assets.py", "resolve_store.py"):
            with self.subTest(module=mod):
                src = Path(ia.__file__).parent.joinpath(mod).read_text(encoding="utf-8")
                self.assertNotIn(".index/thumbs", src)

    def test_emitted_html_carries_no_local_thumbs_path(self):
        """End to end through merge, because the path was never in the template: it
        arrived in the data payload from cache entries written before the switch."""
        import resolve_store as rs
        cache = {"resolved": {"k": {
            "status": "resolved", "id": "68570", "score": 1.0, "legacy": {},
            "detail": {"thumbnail_remote":
                       "https://assetstorev1-prd-cdn.unity3d.com/k.jpg"},
            "thumbnail_local": ".index/thumbs/68570.jpg"}}}
        data = {"generated": "t", "file_count": 1, "asset_count": 1,
                "duplicate_groups": [], "assets": [{
                    "asset_key": "k", "name": "K", "local_name": "K", "author": None,
                    "category": {"path": None, "levels": [], "source": "folder"},
                    "tags": [], "tag_source": "keyword", "thumbnail": None,
                    "store": None, "store_id": None, "non_store": False,
                    "discriminators": [], "flags": [], "versions": [{
                        "version": "1.0", "prerelease": False, "file": "K.unitypackage",
                        "size_bytes": 1, "release_date": None, "editor_version": None,
                        "pipeline": None, "folder_hint": None, "integrity": None,
                        "duplicate": None, "latest": True}],
                    "resolution": {"method": None, "id_verified": False}}]}
        out = os.path.join(tempfile.mkdtemp(), "v.html")
        ia.emit_html(rs.merge(data, cache, {}), out)
        html = Path(out).read_text(encoding="utf-8")
        self.assertNotIn(".index/thumbs", html)
        self.assertIn("assetstorev1-prd-cdn.unity3d.com", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
