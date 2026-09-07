#!/usr/bin/env python3
"""
Store enrichment for the Unity asset library.

Resolution verifies by IDENTITY, not string similarity. The chain is:

  1. web search  -> extract the NUMERIC ID ONLY from result URLs (the result's own
     category path is discarded: a wrong category path plus the right id still
     301-redirects to the correct canonical URL, so the id self-canonicalizes)
  2. legacy API  -> authoritative {name, publisher, category} for that id, from a
     second independent endpoint. A CONJUNCTIVE gate compares it to the local title.
  3. detail page, QUERY STRING STRIPPED -> thumbnail, English breadcrumbs, canonical
     link, rating, price. Stripping is mandatory: Accept-Language does NOT override
     ?locale=, which returns an English Product.name beside CJK breadcrumbs and so
     sails through any name-based gate at confidence 1.0.

Similarity is used only to RANK candidates; it never authorizes a write. No single
threshold separates true from false matches on this library (Epic Toon VFX 2 vs 3
scores 0.933, above a genuine Synty rename at 0.872), and a token-subset bonus fires
on 66 distinct-product pairs.

Transport outcomes are typed Hit | NoResult | Blocked. Blocked aborts the run and
writes nothing: collapsing Blocked into NoResult would cache ~861 permanent false
misses and then report a poisoned cache as a 100% hit rate.

Usage:
    python3 resolve_store.py spike            # 30-asset calibration gate
    python3 resolve_store.py resolve --resume # full pass, restartable
    python3 resolve_store.py enrich           # merge cache + overrides -> assets.json
"""

import argparse
import contextlib
import difflib
import fcntl
import html as _html
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
import urllib.parse
from urllib.parse import urlsplit, urlunsplit

import requests

try:
    from progress import json_progress
except ImportError:  # pragma: no cover - direct legacy imports during bootstrap
    json_progress = None

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
STORE_HOST = "assetstore.unity.com"
LEGACY_API = "https://api.assetstore.unity3d.com/package/latest-version/{id}"
# Measured pool. A single frontend cannot serve 861 lookups: Brave permits roughly
# 4-7 queries then returns 429 for many minutes (10 consecutive blocks over 7.5 min
# observed), and DuckDuckGo intermittently serves a 202 "anomaly" interstitial. Their
# block windows are independent, so rotating across four keeps throughput up without
# any API key. Engines that returned zero package URLs (bing HTML, startpage,
# marginalia, yandex) or 403 (ecosia, mojeek) are deliberately excluded.
TRANSPORTS = (
    # name, url, extra query params. bing's RSS view is the cheapest by far:
    # ~6 KB per response against brave's ~200 KB for the same result set.
    ("bing-rss", "https://www.bing.com/search", {"format": "rss"}),
    ("ddg-html", "https://html.duckduckgo.com/html/", {}),
    ("ddg-lite", "https://lite.duckduckgo.com/lite/", {}),
    ("brave", "https://search.brave.com/search", {}),
)


class AllTransportsBlocked(Exception):
    """Every frontend is cooling down. Distinct from a single transport blocking."""


class SearchPool:
    """Round-robins the transport pool. A block costs the transport, not the asset.

    The single-transport design lost an asset on every block. Here a blocked frontend
    is put in escalating cooldown and the same query is retried on the next one, so a
    block only reduces throughput.
    """

    COOLDOWN_START = 90.0
    COOLDOWN_MAX = 1800.0

    def __init__(self, transports=TRANSPORTS, session=None):
        self.transports = list(transports)
        self.session = session or requests
        self.state = {name: {"cooldown_until": 0.0, "cooldown": 0.0,
                             "hits": 0, "blocks": 0, "empty": 0}
                      for name, _, _ in self.transports}
        self._cursor = 0

    def _available(self, now):
        return [t for t in self.transports if self.state[t[0]]["cooldown_until"] <= now]

    def cooldown_remaining(self, now=None):
        now = now if now is not None else time.time()
        waits = [self.state[n]["cooldown_until"] - now for n, _, _ in self.transports]
        return max(0.0, min(waits)) if waits else 0.0

    def search(self, title):
        """Return (ids, transport_name). Raises AllTransportsBlocked.

        An empty result from ONE transport is weak evidence, not proof: engines differ
        wildly in recall for quoted site: queries. Empty results fall through to the
        next transport; only a unanimous empty across available transports is NoResult.
        """
        quoted = f'site:{STORE_HOST} "{title}"'
        plain = f"site:{STORE_HOST} {title}"
        last_block = None
        last_request_error = None
        saw_success = False
        for query in (quoted, plain):
            now = time.time()
            available = self._available(now)
            if not available:
                raise AllTransportsBlocked(
                    f"all {len(self.transports)} transports cooling; "
                    f"next free in {self.cooldown_remaining(now):.0f}s")
            offset = self._cursor % len(available)
            ordered = available[offset:] + available[:offset]
            self._cursor += 1
            for name, url, extra in ordered:
                st = self.state[name]
                try:
                    ids = search_ids(title, url=url, extra=extra,
                                     session=self.session, query=query)
                except Blocked as exc:
                    st["blocks"] += 1
                    st["cooldown"] = min(self.COOLDOWN_MAX,
                                         self.COOLDOWN_START * (2 ** (st["blocks"] - 1)))
                    st["cooldown_until"] = time.time() + st["cooldown"]
                    last_block = f"{name}: {exc}"
                    continue
                except requests.RequestException as exc:
                    last_request_error = exc
                    continue
                saw_success = True
                if ids:
                    st["hits"] += 1
                    st["cooldown"] = max(0.0, st["cooldown"] * 0.5)
                    return ids, name
                st["empty"] += 1

        if last_block and all(self.state[n]["cooldown_until"] > time.time()
                              for n, _, _ in self.transports):
            raise AllTransportsBlocked(last_block)
        if last_request_error is not None and not saw_success:
            raise last_request_error
        return [], "all-empty"
    def summary(self):
        return {n: {k: v for k, v in st.items() if k in ("hits", "blocks", "empty")}
                for n, st in self.state.items()}

