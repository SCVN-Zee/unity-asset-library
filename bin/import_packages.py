#!/usr/bin/env python3
"""Import indexed .unitypackage files, in order, directly into a Unity project.

Commands: projects; inspect --project PATH; run --request REQUEST.json
Request: {id, repo, root, project, packages: [{asset_key, file}],
          overwrite?: bool (default false), cancel_file?}
`<root>/.data/assets.json` is the authoritative index. No Unity Editor, unity
CLI, bridge or runner takes part in `run`: files are written straight to
Assets/ while preserving GUIDs. Individual files are atomic; Unity refreshes separately.
Output: UL_PROGRESS JSON lines followed by one terminal JSON line. Exit 0 means
completed/cancelled, 1 package failure, 2 preflight failure. Creating cancel_file
or sending SIGINT/SIGTERM stops after the current package, never mid-import.
Preparation copies at most three archives ahead into local OS temp storage.
"""
import argparse
import fcntl
import hashlib
import json
import multiprocessing
import os
import shutil
import signal
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time

import cleanup_versions as cv
import index_assets as ia
from progress import json_progress
from package_install import install_package

STAGE_WINDOW = 3
STAGE_CHUNK = 1 << 20
MAX_PACKAGES = 1000
_stop_requested = False


class RequestError(Exception):
    pass


