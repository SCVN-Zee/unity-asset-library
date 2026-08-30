#!/usr/bin/env python3
"""
Offline index for an archived Unity Asset Store library.

Hard constraint: 845 of 852 .unitypackage files in this tree are OneDrive dataless
placeholders. Opening one hydrates it; opening all would pull ~294 GB. This module
therefore uses os.walk + os.stat ONLY and never opens an archive. The test suite
enforces that by patching every read entry point to raise.

Second constraint: 'tools' and 'Tools' resolve to the same inode on this
case-insensitive volume, and Tools/ holds 191 archives. Directory exclusion is by
resolved absolute path, never by name.

The vault root comes from ../config.json ("vault_root"); --root overrides it for
one run and --state relocates the state directory (tests pass both to sandbox).
State lives in this repo's state/, never inside the vault; index.html and
assets.csv are emitted into the vault root so the gallery travels with the vault.

Usage:
    python3 index_assets.py scan          # write state/assets.json
    python3 index_assets.py emit          # write index.html + assets.csv + review-queue.md
    python3 index_assets.py scan emit
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

ARCHIVE_EXTS = (".unitypackage", ".zip")

# Excluded by resolved path, never by name. The tool lives outside the vault now,
# so only its in-vault triage folders remain excluded.
EXCLUDED_DIR_NAMES = ("_Quarantine", "_Unresolved")

# Integrity thresholds as byte literals. Never "100 KB" — the decimal/binary
# ambiguity already produced wrong counts in this project's history.
# Measured on this library: <10_000 B = 3 files, <100_000 B = 7, <1_000_000 B = 62.
# 1_000_000 was rejected as a threshold: 59 of those 62 are legitimately small editor
# tools and FMOD shims, and burying 3 real broken downloads under 59 false flags makes
# the signal useless.
BROKEN_MAX_BYTES = 10_000
SUSPICIOUS_MAX_BYTES = 100_000

# Parenthetical classification. Roles differ and no single strip-or-keep rule serves them.
EDITOR_PAREN_RE = re.compile(r"^unity\s*[\d.]+f?\d*$", re.I)
VERSION_PAREN_RE = re.compile(r"^v?\s*(\d+(?:\.\d+)+[A-Za-z0-9\-]*)$", re.I)
PIPELINE_PAREN_RE = re.compile(r"^(URP|HDRP|Built-?in)\s+version$", re.I)
DISCRIMINATORS = {"psd", "source", "sourcefiles", "pro", "beta", "preview"}
ANNOTATIONS = {"bug"}  # user's own note, not part of the product name
FLAG_PARENS = {"repack"}

# Pipeline variant only in these narrow forms. A bare 'HDRP' inside a title is part of
# the product name (Beautify HDRP, Volumetric Lights 2 HDRP are separate store SKUs).
PIPELINE_SUFFIX_RE = re.compile(r"[_\s]+(URP|HDRP|Builtin|Built-in)[_\s]+\d{4}\.\d+[.\d]*f?\d*\.*$", re.I)
EDITOR_UNDERSCORE_RE = re.compile(r"_Unity_(\d{4})_(\d+)(?:_(\d+))?", re.I)
EDITOR_DOTTED_RE = re.compile(r"[_\s](\d{4}\.\d+(?:\.\d+)?f?\d*)\.*$")
DATE_PAREN_RE = re.compile(
    r"\((\d{1,2}\s+[A-Za-z]{3}\s+\d{4}|[A-Za-z]{3}\s+\d{1,2}\s+\d{4})\)", re.I
)
UNDERSCORE_VERSION_RE = re.compile(r"_v(\d+(?:_\d+)+)\b", re.I)
V_PREFIX_RE = re.compile(r"(?:^|[\s_])v{1,2}\.?\s*(\d+(?:\.\d+)+[A-Za-z0-9\-]*)", re.I)
TRAILING_DOTTED_RE = re.compile(r"(?:^|\s)(\d+(?:\.\d+)+[A-Za-z0-9\-]*)\s*$")
# Tokens that may follow a version and are not part of the title.
POST_VERSION_NOISE = {"unity"}

PRERELEASE_WORDS = {"pre", "preview", "beta", "alpha", "rc", "ea", "dev", "snapshot"}

# Archives with no Asset Store origin. Explicit, because a wrong non_store flag
# silently skips resolution for a real store asset. Listed in review-queue.md so
# they can be corrected via overrides.
NON_STORE_PATTERNS = (
    re.compile(r"^Kenney Game Assets", re.I),
    re.compile(r"^BattleSimulator$", re.I),
    re.compile(r"^SrRubfish", re.I),
    re.compile(r"_Source_?Files", re.I),
    re.compile(r"^Tank Assets$", re.I),
)
NON_STORE_DIR_PARTS = ("WM_Animset",)

TAG_KEYWORDS = {
    "shader": ("shader", "shading", "toony", "beautify"),
    "terrain": ("terrain", "microsplat", "digger", "landscape"),
    "character": ("character", "hero", "npc", "creature", "monster", "zombie", "humanoid"),
    "animation": ("animation", "animset", "anims", "mocap", "motion", "animancer"),
    "vfx": ("vfx", "particle", "effect", "explosion", "magic circle"),
    "sfx": ("sfx", "sound", "audio", "foley", "swish"),
    "music": ("music", "bgm", "orchestral", "soundtrack", "stinger"),
    "ui": ("gui", "ui ", "interface", "hud", "icon"),
    "ai-pathfinding": ("pathfinding", "behavior", "behaviour", "navmesh", " ai "),
    "networking": ("multiplayer", "network", "netcode", "dissonance", "voice chat"),
    "editor-tool": ("editor", "inspector", "toolkit", "tool", "utility", "optimiz"),
    "template": ("template", "kit", "starter", "engine", "system", "framework"),
    "low-poly": ("low poly", "lowpoly", "polygon", "cube"),
    "stylized": ("stylized", "stylised", "cute", "toon", "cartoon"),
    "environment": ("environment", "city", "village", "forest", "nature", "dungeon", "interior"),
    "prop": ("prop", "weapon", "furniture", "chest", "armor"),
    "vehicle": ("vehicle", "car", "drift", "tank", "aircraft"),
    "skybox": ("skybox", "sky", "allsky", "cloud"),
    "lighting": ("light", "lighting", "lightmap", "bakery", "global illumination"),
    "horror": ("horror", "creepy", "psychiatric", "animatronic"),
    "medieval": ("medieval", "fantasy", "knight", "castle", "rpg"),
    "sci-fi": ("sci-fi", "scifi", "cyberpunk", "solarpunk", "space", "mecha"),
    "urp": ("urp",),
    "hdrp": ("hdrp",),
}


# ---------------------------------------------------------------------------
# scan — os.walk + os.stat only. Never opens a file.
# ---------------------------------------------------------------------------

def scan(root):
    """Return one record per archive. Reads metadata only; never opens a file."""
    root = os.path.abspath(root)
    excluded = set()
    for name in EXCLUDED_DIR_NAMES:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            excluded.add(os.path.realpath(candidate))

    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune by resolved path, not by name. On a case-insensitive volume a
        # name-based skip of "tools" would also drop "Tools" and its 191 archives.
        dirnames[:] = [
            d for d in dirnames
            if os.path.realpath(os.path.join(dirpath, d)) not in excluded
        ]
        for filename in filenames:
            if not filename.endswith(ARCHIVE_EXTS):
                continue
            full = os.path.join(dirpath, filename)
            st = os.stat(full)  # metadata only — no read, no hydration
            records.append({
                "rel_path": os.path.relpath(full, root),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "st_blocks": st.st_blocks,
                "format": "zip" if filename.endswith(".zip") else "unitypackage",
            })
    records.sort(key=lambda r: r["rel_path"])
    return records


# ---------------------------------------------------------------------------
# parse — filename + parent folder. Signature takes rel_path, not basename:
# 15 files carry their version only in the parent folder name.
# ---------------------------------------------------------------------------

def _classify_parens(stem):
    """Pull parentheticals out of stem, classified by role."""
    out = {
        "version": None, "editor": None, "pipeline": None,
        "discriminators": [], "flags": [], "kept": [],
    }
    leading = True
    pieces = []
    pos = 0
    for m in re.finditer(r"\(([^)]*)\)", stem):
        pieces.append((m.start(), m.end(), m.group(1).strip()))
    if not pieces:
        return out, stem

    remove = []
    for start, end, body in pieces:
        low = body.lower()
        is_leading = start <= 1
        if EDITOR_PAREN_RE.match(low):
            out["editor"] = body.split(None, 1)[-1] if " " in body else body
            remove.append((start, end))
        elif VERSION_PAREN_RE.match(low.replace("v ", "v")):
            mm = VERSION_PAREN_RE.match(re.sub(r"^v\s*", "v", low))
            out["version"] = mm.group(1)
            remove.append((start, end))
        elif PIPELINE_PAREN_RE.match(low):
            out["pipeline"] = PIPELINE_PAREN_RE.match(low).group(1).upper().replace("BUILT-IN", "BUILTIN")
            remove.append((start, end))
        elif low in DISCRIMINATORS:
            out["discriminators"].append(body.upper() if len(body) <= 3 else body.title())
            remove.append((start, end))
        elif low in FLAG_PARENS:
            out["flags"].append(low)
            remove.append((start, end))
        elif low in ANNOTATIONS and is_leading:
            # A leading '(Bug)' is the user's own note. A leading '(SE)' is part of
            # the store title. Identical syntax, opposite handling.
            remove.append((start, end))
        else:
            out["kept"].append(body)

    for start, end in sorted(remove, reverse=True):
        stem = stem[:start] + stem[end:]
    return out, stem


def _parse_version_token(stem):
    """Apply the version disambiguation rule. Returns (version, stem_without_version)."""
    # Underscore form first: POLYGON_..._v1_9_2
    m = UNDERSCORE_VERSION_RE.search(stem)
    if m:
        return m.group(1).replace("_", "."), stem[:m.start()] + stem[m.end():]

    # A v-prefixed dotted token wins outright.
    matches = list(V_PREFIX_RE.finditer(stem))
    if matches:
        m = matches[-1]
        rest = stem[m.end():].strip()
        # A token following the version is noise, not title (e.g. "... v2.5.3 Unity").
        if rest and rest.lower().strip(" -") in POST_VERSION_NOISE:
            rest = ""
        return m.group(1), (stem[:m.start()] + " " + rest).strip()

    # Otherwise the last dotted token with >=2 components, and only in trailing
    # position — never a title-position number followed by more title words.
    m = TRAILING_DOTTED_RE.search(stem)
    if m:
        return m.group(1), stem[:m.start()].strip()
    return None, stem


def parse(rel_path):
    """Parse one archive path into structured fields."""
    parent = os.path.basename(os.path.dirname(rel_path))
    filename = os.path.basename(rel_path)
    fmt = "zip" if filename.endswith(".zip") else "unitypackage"
    stem = re.sub(r"\.(unitypackage|zip)$", "", filename)
    stem = re.sub(r"\.+$", "", stem)  # tolerate the '..unitypackage' double-dot typo

    release_date = None
    dm = DATE_PAREN_RE.search(stem)
    if dm:
        raw = dm.group(1)
        for fmt_try in ("%d %b %Y", "%b %d %Y"):
            try:
                release_date = datetime.strptime(raw, fmt_try).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        stem = (stem[:dm.start()] + stem[dm.end():]).strip()

    pipeline = None
    pm = PIPELINE_SUFFIX_RE.search(stem)
    if pm:
        pipeline = pm.group(1).upper().replace("BUILT-IN", "BUILTIN")
        stem = stem[:pm.start()].strip()

    editor = None
    em = EDITOR_UNDERSCORE_RE.search(stem)
    if em:
        editor = ".".join(g for g in em.groups() if g)
        stem = (stem[:em.start()] + stem[em.end():]).strip()
    else:
        em2 = EDITOR_DOTTED_RE.search(stem)
        if em2 and not V_PREFIX_RE.search(stem):
            editor = em2.group(1)
            stem = stem[:em2.start()].strip()

    parens, stem = _classify_parens(stem)
    if parens["pipeline"] and not pipeline:
        pipeline = parens["pipeline"]
    if parens["editor"] and not editor:
        editor = parens["editor"]

    version = parens["version"]
    if version is None:
        version, stem = _parse_version_token(stem)

    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s{2,}", " ", stem).strip(" -")
    title = stem

    # Folder hint: the parent folder supplies a version the filename lacks.
    folder_hint = None
    if version is None:
        fm = re.search(r"\bv(\d+(?:\.\d+)+)\s*$", parent)
        if fm:
            version = fm.group(1)
            folder_hint = parent

    prerelease = False
    if version:
        suf = re.search(r"[-]?([A-Za-z]+)$", version)
        if suf:
            word = suf.group(1).lower()
            prerelease = word in PRERELEASE_WORDS or (len(word) >= 2 and "-" in version)

    non_store = any(p.search(title) or p.search(re.sub(r"\.(unitypackage|zip)$", "", filename))
                    for p in NON_STORE_PATTERNS) or \
                any(part in rel_path for part in NON_STORE_DIR_PARTS)

    return {
        "rel_path": rel_path,
        "title": title,
        "version": version,
        "prerelease": prerelease,
        "release_date": release_date,
        "pipeline": pipeline,
        "editor_version": editor,
        "discriminators": sorted(parens["discriminators"]),
        "flags": sorted(parens["flags"]),
        "folder_hint": folder_hint,
        "format": fmt,
        "non_store": non_store,
    }


def normalize_title(title):
    t = title.casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def base_key(parsed):
    """Identity WITHOUT pipeline. Two pipeline builds of one product share a base key
    but differ in asset_key, which is what lets group() call them variants rather than
    unrelated files."""
    parts = [normalize_title(parsed["title"]).replace(" ", "-")]
    parts += [d.lower() for d in parsed["discriminators"]]
    return "/".join(p for p in parts if p) or "unnamed"


def asset_key(parsed):
    """Identity: normalized title + discriminators + pipeline.

    Discriminators stay in the key so a (PSD)/(Source)/(PRO) archive can never
    share an identity with its plain sibling, making it structurally impossible
    to flag an editable-source pack as redundant.
    """
    parts = [normalize_title(parsed["title"]).replace(" ", "-")]
    for d in parsed["discriminators"]:
        parts.append(d.lower())
    if parsed["pipeline"]:
        parts.append(parsed["pipeline"].lower())
    return "/".join(p for p in parts if p) or "unnamed"


# ---------------------------------------------------------------------------
# version ordering — semver only. mtime is scrambled by OneDrive and is never used.
# ---------------------------------------------------------------------------

def version_key(version, prerelease=None):
    """Sort key. Stable releases always outrank pre-releases, even numerically lower."""
    if not version:
        return (0, (), "")
    nums = tuple(int(x) for x in re.findall(r"\d+", version))
    suf = re.search(r"[-]?([A-Za-z]+)\d*$", version)
    word = suf.group(1).lower() if suf else ""
    if prerelease is None:
        prerelease = word in PRERELEASE_WORDS or (len(word) >= 2 and "-" in version)
    return (0 if prerelease else 1, nums, word)


def pick_latest(items, version_of=lambda x: x[0], prerelease_of=None):
    """Highest stable version. Returns None when nothing is parseable."""
    usable = [i for i in items if version_of(i)]
    if not usable:
        return None
    return max(usable, key=lambda i: version_key(
        version_of(i), prerelease_of(i) if prerelease_of else None))


# ---------------------------------------------------------------------------
# group — size-collision generates candidates, identity discriminates.
# Verdicts describe; they never prescribe an action. Nothing is ever moved.
# ---------------------------------------------------------------------------

def group(entries):
    """Group size-identical archives and classify each cluster."""
    by_size = {}
    for e in entries:
        by_size.setdefault(e.get("size"), []).append(e)

    groups = []
    for size, members in sorted(by_size.items(), key=lambda kv: -(kv[0] or 0)):
        if len(members) < 2:
            continue
        bases = {base_key(m) for m in members}
        pipelines = {m.get("pipeline") for m in members}
        dirs = {os.path.dirname(m["rel_path"]) for m in members}
        versions = {m.get("version") for m in members}

        if len(bases) > 1:
            # Unrelated products that happen to share a byte count, e.g. the two
            # distinct 4096-byte broken downloads. Also covers (PSD)/(Source)/(PRO)
            # siblings, whose discriminators are part of the base key.
            verdict, reason = "distinct", "size-coincidence"
        elif len(pipelines) > 1:
            # Same product, different render pipeline. Related but never redundant.
            verdict, reason = "variant", "pipeline-variants"
        elif len(dirs) == 1:
            # No path heuristic can pick a winner. Decision left to the user.
            verdict, reason = "duplicate", "same-size-same-folder"
        elif len(versions) == 1:
            verdict, reason = "duplicate", "same-size-same-version-different-folder"
        else:
            # Two DIFFERENT releases that happen to share a byte count (AllSky
            # 5.1.0/5.2.0). Byte-identical size across differing versions is anomalous
            # — one filename is probably mislabelled. Calling this "duplicate" and
            # totalling it as "redundant GB" would invite deleting a release the user
            # deliberately kept, so it is surfaced for review and excluded from the
            # redundancy total.
            verdict, reason = "review", "same-size-different-version"

        groups.append({
            "size": size,
            "verdict": verdict,
            "reason": reason,
            "identity_evidence": "size+name, unhashed",
            "members": [m["rel_path"] for m in members],
            # Only true duplicates count toward reclaimable bytes.
            "redundant_bytes": ((size or 0) * (len(members) - 1)
                               if verdict == "duplicate" else 0),
        })
    return groups


def integrity_tier(size):
    if size < BROKEN_MAX_BYTES:
        return "broken"
    if size < SUSPICIOUS_MAX_BYTES:
        return "suspicious"
    return None


def derive_tags(parsed, category):
    hay = (parsed["title"] + " " + (category or "")).casefold()
    tags = {tag for tag, words in TAG_KEYWORDS.items() if any(w in hay for w in words)}
    if parsed["pipeline"]:
        tags.add(parsed["pipeline"].lower())
    return sorted(tags)


FOLDER_CATEGORY = {
    "2D": "2D", "3D": "3D", "Audio": "Audio",
    "Templates": "Templates", "Tools": "Tools", "VFX": "VFX",
}


def build(scanned):
    """Assemble one entry per asset with versions[] newest-first."""
    parsed_all = []
    for rec in scanned:
        p = parse(rec["rel_path"])
        p.update(size=rec["size"], mtime=rec["mtime"], st_blocks=rec["st_blocks"])
        parsed_all.append(p)

    groups = group(parsed_all)
    dup_reason = {}
    for g in groups:
        for member in g["members"]:
            dup_reason[member] = {"verdict": g["verdict"], "reason": g["reason"],
                                  "group_size": g["size"]}

    by_key = {}
    for p in parsed_all:
        by_key.setdefault(asset_key(p), []).append(p)

    assets = []
    for key, members in sorted(by_key.items()):
        top = members[0]
        segments = top["rel_path"].split(os.sep)
        cat_l1 = FOLDER_CATEGORY.get(segments[0]) if len(segments) > 1 else None
        category = {"path": cat_l1, "levels": [cat_l1] if cat_l1 else [], "source": "folder"}

        versions = []
        for m in members:
            versions.append({
                "version": m["version"],
                "prerelease": m["prerelease"],
                "file": m["rel_path"],
                "size_bytes": m["size"],
                "release_date": m["release_date"],
                "editor_version": m["editor_version"],
                "pipeline": m["pipeline"],
                "folder_hint": m["folder_hint"],
                "integrity": integrity_tier(m["size"]),
                "duplicate": dup_reason.get(m["rel_path"]),
                "latest": False,
            })
        versions.sort(key=lambda v: version_key(v["version"], v["prerelease"]), reverse=True)
        latest = pick_latest(versions, version_of=lambda v: v["version"],
                             prerelease_of=lambda v: v["prerelease"])
        if latest:
            latest["latest"] = True

        assets.append({
            "asset_key": key,
            "name": top["title"],
            # The filename-derived name, recorded up front and NEVER overwritten by
            # enrichment. `name` may be replaced by the store's own name; local_name is
            # the fixed point that makes that swap idempotent.
            "local_name": top["title"],
            "author": None,
            "category": category,
            "tags": derive_tags(top, cat_l1),
            "tag_source": "keyword",
            "thumbnail": None,
            "store": None,
            "store_id": None,
            "non_store": top["non_store"],
            "discriminators": top["discriminators"],
            "flags": top["flags"],
            "versions": versions,
            "resolution": {"method": None, "id_verified": False},
        })

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_count": len(scanned),
        "asset_count": len(assets),
        "duplicate_groups": groups,
        "assets": assets,
    }


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

def write_atomic(path, text):
    """tmp -> fsync -> os.replace, so a crash can never truncate the file."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def safe_json(obj):
    """JSON that cannot break out of a <script> block in any tokenizer form.

    Escaping the literal '</script>' is insufficient: the HTML5 script-data end-tag
    match is case-insensitive and also terminates on '</script' followed by
    whitespace or '/'. Escaping '<' itself closes all of those forms at once.
    """
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e")
             .replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def emit_csv(data, path):
    rows = []
    for a in data["assets"]:
        for v in a["versions"]:
            rows.append({
                "name": a["name"],
                "local_name": a.get("local_name") or "",
                "author": a["author"] or "",
                "category": (a["category"]["path"] or ""),
                "tags": ";".join(a["tags"]),
                "version": v["version"] or "",
                "release_date": v["release_date"] or "",
                "size_bytes": v["size_bytes"],
                "latest": v["latest"],
                "integrity": v["integrity"] or "",
                "duplicate": (v["duplicate"] or {}).get("reason", ""),
                "non_store": a["non_store"],
                "path": v["file"],
                "store": a["store"] or "",
            })
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["name"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def emit_review_queue(data, path):
    L = ["# Review Queue", "", f"Generated {data['generated']}", ""]

    dups = [g for g in data["duplicate_groups"] if g["verdict"] == "duplicate"]
    others = [g for g in data["duplicate_groups"] if g["verdict"] != "duplicate"]
    total = sum(g["redundant_bytes"] for g in dups)
    L += ["## Duplicates", "",
          f"{len(dups)} groups, {total/1e9:.2f} GB redundant. "
          "Identity is `size+name, unhashed` — byte equality was never verified, "
          "since hashing would hydrate the placeholders. **Nothing is moved or deleted "
          "by this tool.**", ""]
    for g in dups:
        L.append(f"- **{g['size']/1e6:,.1f} MB** x{len(g['members'])} — `{g['reason']}`"
                 f" — redundant {g['redundant_bytes']/1e9:.2f} GB")
        for m in g["members"]:
            L.append(f"    - `{m}`")
        if g["reason"] == "same-size-same-folder":
            L.append("    - _Same folder: no path heuristic can pick a winner. Your call._")
    L.append("")

    if others:
        L += ["## Size collisions that are NOT duplicates", ""]
        for g in others:
            L.append(f"- {g['size']:,} B x{len(g['members'])} — `{g['reason']}`")
            for m in g["members"]:
                L.append(f"    - `{m}`")
        L.append("")

    tiers = {"broken": [], "suspicious": []}
    for a in data["assets"]:
        for v in a["versions"]:
            if v["integrity"]:
                tiers[v["integrity"]].append((v["size_bytes"], v["file"]))
    L += ["## Integrity", "",
          f"Thresholds are byte literals: `broken < {BROKEN_MAX_BYTES:,} B`, "
          f"`suspicious < {SUSPICIOUS_MAX_BYTES:,} B`. Stated in bytes on purpose — "
          "the decimal/binary reading of \"100 KB\" already produced two wrong counts here.", ""]
    for tier in ("broken", "suspicious"):
        L.append(f"### {tier} ({len(tiers[tier])})")
        for size, f in sorted(tiers[tier]):
            L.append(f"- {size:,} B — `{f}`")
        L.append("")

    nover = [(a["name"], v["file"]) for a in data["assets"] for v in a["versions"]
             if not v["version"]]
    L += [f"## Unparseable version ({len(nover)})", ""]
    L += [f"- `{f}`" for _, f in sorted(nover)] + [""]

    hints = [(v["folder_hint"], v["file"]) for a in data["assets"] for v in a["versions"]
             if v["folder_hint"]]
    L += [f"## Version recovered from parent folder ({len(hints)})", "",
          "These filenames carry no version; the parent folder is the only record.", ""]
    L += [f"- `{f}` <- folder `{h}`" for h, f in sorted(hints)] + [""]

    # Resolution status, with paste-ready override snippets. The whole point of keying
    # overrides on asset_key rather than store id is that the items needing a hand-fix
    # are precisely the ones that have no id yet.
    eligible = [a for a in data["assets"] if not a["non_store"]]
    verified = [a for a in eligible if (a.get("resolution") or {}).get("id_verified")]
    failed = [a for a in eligible
              if (a.get("resolution") or {}).get("method")
              and not a["resolution"].get("id_verified")]
    unattempted = [a for a in eligible
                   if (a.get("resolution") or {}).get("reason") == "not-attempted"]

    L += ["## Store resolution", "",
          f"- id-verified: **{len(verified)}** / {len(eligible)} store-eligible",
          f"- attempted but unresolved: **{len(failed)}**",
          f"- not yet searched: **{len(unattempted)}** "
          f"(no lookup performed — a later run will attempt these; do NOT hand-write "
          f"overrides for them)", ""]

    if failed:
        L += [f"### Needs a manual override ({len(failed)})", "",
              "Each of these was searched and the candidate id failed verification, or no "
              "id was found. Common causes: the publisher renamed the package upstream, "
              "the listing was delisted, or two SKUs share a name. Paste the block below "
              "into `.index/overrides.json` and fill in what you know, then re-run "
              "`resolve_store.py enrich`.", "",
              "```json", "{"]
        for i, a in enumerate(sorted(failed, key=lambda x: x["name"])):
            reason = a["resolution"].get("reason", "unresolved")
            comma = "," if i < len(failed) - 1 else ""
            L.append(f'  "{a["asset_key"]}": {{"store_id": null, "author": null, '
                     f'"store": null}}{comma}   // {a["name"][:48]} [{reason}]')
        L += ["}", "```", ""]

    ns = [a for a in data["assets"] if a["non_store"]]
    L += [f"## Non-store archives ({len(ns)})", "",
          "Flagged `non_store: true`. Phase 2 skips them entirely: no lookups spent, "
          "and they are excluded from the unresolved count and the >=85% denominator. "
          "Correct any misflag via `.index/overrides.json`.", ""]
    for a in ns:
        for v in a["versions"]:
            L.append(f"- `{v['file']}`")
    L.append("")

    write_atomic(path, "\n".join(L))
    return len(dups)


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https://assetstorev1-prd-cdn.unity3d.com data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>Unity Asset Index</title>
<style>
:root{
--bg:#fbfbfc;--surface:#fff;--sunk:#f4f4f6;
--ink:#18181b;--ink-2:#52525b;--ink-3:#6b6b75;
--line:#e4e4e7;--accent:#2563eb;--warn:#b45309;
--r:6px;--r-sm:4px}
@media(prefers-color-scheme:dark){:root{
--bg:#0c0c0e;--surface:#151518;--sunk:#0a0a0b;
--ink:#fafafa;--ink-2:#a1a1aa;--ink-3:#80808a;
--line:#27272a;--accent:#3b82f6;--warn:#d97706}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 ui-sans-serif,-apple-system,"SF Pro Text",system-ui,sans-serif}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
h1{font:600 15px/1 inherit;margin:0;letter-spacing:.01em}
#q{flex:1;min-width:220px;padding:6px 9px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--surface);color:var(--ink);font:inherit}
.count{color:var(--ink-2);font-variant-numeric:tabular-nums;white-space:nowrap}
.stamp{color:var(--ink-3);font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums}
.stamp.stale{color:var(--warn);font-weight:600}
button,select{font:inherit;font-size:12px;padding:5px 9px;border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:var(--r-sm);cursor:pointer}
button.on{background:var(--accent);border-color:var(--accent);color:var(--bg)}
main{display:flex;gap:14px;align-items:flex-start;padding:14px}
#facets{width:220px;flex:0 0 220px;position:sticky;top:56px;max-height:calc(100vh - 72px);overflow-y:auto}
#facetbox>summary{display:none}
aside section{margin-bottom:16px}
aside h2{font:600 11px/1 inherit;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3);margin:0 0 6px}
.facet{display:block;width:100%;text-align:left;border:0;background:none;padding:2px 5px;border-radius:var(--r-sm);color:var(--ink);font-size:12px;cursor:pointer}
.facet:hover{background:var(--sunk)}
.facet.on{background:var(--accent);color:var(--bg)}
.facet .n{float:right;color:var(--ink-3);font-variant-numeric:tabular-nums}
.facet.on .n{color:var(--bg)}
.facet.lvl1{padding-left:15px;font-size:12px}
.facet.lvl2{padding-left:27px;font-size:12px}
.facet.lvl3{padding-left:39px;font-size:11px}
.facet.lvl4{padding-left:51px;font-size:11px}
.hint{color:var(--ink-3);font-size:11px;font-style:italic;padding:4px}
.crumb{font-size:12px;color:var(--ink-2);display:flex;flex-wrap:wrap;gap:3px;align-items:baseline}
.crumb .sep{color:var(--ink-3)}
.pending{color:var(--ink-3);font-style:italic}
.local{font-size:11px;color:var(--ink-3);word-break:break-word}
td .pending{font-size:11px}
#out{flex:1;min-width:0}
#detail{display:none;width:320px;flex:0 0 320px;position:sticky;top:56px;max-height:calc(100vh - 72px);overflow-y:auto;background:var(--surface);border:1px solid var(--line);border-radius:var(--r);padding:10px}
#detail.on{display:block}
#detail .dhd{display:flex;gap:8px;align-items:flex-start;justify-content:space-between;margin:0 0 8px}
#detail .dnm{font:600 15px/1.3 inherit;text-transform:none;letter-spacing:0;color:var(--ink);margin:0}
#detail h2{margin:12px 0 6px}
#detail .ver{border-top:1px solid var(--line);padding:6px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.card{width:100%;text-align:left;font:inherit;color:var(--ink);cursor:pointer;border:1px solid var(--line);border-radius:var(--r);background:var(--surface);padding:8px;display:flex;flex-direction:column;gap:4px;overflow:hidden}
.card.pick{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
.card .th{aspect-ratio:16/10;background:var(--sunk);border:1px solid var(--line);border-radius:var(--r-sm);display:flex;align-items:center;justify-content:center;color:var(--ink-3);font-size:11px;overflow:hidden}
.card .th img{width:100%;height:100%;object-fit:cover}
.card .th .ph{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3);padding:0 6px;text-align:center}
.card .nm{font-weight:600;font-size:13px;line-height:1.3;word-break:break-word;text-wrap:balance;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .mt{color:var(--ink-2);font-size:12px;word-break:break-word;font-variant-numeric:tabular-nums}
.tags{display:flex;flex-wrap:wrap;gap:3px}
.tag{font-size:11px;padding:0 5px;border:1px solid var(--line);border-radius:var(--r-sm);color:var(--ink-3)}
.badge{font-size:11px;padding:0 5px;border-radius:var(--r-sm);background:var(--sunk);color:var(--ink-3);align-self:flex-start}
.badge.warn{color:var(--warn)}
.row{display:flex;gap:5px;flex-wrap:wrap;margin-top:auto;padding-top:4px}
.row button,.row a{font-size:11px;padding:2px 7px;border:1px solid var(--line);border-radius:var(--r-sm);color:var(--ink);text-decoration:none;background:var(--bg);cursor:pointer}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;font-size:12px;vertical-align:top}
th{color:var(--ink-3);font-weight:600;white-space:nowrap}
th.sortable{cursor:pointer;color:var(--ink-2)}
#out tbody tr{cursor:pointer}
#out tbody tr.cur{box-shadow:inset 2px 0 0 var(--ink-3)}
#out tbody tr.pick{background:var(--sunk);box-shadow:inset 2px 0 0 var(--accent)}
.wrap{overflow-x:auto}
.sentinel{height:1px}
@media(max-width:1100px){#detail.on{position:fixed;top:56px;right:0;bottom:0;width:340px;flex:none;max-height:none;z-index:6;border-radius:0;border-width:0 0 0 1px}}
@media(max-width:760px){main{flex-direction:column}
#facets{width:100%;flex:1;position:static;max-height:none}
#facetbox>summary{display:list-item;cursor:pointer;font:600 11px/1 inherit;text-transform:uppercase;letter-spacing:.07em;color:var(--ink-3);padding:4px 0}
#detail.on{width:100%;left:0}}
</style>
</head>
<body>
<header>
  <h1>Unity Asset Index</h1>
  <input id="q" type="search" placeholder="Search name, author, tag, path…" autocomplete="off">
  <button id="view">List view</button>
  <select id="sort"><option value="name">Name</option><option value="size">Size</option><option value="rating">Rating</option><option value="date">Date</option></select>
  <span class="count" id="count" aria-live="polite"></span>
  <span class="stamp" id="generated"></span>
</header>
<main>
  <aside id="facets"><details id="facetbox" open><summary>Filters</summary>
    <div id="facetlist"></div></details></aside>
  <div id="out"></div>
  <aside id="detail" tabindex="-1" aria-label="Asset detail"></aside>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
"use strict";
var DATA = JSON.parse(document.getElementById("data").textContent);
var assets = DATA.assets, grid = true, sortBy = "name";
var selected = null;
// Chunked append, not virtualization: at 835 items the ceiling is ~5,000 nodes,
// and scroll math plus height estimation would buy nothing for that.
var CHUNK = 100, view = [], cursor = 0, obs = null, gen = 0;
var query = "", rowIdx = -1, qt, origin = null;
// Only these columns map to a real sort key. The rest stay unbound and, per the
// CSS above, stop claiming to be clickable.
var SORTABLE = {"Name (store)":"name", "Size":"size"};
var sel = {category:new Set(), tag:new Set(), author:new Set(), flag:new Set()};

function el(t, cls, txt){var n=document.createElement(t); if(cls)n.className=cls;
  if(txt!==undefined&&txt!==null)n.textContent=String(txt); return n;}
function bytes(b){if(!b)return "";var u=["B","KB","MB","GB"],i=0,v=b;
  while(v>=1024&&i<u.length-1){v/=1024;i++;}return v.toFixed(i?1:0)+" "+u[i];}
function latest(a){for(var i=0;i<a.versions.length;i++){if(a.versions[i].latest)return a.versions[i];}
  return a.versions[0];}
function totalSize(a){return a.versions.reduce(function(s,v){return s+(v.size_bytes||0);},0);}
function flagsOf(a){var f=[];
  if(a.non_store)f.push("non-store");
  if(a.pending_enrichment)f.push("pending-enrichment");
  a.versions.forEach(function(v){
    if(v.integrity)f.push(v.integrity);
    if(v.duplicate&&v.duplicate.verdict==="duplicate")f.push("duplicate");
    if(v.duplicate&&v.duplicate.verdict==="variant")f.push("variant");});
  return f;}

function levels(a){
  var L=(a.category&&a.category.levels)||[];
  return L.length?L:(a.category&&a.category.path?a.category.path.split("/"):[]);}
function catPath(a){var L=levels(a); return L.length?L.join("/"):"";}

// Built once per asset, not once per matches() call: facetPool adds four extra passes
// over all 835 assets per render, and rebuilding this string five times was the cost.
function hay(a){
  if(a._hay===undefined)a._hay=(a.name+" "+(a.local_name||"")+" "+(a.author||"")+" "+
    a.tags.join(" ")+" "+catPath(a)+" "+(a.store||"")+" "+
    a.versions.map(function(v){return v.file;}).join(" ")).toLowerCase();
  return a._hay;}

// A selected facet is a path PREFIX, so picking "3D" also matches
// "3D/Environments/Fantasy". Store enrichment supplies levels 2 and 3; before it runs
// only level 1 exists, and the tree simply has no children to expand.
function inCategory(a, prefix){
  if(prefix==="(none)")return levels(a).length===0;
  var p=catPath(a);
  return p===prefix||p.indexOf(prefix+"/")===0;}

// `except` names one facet family to ignore. Only facetPool passes it: the result
// grid always applies every filter.
function matches(a, q, except){
  if(except!=="category"&&sel.category.size){
    var any=false;
    sel.category.forEach(function(p){if(inCategory(a,p))any=true;});
    if(!any)return false;}
  if(except!=="author"&&sel.author.size&&!sel.author.has(a.author||"(unknown)"))return false;
  if(except!=="tag"&&sel.tag.size){for(var t of sel.tag){if(a.tags.indexOf(t)<0)return false;}}
  if(except!=="flag"&&sel.flag.size){var f=flagsOf(a);
    for(var g of sel.flag){if(f.indexOf(g)<0)return false;}}
  if(!q)return true;
  return q.split(/\\s+/).every(function(w){return hay(a).indexOf(w)>=0;});
}

// Every path prefix gets its own count, so a 3-level tree reports totals at each depth.
// Standard faceted refinement: when counting family F, apply every filter EXCEPT F's
// own selections. Counting a family under its own picks drops every sibling to zero.
function facetPool(family){
  return assets.filter(function(a){return matches(a,query,family);});}

function categoryTree(){
  var counts={}, depth={};
  facetPool("category").forEach(function(a){
    var L=levels(a);
    if(!L.length){counts["(none)"]=(counts["(none)"]||0)+1; depth["(none)"]=0; return;}
    for(var i=0;i<L.length;i++){
      var p=L.slice(0,i+1).join("/");
      counts[p]=(counts[p]||0)+1; depth[p]=i;}});
  return {counts:counts, depth:depth};}

// Zero arity on purpose. Two tests slice the template on this declaration's exact
// text, so adding a parameter makes the split raise IndexError and the older test
// errors instead of failing. Filtered inputs come from module scope via facetPool.
function facetCounts(){
  var c={tag:{},author:{},flag:{}};
  facetPool("author").forEach(function(a){
    var au=a.author||"(unknown)"; c.author[au]=(c.author[au]||0)+1;});
  facetPool("tag").forEach(function(a){
    a.tags.forEach(function(t){c.tag[t]=(c.tag[t]||0)+1;});});
  // A duplicate group is one asset with two flagged versions, so undeduped counting
  // reported 20 duplicates where there were 10.
  facetPool("flag").forEach(function(a){
    flagsOf(a).filter(function(x,i,s){return s.indexOf(x)===i;})
      .forEach(function(f){c.flag[f]=(c.flag[f]||0)+1;});});
  return c;}

function renderFacets(){
  var host=document.getElementById("facetlist");
  // The active facet button is about to be destroyed and rebuilt. Remember it by
  // label so focus can land on its replacement instead of falling to <body>.
  var live=document.activeElement, mark=null;
  if(live&&live.className&&live.className.indexOf("facet")===0)
    mark=live.getAttribute("title")||live.firstChild.textContent;
  host.textContent="";

  // Category: hierarchical, indented by depth, sorted as a path tree.
  var tree=categoryTree();
  var paths=Object.keys(tree.counts).sort();
  if(paths.length){
    var sec=el("section"); sec.appendChild(el("h2",null,"Category"));
    paths.forEach(function(p){
      var d=tree.depth[p];
      var label=p==="(none)"?"(uncategorized)":p.split("/").pop();
      var b=el("button","facet lvl"+d+(sel.category.has(p)?" on":""));
      b.appendChild(el("span",null,label));
      b.appendChild(el("span","n",tree.counts[p]));
      b.setAttribute("title",p);
      b.addEventListener("click",function(){
        sel.category.has(p)?sel.category.delete(p):sel.category.add(p); render();});
      sec.appendChild(b);});
    if(paths.every(function(p){return tree.depth[p]===0||p==="(none)";}))
      sec.appendChild(el("div","hint",
        "Sub-categories appear once store enrichment runs."));
    host.appendChild(sec);}

  var counts=facetCounts();
  [["Tags","tag"],["Author","author"],["Flags","flag"]].forEach(function(pair){
    var keys=Object.keys(counts[pair[1]]);
    if(!keys.length)return;
    keys.sort(function(x,y){return counts[pair[1]][y]-counts[pair[1]][x]||x.localeCompare(y);});
    var sec=el("section"); sec.appendChild(el("h2",null,pair[0]));
    keys.slice(0,pair[1]==="author"?25:40).forEach(function(k){
      var b=el("button","facet"+(sel[pair[1]].has(k)?" on":""));
      b.appendChild(el("span",null,k));
      b.appendChild(el("span","n",counts[pair[1]][k]));
      b.addEventListener("click",function(){
        sel[pair[1]].has(k)?sel[pair[1]].delete(k):sel[pair[1]].add(k); render();});
      sec.appendChild(b);});
    host.appendChild(sec);});

  if(mark){
    var all=host.querySelectorAll(".facet");
    for(var i=0;i<all.length;i++){
      if((all[i].getAttribute("title")||all[i].firstChild.textContent)===mark){
        all[i].focus(); break;}}}
}

// Remote or placeholder, nothing between. There is no local mirror left to fall
// back to, so a missing thumbnail and a failed CDN fetch reach this same node.
function thumbFallback(a){
  var L=levels(a);
  return el("span","ph",L.length?L[0]:"uncategorized");}

function card(a){
  // Four elements, one focusable. Everything else the card used to carry lives in
  // detail(); a filtered grid of 835 was ~3,104 tab stops before that split.
  var c=el("button","card"+(selected===a.asset_key?" pick":""));
  c.setAttribute("type","button");
  c.setAttribute("title",a.name);
  c.setAttribute("aria-label",a.name);
  var th=el("div","th");
  if(a.thumbnail&&a.thumbnail.remote){var im=document.createElement("img");
    im.setAttribute("src",a.thumbnail.remote); im.setAttribute("alt","");
    im.setAttribute("loading","lazy"); im.setAttribute("decoding","async");
    // Intrinsic CDN size. The container's aspect-ratio governs layout; these only let
    // the browser reserve the box before any bytes arrive, so nothing shifts.
    im.setAttribute("width","1950"); im.setAttribute("height","1300");
    im.addEventListener("error",function(){
      th.textContent=""; th.appendChild(thumbFallback(a));});
    th.appendChild(im);}
  else th.appendChild(thumbFallback(a));
  c.appendChild(th);
  c.appendChild(el("div","nm",a.name));
  c.appendChild(el("div","mt",a.author||"author pending"));
  c.appendChild(el("div","mt",bytes(totalSize(a))));
  c.addEventListener("click",function(){select(a.asset_key,c);});
  return c;}

// Duplicate membership is a panel count, not a card badge: that an asset shares a
// size with another only matters once you are already looking at that asset.
function dupGroups(a){
  var files={}, out=[];
  a.versions.forEach(function(v){files[v.file]=1;});
  (DATA.duplicate_groups||[]).forEach(function(g){
    if((g.members||[]).some(function(m){return files[m];}))out.push(g);});
  return out;}

function byKey(k){
  for(var i=0;i<assets.length;i++){if(assets[i].asset_key===k)return assets[i];}
  return null;}

function badges(list){
  var box=el("div","tags");
  list.forEach(function(f){
    box.appendChild(el("span","badge"+(f==="broken"||f==="suspicious"?" warn":""),f));});
  return box;}

function closePanel(){
  var prev=document.querySelector("#out .card.pick");
  if(prev)prev.className="card";
  selected=null; detail(null);
  paintRows(false);
  // The close button was just destroyed, so focus would otherwise land on <body> and
  // a keyboard user would restart from the top of the document.
  var back=origin; origin=null;
  if(back&&back.isConnected&&back.focus)back.focus();}

// Selection is a module-scope asset_key, never a DOM reference, so Phase 4's chunked
// append can rebuild every node underneath it without losing the panel.
function select(key, node){
  if(selected===key){closePanel(); return;}
  openDetail(byKey(key), node);
  if(node)node.className="card pick";}

// Enter in table view always opens; it never toggles the row shut underneath itself.
function openDetail(a, node){
  var prev=document.querySelector("#out .card.pick");
  if(prev)prev.className="card";
  selected=a?a.asset_key:null;
  origin=node||null;
  detail(a||null);
  paintRows(false);
  // #out sits before #detail in DOM order, so without this the panel is ~835 tab
  // stops away from the card that opened it.
  if(a){var h=document.getElementById("detail"); h.focus();}}

// Row state is painted from (selected, rowIdx), never mutated in place, so the two
// cannot drift apart the way a directly-assigned class did.
function paintRows(scroll){
  var rows=document.querySelectorAll("#out tbody tr");
  for(var i=0;i<rows.length;i++){
    var cls=[];
    if(i===rowIdx)cls.push("cur");
    if(view[i]&&selected===view[i].asset_key)cls.push("pick");
    rows[i].className=cls.join(" ");}
  if(scroll&&rows[rowIdx])rows[rowIdx].scrollIntoView({block:"nearest"});}

function moveRow(step){
  if(!view.length)return;
  rowIdx=Math.max(0,Math.min(view.length-1,rowIdx+step));
  paintRows(true);}

function detail(a){
  var host=document.getElementById("detail");
  host.textContent="";
  if(!a){host.className=""; return;}
  host.className="on";
  var v=latest(a);

  var hd=el("div","dhd");
  hd.appendChild(el("h2","dnm",a.name));
  var x=el("button",null,"close");
  x.addEventListener("click",closePanel);
  hd.appendChild(x);
  host.appendChild(hd);

  if(a.local_name&&a.local_name!==a.name)
    host.appendChild(el("div","local","on disk: "+a.local_name));
  host.appendChild(el("div","mt",a.author||"author pending"));

  // Full breadcrumb: all levels the store supplied, not just level 1.
  var L=levels(a), bc=el("div","crumb");
  if(L.length){L.forEach(function(seg,i){
    if(i)bc.appendChild(el("span","sep","/"));
    bc.appendChild(el("span",null,seg));});}
  else bc.appendChild(el("span","pending","category pending"));
  host.appendChild(bc);

  if(a.rating)host.appendChild(el("div","mt","★ "+a.rating+
    (a.reviews?" ("+a.reviews+")":"")+(a.price?" · $"+a.price:"")));
  if(a.tags.length){var tg=el("div","tags");
    a.tags.forEach(function(t){tg.appendChild(el("span","tag",t));});
    host.appendChild(tg);}

  var fl=flagsOf(a).filter(function(y,i,z){return z.indexOf(y)===i;});
  if(fl.length)host.appendChild(badges(fl));
  dupGroups(a).forEach(function(g){
    host.appendChild(el("div","mt",
      g.verdict+": "+g.members.length+" files, "+g.reason));});

  host.appendChild(el("h2",null,"Versions"));
  a.versions.forEach(function(y){
    var vr=el("div","ver");
    vr.appendChild(el("div","mt",(y.version?"v"+y.version:"no version")+
      " · "+bytes(y.size_bytes)+(y.latest?" · latest":"")));
    vr.appendChild(el("div","local",y.file));
    var vf=[];
    if(y.integrity)vf.push(y.integrity);
    if(y.prerelease)vf.push("prerelease");
    if(y.duplicate&&y.duplicate.verdict)vf.push(y.duplicate.verdict);
    if(vf.length)vr.appendChild(badges(vf));
    host.appendChild(vr);});

  var row=el("div","row");
  var open=document.createElement("a"); open.setAttribute("href",encodeURI("./"+v.file));
  open.textContent="open"; row.appendChild(open);
  var cp=el("button",null,"copy path");
  cp.addEventListener("click",function(){
    navigator.clipboard&&navigator.clipboard.writeText(v.file);
    cp.textContent="copied"; setTimeout(function(){cp.textContent="copy path";},900);});
  row.appendChild(cp);
  // The store slot is always present so the field is visibly accounted for whether or
  // not enrichment has resolved it yet.
  if(a.store){
    var st=document.createElement("a"); st.setAttribute("href",a.store);
    st.setAttribute("target","_blank"); st.setAttribute("rel","noreferrer");
    st.textContent="store page"; row.appendChild(st);
    var cs=el("button",null,"copy store URL");
    cs.addEventListener("click",function(){
      navigator.clipboard&&navigator.clipboard.writeText(a.store);
      cs.textContent="copied"; setTimeout(function(){cs.textContent="copy store URL";},900);});
    row.appendChild(cs);
  } else {
    row.appendChild(el("span","pending",a.non_store?"not an Asset Store package"
                                                  :"store link pending"));
  }
  host.appendChild(row);}

function setSort(key){
  sortBy=key;
  document.getElementById("sort").value=key;
  render();}

function table(list){
  var wrap=el("div","wrap"), t=el("table"), hd=el("tr");
  ["Name (store)","Name (on disk)","Author","Category (full path)","Version","Size",
   "Files","Store URL","Package path"]
    .forEach(function(h){
      var th=el("th",null,h), key=SORTABLE[h];
      if(key){
        th.className="sortable";
        th.setAttribute("tabindex","0");
        th.setAttribute("role","button");
        th.setAttribute("aria-sort",sortBy===key?"descending":"none");
        th.addEventListener("click",function(){setSort(key);});
        th.addEventListener("keydown",function(e){
          if(e.key==="Enter"||e.key===" "){e.preventDefault(); setSort(key);}});}
      hd.appendChild(th);});
  t.appendChild(el("thead")).appendChild(hd);
  var tb=el("tbody");
  list.forEach(function(a,i){
    var v=latest(a),r=el("tr");
    r.addEventListener("click",function(e){
      if(e.target.tagName==="A")return;   // let the store link do its own job
      rowIdx=i; openDetail(a,r);});
    [a.name,(a.local_name&&a.local_name!==a.name)?a.local_name:"-",
     a.author||"-",catPath(a)||"-",v.version||"-",bytes(totalSize(a)),
     a.versions.length].forEach(function(x){r.appendChild(el("td",null,x));});
    var st=el("td");
    if(a.store){var link=document.createElement("a");
      link.setAttribute("href",a.store); link.setAttribute("target","_blank");
      link.setAttribute("rel","noreferrer"); link.textContent=a.store; st.appendChild(link);}
    else st.appendChild(el("span","pending",a.non_store?"n/a":"pending"));
    r.appendChild(st);
    r.appendChild(el("td",null,v.file));
    tb.appendChild(r);});
  t.appendChild(tb); wrap.appendChild(t); return wrap;}

function appendChunk(g){
  var end=Math.min(cursor+CHUNK,view.length);
  for(var i=cursor;i<end;i++)g.appendChild(card(view[i]));
  cursor=end;}

function render(){
  query=document.getElementById("q").value.trim().toLowerCase();
  var list=assets.filter(function(a){return matches(a,query);});
  list.sort(function(x,y){
    if(sortBy==="size")return totalSize(y)-totalSize(x);
    if(sortBy==="rating"){
      // rating is a float on 552 assets; the other 283 sort last rather than as an
      // implicit zero, which would bury them among the genuine 1-star packages.
      var rx=x.rating,ry=y.rating;
      if(rx==null&&ry==null)return x.name.localeCompare(y.name);
      if(rx==null)return 1;
      if(ry==null)return -1;
      return ry-rx||x.name.localeCompare(y.name);}
    if(sortBy==="date"){
      // Populated on 214 of 861 versions. The old comparator coerced the other 75% to
      // "" and sorted them into one indistinguishable block at an arbitrary end;
      // nulls-last makes the coverage gap legible instead of silently wrong.
      var dx=latest(x).release_date,dy=latest(y).release_date;
      if(!dx&&!dy)return x.name.localeCompare(y.name);
      if(!dx)return 1;
      if(!dy)return -1;
      return dy.localeCompare(dx);}
    return x.name.localeCompare(y.name);});

  // Reset the append cursor and drop the previous observer before anything is built,
  // so a rapid filter change cannot leave a stale sentinel appending into a dead grid.
  var mine=++gen;
  if(obs){obs.disconnect(); obs=null;}
  view=list; cursor=0; rowIdx=-1;

  // The header always reports the full filtered total, independent of how many chunks
  // have actually rendered, so chunking never reads as missing results.
  var files=list.reduce(function(s,a){return s+a.versions.length;},0);
  document.getElementById("count").textContent=
    list.length+" / "+assets.length+" assets · "+files+" files";
  // Counted once per render, from the filtered list, before the first chunk lands.
  renderFacets();

  var out=document.getElementById("out"); out.textContent="";
  if(!grid){out.appendChild(table(list)); paintRows(false); return;}
  var g=el("div","grid"); out.appendChild(g);
  appendChunk(g);
  if(cursor>=view.length)return;
  if(!window.IntersectionObserver){
    while(cursor<view.length)appendChunk(g);
    return;}
  var sen=el("div","sentinel"); out.appendChild(sen);
  obs=new IntersectionObserver(function(entries){
    // disconnect() unregisters targets but does NOT drop entries already queued, so a
    // superseded render's observer still gets one delivery. Without this guard it
    // appends the new list into the old detached grid, advances the shared cursor past
    // those items, and then disconnects whichever observer `obs` now points at.
    if(mine!==gen)return;
    if(!entries[0].isIntersecting)return;
    appendChunk(g);
    if(cursor>=view.length){obs.disconnect(); obs=null; out.removeChild(sen);}},
    {rootMargin:"400px"});
  obs.observe(sen);}

// Manual-only triggering means a stale index is indistinguishable from a fresh one
// unless we say so. This stamp is the whole mitigation for that trigger choice.
(function(){
  var el=document.getElementById("generated");
  if(!el||!DATA.generated){return;}
  var t=Date.parse(DATA.generated);
  if(isNaN(t)){el.textContent="indexed "+DATA.generated; return;}
  var days=Math.floor((Date.now()-t)/86400000);
  var age=days<1?"today":days===1?"1 day ago":days+" days ago";
  el.textContent="indexed "+age;
  el.setAttribute("title",DATA.generated);
  if(days>=7){el.className="stamp stale";}
})();

document.addEventListener("keydown",function(e){
  var focused=document.activeElement, tag=focused?focused.tagName:"";
  var typing=(tag==="INPUT"||tag==="SELECT"||tag==="TEXTAREA");
  if(e.key==="/"&&!typing){
    e.preventDefault(); document.getElementById("q").focus(); return;}
  if(e.key==="Escape"){
    var box=document.getElementById("q");
    // Clear a live search first: it is the wider undo of the two.
    if(box.value){clearTimeout(qt); box.value=""; render(); return;}
    closePanel(); return;}
  // Grid view gets / and Esc only. Two-dimensional arrow navigation needs live
  // column-count math against a responsive auto-fill grid, and Tab already reaches
  // one stop per card.
  if(grid||typing)return;
  if(e.key==="ArrowDown"){e.preventDefault(); moveRow(rowIdx<0?0:1); return;}
  if(e.key==="ArrowUp"){e.preventDefault(); moveRow(-1); return;}
  if(e.key==="Enter"&&rowIdx>=0){
    e.preventDefault();
    openDetail(view[rowIdx],document.querySelectorAll("#out tbody tr")[rowIdx]);}});
// The rail is a disclosure only where it would otherwise eat the whole first screen.
// This has to track the breakpoint, not sample it once: on desktop the summary is
// display:none, so a rail left closed after a resize has no affordance to reopen it.
var narrow=window.matchMedia&&window.matchMedia("(max-width:760px)");
function syncRail(){document.getElementById("facetbox").open=!(narrow&&narrow.matches);}
if(narrow){
  syncRail();
  if(narrow.addEventListener)narrow.addEventListener("change",syncRail);
  else if(narrow.addListener)narrow.addListener(syncRail);}

document.getElementById("q").addEventListener("input",function(){
  clearTimeout(qt); qt=setTimeout(render,120);});
document.getElementById("sort").addEventListener("change",function(e){sortBy=e.target.value;render();});
document.getElementById("view").addEventListener("click",function(e){
  grid=!grid; e.target.textContent=grid?"List view":"Grid view"; render();});
render();
})();
</script>
</body>
</html>
"""


def annotate_pending(data, queue_path):
    """Mark assets awaiting a web search. Read at emit time, not build time: `update`
    writes the queue after scanning, so annotating in build() would land the flag on the
    following run. A missing or malformed queue is a no-op, never a failed emit."""
    keys = set()
    if os.path.exists(queue_path):
        try:
            with open(queue_path, encoding="utf-8") as fh:
                keys = {e["asset_key"] for e in json.load(fh).get("pending", [])}
        except (ValueError, KeyError, TypeError):
            print(f"emit: {os.path.basename(queue_path)} unreadable — pending flags skipped")
    for asset in data.get("assets", []):
        if asset["asset_key"] in keys:
            asset["pending_enrichment"] = True
    return data


def emit_html(data, path):
    write_atomic(path, HTML_TEMPLATE.replace("__DATA__", safe_json(data)))


# ---------------------------------------------------------------------------
# change tracking — diff the previous state, classify what appeared, log it
# ---------------------------------------------------------------------------

CHANGELOG_HEADER = """# Change Log

Sizes are compared, not modification times. OneDrive rewrites mtimes — AllSky ships two
releases with identical sizes where the higher version carries the *older* mtime — so an
mtime change means nothing here and is ignored entirely.

A file still uploading can briefly report a different size and appear as `resized`.
"""


def manifest_of(data):
    """Previous state as {rel_path: size_bytes}, read straight out of assets.json.

    assets.json already records every file's path and size, so it IS the previous-state
    record. A separate manifest artifact would be a second copy to keep in sync.
    """
    out = {}
    for asset in data.get("assets", []):
        for version in asset.get("versions", []):
            if version.get("file"):
                out[version["file"]] = version.get("size_bytes")
    return out


def diff_manifest(prev, now, had_previous=None):
    """Compare on (path, size). Deliberately blind to timestamps.

    Returns added / removed / resized(path, old, new), plus is_baseline when there was
    no previous state — that case must not be reported as hundreds of additions.

    had_previous distinguishes "no prior state existed" from "prior state existed and was
    legitimately empty". Defaults to inferring from prev being non-empty, which conflates
    the two; callers that know should say so.
    """
    if had_previous is None:
        had_previous = bool(prev)
    if not had_previous:
        return {"added": [], "removed": [], "resized": [], "is_baseline": True}
    prev_paths, now_paths = set(prev), set(now)
    resized = [(p, prev[p], now[p]) for p in sorted(prev_paths & now_paths)
               if prev[p] != now[p]]
    return {
        "added": sorted(now_paths - prev_paths),
        "removed": sorted(prev_paths - now_paths),
        "resized": resized,
        "is_baseline": False,
    }


def classify_added(paths, cache):
    """Split added files into new versions of known assets vs genuinely new assets.

    A known asset reuses its cached resolution with no network. A new asset needs a web
    search, so it goes on the queue. An asset that was already searched and failed is
    queued but flagged — re-queuing it silently would imply a fresh lookup will succeed.
    """
    new_assets, new_versions = [], []
    for path in paths:
        parsed = parse(path)
        key = asset_key(parsed)
        record = (cache or {}).get(key)
        entry = {"asset_key": key, "title": parsed["title"], "path": path}
        if record and record.get("status") == "resolved":
            new_versions.append(dict(entry, version=parsed["version"]))
        else:
            new_assets.append(dict(entry, previously_unresolved=bool(record)))
    return {"new_assets": new_assets, "new_versions": new_versions}


def format_changelog_entry(diff, classified, when):
    """Render one newest-first entry, or None when there is nothing to say."""
    if diff.get("is_baseline"):
        return None
    counts = (len(diff["added"]), len(diff["removed"]), len(diff["resized"]))
    if not any(counts):
        return None

    L = [f"## {when} — +{counts[0]} \u2212{counts[1]} ~{counts[2]}", ""]
    new_assets = classified["new_assets"]
    if new_assets:
        L += [f"**NEW ASSETS ({len(new_assets)})** — need enrichment: "
              "`resolve_store.py export-titles --pending`"]
        for a in new_assets:
            note = " _(previously unresolved)_" if a["previously_unresolved"] else ""
            L.append(f"- `{a['path']}` \u2192 `{a['asset_key']}`{note}")
        L.append("")
    if classified["new_versions"]:
        L += [f"**NEW VERSION of a known asset ({len(classified['new_versions'])})** — "
              "resolution reused, no search needed"]
        for a in classified["new_versions"]:
            v = f" (v{a['version']})" if a.get("version") else ""
            L.append(f"- `{a['path']}`{v} \u2192 `{a['asset_key']}`")
        L.append("")
    if diff["removed"]:
        L += [f"**REMOVED ({len(diff['removed'])})**"]
        L += [f"- `{p}`" for p in diff["removed"]] + [""]
    if diff["resized"]:
        L += [f"**RESIZED ({len(diff['resized'])})** — replaced in place without a "
              "version bump; reported only, not re-resolved"]
        L += [f"- `{p}` {old:,} \u2192 {new:,} B" for p, old, new in diff["resized"]]
        L.append("")
    return "\n".join(L)


def append_changelog(path, entry):
    """Prepend under the header so newest is first. Rewrites the whole file atomically —
    it is small, and a partial changelog is worse than a slow one."""
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    # Find the body by its first entry marker rather than slicing by len(CHANGELOG_HEADER).
    # A length-based slice silently destroys all prior history the moment the header text
    # is edited, which defeats the whole point of an append-only log.
    if existing.startswith("## "):
        body = existing                      # no header at all — all of it is entries
    else:
        marker = existing.find("\n## ")
        body = existing[marker + 1:] if marker != -1 else ""
    write_atomic(path, CHANGELOG_HEADER + "\n" + entry.rstrip() + "\n\n" + body)


def write_pending_queue(path, new_assets):
    """Merge into the queue, preserving first_seen for entries already there."""
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = {e["asset_key"]: e for e in json.load(fh).get("pending", [])}
        except (ValueError, KeyError):
            # Do not silently forget the queue — that is exactly the failure the
            # accumulate-with-first_seen design exists to avoid. Keep the bad file.
            existing = {}
            try:
                os.replace(path, path + ".corrupt")
                print(f"update: pending queue unreadable — preserved as "
                      f"{os.path.basename(path)}.corrupt, starting a fresh queue")
            except OSError:
                print("update: pending queue unreadable and could not be preserved")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for a in new_assets:
        if a["asset_key"] not in existing:
            existing[a["asset_key"]] = {
                "asset_key": a["asset_key"], "title": a["title"],
                "first_seen": today,
                "previously_unresolved": a["previously_unresolved"],
            }
    payload = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "pending": [existing[k] for k in sorted(existing)]}
    write_atomic(path, json.dumps(payload, indent=1, sort_keys=True))
    return len(payload["pending"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def repo_dir():
    """The tool's own repository root. bin/ sits directly inside it."""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


def state_dir():
    """Tool state lives in the repo's state/, never inside the vault."""
    return os.path.join(repo_dir(), "state")


def load_config():
    """Per-machine vault location. The tool no longer assumes it sits beside the vault."""
    with open(os.path.join(repo_dir(), "config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("steps", nargs="+", choices=["scan", "emit", "update"])
    ap.add_argument("--root", default=None,
                    help="vault root override (default: config.json vault_root)")
    ap.add_argument("--state", default=None,
                    help="state dir override (default: repo state/)")
    args = ap.parse_args(argv)

    cfg = {} if args.root else load_config()
    root = os.path.abspath(args.root) if args.root else cfg["vault_root"]
    index_dir = os.path.abspath(args.state) if args.state else state_dir()
    assets_json = os.path.join(index_dir, "assets.json")

    data = None

    if "update" in args.steps:
        # Read the previous state BEFORE overwriting it. assets.json is the record.
        prev, had_previous = {}, False
        if os.path.exists(assets_json):
            try:
                with open(assets_json, encoding="utf-8") as fh:
                    prev = manifest_of(json.load(fh))
                had_previous = True
            except ValueError:
                print("update: assets.json unreadable — establishing baseline")
        else:
            print("update: no previous state — establishing baseline")

        scanned = scan(root)
        data = build(scanned)
        diff = diff_manifest(prev, {r["rel_path"]: r["size"] for r in scanned},
                             had_previous=had_previous)

        cache = {}
        cache_path = os.path.join(index_dir, "cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cache = json.load(fh).get("resolved", {})
            except ValueError:
                print("update: cache.json unreadable — treating all additions as new")

        classified = classify_added(diff["added"], cache)

        # build() regenerates entries from disk with empty store metadata, so writing it
        # straight out would wipe every enrichment from the index (recoverable from
        # cache.json, but the emitted viewer would be bare until someone noticed).
        # The documented pipeline is scan -> enrich -> emit; update must not skip enrich.
        # Imported lazily: resolve_store pulls write_atomic from this module.
        try:
            import resolve_store
            overrides = {}
            overrides_path = os.path.join(index_dir, "overrides.json")
            if os.path.exists(overrides_path):
                try:
                    with open(overrides_path, encoding="utf-8") as fh:
                        overrides = json.load(fh)
                except ValueError:
                    print("update: overrides.json unreadable — ignored")
            data = resolve_store.merge(data, {"resolved": cache}, overrides)
        except ImportError:
            print("update: resolve_store unavailable — index written without enrichment")

        write_atomic(assets_json, json.dumps(data, indent=1, sort_keys=True))

        entry = format_changelog_entry(
            diff, classified, datetime.now().strftime("%Y-%m-%d %H:%M"))
        if entry:
            append_changelog(os.path.join(index_dir, "CHANGES.md"), entry)
        queued = write_pending_queue(
            os.path.join(index_dir, "pending-enrichment.json"),
            classified["new_assets"])

        print(f"update: +{len(diff['added'])} -{len(diff['removed'])} "
              f"~{len(diff['resized'])}"
              + (f", {len(classified['new_assets'])} new asset(s) queued "
                 f"({queued} pending total)" if classified["new_assets"] else "")
              + ("" if entry else ", no changelog entry"))
        # update already scanned, merged and wrote assets.json. A standalone "scan" step
        # afterwards would rebuild from disk with empty store metadata and overwrite it —
        # the same 717-to-0 enrichment wipe, reachable via `scan update` in one call.
        args.steps = [st for st in args.steps if st != "scan"] + ["emit"]

    if "scan" in args.steps:
        scanned = scan(root)
        data = build(scanned)
        write_atomic(assets_json, json.dumps(data, indent=1, sort_keys=True))
        dups = [g for g in data["duplicate_groups"] if g["verdict"] == "duplicate"]
        print(f"scan: {data['file_count']} files -> {data['asset_count']} assets, "
              f"{len(dups)} duplicate groups "
              f"({sum(g['redundant_bytes'] for g in dups)/1e9:.2f} GB redundant)")

    if "emit" in args.steps:
        if data is None:
            with open(assets_json, encoding="utf-8") as fh:
                data = json.load(fh)
        data = annotate_pending(
            data, os.path.join(index_dir, "pending-enrichment.json"))
        out_dir = os.path.abspath(cfg.get("output_dir") or root)
        emit_html(data, os.path.join(out_dir, "index.html"))
        rows = emit_csv(data, os.path.join(out_dir, "assets.csv"))
        emit_review_queue(data, os.path.join(index_dir, "review-queue.md"))
        print(f"emit: index.html, assets.csv ({rows} rows), review-queue -> {index_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
