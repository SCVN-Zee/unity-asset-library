#!/usr/bin/env python3
"""Phase 2 tests. Hermetic — every network shape comes from a captured fixture.

Run:  python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import resolve_store as rs  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8", errors="replace") as fh:
        return fh.read()


class FakeResponse:
    def __init__(self, text="", status=200, headers=None, content=b""):
        self.text = text
        self.status_code = status
        self.headers = headers or {}
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)

    def iter_content(self, n):
        for i in range(0, len(self._content), n):
            yield self._content[i:i + n]


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.response


class TestTransportOutcomesAreTyped(unittest.TestCase):
    """Blocked must never be mistaken for NoResult: that is what would cache ~861
    permanent false misses and then report the poisoned cache as a 100% hit rate."""

    def test_brave_429_is_blocked(self):
        s = FakeSession(FakeResponse(fixture("brave-429.html"), status=429))
        with self.assertRaises(rs.Blocked):
            rs.search_ids("Amplify Shader Editor", session=s)

    def test_ddg_202_anomaly_is_blocked_not_no_result(self):
        """Parseable and empty — indistinguishable from 'no results' without this check."""
        s = FakeSession(FakeResponse(fixture("ddg-anomaly-202.html"), status=202))
        with self.assertRaises(rs.Blocked):
            rs.search_ids("anything", session=s)

    def test_anomaly_body_at_http_200_is_still_blocked(self):
        s = FakeSession(FakeResponse(fixture("ddg-anomaly-202.html"), status=200))
        with self.assertRaises(rs.Blocked):
            rs.search_ids("anything", session=s)

    def test_empty_results_is_no_result_not_blocked(self):
        s = FakeSession(FakeResponse(fixture("brave-empty.html"), status=200))
        self.assertEqual(rs.search_ids("nonexistent", session=s), [])

    def test_blocked_is_an_exception_so_it_cannot_be_cached_as_a_miss(self):
        self.assertTrue(issubclass(rs.Blocked, Exception))


class TestIdExtraction(unittest.TestCase):
    def test_extracts_ids_from_real_brave_page(self):
        ids = rs.extract_ids(fixture("brave-amplify.html"))
        self.assertIn("68570", ids)
        self.assertGreater(len(ids), 1, "fixture should contain decoys too")

    def test_locale_query_string_does_not_break_extraction(self):
        html = 'x https://assetstore.unity.com/packages/tools/visual-scripting/amplify-shader-editor-68570?locale=zh-CN y'
        self.assertEqual(rs.extract_ids(html), ["68570"])

    def test_canonical_url_strips_query_and_fragment(self):
        got = rs.canonical_url(
            "https://assetstore.unity.com/packages/tools/x/y-68570?locale=zh-CN#reviews")
        self.assertEqual(got, "https://assetstore.unity.com/packages/tools/x/y-68570")


class TestConjunctiveGate(unittest.TestCase):
    """Identity, not similarity. No single threshold separates these on this library."""

    def test_exact_match_accepted(self):
        ok, _, score = rs.verify_identity("Amplify Shader Editor", "Amplify Shader Editor")
        self.assertTrue(ok)
        self.assertEqual(score, 1.0)

    def test_synty_rename_accepted(self):
        ok, reason, _ = rs.verify_identity(
            "POLYGON Prototype", "POLYGON - Prototype Pack - Art by Synty")
        self.assertTrue(ok, reason)

    def test_sibling_numeric_mismatch_rejected(self):
        for local, store in [
            ("Pure Nature 2 Meadows", "Pure Nature 2 Mountains"),
            ("Epic Toon VFX 2", "Epic Toon VFX 3"),
            ("Monsters Ultimate Pack 01 Cute Series", "Monsters Ultimate Pack 03 Cute Series"),
        ]:
            with self.subTest(local=local):
                ok, reason, _ = rs.verify_identity(local, store)
                self.assertFalse(ok, f"{local} -> {store} wrongly accepted")

    def test_superset_store_name_rejected(self):
        """Token-subset scoring fires on 66 distinct-product pairs here."""
        for local, store in [
            ("GPU Instancer", "GPU Instancer Pro"),
            ("Character Auras", "Character Auras 3"),
            ("POLYGON City", "POLYGON City Zombies"),
            ("Modern UI Pack", "Modern UI SFX - sound pack"),
        ]:
            with self.subTest(local=local):
                ok, reason, _ = rs.verify_identity(local, store)
                self.assertFalse(ok, f"{local} -> {store} wrongly accepted ({reason})")

    def test_total_mismatch_rejected(self):
        ok, _, _ = rs.verify_identity("epic orchestral music collection", "Limitless Gravity 2D")
        self.assertFalse(ok)

    def test_ranking_score_alone_never_authorizes(self):
        """A wrong pair outscores a genuine same-product pair, so no similarity
        threshold can serve as the gate.

        Epic Toon VFX 2 vs 3 (different products) scores above the Synty
        underscore-era rename POLYGON_NatureBiomes_MeadowForest vs
        'POLYGON Meadow Forest - Nature Biomes' (same product, reordered words).
        """
        false_pair = rs.similarity("Epic Toon VFX 2", "Epic Toon VFX 3")
        true_pair = rs.similarity("POLYGON NatureBiomes MeadowForest",
                                  "POLYGON Meadow Forest - Nature Biomes")
        self.assertGreater(false_pair, true_pair,
                           f"expected inversion, got false={false_pair:.3f} true={true_pair:.3f}")
        # The gate rejects the higher-scoring wrong pair regardless of that ordering.
        self.assertFalse(rs.verify_identity("Epic Toon VFX 2", "Epic Toon VFX 3")[0])

    def test_reordered_synty_rename_is_a_known_safe_false_negative(self):
        """The gate rejects this genuine same-product pair because the store reordered
        the words. That is the intended trade: a false negative costs one review-queue
        entry and an override, while a false positive silently writes wrong metadata.
        """
        ok, reason, _ = rs.verify_identity("POLYGON NatureBiomes MeadowForest",
                                           "POLYGON Meadow Forest - Nature Biomes")
        self.assertFalse(ok)
        self.assertIn("extra token", reason)


class TestLegacyLookup(unittest.TestCase):
    def test_parses_authoritative_record(self):
        s = FakeSession(FakeResponse(fixture("legacy-68570.json")))
        rec = rs.legacy_lookup("68570", session=s)
        self.assertEqual(rec["name"], "Amplify Shader Editor")
        self.assertEqual(rec["publisher"], "Amplify Creations")

    def test_confirms_a_renamed_package(self):
        s = FakeSession(FakeResponse(fixture("legacy-137126.json")))
        rec = rs.legacy_lookup("137126", session=s)
        self.assertEqual(rec["publisher"], "Synty Studios")
        self.assertIn("Prototype", rec["name"])

    def test_non_200_yields_none(self):
        self.assertIsNone(rs.legacy_lookup("1", session=FakeSession(FakeResponse("", 404))))

    def test_garbage_body_yields_none(self):
        self.assertIsNone(rs.legacy_lookup("1", session=FakeSession(FakeResponse("<html>"))))


class TestDetailFetch(unittest.TestCase):
    def test_extracts_all_four_store_only_fields(self):
        meta = rs.parse_ld_json(fixture("detail-amplify.html"))
        self.assertEqual(meta["name"], "Amplify Shader Editor")
        self.assertEqual(meta["author"], "Amplify Creations")
        self.assertEqual(meta["category_path"], "Tools/Visual Scripting")
        self.assertTrue(meta["thumbnail_remote"].startswith("https://"),
                        "protocol-relative //... must be absolutized")
        self.assertIn("68570", meta["store"])
        self.assertEqual(meta["rating"], 5.0)
        self.assertGreater(meta["reviews"], 100)

    def test_localized_page_is_rejected(self):
        """?locale=zh-CN keeps an English Product.name beside CJK breadcrumbs, so a
        name-based gate passes at 1.0 while the category is wrong."""
        meta = rs.parse_ld_json(fixture("detail-locale-zh.html"))
        self.assertEqual(meta["name"], "Amplify Shader Editor")  # gate would pass
        self.assertTrue(any(rs.has_non_latin(c) for c in meta["category_levels"]),
                        "fixture must contain a localized breadcrumb")

        s = FakeSession(FakeResponse(fixture("detail-locale-zh.html")))
        got = rs.fetch_detail("68570", session=s)
        self.assertIn("_rejected", got)
        self.assertIn("localized", got["_rejected"])

    def test_offers_url_id_mismatch_is_rejected(self):
        s = FakeSession(FakeResponse(fixture("detail-amplify.html")))
        got = rs.fetch_detail("99999", session=s)
        self.assertIn("_rejected", got)
        self.assertIn("offers.url", got["_rejected"])

    def test_no_ld_json_returns_none_not_crash(self):
        self.assertIsNone(rs.parse_ld_json("<html><body>nothing</body></html>"))

    def test_malformed_ld_json_returns_none(self):
        self.assertIsNone(rs.parse_ld_json(
            '<script type="application/ld+json">{not json}</script>'))

    def test_missing_rating_is_absent_not_zero(self):
        meta = rs.parse_ld_json(
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"X","brand":{"name":"Y"}}</script>')
        self.assertNotIn("rating", meta)

    def test_has_non_latin(self):
        self.assertTrue(rs.has_non_latin("工具"))
        self.assertTrue(rs.has_non_latin("ツール"))
        self.assertFalse(rs.has_non_latin("Visual Scripting"))
        self.assertFalse(rs.has_non_latin("Textures & Materials"))


class TestThumbnailValidation(unittest.TestCase):
    IMG = b"\xff\xd8\xff" + b"\x00" * 64

    def test_accepts_allowlisted_host(self):
        s = FakeSession(FakeResponse(status=200, headers={"Content-Type": "image/jpeg"},
                                     content=self.IMG))
        import tempfile
        dest, err = rs.mirror_thumbnail(
            "https://assetstorev1-prd-cdn.unity3d.com/key-image/x.jpg", "68570",
            tempfile.mkdtemp(), session=s)
        self.assertIsNone(err)
        self.assertTrue(dest.endswith("68570.jpg"))

    def test_rejects_foreign_host(self):
        _, err = rs.mirror_thumbnail("https://evil.example/x.jpg", "1", "/tmp",
                                     session=FakeSession(FakeResponse()))
        self.assertIn("allowlist", err)

    def test_rejects_non_https(self):
        for url in ("http://assetstore.unity.com/x.jpg", "file:///etc/passwd"):
            with self.subTest(url=url):
                _, err = rs.mirror_thumbnail(url, "1", "/tmp",
                                             session=FakeSession(FakeResponse()))
                self.assertIn("https", err)

    def test_rejects_non_image_content_type(self):
        s = FakeSession(FakeResponse(status=200, headers={"Content-Type": "text/html"}))
        _, err = rs.mirror_thumbnail(
            "https://assetstore.unity.com/x.jpg", "1", "/tmp", session=s)
        self.assertIn("not an image", err)

    def test_enforces_size_cap(self):
        s = FakeSession(FakeResponse(status=200, headers={"Content-Type": "image/png"},
                                     content=b"\x00" * (3 * 1024 * 1024)))
        _, err = rs.mirror_thumbnail(
            "https://assetstore.unity.com/x.png", "1", "/tmp", session=s)
        self.assertIn("2 MB", err)

    def test_filename_comes_from_numeric_id_not_url_tail(self):
        _, err = rs.mirror_thumbnail("https://assetstore.unity.com/x.jpg",
                                     "68570?locale=zh-CN", "/tmp",
                                     session=FakeSession(FakeResponse()))
        self.assertIn("non-numeric", err)

    def test_extension_from_sniffed_type_not_hardcoded(self):
        import tempfile
        s = FakeSession(FakeResponse(status=200, headers={"Content-Type": "image/png"},
                                     content=self.IMG))
        dest, _ = rs.mirror_thumbnail("https://assetstore.unity.com/x", "42",
                                      tempfile.mkdtemp(), session=s)
        self.assertTrue(dest.endswith(".png"))

    def test_redirects_are_not_followed(self):
        import tempfile
        s = FakeSession(FakeResponse(status=200, headers={"Content-Type": "image/jpeg"},
                                     content=self.IMG))
        rs.mirror_thumbnail("https://assetstore.unity.com/x.jpg", "7",
                            tempfile.mkdtemp(), session=s)
        self.assertFalse(s.calls[0][1]["allow_redirects"])


class TestMerge(unittest.TestCase):
    def _data(self, **kw):
        asset = {"asset_key": "amplify-shader-editor", "name": "Amplify Shader Editor",
                 "author": None, "category": {"path": "Tools", "levels": ["Tools"],
                                              "source": "folder"},
                 "tags": ["shader"], "tag_source": "keyword", "thumbnail": None,
                 "store": None, "store_id": None, "non_store": False,
                 "discriminators": [], "flags": [], "versions": [],
                 "resolution": {"method": None, "id_verified": False}}
        asset.update(kw)
        return {"assets": [asset]}

    def _cache(self):
        return {"resolved": {"amplify-shader-editor": {
            "status": "resolved", "id": "68570", "score": 1.0,
            "legacy": {"publisher": "Amplify Creations", "version": "1.9.9.12"},
            "detail": {"author": "Amplify Creations",
                       "category_path": "Tools/Visual Scripting",
                       "category_levels": ["Tools", "Visual Scripting"],
                       "store": "https://assetstore.unity.com/packages/x/y-68570",
                       "thumbnail_remote": "https://assetstorev1-prd-cdn.unity3d.com/k.jpg",
                       "rating": 5.0},
            "thumbnail_local": ".index/thumbs/68570.jpg"}}, "misses": {}}

    def test_resolved_fields_applied(self):
        out = rs.merge(self._data(), self._cache(), {})
        a = out["assets"][0]
        self.assertEqual(a["author"], "Amplify Creations")
        self.assertEqual(a["category"]["path"], "Tools/Visual Scripting")
        self.assertEqual(a["category"]["source"], "store")
        self.assertTrue(a["resolution"]["id_verified"])
        self.assertEqual(a["store_id"], "68570")

    def test_merge_emits_no_local_thumbnail_path(self):
        """_cache() still carries thumbnail_local, on purpose. The live cache no longer
        does -- phase 6 stripped it from all 717 records -- so this fixture is the only
        remaining guard that a pre-phase-6 cache, or one restored from a backup, cannot
        put a path to the deleted directory back into assets.json."""
        a = rs.merge(self._data(), self._cache(), {})["assets"][0]
        self.assertEqual(a["thumbnail"],
                         {"remote": "https://assetstorev1-prd-cdn.unity3d.com/k.jpg"})
        self.assertNotIn("local", a["thumbnail"])

    def test_unresolved_never_gets_store_fields(self):
        out = rs.merge(self._data(), {"resolved": {}}, {})
        a = out["assets"][0]
        self.assertIsNone(a["author"])
        self.assertIsNone(a["store"])
        self.assertIsNone(a["thumbnail"])
        self.assertFalse(a["resolution"]["id_verified"])

    def test_non_store_is_skipped_and_excluded_from_denominator(self):
        out = rs.merge(self._data(non_store=True), self._cache(), {})
        a = out["assets"][0]
        self.assertEqual(a["resolution"]["reason"], "non_store")
        self.assertFalse(a["resolution"]["id_verified"])
        self.assertIsNone(a["author"])
        self.assertEqual(out["enrichment"]["store_eligible"], 0)
        self.assertEqual(out["enrichment"]["non_store_skipped"], 1)

    def test_override_wins_and_works_for_unresolved_items(self):
        """The predecessor keyed overrides by store id, so the items most needing a
        hand-fix — the unresolved ones, which have no id — could not be addressed."""
        out = rs.merge(self._data(), {"resolved": {}},
                       {"amplify-shader-editor": {"author": "Hand Typed",
                                                  "tags": ["custom"]}})
        a = out["assets"][0]
        self.assertEqual(a["author"], "Hand Typed")
        self.assertEqual(a["tag_source"], "manual")
        self.assertTrue(a["resolution"]["override"])

    def test_override_beats_resolved_value(self):
        out = rs.merge(self._data(), self._cache(),
                       {"amplify-shader-editor": {"author": "Corrected"}})
        self.assertEqual(out["assets"][0]["author"], "Corrected")

    def test_override_for_missing_key_warns_not_crashes(self):
        out = rs.merge(self._data(), {"resolved": {}}, {"ghost-key": {"author": "x"}})
        self.assertTrue(any("ghost-key" in w for w in out["enrichment"]["override_warnings"]))


class TestPacer(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        p = rs.Pacer(base=1.0)
        first = p.blocked()
        second = p.blocked()
        self.assertGreater(second, first)
        for _ in range(20):
            p.blocked()
        self.assertLessEqual(p.penalty, 240.0)

    def test_success_decays_penalty(self):
        p = rs.Pacer()
        p.blocked()
        before = p.penalty
        p.ok()
        self.assertLess(p.penalty, before)




class TestReviewFindingFixes(unittest.TestCase):
    """Regressions for defects found in code review after the first implementation."""

    def test_legacy_api_block_raises_not_returns_none(self):
        """C2: a 429 here read as 'lookup failed' -> 'unverified' -> cached ->
        permanently skipped by --resume. Cache poisoning via the second transport."""
        for status in (429, 403, 202):
            with self.subTest(status=status):
                s = FakeSession(FakeResponse("rate limit exceeded", status=status))
                with self.assertRaises(rs.Blocked):
                    rs.legacy_lookup("68570", session=s)

    def test_legacy_api_real_404_still_returns_none(self):
        s = FakeSession(FakeResponse("not found", status=404))
        self.assertIsNone(rs.legacy_lookup("1", session=s))

    def test_offers_url_gate_fails_closed(self):
        """M3: absent or unparseable offers.url no-oped the gate, leaving the fuzzy
        path as the only guard — exactly what this design replaced."""
        cases = [
            ('{"@type":"Product","name":"X","brand":{"name":"Y"},"offers":{}}',
             "missing or unparseable"),
            # A foreign host fails the URL pattern itself (which embeds the store host),
            # so it is rejected one step earlier than the scheme/host check.
            ('{"@type":"Product","name":"X","brand":{"name":"Y"},'
             '"offers":{"url":"https://evil.example/packages/a/b/c-68570"}}',
             "missing or unparseable"),
            ('{"@type":"Product","name":"X","brand":{"name":"Y"},'
             '"offers":{"url":"/relative/path"}}',
             "missing or unparseable"),
            # http:// on the right host reaches and fails the scheme check.
            ('{"@type":"Product","name":"X","brand":{"name":"Y"},'
             '"offers":{"url":"http://assetstore.unity.com/packages/a/b/c-68570"}}',
             "host/scheme not trusted"),
        ]
        for body, expect in cases:
            with self.subTest(expect=expect):
                s = FakeSession(FakeResponse(
                    f'<script type="application/ld+json">{body}</script>'))
                got = rs.fetch_detail("68570", session=s)
                self.assertIn("_rejected", got)
                self.assertIn(expect, got["_rejected"])

    def test_store_url_is_canonicalized_on_accept(self):
        s = FakeSession(FakeResponse(
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"X","brand":{"name":"Y"},"offers":'
            '{"url":"https://assetstore.unity.com/packages/a/b/c-68570?locale=zh-CN"}}'
            '</script>'))
        got = rs.fetch_detail("68570", session=s)
        self.assertNotIn("_rejected", got)
        self.assertNotIn("?", got["store"])

    def test_shared_store_id_is_reported_not_silently_merged(self):
        """H2: two Synty naming eras can resolve to one id. Not auto-merged, but the
        duplication must be visible rather than silent."""
        def asset(key):
            return {"asset_key": key, "name": key, "author": None,
                    "category": {"path": None, "levels": [], "source": "folder"},
                    "tags": [], "tag_source": "keyword", "thumbnail": None, "store": None,
                    "store_id": None, "non_store": False, "discriminators": [],
                    "flags": [], "versions": [],
                    "resolution": {"method": None, "id_verified": False}}
        data = {"assets": [asset("polygon-prototype"), asset("polygon-prototype-synty")]}
        rec = {"status": "resolved", "id": "137126", "score": 1.0,
               "legacy": {"publisher": "Synty Studios"}, "detail": {}}
        cache = {"resolved": {"polygon-prototype": rec, "polygon-prototype-synty": rec}}
        out = rs.merge(data, cache, {})
        self.assertIn("137126", out["enrichment"]["shared_store_ids"])
        self.assertTrue(any("137126" in w for w in out["enrichment"]["override_warnings"]))


class MultiSession:
    """Serves a different response per transport host."""

    def __init__(self, by_host):
        self.by_host = by_host
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        for frag, resp in self.by_host.items():
            if frag in url:
                return resp
        return FakeResponse("", status=404)


class TestSearchPool(unittest.TestCase):
    """A single frontend cannot serve 861 lookups (Brave: ~4-7 queries then 429 for
    minutes). Rotation must make a block cost the transport, not the asset."""

    HIT = 'https://assetstore.unity.com/packages/tools/visual-scripting/x-68570'

    def test_pool_has_multiple_measured_transports(self):
        names = [n for n, _, _ in rs.TRANSPORTS]
        self.assertGreaterEqual(len(names), 3)
        self.assertIn("bing-rss", names)
        self.assertIn("brave", names)

    def test_block_costs_the_transport_not_the_asset(self):
        """The whole point of rotation: first transport 429s, the query still resolves."""
        s = MultiSession({
            "bing.com": FakeResponse("too many requests", status=429),
            "duckduckgo.com": FakeResponse(self.HIT, status=200),
            "brave.com": FakeResponse(self.HIT, status=200),
        })
        pool = rs.SearchPool(session=s)
        ids, via = pool.search("Amplify Shader Editor")
        self.assertEqual(ids, ["68570"])
        self.assertNotEqual(via, "bing-rss")
        self.assertEqual(pool.state["bing-rss"]["blocks"], 1)
        self.assertGreater(pool.state["bing-rss"]["cooldown_until"], 0)

    def test_blocked_transport_is_skipped_while_cooling(self):
        s = MultiSession({"bing.com": FakeResponse("rate limit", status=429),
                          "duckduckgo.com": FakeResponse(self.HIT),
                          "brave.com": FakeResponse(self.HIT)})
        pool = rs.SearchPool(session=s)
        pool.search("x")
        before = len(s.calls)
        pool.search("y")
        # bing must not be retried during its cooldown
        self.assertNotIn("bing.com", "".join(s.calls[before:]))

    def test_all_blocked_raises_its_own_exception(self):
        s = MultiSession({"": FakeResponse("too many requests", status=429)})
        pool = rs.SearchPool(session=s)
        with self.assertRaises(rs.AllTransportsBlocked):
            pool.search("x")
        # and it is NOT a plain Blocked, so the driver can tell them apart
        self.assertFalse(issubclass(rs.AllTransportsBlocked, rs.Blocked))

    def test_cooldown_escalates_and_caps(self):
        """Escalation must survive the fact that a cooling transport is never retried:
        it is keyed to the block count, and only advances as cooldowns expire (which is
        what happens across a multi-hour run)."""
        s = MultiSession({"": FakeResponse("429", status=429)})
        pool = rs.SearchPool(session=s)
        seen = []
        for _ in range(8):
            try:
                pool.search("x")
            except rs.AllTransportsBlocked:
                pass
            seen.append(pool.state["brave"]["cooldown"])
            # Simulate elapsed time so the pool is willing to try again.
            for st in pool.state.values():
                st["cooldown_until"] = 0.0
        self.assertGreater(seen[-1], seen[0], f"no escalation: {seen}")
        self.assertLessEqual(max(seen), rs.SearchPool.COOLDOWN_MAX)
        self.assertEqual(seen[0], rs.SearchPool.COOLDOWN_START)

    def test_empty_result_is_not_a_block(self):
        s = MultiSession({"": FakeResponse("no results here", status=200)})
        pool = rs.SearchPool(session=s)
        ids, via = pool.search("nonexistent")
        self.assertEqual(ids, [])
        self.assertEqual(via, "all-empty")
        self.assertTrue(all(st["blocks"] == 0 for st in pool.state.values()))
        self.assertTrue(all(st["empty"] >= 1 for st in pool.state.values()))

    def test_all_transport_request_errors_raise_last_error(self):
        class OfflineSession:
            def get(self, url, **kwargs):
                raise rs.requests.ConnectionError("offline")

        pool = rs.SearchPool(session=OfflineSession())
        with self.assertRaises(rs.requests.ConnectionError):
            pool.search("offline")

    def test_one_transport_returning_empty_falls_through_to_the_next(self):
        """The failure that produced a 3.3% spike: bing's RSS view returned 200-with-no-
        results for 28 of 30 real titles, and an early `return` treated that as proof of
        absence, so DuckDuckGo — which had the answers — was never asked."""
        s = MultiSession({
            "bing.com": FakeResponse("no results", status=200),
            "duckduckgo.com": FakeResponse(self.HIT, status=200),
            "brave.com": FakeResponse(self.HIT, status=200),
        })
        pool = rs.SearchPool(session=s)
        ids, via = pool.search("Amplify Shader Editor")
        self.assertEqual(ids, ["68570"])
        self.assertNotEqual(via, "bing-rss")
        self.assertEqual(pool.state["bing-rss"]["empty"], 1)

    def test_unquoted_query_is_tried_when_every_transport_is_empty_on_quoted(self):
        class LadderSession:
            def __init__(self):
                self.queries = []

            def get(self, url, **kw):
                q = kw.get("params", {}).get("q", "")
                self.queries.append(q)
                # Only the unquoted form yields a result.
                if '"' in q:
                    return FakeResponse("no results", status=200)
                return FakeResponse(TestSearchPool.HIT, status=200)

        s = LadderSession()
        pool = rs.SearchPool(session=s)
        ids, _ = pool.search("Some Obscure Pack")
        self.assertEqual(ids, ["68570"])
        self.assertTrue(any('"' in q for q in s.queries), "quoted form must be tried first")
        self.assertTrue(any('"' not in q for q in s.queries), "unquoted fallback must run")

    def test_rotation_spreads_load(self):
        s = MultiSession({"": FakeResponse(self.HIT)})
        pool = rs.SearchPool(session=s)
        used = {pool.search("q%d" % i)[1] for i in range(len(rs.TRANSPORTS))}
        self.assertGreater(len(used), 1, "cursor must rotate, not pin one transport")

    def test_bing_rss_response_shape_parses(self):
        """bing's RSS view returns XML, ~6 KB vs brave's ~200 KB for the same ids."""
        rss = ('<?xml version="1.0"?><rss><channel><item><link>'
               'https://assetstore.unity.com/packages/tools/visual-scripting/'
               'amplify-shader-editor-68570</link></item></channel></rss>')
        self.assertEqual(rs.extract_ids(rss), ["68570"])

    def test_percent_encoded_redirect_wrapper_is_decoded(self):
        wrapped = ("/l/?uddg=https%3A%2F%2Fassetstore.unity.com%2Fpackages%2Ftools%2F"
                   "visual-scripting%2Famplify-shader-editor-68570")
        self.assertEqual(rs.extract_ids(wrapped), ["68570"])


class TestStoreNameIsAuthoritative(unittest.TestCase):
    """The store's own name replaces the filename-derived one for display; the local
    name is retained and stays searchable, and asset_key never moves."""

    def _asset(self, name="POLYGON Prototype"):
        return {"asset_key": "polygon-prototype", "name": name, "local_name": None,
                "author": None,
                "category": {"path": None, "levels": [], "source": "folder"},
                "tags": [], "tag_source": "keyword", "thumbnail": None, "store": None,
                "store_id": None, "non_store": False, "discriminators": [], "flags": [],
                "versions": [], "resolution": {"method": None, "id_verified": False}}

    def _cache(self, store_name):
        return {"resolved": {"polygon-prototype": {
            "status": "resolved", "id": "137126", "score": 0.9,
            "legacy": {"publisher": "Synty Studios", "name": store_name},
            "detail": {"author": "Synty Studios", "name": store_name,
                       "store": "https://assetstore.unity.com/packages/a/b/c-137126"}}},
            "misses": {}}

    def test_store_name_replaces_local_name(self):
        data = {"assets": [self._asset()]}
        out = rs.merge(data, self._cache("POLYGON - Prototype Pack - Art by Synty"), {})
        a = out["assets"][0]
        self.assertEqual(a["name"], "POLYGON - Prototype Pack - Art by Synty")
        self.assertEqual(a["local_name"], "POLYGON Prototype")

    def test_asset_key_is_unchanged_by_the_rename(self):
        """If the key moved with the display name every override would detach."""
        data = {"assets": [self._asset()]}
        out = rs.merge(data, self._cache("Totally Different Store Name"), {})
        self.assertEqual(out["assets"][0]["asset_key"], "polygon-prototype")

    def test_identical_names_still_record_local_name(self):
        data = {"assets": [self._asset("Amplify Shader Editor")]}
        out = rs.merge(data, self._cache("Amplify Shader Editor"), {})
        a = out["assets"][0]
        self.assertEqual(a["name"], "Amplify Shader Editor")
        self.assertEqual(a["local_name"], "Amplify Shader Editor")

    def test_unresolved_keeps_the_local_name_as_display_name(self):
        data = {"assets": [self._asset()]}
        out = rs.merge(data, {"resolved": {}}, {})
        a = out["assets"][0]
        self.assertEqual(a["name"], "POLYGON Prototype")
        self.assertIsNone(a["local_name"])

    def test_detail_name_wins_over_legacy_name(self):
        cache = self._cache("Legacy Name")
        cache["resolved"]["polygon-prototype"]["detail"]["name"] = "Current Store Name"
        out = rs.merge({"assets": [self._asset()]}, cache, {})
        self.assertEqual(out["assets"][0]["name"], "Current Store Name")

    def test_override_can_still_correct_the_name(self):
        out = rs.merge({"assets": [self._asset()]},
                       self._cache("Wrong Store Name"),
                       {"polygon-prototype": {"name": "Hand Corrected"}})
        self.assertEqual(out["assets"][0]["name"], "Hand Corrected")


class TestTrailingIdExtraction(unittest.TestCase):
    """A non-greedy match captured the first digit run inside the slug instead of the
    trailing id, silently mis-identifying every package whose slug contains a number.
    The fail-closed offers.url check is what exposed it."""

    CASES = [
        ("templates/systems/fps-framework-2-0-278978", "278978"),
        ("vfx/particles/glowing-orbs-pack-vol-3-153187", "153187"),
        ("3d/characters/robots-ultimate-pack-02-cute-series-213777", "213777"),
        ("2d/gui/gui-pro-simple-casual-1-0-7-193261", "193261"),
        ("tools/visual-scripting/amplify-shader-editor-68570", "68570"),
        ("3d/environments/polygon-battle-royale-low-poly-3d-art-by-synty-128513", "128513"),
    ]

    def test_trailing_id_wins_over_digits_inside_the_slug(self):
        for path, want in self.CASES:
            with self.subTest(path=path):
                url = "https://assetstore.unity.com/packages/" + path
                self.assertEqual(rs.extract_ids(url), [want])

    def test_query_string_does_not_shift_the_id(self):
        url = ("https://assetstore.unity.com/packages/templates/systems/"
               "fps-framework-2-0-278978?locale=zh-CN")
        self.assertEqual(rs.extract_ids(url), ["278978"])

    def test_offers_url_check_accepts_a_numeric_slug(self):
        body = ('{"@type":"Product","name":"FPS Framework 2.0","brand":{"name":"Akila"},'
                '"offers":{"url":"https://assetstore.unity.com/packages/templates/'
                'systems/fps-framework-2-0-278978"}}')
        s = FakeSession(FakeResponse(
            f'<script type="application/ld+json">{body}</script>'))
        got = rs.fetch_detail("278978", session=s)
        self.assertNotIn("_rejected", got)


class TestGateRelaxations(unittest.TestCase):
    """Accept more true matches without letting any sibling SKU through."""

    NEWLY_ACCEPTED = [
        ("Animancer Pro", "Animancer Pro v8"),                      # version marker
        ("Train Controller (Railroad System)",
         "Train Controller (Railroad System) v3.4"),
        ("ModernCity", "Modern City Pack"),                         # word-split only
        ("School - Low Poly Asset Pack by ithappy",
         "School - Low Poly 3D Models Pack"),                       # differing tail
    ]

    STILL_REJECTED = [
        ("Epic Toon VFX 2", "Epic Toon VFX 3"),
        ("GPU Instancer", "GPU Instancer Pro"),
        ("Character Auras", "Character Auras 3"),
        ("Pure Nature 2 Meadows", "Pure Nature 2 Mountains"),
        ("Monsters Ultimate Pack 01 Cute Series",
         "Monsters Ultimate Pack 03 Cute Series"),
        ("POLYGON City", "POLYGON City Zombies"),
        ("Robots Ultimate Pack 02 Cute Series",
         "Robots Ultimate Pack 03 Cute Series"),
    ]

    def test_newly_accepted(self):
        for local, store in self.NEWLY_ACCEPTED:
            with self.subTest(local=local):
                ok, reason, _ = rs.verify_identity(local, store)
                self.assertTrue(ok, f"{local} -> {store}: {reason}")

    def test_relaxations_do_not_open_the_sibling_hole(self):
        for local, store in self.STILL_REJECTED:
            with self.subTest(local=local):
                ok, reason, _ = rs.verify_identity(local, store)
                self.assertFalse(ok, f"{local} -> {store} wrongly accepted ({reason})")

    def test_only_v_prefixed_versions_are_stripped_from_store_names(self):
        """A bare trailing number is identity; stripping it would merge Epic Toon VFX 2
        and 3 into one product."""
        self.assertTrue(rs.verify_identity("Foo", "Foo v2.1")[0])
        self.assertFalse(rs.verify_identity("Foo 2", "Foo 3")[0])

    def test_despace_removes_no_tokens(self):
        self.assertNotEqual(rs.despace("GPU Instancer"), rs.despace("GPU Instancer Pro"))
        self.assertEqual(rs.despace("ModernCity"), rs.despace("Modern City"))


class TestHtmlEntitiesDecoded(unittest.TestCase):
    """Store JSON-LD carries entities; 'Textures &amp; Materials' would render literally."""

    def test_parse_decodes_entities(self):
        body = ('{"@type":"Product","name":"Foo &amp; Bar","brand":{"name":"A &amp; B"},'
                '"offers":{"url":"https://assetstore.unity.com/packages/a/b/c-1"}}')
        crumbs = ('{"@type":"BreadcrumbList","itemListElement":['
                  '{"item":{"name":"Home"}},{"item":{"name":"2D"}},'
                  '{"item":{"name":"Textures &amp; Materials"}},{"item":{"name":"Foo"}}]}')
        meta = rs.parse_ld_json(
            f'<script type="application/ld+json">{body}</script>'
            f'<script type="application/ld+json">{crumbs}</script>')
        self.assertEqual(meta["name"], "Foo & Bar")
        self.assertEqual(meta["author"], "A & B")
        self.assertIn("Textures & Materials", meta["category_levels"])
        self.assertNotIn("&amp;", meta["category_path"])

    def test_merge_repairs_an_already_cached_entity(self):
        asset = {"asset_key": "k", "name": "Foo", "local_name": None, "author": None,
                 "category": {"path": None, "levels": [], "source": "folder"},
                 "tags": [], "tag_source": "keyword", "thumbnail": None, "store": None,
                 "store_id": None, "non_store": False, "discriminators": [], "flags": [],
                 "versions": [], "resolution": {}}
        cache = {"resolved": {"k": {
            "status": "resolved", "id": "1", "score": 1.0,
            "legacy": {"publisher": "P &amp; Q", "name": "Foo &amp; Bar"},
            "detail": {"author": "P &amp; Q", "name": "Foo &amp; Bar",
                       "category_levels": ["2D", "Textures &amp; Materials"],
                       "category_path": "2D/Textures &amp; Materials"}}}}
        out = rs.merge({"assets": [asset]}, cache, {})
        a = out["assets"][0]
        self.assertEqual(a["author"], "P & Q")
        self.assertEqual(a["name"], "Foo & Bar")
        self.assertEqual(a["category"]["path"], "2D/Textures & Materials")


class TestControlCharactersInJsonLd(unittest.TestCase):
    """The store embeds raw newlines/tabs inside the description string. Strict JSON
    rejects those as control characters, and catching the ValueError silently dropped
    the Product block for every package with a multi-line description."""

    def _page(self, desc):
        return ('<script type="application/ld+json">'
                '{"@context":"https://schema.org/","@type":"Product",'
                '"name":"Final IK","brand":{"name":"RootMotion"},'
                f'"description":"{desc}",'
                '"offers":{"url":"https://assetstore.unity.com/packages/tools/animation/'
                'final-ik-14290"}}'
                '</script>')

    def test_raw_newline_in_description_still_parses(self):
        meta = rs.parse_ld_json(self._page("line one\nline two\ttabbed"))
        self.assertIsNotNone(meta, "a raw control char must not drop the Product block")
        self.assertEqual(meta["name"], "Final IK")
        self.assertEqual(meta["author"], "RootMotion")

    def test_clean_description_unaffected(self):
        meta = rs.parse_ld_json(self._page("all on one line"))
        self.assertEqual(meta["name"], "Final IK")

    def test_invalid_backslash_escape_still_parses(self):
        """strict=False covers control chars but NOT an invalid escape. A stray
        backslash (Windows path, "\\ " separator) in a publisher description killed the
        whole Product block, leaving a verified id with no store URL or thumbnail."""
        meta = rs.parse_ld_json(self._page("see C:\\path\\ for details"))
        self.assertIsNotNone(meta)
        self.assertEqual(meta["name"], "Final IK")

    def test_valid_escapes_survive_the_repair(self):
        got = rs.loads_lenient(r'{"@type":"Product","name":"A\"q\"","d":"ok\nfine"}')
        self.assertEqual(got["name"], 'A"q"')
        self.assertEqual(got["d"], "ok\nfine")

    def test_genuinely_malformed_json_still_returns_none(self):
        self.assertIsNone(rs.parse_ld_json(
            '<script type="application/ld+json">{"@type":"Product",,,}</script>'))

    def test_fetch_recovers_store_url_for_such_a_page(self):
        s = FakeSession(FakeResponse(self._page("multi\nline")))
        got = rs.fetch_detail("14290", session=s)
        self.assertNotIn("_rejected", got)
        self.assertIn("14290", got["store"])


class TestEnrichIsIdempotent(unittest.TestCase):
    """merge() writes back into assets.json, which is its own input next run. Deriving
    local_name from asset["name"] therefore destroyed the on-disk name on a second
    enrich — observed as the store-renamed count collapsing from 159 to 7."""

    def _asset(self):
        return {"asset_key": "k", "name": "POLYGON Prototype",
                "local_name": "POLYGON Prototype", "author": None,
                "category": {"path": None, "levels": [], "source": "folder"},
                "tags": [], "tag_source": "keyword", "thumbnail": None, "store": None,
                "store_id": None, "non_store": False, "discriminators": [], "flags": [],
                "versions": [], "resolution": {}}

    def _cache(self):
        return {"resolved": {"k": {
            "status": "resolved", "id": "137126", "score": 1.0,
            "legacy": {"publisher": "Synty Studios",
                       "name": "POLYGON - Prototype Pack - Art by Synty"},
            "detail": {}}}}

    def test_second_enrich_preserves_the_on_disk_name(self):
        data = {"assets": [self._asset()]}
        for run in (1, 2, 3):
            data = rs.merge(data, self._cache(), {})
            a = data["assets"][0]
            self.assertEqual(a["name"], "POLYGON - Prototype Pack - Art by Synty",
                             f"run {run}")
            self.assertEqual(a["local_name"], "POLYGON Prototype",
                             f"local_name clobbered on run {run}")

    def test_renamed_count_is_stable_across_runs(self):
        data = {"assets": [self._asset()]}
        counts = []
        for _ in range(3):
            data = rs.merge(data, self._cache(), {})
            counts.append(sum(1 for x in data["assets"]
                              if x.get("local_name") and x["local_name"] != x["name"]))
        self.assertEqual(counts, [1, 1, 1], f"unstable: {counts}")


class TestPendingWorklist(unittest.TestCase):
    """`export-titles --pending` narrows the worklist to queued assets so a session
    spends its search budget only on what actually needs it."""

    def test_filter_returns_only_queued(self):
        assets = [{"asset_key": "a", "name": "A", "non_store": False},
                  {"asset_key": "b", "name": "B", "non_store": False},
                  {"asset_key": "c", "name": "C", "non_store": False}]
        got = rs.filter_worklist(assets, pending_keys={"a", "c"})
        self.assertEqual([r["asset_key"] for r in got], ["a", "c"])

    def test_no_filter_returns_all_store_eligible(self):
        assets = [{"asset_key": "a", "name": "A", "non_store": False},
                  {"asset_key": "n", "name": "N", "non_store": True}]
        got = rs.filter_worklist(assets, pending_keys=None)
        self.assertEqual([r["asset_key"] for r in got], ["a"])

    def test_non_store_excluded_even_if_somehow_queued(self):
        assets = [{"asset_key": "n", "name": "N", "non_store": True}]
        self.assertEqual(rs.filter_worklist(assets, pending_keys={"n"}), [])

    def test_empty_queue_yields_empty_worklist(self):
        assets = [{"asset_key": "a", "name": "A", "non_store": False}]
        self.assertEqual(rs.filter_worklist(assets, pending_keys=set()), [])


class TestQueueCleanup(unittest.TestCase):
    """Resolved assets must leave the queue, or it grows forever and stops meaning
    anything. Unresolved ones must stay — dropping them silently forgets an asset."""

    def _queue(self, keys):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "pending-enrichment.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"generated": "t", "pending": [
                {"asset_key": k, "title": k, "first_seen": "2026-08-02",
                 "previously_unresolved": False} for k in keys]}, fh)
        return p

    def test_resolved_entry_is_removed(self):
        p = self._queue(["a", "b"])
        cache = {"resolved": {"a": {"status": "resolved"}}}
        left = rs.prune_pending_queue(p, cache)
        self.assertEqual(left, ["b"])
        with open(p, encoding="utf-8") as fh:
            self.assertEqual([e["asset_key"] for e in json.load(fh)["pending"]], ["b"])

    def test_unresolved_entry_survives(self):
        p = self._queue(["a"])
        cache = {"resolved": {"a": {"status": "unverified"}}}
        self.assertEqual(rs.prune_pending_queue(p, cache), ["a"])

    def test_missing_queue_is_a_noop(self):
        self.assertEqual(rs.prune_pending_queue("/nonexistent/q.json", {"resolved": {}}), [])

    def test_malformed_queue_does_not_raise(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "q.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        self.assertEqual(rs.prune_pending_queue(p, {"resolved": {}}), [])

    def test_no_tmp_left_behind(self):
        p = self._queue(["a"])
        rs.prune_pending_queue(p, {"resolved": {"a": {"status": "resolved"}}})
        d = os.path.dirname(p)
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class TestEnrichmentDoesNotMirror(unittest.TestCase):
    """The viewer reads the CDN URL now, so a mirror written here would be 303 MB of
    OneDrive-synced bytes nothing reads. mirror_thumbnail itself is deliberately
    retained -- it is the reversal path if the remote-only decision is revisited, and
    TestThumbnailValidation above is the host-allowlist and scheme-rejection coverage
    that would be expensive to rebuild. Retained, but never reached from enrichment.
    """

    REMOTE = "https://assetstorev1-prd-cdn.unity3d.com/key-image/x.jpg"

    @staticmethod
    def _explode(*a, **k):
        raise AssertionError("enrichment called mirror_thumbnail")

    def test_prefetched_detail_writes_no_mirror(self):
        from unittest import mock
        rec = {"status": "resolved", "id": "68570",
               "detail": {"thumbnail_remote": self.REMOTE}}
        with mock.patch.object(rs, "mirror_thumbnail", self._explode):
            got = rs.attach_detail(rec)
        self.assertNotIn("thumbnail_local", got)

    def test_fetched_detail_writes_no_mirror(self):
        from unittest import mock
        rec = {"status": "resolved", "id": "68570"}
        with mock.patch.object(rs, "fetch_detail",
                               lambda *a, **k: {"thumbnail_remote": self.REMOTE}), \
             mock.patch.object(rs, "mirror_thumbnail", self._explode):
            got = rs.attach_detail(rec)
        self.assertEqual(got["detail"]["thumbnail_remote"], self.REMOTE)
        self.assertNotIn("thumbnail_local", got)

    def test_attach_detail_has_no_write_destination(self):
        """index_dir and root existed only to locate .index/thumbs. Left in place once
        nothing writes, they invite a caller to pass a directory and reasonably expect
        a mirror to land in it. Removing them makes the guarantee structural rather
        than a behaviour someone can reintroduce by filling the parameter back in."""
        import inspect
        params = inspect.signature(rs.attach_detail).parameters
        for dead in ("index_dir", "root"):
            with self.subTest(parameter=dead):
                self.assertNotIn(dead, params)

class _FakePacer:
    def __init__(self, base=0.0):
        pass

    def ok(self):
        pass

    def wait(self):
        pass

    def blocked(self):
        return 0.0


class _FakePool:
    transports = [("fake", None, None)]

    def __init__(self, session=None):
        pass

    def cooldown_remaining(self):
        return 0.0
    def summary(self):
        return {}


class TestPendingResolveScope(unittest.TestCase):
    """`resolve --pending` attempts only queued unresolved store assets. A missing,
    empty, or malformed queue must attempt NOTHING — never widen into a full
    store-eligible sweep. Non-store assets stay excluded even when queued."""

    def setUp(self):
        import tempfile
        self.state = tempfile.mkdtemp()
        self.patchers = []
        self.started = False

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()

    def _write(self, name, obj):
        with open(os.path.join(self.state, name), "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh)

    def _asset(self, key, non_store=False):
        return {"asset_key": key, "name": key.upper(), "non_store": non_store}

    def _run(self, args, assets, cache=None, queue=None, resolve_fn=None):
        import fcntl
        from unittest import mock
        import index_assets as ia
        self._write("assets.json", {"assets": assets})
        if cache is not None:
            self._write("cache.json", cache)
        if queue is not None:
            self._write("pending-enrichment.json", queue)
        attempted = []

        def fake_resolve(asset, pool, session=None):
            attempted.append(asset["asset_key"])
            return {"status": "resolved", "id": "42", "transport": "fake"}

        if not self.started:
            self.patchers = [
                mock.patch.object(ia, "state_dir", return_value=self.state),
                mock.patch.object(rs, "resolve_asset", side_effect=resolve_fn or fake_resolve),
                mock.patch.object(rs, "fetch_detail", return_value={"author": "p"}),
                mock.patch.object(rs, "Pacer", _FakePacer),
                mock.patch.object(rs, "SearchPool", _FakePool),
                mock.patch.object(rs.requests, "Session", lambda: None),
                mock.patch.object(rs.time, "sleep", lambda s: None),
            ]
            for p in self.patchers:
                p.start()
            self.started = True
            self.fcntl = fcntl
        code = rs.main(["resolve"] + args)
        return code, attempted

    def test_pending_resume_attempts_only_queued_unresolved(self):
        cache = {"resolved": {"b": {"status": "resolved"}}}
        queue = {"pending": [{"asset_key": "a"}, {"asset_key": "b"}]}
        code, attempted = self._run(
            ["--pending", "--resume"],
            [self._asset("a"), self._asset("b"), self._asset("c")],
            cache=cache, queue=queue)
        self.assertEqual(code, 0)
        self.assertEqual(attempted, ["a"])

    def test_pending_without_resume_retries_resolved_entry(self):
        cache = {"resolved": {"b": {"status": "resolved"}}}
        queue = {"pending": [{"asset_key": "a"}, {"asset_key": "b"}]}
        code, attempted = self._run(
            ["--pending"],
            [self._asset("a"), self._asset("b")],
            cache=cache, queue=queue)
        self.assertEqual(code, 0)
        self.assertEqual(attempted, ["a", "b"])

    def test_empty_queue_attempts_zero(self):
        code, attempted = self._run(
            ["--pending", "--resume"], [self._asset("a")],
            cache={"resolved": {}}, queue={"pending": []})
        self.assertEqual(code, 0)
        self.assertEqual(attempted, [])

    def test_missing_queue_attempts_zero(self):
        code, attempted = self._run(
            ["--pending", "--resume"], [self._asset("a")], cache={"resolved": {}})
        self.assertEqual(code, 0)
        self.assertEqual(attempted, [])

    def test_malformed_queue_attempts_zero(self):
        code, attempted = self._run(
            ["--pending", "--resume"], [self._asset("a")],
            cache={"resolved": {}}, queue="{ not json")
        self.assertEqual(code, 0)
        self.assertEqual(attempted, [])

    def test_no_pending_flag_keeps_full_scope(self):
        queue = {"pending": [{"asset_key": "a"}]}
        code, attempted = self._run(
            ["--resume"], [self._asset("a"), self._asset("b"), self._asset("c")],
            cache={"resolved": {}}, queue=queue)
        self.assertEqual(code, 0)
        self.assertEqual(attempted, ["a", "b", "c"])

    def test_non_store_queued_is_skipped(self):
        queue = {"pending": [{"asset_key": "n"}]}
        code, attempted = self._run(
            ["--pending"],
            [self._asset("a"), self._asset("n", non_store=True)],
            cache={"resolved": {}}, queue=queue)
        self.assertEqual(code, 0)
        self.assertEqual(attempted, [])

    def test_all_blocked_abort_returns_failure_after_six_attempts(self):
        def blocked(asset, pool, session=None):
            raise rs.AllTransportsBlocked("offline")

        assets = [self._asset(chr(ord("a") + i)) for i in range(6)]
        code, attempted = self._run([], assets, cache={"resolved": {}},
                                    resolve_fn=blocked)
        self.assertEqual(code, 3)
        self.assertEqual(attempted, [])

class TestStructuredProgress(unittest.TestCase):
    def test_progress_json_is_prefixed_machine_data_and_disabled_by_default(self):
        import contextlib
        import io

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rs._progress(True, "resolve", 1, 2, "asset-a",
                         resolved=1, failed=0, blocked=0, skipped=0)
        line = stream.getvalue().strip()
        self.assertTrue(line.startswith("UL_PROGRESS "))
        event = json.loads(line[len("UL_PROGRESS "):])
        self.assertEqual(event, {
            "stage": "resolve", "completed": 1, "total": 2,
            "current_item": "asset-a",
            "counts": {"resolved": 1, "failed": 0, "blocked": 0, "skipped": 0},
        })
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            rs._progress(False, "resolve", 2, 2)
        self.assertEqual(stream.getvalue(), "")



class TestCacheWriteLock(unittest.TestCase):
    """resolve/spike/import-ids lifetime-hold cache-write.lock and fail fast on
    contention with a nonzero exit and zero cache writes — a second whole-snapshot
    writer would silently drop the first writer's per-asset entries."""

    def setUp(self):
        import tempfile
        self.state = tempfile.mkdtemp()
        self.patchers = []

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()

    def _asset(self, key):
        return {"asset_key": key, "name": key.upper(), "non_store": False}

    def _start(self):
        import tempfile
        from unittest import mock
        import index_assets as ia
        self._write("assets.json", {"assets": [self._asset("a")]})
        self._write("cache.json", {"resolved": {}})
        self.patchers = [
            mock.patch.object(ia, "state_dir", return_value=self.state),
            mock.patch.object(rs, "resolve_asset",
                              side_effect=AssertionError("resolver ran under contention")),
            mock.patch.object(rs, "Pacer", _FakePacer),
            mock.patch.object(rs, "SearchPool", _FakePool),
            mock.patch.object(rs.requests, "Session", lambda: None),
        ]
        for p in self.patchers:
            p.start()

    def _write(self, name, obj):
        import json as _json
        with open(os.path.join(self.state, name), "w", encoding="utf-8") as fh:
            _json.dump(obj, fh)

    def _read_cache(self):
        with open(os.path.join(self.state, "cache.json"), encoding="utf-8") as fh:
            return fh.read()

    def test_contended_lock_fails_fast_then_runs_after_release(self):
        import fcntl
        self._start()
        lock_path = os.path.join(self.state, "cache-write.lock")
        holder = open(lock_path, "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            with self.assertRaises(SystemExit) as cm:
                rs.main(["resolve", "--pending"])
            self.assertEqual(cm.exception.code, 3)
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
        self.assertEqual(self._read_cache(), '{"resolved": {}}')
        # After release the identical command runs and the lock is reusable.
        self.assertEqual(rs.main(["resolve", "--pending"]), 0)
        self.assertEqual(rs.main(["resolve", "--pending"]), 0)

    def test_lock_file_lives_in_state_dir(self):
        self._start()
        self.assertEqual(rs.main(["resolve", "--pending"]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.state, "cache-write.lock")))


    def test_enrich_lock_handoff_skips_nested_lock(self):
        from unittest import mock
        with mock.patch.object(rs, "_main_unlocked", return_value=7) as run:
            self.assertEqual(rs.main(["enrich"], state_lock_held=True), 7)
        run.assert_called_once_with(["enrich"])
    def test_enrich_acquires_shared_state_lock(self):
        import index_assets as ia
        from unittest import mock
        with mock.patch.object(ia, "state_dir", return_value=self.state), \
                mock.patch.object(rs, "_main_unlocked", return_value=8) as run:
            self.assertEqual(rs.main(["enrich"]), 8)
        run.assert_called_once_with(["enrich"])
        self.assertTrue(os.path.exists(os.path.join(self.state, "state-write.lock")))

if __name__ == "__main__":
    unittest.main(verbosity=2)