THUMB_HOSTS = {"assetstorev1-prd-cdn.unity3d.com", STORE_HOST}
THUMB_MAX_BYTES = 2 * 1024 * 1024
CONTENT_TYPE_EXT = {"image/jpeg": ".jpg", "image/png": ".png",
                    "image/webp": ".webp", "image/gif": ".gif"}

# The id is the digit run at the END of the slug. A non-greedy match without the
# trailing guard captured the first digits inside the slug instead — "2" from
# fps-framework-2-0-278978, "3" from glowing-orbs-pack-vol-3-153187, "02" from
# robots-ultimate-pack-02-cute-series-213777 — silently mis-identifying every package
# whose slug contains a number. The (?![\w-]) guard forces the last run.
PACKAGE_URL_RE = re.compile(
    r"assetstore\.unity\.com/packages/[A-Za-z0-9/._\-]*?-(\d+)(?![\w-])")
LD_JSON_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)

# Marketing tails that may appear in a store name but not the local filename.
TAIL_RE = re.compile(
    r"\s*[-–]?\s*(low\s*poly\s*3d\s*models?\s*pack|low\s*poly\s*3d\s*art\s*by\s*synty|"
    r"art\s*by\s*synty|by\s+ithappy|low\s*poly\s*asset\s*pack|3d\s*models?\s*pack|"
    r"asset\s*pack|pack)\s*$", re.I)

# A trailing v-prefixed version on a STORE name is a release marker, not identity:
# "Animancer Pro v8" is "Animancer Pro". Only v-prefixed forms are stripped — a bare
# trailing number IS identity ("Epic Toon VFX 2" vs "3" must never collapse).
STORE_VERSION_TAIL_RE = re.compile(r"\s+v\.?\s*\d+(?:\.\d+)*[a-z]*\s*$", re.I)

SIMILARITY_FLOOR = 0.55  # ranking aid only — never the authorization

# Blocked-transport signatures, from responses actually observed during planning.
BLOCK_MARKERS = ("anomaly", "unusual traffic", "captcha", "too many requests",
                 "rate limit", "are you a robot", "verify you are human")


class Blocked(Exception):
    """Transport refused us. Never cached, never confused with 'no such package'."""


# ---------------------------------------------------------------------------
# normalization + the conjunctive gate
# ---------------------------------------------------------------------------

def normalize(text):
    """Casefold, strip marketing tails, reduce to alphanumeric tokens.

    Tail stripping iterates: 'POLYGON - Prototype Pack - Art by Synty' must lose
    '- Art by Synty' and then the newly-trailing 'Pack'. A single pass leaves 'pack'
    behind and rejects a genuine rename.
    """
    text = unicodedata.normalize("NFKD", text or "").casefold()
    for _ in range(4):  # bounded; each pass removes at most one tail
        stripped = TAIL_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity(local, store):
    a, b = normalize(local), normalize(store)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def numeric_tokens(text):
    return set(re.findall(r"\d+", normalize(text)))


def despace(text):
    return normalize(text).replace(" ", "")


def verify_identity(local_title, store_name, discriminators=()):
    """Conjunctive gate. Returns (ok: bool, reason: str, score: float).

    Deliberately NOT a similarity threshold. All three conditions must hold:
      1. similarity clears a low floor (catches total mismatch)
      2. numeric tokens agree exactly  (Epic Toon VFX 2 != 3, Pure Nature 2 sibs)
      3. no store token missing from local beyond a whitelisted marketing tail
         (GPU Instancer != GPU Instancer Pro, Character Auras != Character Auras 3)
    """
    store_name = STORE_VERSION_TAIL_RE.sub("", store_name or "")
    score = similarity(local_title, store_name)

    # Word-boundary differences alone must not reject: "ModernCity" vs "Modern City
    # Pack" is the same product. Despacing cannot merge distinct siblings, because it
    # removes no tokens — "gpuinstancer" != "gpuinstancerpro".
    if despace(local_title) and despace(local_title) == despace(store_name):
        return True, "ok (despaced match)", max(score, SIMILARITY_FLOOR)

    if score < SIMILARITY_FLOOR:
        return False, f"similarity {score:.3f} below floor {SIMILARITY_FLOOR}", score

    if numeric_tokens(local_title) != numeric_tokens(store_name):
        return False, "numeric-token mismatch", score

    local_words = set(normalize(local_title).split())
    store_words = set(normalize(store_name).split())
    extra = store_words - local_words
    if extra:
        return False, f"store name has extra token(s) {sorted(extra)}", score

    return True, "ok", score


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------

def _looks_blocked(status, body):
    if status in (429, 403, 202):
        return True
    low = body[:4000].lower()
    return any(m in low for m in BLOCK_MARKERS)


