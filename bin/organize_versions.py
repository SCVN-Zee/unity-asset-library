#!/usr/bin/env python3
"""Move indexed store archives into full breadcrumb category folders.

Deterministic plan, fail-closed apply. Filenames are never changed; destinations
are `<vault>/<category.levels...>/<source basename>`. Planned/occupied destination
collisions skip every contender; a mid-batch failure reverses completed moves.
Metadata only — no archive is ever opened.

CLI:
    python3 organize_versions.py            # dry-run plan
    python3 organize_versions.py --apply    # perform moves + one index update
"""

import argparse
from contextlib import nullcontext
import json
import os
import stat
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup_versions as cv  # noqa: E402
import index_assets as ia  # noqa: E402

SafetyError = cv.SafetyError


class StaleIndex(SafetyError):
    """assets.json no longer matches the live vault — Resync is the fix."""

_stat = os.stat
_lstat = os.lstat

# Forbidden destination/source components, compared after NFC normalization and
# casefolding: the target APFS volume is case-insensitive and
# normalization-insensitive, so a store level of "_quarantine" would resolve
# onto the real "_Quarantine" tree.
_EXCLUDED = tuple(unicodedata.normalize("NFC", n).casefold() for n in ia.EXCLUDED_DIR_NAMES)


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------

def _rel_parts(rel):
    """Normalize one repo-relative path to forward-slash components."""
    parts = rel.split("/") if rel else []
    if any(part in ("", ".", "..") for part in parts):
        raise SafetyError(f"unsafe relative path: {rel!r}")
    return parts


def _is_excluded_component(part):
    return unicodedata.normalize("NFC", part).casefold() in _EXCLUDED


def _contained_path(root, rel):
    """Absolute path for rel with resolved containment inside root."""
    root = os.path.realpath(os.path.abspath(root))
    path = os.path.abspath(os.path.join(root, *rel.split("/")))
    try:
        if os.path.commonpath((root, path)) != root or \
                os.path.commonpath((root, os.path.realpath(path))) != root:
            raise SafetyError(f"path escapes vault: {rel}")
    except ValueError as exc:
        raise SafetyError(f"path escapes vault: {rel}") from exc
    return path


def _source_path(root, rel):
    """Source validation: archive suffix plus cleanup's containment checks."""
    _rel_parts(rel)
    return cv._candidate_path(root, rel)


def _validate_category_levels(levels):
    """Reject unsafe breadcrumb components. Replacement is never attempted:
    substituting characters could alias two distinct store categories."""
    if not isinstance(levels, list) or not levels:
        raise SafetyError(f"store category has no breadcrumb levels: {levels!r}")
    out = []
    for level in levels:
        if not isinstance(level, str):
            raise SafetyError(f"non-string category level: {level!r}")
        part = unicodedata.normalize("NFC", level).strip()
        if not part or part in (".", ".."):
            raise SafetyError(f"empty/dot category level: {level!r}")
        if any(sep in part for sep in ("/", "\\", os.sep)) or os.path.isabs(part):
            raise SafetyError(f"separator in category level: {level!r}")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise SafetyError(f"control character in category level: {level!r}")
        if _is_excluded_component(part):
            raise SafetyError(f"category level collides with an excluded directory: {level!r}")
        out.append(part)
    return out


def _dest_key(rel):
    """Collision key: NFC-normalized, case-folded destination path."""
    return unicodedata.normalize("NFC", rel).casefold()


