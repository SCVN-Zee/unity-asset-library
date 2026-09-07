#!/usr/bin/env python3
"""Report or remove superseded Unity Asset Store archive versions.

Dry-run is the default. Cleanup uses the existing offline scanner and parser, so
archive contents are never opened and OneDrive placeholders are not hydrated.
"""

import argparse
import fcntl
import hashlib
import os
import stat
import sys
import tempfile
from collections import defaultdict
from contextlib import contextmanager, nullcontext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from progress import emit_progress
import index_assets as ia  # noqa: E402


class SafetyError(RuntimeError):
    """Raised when the vault or a planned candidate changed unexpectedly."""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result

def _parsed_records(scanned):
    records = []
    for item in scanned:
        parsed = ia.parse(item["rel_path"])
        parsed.update(item)
        records.append(parsed)
    return records


def _rank(record):
    return (ia.version_key(record["version"], record["prerelease"]), record["release_date"] or "")


def plan_cleanup(scanned):
    """Return a deterministic, fail-closed cleanup plan from current scan rows."""
    families = defaultdict(list)
    for record in _parsed_records(scanned):
        families[ia.asset_key(record)].append(record)

    result = {"families": [], "removals": []}
    for key in sorted(families):
        members = sorted(families[key], key=lambda r: r["rel_path"])
        family = {"asset_key": key, "members": members, "survivor": None,
                  "removals": [], "reason": None}
        if len(members) < 2:
            family["reason"] = "single-archive"
        elif any(not member["version"] for member in members):
            family["reason"] = "unparseable-version"
        else:
            maximum = max(_rank(member) for member in members)
            winners = [member for member in members if _rank(member) == maximum]
            family["survivor"] = winners[0] if len(winners) == 1 else None
            if len(winners) > 1:
                family["reason"] = "maximum-tie"
            else:
                family["reason"] = "superseded"
                family["removals"] = [member for member in members if member is not winners[0]]
                result["removals"].extend(family["removals"])
        result["families"].append(family)

    result["removals"].sort(key=lambda r: r["rel_path"])
    result["candidate_bytes"] = sum(r["size"] for r in result["removals"])
    result["candidate_files"] = len(result["removals"])
    result["candidate_families"] = sum(bool(f["removals"]) for f in result["families"])
    return result


def _display_version(record):
    return record["version"] or "?"


def render_plan(plan, apply=False):
    lines = ["cleanup: apply" if apply else "cleanup: dry-run"]
    for family in plan["families"]:
        if family["removals"]:
            survivor = family["survivor"]
            lines.append(f"family {family['asset_key']} ({family['reason']})")
            lines.append(f"  keep {_display_version(survivor)} {survivor['release_date'] or '-'} {survivor['rel_path']}")
            for candidate in family["removals"]:
                lines.append(f"  remove {_display_version(candidate)} {candidate['release_date'] or '-'} {candidate['size']} {candidate['rel_path']}")
        elif len(family["members"]) > 1:
            lines.append(f"protected {family['asset_key']} ({family['reason']})")
            for member in family["members"]:
                lines.append(f"  keep {_display_version(member)} {member['release_date'] or '-'} {member['rel_path']}")
    lines.append(f"summary: {plan['candidate_files']} files, {plan['candidate_families']} families, {plan['candidate_bytes']} bytes")
    return "\n".join(lines)


def _manifest(scanned):
    return tuple(sorted((row["rel_path"], row["size"]) for row in scanned))


def _root_identity(root):
    try:
        st = os.lstat(root)
    except OSError as exc:
        raise SafetyError(f"vault root unavailable: {root}") from exc
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise SafetyError(f"vault root is not a real directory: {root}")
    return st.st_dev, st.st_ino


def _candidate_path(root, rel_path):
    parts = rel_path.replace("\\", "/").split("/")
    if os.path.isabs(rel_path) or rel_path in ("", ".") or ".." in parts:
        raise SafetyError(f"unsafe candidate path: {rel_path!r}")
    root = os.path.realpath(os.path.abspath(root))
    path = os.path.abspath(os.path.join(root, rel_path))
    try:
        if os.path.commonpath((root, path)) != root or os.path.commonpath((root, os.path.realpath(path))) != root:
            raise SafetyError(f"candidate escapes vault: {rel_path}")
    except ValueError as exc:
        raise SafetyError(f"candidate escapes vault: {rel_path}") from exc
    if not path.lower().endswith(ia.ARCHIVE_EXTS):
        raise SafetyError(f"unsupported candidate suffix: {rel_path}")
    return path