def extract_ids(html):
    """Ordered, de-duplicated package ids. Result category paths are discarded.

    Unquoted twice first: several frontends wrap outbound links in a percent-encoded
    redirect parameter, sometimes doubly encoded.
    """
    try:
        html = urllib.parse.unquote(urllib.parse.unquote(html))
    except Exception:
        pass
    ids = []
    for m in PACKAGE_URL_RE.finditer(html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def search_ids(title, url=None, extra=None, session=None, timeout=25, query=None):
    """Hit | NoResult | Blocked. Raises Blocked; returns [] for NoResult."""
    session = session or requests
    query = query or f'site:{STORE_HOST} "{title}"'
    params = {"q": query}
    params.update(extra or {})
    resp = session.get(url or TRANSPORTS[0][1], params=params,
                       headers={"User-Agent": UA,
                                "Accept-Language": "en-US,en;q=0.9"},
                       timeout=timeout)
    if _looks_blocked(resp.status_code, resp.text):
        raise Blocked(f"HTTP {resp.status_code} from search transport")
    resp.raise_for_status()
    return extract_ids(resp.text)


def legacy_lookup(package_id, session=None, timeout=20):
    """Authoritative {name, publisher, category, version} for an id, or None."""
    session = session or requests
    resp = session.get(LEGACY_API.format(id=package_id),
                       headers={"User-Agent": UA}, timeout=timeout)
    # This endpoint was never rate-limited during calibration, but "never observed"
    # is not "cannot happen" across 861 assets x up to 5 lookups. Without this check a
    # 429 here would read as "legacy lookup failed" -> status "unverified" -> written
    # to cache -> permanently skipped by --resume. That is cache poisoning through the
    # second transport.
    if _looks_blocked(resp.status_code, resp.text):
        raise Blocked(f"HTTP {resp.status_code} from legacy API")
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if data.get("id") else None


def canonical_url(url):
    """Strip query and fragment. Accept-Language does not override ?locale=."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc or STORE_HOST,
                       parts.path, "", ""))


# A backslash in JSON may only introduce one of these. Publisher-written descriptions
# routinely contain stray backslashes (Windows paths, "\ " separators) that make the
# whole block unparseable even with strict=False.
INVALID_ESCAPE_RE = re.compile(r'\\(?![\\"/bfnrtu])')


def loads_lenient(block):
    """Parse a store JSON-LD block, repairing the two malformations it actually ships.

    strict=False covers raw control characters (newlines/tabs inside a description).
    It does NOT cover an invalid escape, so a stray backslash still kills the block —
    which silently dropped the Product and left the asset with no store URL, category
    or thumbnail despite a verified id.
    """
    try:
        return json.loads(block, strict=False)
    except ValueError:
        pass
    try:
        return json.loads(INVALID_ESCAPE_RE.sub(r"\\\\", block), strict=False)
    except ValueError:
        return None


def unescape(value):
    """JSON-LD on the store carries HTML entities: a breadcrumb comes through as
    'Textures &amp; Materials' and would render literally in the viewer."""
    if isinstance(value, str):
        return _html.unescape(value)
    if isinstance(value, list):
        return [unescape(v) for v in value]
    return value


def parse_ld_json(html):
    """Pull Product + BreadcrumbList out of a detail page. None when absent."""
    product, crumbs = None, None
    for block in LD_JSON_RE.findall(html):
        data = loads_lenient(block)
        if data is None:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product":
                product = item
            elif item.get("@type") == "BreadcrumbList":
                crumbs = [
                    (e.get("item") or {}).get("name") if isinstance(e.get("item"), dict)
                    else e.get("name")
                    for e in item.get("itemListElement", [])
                ]
    if not product:
        return None

    image = (product.get("image") or [None])
    image = image[0] if isinstance(image, list) else image
    if image and image.startswith("//"):
        image = "https:" + image

    offers = product.get("offers") or {}
    rating = product.get("aggregateRating") or {}
    category_levels = [unescape(c) for c in (crumbs or [])
                       if c and c.lower() != "home"][:-1]

    out = {
        "name": unescape(product.get("name")),
        "author": unescape((product.get("brand") or {}).get("name")),
        "thumbnail_remote": image,
        "store": offers.get("url"),
        "price": offers.get("price"),
        "category_levels": category_levels,
        "category_path": "/".join(category_levels) if category_levels else None,
    }
    if rating.get("ratingValue"):
        out["rating"] = float(rating["ratingValue"])
    if rating.get("reviewCount"):
        out["reviews"] = int(rating["reviewCount"])
    return out


def has_non_latin(text):
    """A non-Latin breadcrumb means the query string leaked and the locale won."""
    for ch in text or "":
        if ch.isalpha() and not (
            "LATIN" in unicodedata.name(ch, "") or ch.isascii()
        ):
            return True
    return False


def fetch_detail(package_id, session=None, timeout=25, url_hint=None):
    """Fetch the canonical detail page for an id. Query string always stripped."""
    session = session or requests
    url = canonical_url(url_hint) if url_hint else \
        f"https://{STORE_HOST}/packages/slug/{package_id}"
    resp = session.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                       timeout=timeout, allow_redirects=True)
    if _looks_blocked(resp.status_code, resp.text):
        raise Blocked(f"HTTP {resp.status_code} from store")
    if resp.status_code != 200:
        return None
    meta = parse_ld_json(resp.text)
    if not meta:
        return None

    # The id in offers.url must match the id we asked about. This is the deterministic
    # check the predecessor lacked: the wrong-package case is a 301 chain, and
    # offers.url exposes the mismatch byte-exactly.
    # Fail CLOSED: an absent or unparseable offers.url means the deterministic id
    # check cannot be performed, so the record is not trustworthy. Accepting it would
    # leave the fuzzy path as the only guard, which is what this design replaced.
    store_url = meta.get("store") or ""
    found = PACKAGE_URL_RE.search(store_url)
    if not found:
        return {"_rejected": f"offers.url missing or unparseable: {store_url[:80]!r}"}
    if found.group(1) != str(package_id):
        return {"_rejected": f"offers.url id {found.group(1)} != {package_id}"}
    parts = urlsplit(store_url)
    if parts.scheme != "https" or parts.hostname != STORE_HOST:
        return {"_rejected": f"offers.url host/scheme not trusted: {store_url[:80]!r}"}
    meta["store"] = canonical_url(store_url)

    if any(has_non_latin(c) for c in meta.get("category_levels") or []):
        return {"_rejected": "localized breadcrumb — query string leaked"}
    return meta


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def load_cache(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"resolved": {}, "misses": {}}


def save_cache(path, cache):
    from index_assets import write_atomic
    write_atomic(path, json.dumps(cache, indent=1, sort_keys=True))


# ---------------------------------------------------------------------------
# resolution driver
# ---------------------------------------------------------------------------

def filter_worklist(assets, pending_keys=None):
    """Worklist rows for the searcher. Non-store assets are always excluded — they have
    no store page, so a lookup can only waste budget. pending_keys narrows to the queue."""
    rows = []
    for asset in assets:
        if asset.get("non_store"):
            continue
        if pending_keys is not None and asset["asset_key"] not in pending_keys:
            continue
        rows.append({"asset_key": asset["asset_key"], "title": asset["name"]})
    return rows


def prune_pending_queue(path, cache):
    """Drop entries whose cache record is resolved; keep everything else.

    Only `resolved` leaves the queue. Dropping an unresolved entry would silently forget
    an asset, which is the failure the accumulate-with-first_seen design exists to avoid.
    Returns the remaining asset_keys.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            pending = json.load(fh).get("pending", [])
    except (ValueError, KeyError, TypeError):
        print(f"{os.path.basename(path)} unreadable — queue left untouched")
        return []
    resolved = (cache or {}).get("resolved", {})
    kept = [e for e in pending
            if (resolved.get(e["asset_key"]) or {}).get("status") != "resolved"]
    if len(kept) != len(pending):
        write_atomic_json(path, {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pending": kept})
    return [e["asset_key"] for e in kept]


def write_atomic_json(path, obj):
    from index_assets import write_atomic
    write_atomic(path, json.dumps(obj, indent=1, sort_keys=True))


def _progress(enabled, stage, completed=0, total=None, current_item=None, **counts):
    """Emit one machine-readable progress event without changing human output."""
    if enabled and json_progress is not None:
        if len(counts) == 1 and "counts" in counts:
            counts = counts["counts"]
        json_progress({"stage": stage, "completed": completed, "total": total,
                       "current_item": current_item, "counts": counts})


def verify_candidates(asset, ids, session=None, sleep=None):
    """Run the conjunctive identity gate over candidate ids. Transport-agnostic:
    used by both the search path and externally-supplied ids."""
    title = asset["name"]
    attempts = []
    for package_id in ids[:5]:
        legacy = legacy_lookup(package_id, session=session)
        if sleep:
            sleep()

        prefetched, source = None, None
        if legacy and legacy.get("name"):
            store_name, source = legacy["name"], "legacy-api"
        else:
            # The legacy endpoint has no record for a sizeable minority of ids
            # (newer listings, and ones dropped from that index). The store's own
            # detail page carries the same authority for identity, and its
            # offers.url id check independently confirms the id is canonical — so
            # requiring the legacy API was rejecting assets the store could verify.
            try:
                prefetched = fetch_detail(
                    package_id, session=session,
                    url_hint=f"https://{STORE_HOST}/packages/slug/{package_id}")
            except (Blocked, requests.RequestException):
                prefetched = None
            if prefetched and "_rejected" not in prefetched and prefetched.get("name"):
                store_name, source = prefetched["name"], "detail-page"
            else:
                attempts.append({
                    "id": package_id,
                    "reason": "no authoritative name (legacy API miss and "
                              + ((prefetched or {}).get("_rejected") or "detail unavailable")})
                continue

        ok, reason, score = verify_identity(title, store_name,
                                            asset.get("discriminators", ()))
        if not ok:
            attempts.append({"id": package_id, "store_name": store_name,
                             "reason": reason, "score": round(score, 3),
                             "verified_via": source})
            continue
        rec = {"status": "resolved", "title": title, "id": package_id,
               "legacy": legacy or {}, "score": round(score, 3),
               "verified_via": source, "candidates_rejected": attempts}
        if prefetched:
            rec["detail"] = prefetched
        return rec
    return {"status": "unverified", "title": title, "candidates_rejected": attempts}


def attach_detail(rec, session=None):
    """Fetch the canonical page. Never raises: a detail failure must not lose an
    already-verified id.

    Records carry thumbnail_remote only. The viewer loads that URL from the CDN, so
    mirroring a local copy here would write bytes nothing reads. mirror_thumbnail is
    kept in this module as the way back if that decision is ever revisited, but no
    enrichment path reaches it, and this function no longer accepts a destination to
    write one to."""
    if rec.get("detail"):
        # verify_candidates already fetched it as the identity fallback.
        return rec
    try:
        detail = fetch_detail(rec["id"], session=session,
                              url_hint=f"https://{STORE_HOST}/packages/slug/{rec['id']}")
    except (Blocked, requests.RequestException) as exc:
        rec["detail_error"] = str(exc)
        return rec
    if detail and detail.get("_rejected"):
        rec["status"] = "unverified"
        rec["detail_rejected"] = detail["_rejected"]
        return rec
    if not detail:
        rec["detail_error"] = "no Product found in page JSON-LD"
        return rec
    rec["detail"] = detail
    return rec


def resolve_asset(asset, pool, session=None, sleep=None):
    """Resolve one asset via the transport pool.

    Raises AllTransportsBlocked (every frontend cooling) or Blocked (legacy API).
    Never returns a partially-verified record: the caller either gets a resolved id
    or an explicit no-result/unverified status.
    """
    title = asset["name"]
    ids, transport = pool.search(title)
    if not ids:
        return {"status": "no-result", "title": title, "transport": transport}

    attempts = []
    for package_id in ids[:5]:
        legacy = legacy_lookup(package_id, session=session)
        if sleep:
            sleep()
        if not legacy:
            attempts.append({"id": package_id, "reason": "legacy lookup failed"})
            continue
        ok, reason, score = verify_identity(title, legacy.get("name", ""),
                                            asset.get("discriminators", ()))
        if not ok:
            attempts.append({"id": package_id, "store_name": legacy.get("name"),
                             "reason": reason, "score": round(score, 3)})
            continue
        return {
            "status": "resolved",
            "title": title,
            "id": package_id,
            "legacy": legacy,
            "score": round(score, 3),
            "transport": transport,
            "candidates_rejected": attempts,
        }
    return {"status": "unverified", "title": title, "transport": transport,
            "candidates_rejected": attempts}


class Pacer:
    """Adaptive pacing. Brave tolerates ~4 queries then 429s; back off hard."""

    def __init__(self, base=4.0):
        self.base = base
        self.penalty = 0.0

    def wait(self):
        time.sleep(self.base + self.penalty + random.uniform(0, 1.5))

    def blocked(self):
        self.penalty = min(240.0, (self.penalty or 15.0) * 2)
        return self.penalty

    def ok(self):
        self.penalty = max(0.0, self.penalty * 0.5)


# ---------------------------------------------------------------------------
# thumbnails
# ---------------------------------------------------------------------------

def mirror_thumbnail(url, package_id, out_dir, session=None, timeout=25):
    """Download one key image with scheme/host/type/size validation."""
    session = session or requests
    if not str(package_id).isdigit():
        return None, "non-numeric id"
    parts = urlsplit(url or "")
    if parts.scheme != "https":
        return None, f"scheme {parts.scheme!r} not https"
    if parts.hostname not in THUMB_HOSTS:
        return None, f"host {parts.hostname!r} not allowlisted"

    resp = session.get(url, headers={"User-Agent": UA}, timeout=timeout,
                       stream=True, allow_redirects=False)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        return None, f"content-type {ctype!r} not an image"

    chunks, total = [], 0
    for chunk in resp.iter_content(64 * 1024):
        total += len(chunk)
        if total > THUMB_MAX_BYTES:
            return None, "exceeds 2 MB cap"
        chunks.append(chunk)

    ext = CONTENT_TYPE_EXT.get(ctype, ".img")
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{package_id}{ext}")
    with open(dest, "wb") as fh:
        fh.write(b"".join(chunks))
    return dest, None


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def merge(data, cache, overrides):
    """Apply cache then overrides. Overrides are keyed by asset_key, so a fix to an
    UNRESOLVED item is expressible — the predecessor's id-keyed overrides were not."""
    resolved = cache.get("resolved", {})
    applied, skipped_non_store = 0, 0

    for asset in data["assets"]:
        key = asset["asset_key"]
        if asset.get("non_store"):
            skipped_non_store += 1
            asset["resolution"] = {"method": "skipped", "id_verified": False,
                                   "reason": "non_store"}
            continue
        rec = resolved.get(key)
        if not rec or rec.get("status") != "resolved":
            # Distinguish "searched and failed" from "never searched". Conflating them
            # would tell the user to hand-write overrides for assets a later run will
            # resolve on its own.
            asset["resolution"] = {
                "method": "web-search" if rec else None,
                "id_verified": False,
                "reason": rec.get("status") if rec else "not-attempted",
            }
            continue

        legacy = rec.get("legacy") or {}
        detail = rec.get("detail") or {}
        asset["store_id"] = rec["id"]

        # The store name is authoritative and frequently differs from the filename-derived
        # one ("POLYGON Prototype" on disk vs "POLYGON - Prototype Pack - Art by Synty"
        # upstream). Display the store name, but keep the local one as local_name: it is
        # what the user recognises on disk, so it stays searchable. asset_key is NOT
        # rederived from it — the key must stay stable or every override detaches.
        # local_name is the fixed point: seeded from the filename at scan time and never
        # reassigned here. Deriving it from asset["name"] instead made merge()
        # non-idempotent — a second enrich saw the already-substituted store name as the
        # local one and overwrote the real on-disk name with it.
        store_name = unescape(detail.get("name") or legacy.get("name"))
        if store_name:
            # `not ...get(...)` rather than setdefault: an assets.json written before
            # local_name was seeded at scan time carries an explicit None, which
            # setdefault would leave in place.
            if not asset.get("local_name"):
                asset["local_name"] = asset["name"]
            asset["name"] = store_name

        asset["author"] = unescape(detail.get("author") or legacy.get("publisher"))
        if detail.get("category_path"):
            levels = unescape(detail["category_levels"])
            asset["category"] = {"path": "/".join(levels),
                                 "levels": levels,
                                 "source": "store"}
        if detail.get("store"):
            asset["store"] = detail["store"]
        if detail.get("thumbnail_remote"):
            # remote only. Cache entries written before the switch still carry
            # thumbnail_local; copying it forward would leave assets.json pointing at
            # a directory that no longer exists.
            asset["thumbnail"] = {"remote": detail["thumbnail_remote"]}
        for field in ("rating", "reviews", "price"):
            if detail.get(field) is not None:
                asset[field] = detail[field]
        if legacy.get("version"):
            asset["upstream_version"] = legacy["version"]
        asset["resolution"] = {"method": "web-search", "id_verified": True,
                               "score": rec.get("score")}
        applied += 1

    warnings = []
    for key, patch in (overrides or {}).items():
        target = next((a for a in data["assets"] if a["asset_key"] == key), None)
        if target is None:
            warnings.append(f"override for unknown asset_key {key!r} — ignored")
            continue
        for field, value in patch.items():
            target[field] = value
        if "tags" in patch:
            target["tag_source"] = "manual"
        target.setdefault("resolution", {})["override"] = True

    # Two archives can legitimately resolve to one store id (Synty's naming eras).
    # They are NOT auto-merged: merging would need a rule for combining versions,
    # thumbnails and overrides that nothing yet requires. Detected and reported so the
    # duplication is visible rather than silent.
    by_store_id = {}
    for asset in data["assets"]:
        if asset.get("store_id"):
            by_store_id.setdefault(asset["store_id"], []).append(asset["asset_key"])
    shared = {sid: keys for sid, keys in by_store_id.items() if len(keys) > 1}
    for sid, keys in shared.items():
        warnings.append(f"store id {sid} claimed by {len(keys)} asset_keys: {sorted(keys)} "
                        "— shown as separate entries, not merged")

    data["enrichment"] = {
        "shared_store_ids": {sid: sorted(keys) for sid, keys in shared.items()},
        "resolved": applied,
        "non_store_skipped": skipped_non_store,
        "store_eligible": sum(1 for a in data["assets"] if not a.get("non_store")),
        "override_warnings": warnings,
    }
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _cache_write_lock(index_dir, command):
    """Lifetime hold for the whole-snapshot cache writers (resolve/spike/
    import-ids). Fail-fast and non-blocking: a concurrent second writer would
    silently drop the first one's per-asset entries in its whole-snapshot
    rewrite, so contention is an immediate nonzero exit, never a wait."""
    path = os.path.join(index_dir, "cache-write.lock")
    fh = open(path, "a")
    got = False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except BlockingIOError:
            print(f"{command}: another cache writer holds cache-write.lock — "
                  "run this after the current resolve/spike/import-ids finishes")
            raise SystemExit(3)
        yield
    finally:
        if got:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def load_pending_keys(index_dir, label):
    """Queued asset_keys as a set. Missing, empty, or malformed queues yield an
    EMPTY set — never None — so a broken queue can never widen a --pending run
    into a full store-eligible sweep."""
    path = os.path.join(index_dir, "pending-enrichment.json")
    keys = set()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                keys = {e["asset_key"] for e in json.load(fh).get("pending", [])}
        except (ValueError, KeyError, TypeError):
            print(f"{label}: pending queue unreadable — attempting nothing")
    return keys


def main(argv=None, state_lock_held=False):
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("command", choices=["spike", "resolve", "enrich", "export-titles", "import-ids"])
    probe.add_argument("--state-lock-held", action="store_true")
    probe_args, _ = probe.parse_known_args(argv)
    held = state_lock_held or probe_args.state_lock_held
    if probe_args.command == "enrich" and not held:
        from index_assets import state_dir, state_write_lock
        with state_write_lock(state_dir(), "resolve_store enrich", blocking=True):
            return _main_unlocked(argv)
    return _main_unlocked(argv)
def _main_unlocked(argv=None):
    # This run takes hours and is normally watched through a pipe or a log file.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from index_assets import state_dir
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["spike", "resolve", "enrich", "export-titles", "import-ids"])
    ap.add_argument("--ids-file", default=None,
                    help="import-ids: JSON {asset_key: [candidate_id, ...]}")
    ap.add_argument("--out", default=None, help="export-titles: output path")
    ap.add_argument("--pending", action="store_true",
                    help="export-titles: only assets queued in pending-enrichment.json")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--state-lock-held", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--progress-json", action="store_true",
                    help="emit structured UL_PROGRESS events to stdout")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--base-delay", type=float, default=4.0)
    args = ap.parse_args(argv)

    index_dir = state_dir()
    assets_json = os.path.join(index_dir, "assets.json")
    cache_path = os.path.join(index_dir, "cache.json")
    overrides_path = os.path.join(index_dir, "overrides.json")
    with open(assets_json, encoding="utf-8") as fh:
        data = json.load(fh)

    if args.command == "export-titles":
        pending_keys = load_pending_keys(index_dir, "export-titles") if args.pending else None
        rows = filter_worklist(data["assets"], pending_keys)
        out = args.out or os.path.join(index_dir, "search-worklist.json")
        write_atomic_json(out, rows)
        scope = "queued" if args.pending else "store-eligible"
        print(f"export-titles: {len(rows)} {scope} titles -> {out}")
        if args.pending and not rows:
            print("  (queue is empty — nothing awaiting enrichment)")
        _progress(args.progress_json, "export-titles", 1, 1,
                  counts={"resolved": 0, "failed": 0, "skipped": len(data["assets"]) - len(rows)})
        return 0

    if args.command == "import-ids":
        if not args.ids_file:
            print("import-ids requires --ids-file")
            return 2
        with open(args.ids_file, encoding="utf-8") as fh:
            supplied = json.load(fh)
        with _cache_write_lock(index_dir, args.command):
            by_key = {a["asset_key"]: a for a in data["assets"]}
            cache = load_cache(cache_path)
            session = requests.Session()
            stats = {"resolved": 0, "unverified": 0, "no-result": 0, "unknown-key": 0}
            print(f"import-ids: {len(supplied)} entries from {args.ids_file}")
            for n, (key, ids) in enumerate(sorted(supplied.items()), 1):
                _progress(args.progress_json, "import-ids", n - 1, len(supplied), key,
                          resolved=stats["resolved"], failed=stats["unverified"],
                          skipped=stats["unknown-key"])
                asset = by_key.get(key)
                if asset is None:
                    stats["unknown-key"] += 1
                    print(f"  [{n}] unknown asset_key {key!r} — ignored")
                    continue
                if isinstance(ids, dict):
                    ids = ids.get("ids", [])
                ids = [str(i) for i in (ids or []) if str(i).isdigit()]
                if not ids:
                    rec = {"status": "no-result", "title": asset["name"], "transport": "external"}
                else:
                    rec = verify_candidates(asset, ids, session=session)
                if rec["status"] == "resolved":
                    rec = attach_detail(rec, session=session)
                cache["resolved"][key] = rec
                stats[rec["status"]] = stats.get(rec["status"], 0) + 1
                mark = {"resolved": "ok", "unverified": "??", "no-result": "--"}[rec["status"]]
                pub = ((rec.get("legacy") or {}).get("publisher")
                       or (rec.get("detail") or {}).get("author") or "?")
                print(f"  [{n}/{len(supplied)}] {mark} {asset['name'][:52]}"
                      + (f"  -> {rec['id']} ({pub}, via {rec.get('verified_via','?')})"
                         if rec["status"] == "resolved" else ""))
                save_cache(cache_path, cache)
            print(f"\nimport-ids done: {stats}")
            elig = sum(1 for a in data["assets"] if not a.get("non_store"))
            print(f"verified: {stats['resolved']}/{elig} store-eligible "
                  f"({100*stats['resolved']/elig:.1f}%)" if elig else "verified: 0/0 store-eligible (0.0%)")
            _progress(args.progress_json, "import-ids", len(supplied), len(supplied),
                      counts={"resolved": stats["resolved"], "failed": stats["unverified"],
                              "skipped": stats["unknown-key"]})
            return 0

    if args.command == "enrich":
        cache = load_cache(cache_path)
        overrides = {}
        if os.path.exists(overrides_path):
            with open(overrides_path, encoding="utf-8") as fh:
                overrides = json.load(fh)
        eligible_count = sum(1 for asset in data["assets"] if not asset.get("non_store"))
        _progress(args.progress_json, "enrich", 0, eligible_count,
                  counts={"inventory_resolved": 0,
                          "inventory_unresolved": eligible_count,
                          "skipped": len(data["assets"]) - eligible_count})
        data = merge(data, cache, overrides)
        from index_assets import write_atomic
        write_atomic(assets_json, json.dumps(data, indent=1, sort_keys=True))
        remaining = prune_pending_queue(
            os.path.join(index_dir, "pending-enrichment.json"),
            {"resolved": cache.get("resolved", {})})
        e = data["enrichment"]
        _progress(args.progress_json, "enrich", e["resolved"], e["store_eligible"],
                  counts={"inventory_resolved": e["resolved"],
                          "inventory_unresolved": e["store_eligible"] - e["resolved"],
                          "skipped": e["non_store_skipped"], "remaining": len(remaining)})
        pct = 100 * e["resolved"] / e["store_eligible"] if e["store_eligible"] else 0
        print(f"enrich: {e['resolved']}/{e['store_eligible']} store-eligible id-verified "
              f"({pct:.1f}%), {e['non_store_skipped']} non-store skipped")
        if remaining:
            print(f"  {len(remaining)} asset(s) still awaiting enrichment")
        for w in e["override_warnings"]:
            print(f"  warn: {w}")
        return 0

    with _cache_write_lock(index_dir, args.command):
        cache = load_cache(cache_path)
        eligible = [a for a in data["assets"] if not a.get("non_store")]
        if args.pending:
            pending_keys = load_pending_keys(index_dir, args.command)
            eligible = [a for a in eligible if a["asset_key"] in pending_keys]
            if not pending_keys:
                print("  (pending queue is empty — attempting nothing)")

        def already_done(asset):
            rec = cache["resolved"].get(asset["asset_key"])
            return bool(rec) and rec.get("status") == "resolved"

        todo = [a for a in eligible if not (args.resume and already_done(a))]
        if args.command == "spike":
            random.seed(20260730)
            todo = random.sample(todo, min(args.limit, len(todo)))

        pacer = Pacer(base=args.base_delay)
        session = requests.Session()
        pool = SearchPool(session=session)
        stats = {"resolved": 0, "unverified": 0, "no-result": 0,
                 "failed": 0, "blocked": 0,
                 "skipped": sum(1 for a in data["assets"] if a.get("non_store"))}
        all_blocked_streak = 0
        total = len(todo)
        _progress(args.progress_json, args.command, 0, total,
                  counts={"resolved": 0, "failed": 0, "blocked": 0,
                          "skipped": stats["skipped"]})
        print(f"{args.command}: {len(todo)} assets to attempt "
              f"({len(eligible)} store-eligible, "
              f"{sum(1 for a in data['assets'] if a.get('non_store'))} non-store skipped)")
        if args.pending:
            print("  scope: pending queue only")
        print(f"transport pool: {', '.join(n for n, _, _ in pool.transports)}")

        aborted = False
        processed = 0
        for i, asset in enumerate(todo, 1):
            key = asset["asset_key"]
            _progress(args.progress_json, args.command, processed, total, key,
                      resolved=stats["resolved"], failed=stats["failed"],
                      blocked=stats["blocked"], skipped=stats["skipped"])
            try:
                rec = resolve_asset(asset, pool, session=session)
                all_blocked_streak = 0
                pacer.ok()
            except AllTransportsBlocked as exc:
                all_blocked_streak += 1
                stats["blocked"] += 1
                processed += 1
                wait = max(30.0, pool.cooldown_remaining() + 5)
                print(f"  [{i}/{len(todo)}] all transports cooling ({exc}) — "
                      f"waiting {wait:.0f}s. Nothing cached; --resume will retry.")
                _progress(args.progress_json, args.command, processed, total, key,
                          resolved=stats["resolved"], failed=stats["failed"],
                          blocked=stats["blocked"], skipped=stats["skipped"])
                if all_blocked_streak >= 6:
                    print("  aborting: pool exhausted 6 times in a row. Cache is intact; "
                          "re-run with --resume later.")
                    aborted = True
                    break
                time.sleep(wait)
                continue
            except Blocked as exc:
                stats["blocked"] += 1
                processed += 1
                delay = pacer.blocked()
                print(f"  [{i}/{len(todo)}] legacy API blocked ({exc}) — backing off "
                      f"{delay:.0f}s. Nothing cached; --resume will retry.")
                _progress(args.progress_json, args.command, processed, total, key,
                          resolved=stats["resolved"], failed=stats["failed"],
                          blocked=stats["blocked"], skipped=stats["skipped"])
                time.sleep(delay)
                continue
            except requests.RequestException as exc:
                stats["failed"] += 1
                processed += 1
                print(f"  [{i}/{len(todo)}] network error: {exc}")
                _progress(args.progress_json, args.command, processed, total, key,
                          resolved=stats["resolved"], failed=stats["failed"],
                          blocked=stats["blocked"], skipped=stats["skipped"])
                continue

            if rec["status"] == "resolved":
                try:
                    detail = fetch_detail(rec["id"], session=session,
                                          url_hint=f"https://{STORE_HOST}/packages/slug/{rec['id']}")
                except Blocked as exc:
                    stats["blocked"] += 1
                    processed += 1
                    print(f"  [{i}/{len(todo)}] detail fetch blocked ({exc}) — not cached; --resume will retry.")
                    _progress(args.progress_json, args.command, processed, total, key,
                              resolved=stats["resolved"], failed=stats["failed"],
                              blocked=stats["blocked"], skipped=stats["skipped"])
                    continue
                except requests.RequestException as exc:
                    stats["failed"] += 1
                    processed += 1
                    print(f"  [{i}/{len(todo)}] detail fetch failed ({exc}) — not cached; --resume will retry.")
                    _progress(args.progress_json, args.command, processed, total, key,
                              resolved=stats["resolved"], failed=stats["failed"],
                              blocked=stats["blocked"], skipped=stats["skipped"])
                    continue
                if detail and not detail.get("_rejected"):
                    rec["detail"] = detail
                elif detail and detail.get("_rejected"):
                    rec["status"] = "unverified"
                    rec["detail_rejected"] = detail["_rejected"]

            cache["resolved"][key] = rec
            stats[rec["status"]] = stats.get(rec["status"], 0) + 1
            processed += 1
            mark = {"resolved": "ok", "unverified": "??", "no-result": "--"}[rec["status"]]
            via = rec.get("transport", "?")
            pub = ((rec.get("legacy") or {}).get("publisher")
                   or (rec.get("detail") or {}).get("author") or "?")
            print(f"  [{i}/{len(todo)}] {mark} [{via}] {asset['name'][:52]}"
                  + (f"  -> {rec['id']} ({pub})" if rec["status"] == "resolved" else ""))
            save_cache(cache_path, cache)
            _progress(args.progress_json, args.command, processed, total, key,
                      resolved=stats["resolved"], failed=stats["failed"],
                      blocked=stats["blocked"], skipped=stats["skipped"])
            pacer.wait()
        attempted = processed
        print(f"\n{args.command} done: {stats} of {attempted} attempted")
        print("transport health:")
        for name, st in pool.summary().items():
            print(f"  {name:10} hits={st['hits']:<4} empty={st['empty']:<4} blocks={st['blocks']}")
        if attempted:
            rate = 100 * stats["resolved"] / attempted
            print(f"verified rate: {rate:.1f}%")
            if args.command == "spike":
                gate = "PROCEED" if rate >= 85 else ("PROCEED WITH CAUTION" if rate >= 70 else "STOP — re-open the transport question")
                print(f"gate (>=85 proceed / 70-85 caution / <70 stop): {gate}")
        return 3 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