def _source_identity(path):
    try:
        st = _lstat(path)
    except OSError as exc:
        raise SafetyError(f"source unavailable: {path}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise SafetyError(f"source is not a regular file: {path}")
    return (st.st_dev, st.st_ino, st.st_size)


def _check_source_rel(rel):
    """A source under an excluded directory is a hard error, never a move."""
    parts = _rel_parts(rel)
    if any(_is_excluded_component(part) for part in parts[:-1]):
        raise SafetyError(f"source under an excluded directory: {rel}")
    return parts


def _blocked_ancestor(root, levels):
    """First existing ancestor that is not a real directory, else None."""
    for i in range(1, len(levels) + 1):
        anc_rel = "/".join(levels[:i])
        anc = _contained_path(root, anc_rel)
        try:
            st = _lstat(anc)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return anc_rel
    return None


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def plan_organize(root, data, scanned=None):
    """Deterministic plan from server/CLI-owned assets.json plus a strict live
    scan. Validates the live manifest against the index (stale state aborts with
    "run Resync"), then transforms without touching disk or generated state."""
    if scanned is None:
        scanned = ia.scan(root, strict=True)
    live = sorted((row["rel_path"], row["size"]) for row in scanned)
    indexed = sorted((path, size) for path, size in ia.manifest_of(data).items())
    if live != indexed:
        raise StaleIndex(
            f"index is stale — run Resync first (index {len(indexed)} files vs "
            f"live {len(live)} files)")

    moves, warnings = [], []
    skips = {"non-store": [], "no-category": [], "already-correct": [],
             "blocked-ancestor": [], "collision": []}
    assets_total = 0
    candidates = []

    for asset in sorted(data.get("assets", []), key=lambda a: a["asset_key"]):
        versions = asset.get("versions", [])
        files = sorted(v["file"] for v in versions)
        if asset.get("non_store"):
            skips["non-store"].append(
                {"asset_key": asset["asset_key"], "name": asset.get("name"),
                 "files": files})
            continue
        category = asset.get("category") or {}
        levels = category.get("levels")
        if category.get("source") != "store" or not levels:
            resolvable = not (asset.get("resolution") or {}).get("id_verified")
            skips["no-category"].append(
                {"asset_key": asset["asset_key"], "name": asset.get("name"),
                 "files": files, "resolvable": resolvable})
            continue

        levels = _validate_category_levels(levels)
        blocked = _blocked_ancestor(root, levels)
        if blocked is not None:
            skips["blocked-ancestor"].append(
                {"asset_key": asset["asset_key"], "name": asset.get("name"),
                 "path": blocked})
            continue

        for v in sorted(versions, key=lambda v: v["file"]):
            src = v["file"].replace(os.sep, "/")
            _check_source_rel(src)
            basename = src.rsplit("/", 1)[-1]
            dst = "/".join(levels + [basename])
            if src == dst:
                skips["already-correct"].append({"asset_key": asset["asset_key"], "src": src})
                continue
            if v.get("folder_hint"):
                warnings.append({"src": src,
                                 "warning": f"folder-supplied version in {v['folder_hint']!r}; "
                                            f"filename stays unchanged"})
            candidates.append({"asset_key": asset["asset_key"], "name": asset.get("name"),
                               "category": "/".join(levels), "levels": levels,
                               "src": src, "dst": dst,
                               "size_bytes": v.get("size_bytes"),
                               "folder_version_warning": bool(v.get("folder_hint"))})

    # Collisions: an occupied destination, or two candidates aliasing to one
    # NFC/case-folded destination. Every contender skips; no winner is chosen.
    by_key = {}
    for cand in candidates:
        by_key.setdefault(_dest_key(cand["dst"]), []).append(cand)
    for group in by_key.values():
        if len(group) > 1:
            skips["collision"].append(
                {"dst": group[0]["dst"],
                 "candidates": sorted(c["src"] for c in group),
                 "asset_keys": sorted(c["asset_key"] for c in group)})
            continue
        cand = group[0]
        dst_abs = _contained_path(root, cand["dst"])
        try:
            _lstat(dst_abs)
            occupied = True
        except OSError:
            occupied = False
        if occupied:
            skips["collision"].append(
                {"dst": cand["dst"], "candidates": [cand["src"]], "occupants": [cand["dst"]],
                 "asset_keys": [cand["asset_key"]]})
            continue
        moves.append(cand)

    moves.sort(key=lambda m: m["src"])
    for key in skips:
        skips[key].sort(key=lambda s: str(sorted(s.items())))
    groups = {}
    for move in moves:
        entry = groups.setdefault(move["category"], {"category": move["category"],
                                                     "srcs": [], "dsts": [], "bytes": 0})
        entry["srcs"].append(move["src"])
        entry["dsts"].append(move["dst"])
        entry["bytes"] += move["size_bytes"] or 0
    warnings.sort(key=lambda w: w["src"])
    return {
        "moves": moves,
        "groups": [groups[key] for key in sorted(groups)],
        "skips": skips,
        "warnings": warnings,
        "totals": {
            "assets_total": assets_total,
            "moves": len(moves),
            "move_bytes": sum(m["size_bytes"] or 0 for m in moves),
            "skips": {key: len(skips[key]) for key in sorted(skips)},
        },
    }


# ---------------------------------------------------------------------------
# snapshot and apply
# ---------------------------------------------------------------------------

def capture_snapshot(root, plan, scanned):
    """Bind root identity, live manifest, and (device, inode, size) source
    identities. mtime_ns is deliberately excluded — OneDrive rewrites it."""
    snapshot = {
        "root": cv._root_identity(root),
        "manifest": cv._manifest(scanned),
        "sources": {},
    }
    for move in plan["moves"]:
        snapshot["sources"][move["src"]] = _source_identity(
            _source_path(root, move["src"]))
    return snapshot


def _preflight(root, plan, snapshot):
    """Abort before the first move on any drift. mtime-only source changes are
    non-drift by identity design."""
    if cv._root_identity(root) != snapshot["root"]:
        raise SafetyError("vault root changed before organize")
    if cv._manifest(ia.scan(root, strict=True)) != snapshot["manifest"]:
        raise SafetyError("vault archive manifest changed before organize")
    for move in plan["moves"]:
        src_abs = _source_path(root, move["src"])
        if _source_identity(src_abs) != snapshot["sources"][move["src"]]:
            raise SafetyError(f"source changed before organize: {move['src']}")
        dst_abs = _contained_path(root, move["dst"])
        try:
            _lstat(dst_abs)
            raise SafetyError(f"destination appeared before organize: {move['dst']}")
        except OSError:
            pass
        blocked = _blocked_ancestor(root, move["levels"])
        if blocked is not None:
            raise SafetyError(f"destination ancestor is not a real directory: {blocked}")
        parent = os.path.dirname(dst_abs)
        while True:
            try:
                st = _stat(parent)
                break
            except FileNotFoundError:
                parent = os.path.dirname(parent)
        if st.st_dev != snapshot["root"][0]:
            raise SafetyError(f"destination parent is on another volume: {move['dst']}")


def _reverse_completed(root, ledger):
    """Reverse the ledger, newest first, identity-checked and no-clobber.
    Entries whose source deletion never committed only drop the new
    destination link — the untouched original stays authoritative. Returns
    the residual destination paths that could not be reversed."""
    residuals = []
    for src_rel, dst_rel, ident, unlinked in reversed(ledger):
        try:
            dst_abs = _contained_path(root, dst_rel)
            src_abs = _source_path(root, src_rel)
            st = _lstat(dst_abs)
            if (st.st_dev, st.st_ino, st.st_size) != ident:
                residuals.append(dst_rel)
                continue
            if not unlinked:
                os.unlink(dst_abs)
                continue
            if os.path.lexists(src_abs):
                residuals.append(dst_rel)
                continue
            os.link(dst_abs, src_abs)
            os.unlink(dst_abs)
        except OSError:
            residuals.append(dst_rel)
    return residuals


def _remove_empty_parents(root, moves, category_prefixes):
    """rmdir recorded former source parents, deepest-first. Stop at the vault
    root, an excluded directory, a destination category directory, or the
    first non-empty directory."""
    parents = sorted({move["src"].rsplit("/", 1)[0] for move in moves if "/" in move["src"]},
                     key=lambda p: (-p.count("/"), p))
    removed, doomed = [], set()
    for rel in parents:
        parts = _rel_parts(rel)
        if any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in doomed):
            continue
        if any(_is_excluded_component(part) for part in parts):
            continue
        if rel in category_prefixes:
            continue
        try:
            os.rmdir(_contained_path(root, rel))
            removed.append(rel)
        except OSError:
            doomed.add(rel)
            for i in range(1, len(parts)):
                doomed.add("/".join(parts[:i]))
    return removed