def _norm(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise RequestError("Expected a nonempty filesystem path")
    return os.path.realpath(os.path.abspath(value))


def _inside(child, parent):
    return child.startswith(parent + os.sep)


def unity_bin():
    found = shutil.which("unity") or os.path.expanduser("~/.unity/bin/unity")
    if not os.path.isfile(found) or not os.access(found, os.X_OK):
        raise RequestError("Install unity CLI: it was not found on PATH or ~/.unity/bin/unity")
    return found


def run_unity_json(args):
    proc = subprocess.run([unity_bin(), "--non-interactive"] + args,
                          capture_output=True, text=True, timeout=120)
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise RequestError("unity returned unreadable output: " + (proc.stderr or proc.stdout)[-1000:])
    if proc.returncode or not isinstance(result, dict) or result.get("success") is not True:
        raise RequestError("unity command failed: " + json.dumps(result.get("errors", result) if isinstance(result, dict) else result)[-1000:])
    return result.get("data")


def list_projects():
    data = run_unity_json(["projects", "list", "--json"])
    if not isinstance(data, list):
        raise RequestError("unity projects returned an unsupported response")
    return [{"path": _norm(p["path"]), "title": p.get("title") or os.path.basename(p["path"]),
             "version": p.get("version") or ""} for p in data
            if isinstance(p, dict) and isinstance(p.get("path"), str) and p["path"]]


def project_version(project):
    if not os.path.isdir(os.path.join(project, "Assets")):
        raise RequestError("Not a Unity project: missing Assets directory")
    try:
        with open(os.path.join(project, "ProjectSettings", "ProjectVersion.txt"), encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("m_EditorVersion:") and line.split(":", 1)[1].strip():
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    raise RequestError("Not a Unity project: missing Editor version")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _exclusive_lock(path):
    stream = open(path, "a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RequestError("Another import or library operation is already running")
    return stream  # Closing releases the flock, including exception paths.


def _per_project_lock(project):
    key = hashlib.sha256(_norm(project).encode()).hexdigest()[:20]
    return _exclusive_lock(os.path.join(tempfile.gettempdir(), "ual-import-" + key + ".lock"))


def _library_lock(root):
    # Same identity and filename as cleanup_versions._cleanup_lock and server.engine_lock_attempt.
    key = hashlib.sha256(str(cv._root_identity(root)).encode()).hexdigest()[:20]
    return _exclusive_lock(os.path.join(tempfile.gettempdir(), "unity-asset-cleanup-" + key + ".lock"))


def project_info(path):
    project = _norm(path)
    return {"path": project, "title": os.path.basename(project), "version": project_version(project)}


def _archive(root, relative):
    if not isinstance(relative, str) or not relative or "\0" in relative or os.path.isabs(relative):
        raise RequestError("Package paths must be library-relative")
    full = _norm(os.path.join(root, relative))
    if not relative.lower().endswith(".unitypackage") or not _inside(full, root) or not os.path.isfile(full):
        raise RequestError("Package is missing, outside the library, or not a .unitypackage: " + relative)
    return full


def validate_request(request):
    if not isinstance(request, dict):
        raise RequestError("Import request must be a JSON object")
    if set(request) - {"id", "repo", "root", "project", "packages", "overwrite", "cancel_file"}:
        raise RequestError("Unsupported import request fields")
    for key in ("id", "repo", "root", "project"):
        if not isinstance(request.get(key), str) or not request[key].strip() or "\0" in request[key]:
            raise RequestError("Request requires a nonempty " + key)
    overwrite = request.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise RequestError("overwrite must be a boolean")
    rows = request.get("packages")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PACKAGES:
        raise RequestError("Select between 1 and 1000 packages")
    fields = {"id": request["id"], "overwrite": overwrite}
    fields.update({key: _norm(request[key]) for key in ("repo", "root", "project")})
    fields["cancel_file"] = _norm(request["cancel_file"]) if request.get("cancel_file") is not None else None
    fields["packages"] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"asset_key", "file"} or not isinstance(row.get("asset_key"), str) or not row["asset_key"].strip():
            raise RequestError("Each package requires an asset_key and file")
        full = _archive(fields["root"], row.get("file"))
        if full in seen:
            raise RequestError("The same archive is selected more than once")
        seen.add(full)
        fields["packages"].append({"asset_key": row["asset_key"], "file": os.path.normpath(row["file"])})
    return fields


def _load_index_members(state_dir):
    data = _read_json(os.path.join(state_dir, "assets.json"))
    if not data or not isinstance(data.get("assets"), list):
        raise RequestError("Library index is missing or unreadable; resync the library")
    return {(asset["asset_key"], os.path.normpath(version["file"]))
            for asset in data["assets"] if isinstance(asset, dict) and isinstance(asset.get("asset_key"), str)
            for version in (asset.get("versions") or [])
            if isinstance(version, dict) and isinstance(version.get("file"), str)}


def _cancelled(fields):
    return _stop_requested or bool(fields.get("cancel_file") and os.path.exists(fields["cancel_file"]))


def _staged_name(index, relative):
    stem = os.path.basename(relative)[:-len(".unitypackage")]
    stem = "".join(c if c.isalnum() or c in "._-" else "_" for c in stem) or "package"
    return "%04d-%s.unitypackage" % (index, stem)


PREP_TERMINAL = ("ready", "failed")
_emit_lock = threading.Lock()


def _progress(event):
    # json_progress from multiple threads: serialize so UL_PROGRESS lines never interleave.
    with _emit_lock:
        json_progress(event)


def _emit(fields, results, completed, current):
    _progress({"stage": "installing", "completed": completed, "total": len(results),
               "current_item": current, "results": results,
               "counts": {status: sum(row["status"] == status for row in results)
                          for status in ("pending", "preparing", "downloading", "ready",
                                         "installing", "installed", "failed", "cancelled")}})


def _stage_copy(src, dest, result):
    """Runs in a child process so cancellation can terminate a hung provider read.
    Identity is taken from the open handle (fstat) and re-checked on the handle and
    the path afterwards, so a source swapped for a same-size/same-mtime file mid
    copy is rejected. O_NOFOLLOW refuses a symlink swap outside the library."""
    ok, error, total = False, "copy worker died", None
    part = dest + ".part"
    src_fd = None
    try:
        src_fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        st0 = os.fstat(src_fd)
        total = st0.st_size
        with open(part, "wb") as out:
            while True:
                chunk = os.read(src_fd, STAGE_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        st1 = os.fstat(src_fd)
        stp = os.stat(src)
        got = os.path.getsize(part)
        identity0 = (st0.st_dev, st0.st_ino, st0.st_size, st0.st_mtime_ns)
        if identity0 != (st1.st_dev, st1.st_ino, st1.st_size, st1.st_mtime_ns) or \
                (stp.st_dev, stp.st_ino) != (st0.st_dev, st0.st_ino) or \
                stp.st_size != st0.st_size or stp.st_mtime_ns != st0.st_mtime_ns:
            error = "source changed during copy"
        elif got != st0.st_size:
            error = "short copy: %d of %d bytes" % (got, st0.st_size)
        else:
            os.replace(part, dest)
            ok, error = True, None
    except Exception as exc:
        error = str(exc)
    finally:
        if src_fd is not None:
            os.close(src_fd)
        try:
            os.unlink(part)
        except OSError:
            pass
    try:
        ia.write_atomic(result, json.dumps({"ok": ok, "error": error, "bytes_total": total}))
    except OSError:
        pass


class _Preparation:
    """Bounded concurrent staging: at most STAGE_WINDOW packages staged ahead of
    the install cursor, each copy in a
    child process so cancellation terminates it without hanging. After the first
    failure no new staging starts, but copies already in flight still complete so
    an earlier package can never be blocked by a later failed one."""

    def __init__(self, fields, rows, staging):
        self.fields = fields
        self.rows = rows
        self.staging = staging
        self.lock = threading.Lock()
        self.tick = threading.Event()
        self.stop_evt = threading.Event()
        self.spawn_stopped = False
        self.procs = {}
        self.cursor = 0
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def staged_path(self, i):
        return os.path.join(self.staging, _staged_name(i, self.rows[i]["file"]))

    def error(self, i):
        return self.rows[i].get("error")

    def consume(self, i):
        """Advance the window past an installed package and drop its staged copy."""
        with self.lock:
            self.cursor = max(self.cursor, i + 1)
            try:
                os.unlink(self.staged_path(i))
            except OSError:
                pass

    def await_ready(self, i):
        """Block until package i is ready/failed; user cancel aborts the pipeline.
        Runs on the importing thread, so byte progress is emitted from here."""
        last = None
        while True:
            with self.lock:
                status = self.rows[i]["status"]
                row = dict(self.rows[i])
            if row["bytes_completed"] != last:
                last = row["bytes_completed"]
                _emit(self.fields, self.rows, sum(r["status"] in ("installed", "failed") for r in self.rows), None)
            if status in PREP_TERMINAL:
                return status
            if _cancelled(self.fields):
                self.stop(cancelled=True)
                with self.lock:
                    if self.rows[i]["status"] not in PREP_TERMINAL:
                        self.rows[i]["status"] = "cancelled"
                        self.rows[i]["error"] = "cancelled during preparation"
                return "cancelled"
            self.tick.wait(0.1)

    def stop(self, cancelled=False):
        self.stop_evt.set()
        self.tick.set()
        with self.lock:
            for j, (proc, dest, result) in list(self.procs.items()):
                try:
                    if proc.is_alive():
                        proc.terminate()
                    proc.join(timeout=5)
                    if proc.is_alive():
                        proc.kill()
                        proc.join(timeout=5)
                except Exception:
                    pass
                for path in (dest, dest + ".part"):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                if self.rows[j]["status"] in ("pending", "preparing", "downloading"):
                    self.rows[j]["status"] = "cancelled"
                    self.rows[j]["error"] = ("cancelled during preparation" if cancelled
                                             else "stopped after an earlier failure")
            self.procs.clear()
        if self.thread is not None:
            self.thread.join()

    def _spawn(self, j):
        # Called under self.lock from _run.
        try:
            src = _archive(self.fields["root"], self.rows[j]["file"])
            dest = self.staged_path(j)
            info = os.stat(src)
            cloud = bool(getattr(info, "st_flags", 0) & getattr(stat_module, "SF_DATALESS", 0x40000000))
            self.rows[j].update(status="downloading" if cloud else "preparing", bytes_completed=0, bytes_total=info.st_size)
            proc = multiprocessing.Process(target=_stage_copy, args=(src, dest, dest + ".result.json"), daemon=True)
            self.procs[j] = (proc, dest, dest + ".result.json")
            proc.start()
        except Exception as exc:
            self.procs.pop(j, None)
            self._adopt(j, {"ok": False, "error": str(exc), "bytes_total": None})

    def _adopt(self, j, receipt):
        # Called under self.lock from _run/_spawn. Only pre-install states may
        # transition here: once installing/installed the staged copy is already
        # consumed and a late staging outcome must not rewrite history.
        row = self.rows[j]
        if row["status"] not in ("pending", "preparing", "downloading"):
            return
        if receipt.get("ok"):
            row.update(status="ready", bytes_completed=receipt.get("bytes_total"), bytes_total=receipt.get("bytes_total"), error=None)
        else:
            row.update(status="failed", error=receipt.get("error") or "staging failed")
            self.spawn_stopped = True  # ordered imports stop on the first failure

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                with self.lock:
                    if not self.spawn_stopped:
                        for j in range(self.cursor, min(self.cursor + STAGE_WINDOW, len(self.rows))):
                            if self.rows[j]["status"] == "pending" and j not in self.procs:
                                self._spawn(j)
                                if self.spawn_stopped:
                                    break
                    for j, (proc, dest, result) in list(self.procs.items()):
                        receipt = _read_json(result)
                        if receipt is not None:
                            try:
                                proc.join(timeout=5)
                            except Exception:
                                pass
                            del self.procs[j]
                            self._adopt(j, receipt)
                            continue
                        alive = proc.is_alive()
                        try:
                            size = os.path.getsize(dest + ".part") if os.path.exists(dest + ".part") else \
                                (os.path.getsize(dest) if os.path.exists(dest) else 0)
                        except OSError:
                            size = 0
                        row = self.rows[j]
                        if row["status"] in ("pending", "preparing", "downloading"):
                            row["bytes_completed"] = size
                        if not alive:
                            # Dead child without a result: never leave await_ready hanging.
                            del self.procs[j]
                            self._adopt(j, {"ok": False, "error": "copy worker died without a result"})
            except Exception as exc:  # the scheduler must never die silently
                with self.lock:
                    for j, (proc, dest, result) in list(self.procs.items()):
                        if self.rows[j]["status"] not in PREP_TERMINAL and _read_json(result) is None:
                            try:
                                if proc.is_alive():
                                    proc.terminate()
                                proc.join(timeout=5)
                                if proc.is_alive():
                                    proc.kill()
                                    proc.join(timeout=5)
                            except Exception:
                                pass
                            del self.procs[j]
                            self._adopt(j, {"ok": False, "error": "staging scheduler failed: %s" % exc})
            _emit(self.fields, self.rows, sum(r["status"] in ("installed", "failed") for r in self.rows), None)
            self.tick.set()
            self.tick.clear()
            self.tick.wait(0.25)


def run_import(fields):
    results = [{"file": row["file"], "status": "pending", "bytes_completed": 0, "bytes_total": None}
               for row in fields["packages"]]
    current = None
    completed = 0
    project = fields["project"]
    project_version(project)
    state_dir = ia.data_dir(fields["root"])
    with ia.state_write_lock(state_dir, "import", blocking=False), \
            _per_project_lock(project), _library_lock(fields["root"]):
        members = _load_index_members(state_dir)
        for row in fields["packages"]:
            if (row["asset_key"], row["file"]) not in members:
                raise RequestError("Archive no longer matches the library index: " + row["file"])
        staging = tempfile.mkdtemp(prefix="ual-staging-")
        prep = _Preparation(fields, results, staging)
        prep.start()
        try:
            for i, row in enumerate(fields["packages"]):
                if _cancelled(fields):
                    break
                status = prep.await_ready(i)
                if status != "ready" or _cancelled(fields):
                    break
                current = row["file"]
                results[i]["status"] = "installing"
                _emit(fields, results, completed, current)
                parse_dir = tempfile.mkdtemp(prefix="ual-parse-", dir=staging)
                try:
                    backups = install_package(project, prep.staged_path(i), fields["overwrite"], parse_dir)
                    results[i]["status"] = "installed"
                    results[i]["bytes_completed"] = results[i]["bytes_total"]
                    if backups:
                        results[i]["backup_path"] = backups
                    completed += 1
                    prep.consume(i)  # installed: the staged copy may go
                except Exception as error:
                    results[i]["status"] = "failed"
                    results[i]["error"] = str(error)
                    if getattr(error, "backup_path", None):
                        results[i]["backup_path"] = error.backup_path
                finally:
                    shutil.rmtree(parse_dir, ignore_errors=True)
                _emit(fields, results, completed, current)
                if results[i]["status"] == "failed":
                    break
        finally:
            prep.stop()
            shutil.rmtree(staging, ignore_errors=True)
    failed = next((row for row in results if row["status"] == "failed"), None)
    cancelled = not failed and completed < len(results) and _cancelled(fields)
    if failed or cancelled:
        for row in results:
            if row["status"] in ("pending", "preparing", "downloading", "ready", "installing"):
                row["status"] = "cancelled"
    return {"status": "failed" if failed else "cancelled" if cancelled else "completed",
            "completed": completed, "total": len(results), "current_item": current,
            "results": results, "error": failed["error"] if failed else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("projects", help="List Hub projects as JSON")
    commands.add_parser("inspect").add_argument("--project", required=True)
    commands.add_parser("run").add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "projects":
            result = list_projects()
        elif args.command == "inspect":
            result = project_info(args.project)
        else:
            with open(args.request, encoding="utf-8") as stream:
                fields = validate_request(json.load(stream))
            result = run_import(fields)
        print(json.dumps(result), flush=True)
        return 1 if isinstance(result, dict) and result.get("status") == "failed" else 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error), "results": []}), flush=True)
        return 2


if __name__ == "__main__":
    def request_stop(signum, frame):
        global _stop_requested
        _stop_requested = True
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)
    sys.exit(main())