def validate_candidate(root, rel_path, expected):
    """Validate one relative archive and return its current lstat identity."""
    path = _candidate_path(root, rel_path)
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise SafetyError(f"candidate unavailable: {rel_path}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise SafetyError(f"candidate is not a regular file: {rel_path}")
    current = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if expected and current != expected:
        raise SafetyError(f"candidate changed: {rel_path}")
    return current


def capture_snapshot(root, scanned, progress=None):
    """Capture root, manifest, and original archive identities before planning."""
    snapshot = {"root": _root_identity(root), "manifest": _manifest(scanned), "identities": {}}
    for i, row in enumerate(scanned):
        emit_progress(progress, "validate", i, len(scanned), row["rel_path"], examined=i)
        snapshot["identities"][row["rel_path"]] = validate_candidate(root, row["rel_path"], {})
    emit_progress(progress, "validate", len(scanned), len(scanned), None, examined=len(scanned))
    return snapshot


def preflight(root, scanned, snapshot, progress=None):
    """Require unchanged root/manifest and validate every original archive identity."""
    if _root_identity(root) != snapshot["root"]:
        raise SafetyError("vault root changed before cleanup")
    if _manifest(ia.scan(root, strict=True, progress=progress)) != snapshot["manifest"]:
        raise SafetyError("vault archive manifest changed before cleanup")
    for i, row in enumerate(scanned):
        emit_progress(progress, "validate", i, len(scanned), row["rel_path"], examined=i)
        validate_candidate(root, row["rel_path"], snapshot["identities"][row["rel_path"]])
    emit_progress(progress, "validate", len(scanned), len(scanned), None, examined=len(scanned))


def _update_args(state, root, root_override):
    argv = ["update"]
    if root_override:
        argv.extend(("--root", root))
    if state:
        argv.extend(("--state", state))
    return argv


@contextmanager
def _cleanup_lock(root):
    """Serialize cooperating cleanup processes without creating a vault file."""
    name = hashlib.sha256(f"{_root_identity(root)}".encode()).hexdigest()[:20]
    # Kept across the Unity Asset Library rename: old and new engines must
    # contend on the same lock file.
    path = os.path.join(tempfile.gettempdir(), f"unity-asset-cleanup-{name}.lock")
    with open(path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _rollback_staged(root, staged, progress=None, stats=None):
    errors = []
    restored = 0
    for i, (rel_path, staged_path) in enumerate(reversed(staged)):
        emit_progress(progress, "rollback", i, len(staged), rel_path,
                      rolled_back=restored, rollback_failed=len(errors))
        if not os.path.lexists(staged_path):
            continue
        source = _candidate_path(root, rel_path)
        if os.path.lexists(source):
            errors.append(f"cannot restore {rel_path}: destination exists")
            continue
        try:
            os.makedirs(os.path.dirname(source), exist_ok=True)
            os.replace(staged_path, source)
            restored += 1
        except OSError as exc:
            errors.append(f"cannot restore {rel_path}: {exc}")
    if stats is not None:
        stats["rolled_back"] = restored
    emit_progress(progress, "rollback", len(staged), len(staged), None,
                  rolled_back=restored, rollback_failed=len(errors))
    return errors


def _reconcile(root, state, root_override, expected_root, progress=None, state_lock_held=False):
    if _root_identity(root) != expected_root:
        raise SafetyError("vault root changed during reconciliation")
    args = _update_args(state, root, root_override)
    emit_progress(progress, "refresh", 0, 1, None)
    kwargs = {}
    if progress is not None:
        kwargs["progress"] = progress
    if state_lock_held:
        kwargs["state_lock_held"] = True
    result = ia.main(args, **kwargs) if kwargs else ia.main(args)
    emit_progress(progress, "refresh", 1, 1, None)
    return result


def _remove_empty_tree(path):
    for directory, subdirectories, files in os.walk(path, topdown=False):
        if files:
            raise OSError("staging tree contains unexpected files")
        for subdirectory in subdirectories:
            os.rmdir(os.path.join(directory, subdirectory))
    os.rmdir(path)


def apply_cleanup(root, state, scanned, plan, root_override=False, snapshot=None,
                  lock_already_held=False, progress=None, state_lock_held=False):
    snapshot = snapshot or capture_snapshot(root, scanned, progress=progress)
    candidate_paths = {row["rel_path"] for row in plan["removals"]}
    lock = nullcontext() if lock_already_held else _cleanup_lock(root)
    with lock:
        print(render_plan(plan, apply=True))
        preflight(root, scanned, snapshot, progress=progress)
        quarantine = os.path.join(root, "_Quarantine")
        if os.path.lexists(quarantine):
            st = os.lstat(quarantine)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise SafetyError("cleanup quarantine is not a real directory")
        else:
            os.makedirs(quarantine)
        stage_root = tempfile.mkdtemp(prefix=".cleanup-", dir=quarantine)
        staged = []
        moved = False
        removed = 0
        state_refreshed = False
        destructive_started = False
        failure = None
        update_result = 0
        counts = {"staged": 0, "removed": 0, "rolled_back": 0, "rollback_failed": 0}
        try:
            for i, candidate in enumerate(plan["removals"]):
                rel_path = candidate["rel_path"]
                emit_progress(progress, "stage", i, len(plan["removals"]), rel_path, **counts)
                validate_candidate(root, rel_path, snapshot["identities"][rel_path])
                staged_path = os.path.join(stage_root, rel_path)
                os.makedirs(os.path.dirname(staged_path), exist_ok=True)
                staged.append((rel_path, staged_path))
                moved = True
                os.replace(_candidate_path(root, rel_path), staged_path)
                validate_candidate(stage_root, rel_path, snapshot["identities"][rel_path])
                counts["staged"] += 1
                emit_progress(progress, "stage", counts["staged"], len(plan["removals"]), rel_path, **counts)

            expected_manifest = tuple(sorted((row["rel_path"], row["size"])
                                             for row in scanned if row["rel_path"] not in candidate_paths))
            if _manifest(ia.scan(root, strict=True, progress=progress)) != expected_manifest:
                raise SafetyError("vault manifest changed while staging cleanup")
            for rel_path, staged_path in staged:
                validate_candidate(stage_root, rel_path, snapshot["identities"][rel_path])
            for row in scanned:
                if row["rel_path"] not in candidate_paths:
                    validate_candidate(root, row["rel_path"], snapshot["identities"][row["rel_path"]])

            try:
                for i, (rel_path, staged_path) in enumerate(staged):
                    emit_progress(progress, "remove", i, len(staged), rel_path, **counts)
                    destructive_started = True
                    os.unlink(staged_path)
                    removed += 1
                    counts["removed"] = removed
                    emit_progress(progress, "remove", removed, len(staged), rel_path, **counts)
                    print(f"removed: {rel_path}")
            except BaseException as exc:
                failure = exc
        except BaseException as exc:
            failure = exc
        finally:
            rollback_errors = []
            if failure is not None and moved:
                rollback_stats = {}
                rollback_errors = _rollback_staged(root, staged, progress=progress, stats=rollback_stats)
                counts["rolled_back"] = rollback_stats.get("rolled_back", 0)
                counts["rollback_failed"] = len(rollback_errors)
                if rollback_errors:
                    failure = SafetyError(f"{failure}; rollback failed: {'; '.join(rollback_errors)}")
                else:
                    moved = False
            if failure is not None and (destructive_started or rollback_errors):
                try:
                    update_result = _reconcile(root, state, root_override, snapshot["root"],
                                               progress=progress, state_lock_held=state_lock_held)
                    if update_result != 0:
                        raise SafetyError("index reconciliation returned nonzero status")
                    state_refreshed = True
                except BaseException as update_exc:
                    failure = SafetyError(f"{failure}; reconciliation failed: {update_exc}")
            elif failure is None:
                try:
                    if _root_identity(root) != snapshot["root"]:
                        raise SafetyError("vault root changed after cleanup")
                    emit_progress(progress, "refresh", 0, 1, None)
                    update_args = _update_args(state, root, root_override)
                    kwargs = {}
                    if progress is not None:
                        kwargs["progress"] = progress
                    if state_lock_held:
                        kwargs["state_lock_held"] = True
                    update_result = ia.main(update_args, **kwargs) if kwargs else ia.main(update_args)
                    emit_progress(progress, "refresh", 1, 1, None)
                    if update_result != 0:
                        raise SafetyError("index update returned nonzero status")
                    state_refreshed = True
                except BaseException as update_exc:
                    failure = update_exc if isinstance(update_exc, SafetyError) else SafetyError(f"index update failed: {update_exc}")
            if failure is None:
                try:
                    _remove_empty_tree(stage_root)
                except OSError as exc:
                    failure = SafetyError(f"cleanup staging residue: {exc}")
        if failure is not None:
            result = {"applied": removed > 0 or bool(rollback_errors),
                      "state_refreshed": state_refreshed,
                      "counts": dict(counts),
                      "rollback_incomplete": bool(rollback_errors),
                      "residuals": list(rollback_errors)}
            if isinstance(failure, SafetyError):
                failure.result = result
                raise failure
            raise SafetyError(f"cleanup failed: {failure}", result=result) from failure
        emit_progress(progress, "complete", 1, 1, None, **counts)
        return update_result
def main(argv=None, progress=None, state_lock_held=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="vault root override")
    parser.add_argument("--state", help="state directory override")
    parser.add_argument("--apply", action="store_true", help="remove candidates and run index update")
    args = parser.parse_args(argv)

    cfg = {} if args.root else ia.load_config()
    root = os.path.abspath(args.root or cfg["vault_root"])
    state = os.path.abspath(args.state) if args.state else ia.state_dir()
    try:
        _root_identity(root)
        scanned = ia.scan(root, strict=True, progress=progress)
        if not args.apply:
            print(render_plan(plan_cleanup(scanned)))
            return 0
        snapshot = capture_snapshot(root, scanned, progress=progress)
        plan = plan_cleanup(scanned)
        return apply_cleanup(root, state, scanned, plan, bool(args.root), snapshot,
                             progress=progress, state_lock_held=state_lock_held)
    except (KeyError, OSError, SafetyError) as exc:
        print(f"cleanup aborted: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