def apply_organize(root, state, plan, snapshot, root_override=False,
                   lock_already_held=False):
    """Fail-closed batch apply. Acquires cleanup's per-vault lock; no-clobber
    os.link + os.unlink moves; reversed ledger on mid-batch failure; one index
    update at the end."""
    category_prefixes = set()
    for move in plan["moves"]:
        for i in range(1, len(move["levels"]) + 1):
            category_prefixes.add("/".join(move["levels"][:i]))

    lock = nullcontext() if lock_already_held else cv._cleanup_lock(root)
    with lock:
        _preflight(root, plan, snapshot)

        for prefix in sorted(category_prefixes):
            path = _contained_path(root, prefix)
            if os.path.lexists(path):
                st = _lstat(path)
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    raise SafetyError(f"destination ancestor is not a real directory: {prefix}")
            else:
                os.makedirs(path)
        for move in plan["moves"]:
            try:
                _lstat(_contained_path(root, move["dst"]))
                raise SafetyError(f"destination appeared before organize: {move['dst']}")
            except OSError:
                pass

        ledger = []
        skipped_collisions = []
        failure = None
        destructive_started = False
        try:
            for move in plan["moves"]:
                src_abs = _source_path(root, move["src"])
                dst_abs = _contained_path(root, move["dst"])
                ident = _source_identity(src_abs)
                if ident != snapshot["sources"][move["src"]]:
                    raise SafetyError(f"source changed during organize: {move['src']}")
                try:
                    os.link(src_abs, dst_abs)
                except FileExistsError:
                    skipped_collisions.append({"src": move["src"], "dst": move["dst"]})
                    continue
                # Register before anything commits: the destination link now
                # exists even though the source deletion has not run yet.
                ledger.append([move["src"], move["dst"], ident, False])
                st = _lstat(dst_abs)
                if (st.st_dev, st.st_ino, st.st_size) != ident:
                    raise SafetyError(f"moved bytes do not match source identity: {move['dst']}")
                destructive_started = True
                os.unlink(src_abs)
                ledger[-1][3] = True
        except BaseException as exc:
            failure = exc

        if failure is not None:
            residuals = _reverse_completed(root, ledger)
            try:
                cv._reconcile(root, state, root_override, snapshot["root"])
            except BaseException as reconcile_exc:
                failure = SafetyError(f"{failure}; reconciliation failed: {reconcile_exc}")
            if residuals:
                raise SafetyError(
                    f"{failure}; organize rollback incomplete, residual destinations: "
                    f"{', '.join(sorted(residuals))}")
            if isinstance(failure, SafetyError):
                raise failure
            raise SafetyError(f"organize failed: {failure}") from failure

        removed_dirs = _remove_empty_parents(root, plan["moves"], category_prefixes)
        try:
            if cv._root_identity(root) != snapshot["root"]:
                raise SafetyError("vault root changed after organize")
            update_result = ia.main(cv._update_args(state, root, root_override))
        except BaseException as update_exc:
            raise SafetyError(
                f"organize applied {len(ledger)} moves but index update failed: "
                f"{update_exc}; Resync is the reconciliation path") from update_exc

    return {
        "moves_applied": len(ledger),
        "skipped_collisions": skipped_collisions,
        "removed_dirs": removed_dirs,
        "update_result": update_result,
    }


# ---------------------------------------------------------------------------
# render and CLI
# ---------------------------------------------------------------------------

def render_plan(plan, apply=False):
    lines = [f"organize: {'apply' if apply else 'dry-run'}"]
    for group in plan["groups"]:
        lines.append(f"[{group['category']}] {len(group['srcs'])} file(s), "
                     f"{group['bytes']} bytes")
        for src, dst in zip(group["srcs"], group["dsts"]):
            lines.append(f"  {src} -> {dst}")
    for warning in plan["warnings"]:
        lines.append(f"warning: {warning['warning']} ({warning['src']})")
    labels = {
        "non-store": "non-store (never moves)",
        "no-category": "no store category",
        "already-correct": "already in place",
        "blocked-ancestor": "blocked ancestor",
        "collision": "collision (all contenders skipped)",
    }
    for key in sorted(plan["skips"]):
        if plan["skips"][key]:
            lines.append(f"skipped {labels[key]}: {len(plan['skips'][key])}")
    totals = plan["totals"]
    lines.append(f"total: {totals['moves']} move(s), {totals['move_bytes']} bytes")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="vault root override")
    parser.add_argument("--state", help="state directory override")
    parser.add_argument("--apply", action="store_true",
                        help="move archives and run one index update")
    args = parser.parse_args(argv)

    cfg = {} if args.root else ia.load_config()
    root = os.path.abspath(args.root or cfg["vault_root"])
    state = os.path.abspath(args.state) if args.state else ia.state_dir()
    try:
        cv._root_identity(root)
        scanned = ia.scan(root, strict=True)
        with open(os.path.join(state, "assets.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        plan = plan_organize(root, data, scanned)
        if not args.apply:
            print(render_plan(plan))
            return 0
        snapshot = capture_snapshot(root, plan, scanned)
        report = apply_organize(root, state, plan, snapshot, bool(args.root))
        print(render_plan(plan, apply=True))
        print(f"applied: {report['moves_applied']} move(s), "
              f"{len(report['skipped_collisions'])} collision skip(s), "
              f"{len(report['removed_dirs'])} empty director(ies) removed")
        return 0
    except (KeyError, OSError, SafetyError, json.JSONDecodeError) as exc:
        print(f"organize aborted: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
