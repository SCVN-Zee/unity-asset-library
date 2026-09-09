#!/usr/bin/env python3
"""Import indexed .unitypackage files, in order, into a closed or live Unity project.

Commands: projects; inspect --project PATH; install --project PATH;
          run --request REQUEST.json
Request: {id, repo, root, project, mode: "closed"|"live",
          packages: [{asset_key, file}], cancel_file?}
`<root>/.data/assets.json` is the authoritative index.
Output: UL_PROGRESS JSON lines followed by one terminal JSON line. Exit 0 means
completed/cancelled, 1 package failure, 2 preflight failure. Creating cancel_file
or sending SIGINT/SIGTERM stops after the current package, never mid-import.
Live mode requires explicit bridge installation and a loaded, idle Editor.
Preparation copies at most three archives ahead into local OS temp storage.
Closed batches use one temporary Editor runner; Unity imports remain ordered.
"""
import argparse
from datetime import datetime, timezone
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

BIN_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_SUBDIR = "UnityAssetLibraryImport"
BRIDGE_NAME = "UnityAssetLibraryImport.cs"
BRIDGE_ASMDEF = "UnityAssetLibraryImport.asmdef"
BRIDGE_VERSION = "1"
RUNNER_NAME = "UnityAssetLibraryBatchRunner.cs"
RUNNER_ASMDEF = "UnityAssetLibraryBatchRunner.asmdef"
RUNNER_SUBDIR = "UnityAssetLibraryBatch"
RUNNER_METHOD = "UnityAssetLibraryBatch.BatchRunner.Run"
STAGE_CONCURRENCY = 3
STAGE_WINDOW = 3
STAGE_CHUNK = 1 << 20
STAGE_FILE_TIMEOUT_SECONDS = 900
MAX_PACKAGES = 1000
BRIDGE_READY_TIMEOUT_SECONDS = 120
_stop_requested = False


class RequestError(Exception):
    pass


def _norm(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise RequestError("Expected a nonempty filesystem path")
    return os.path.realpath(os.path.abspath(value))


def _inside(child, parent):
    return _norm(child).startswith(_norm(parent) + os.sep)


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


def list_running():
    data = run_unity_json(["editors", "running", "--json"])
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise RequestError("Cannot determine which Unity projects are open")
    instances = []
    for row in data["instances"]:
        if not isinstance(row, dict):
            raise RequestError("Unreadable Unity process identity")
        if not row.get("projectPath"):  # An Editor without an open project.
            continue
        if not isinstance(row.get("pid"), int) or row["pid"] <= 0:
            raise RequestError("Unreadable Unity process PID")
        instances.append({"projectPath": _norm(row["projectPath"]), "pid": row["pid"],
                          "unityVersion": row.get("unityVersion") or ""})
    return instances


def editor_executable(version):
    data = run_unity_json(["editors", "--installed", "--json"])
    if not isinstance(data, list):
        raise RequestError("Cannot determine installed Unity versions")
    editor = next((row for row in data if isinstance(row, dict) and row.get("version") == version), None)
    if editor is None:
        raise RequestError("Required Unity Editor is not installed: " + version)
    location = editor.get("location")
    if not isinstance(location, str) or not os.path.isabs(location):
        raise RequestError("Installed Unity Editor location is unavailable: " + version)
    executable = os.path.join(location, "Contents", "MacOS", "Unity")
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise RequestError("Installed Unity Editor is not executable: " + executable)
    return executable


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


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def bridge_installed(project):
    for name in (BRIDGE_NAME, BRIDGE_ASMDEF):
        dest = os.path.join(project, "Assets", BRIDGE_SUBDIR, name)
        if not _inside(dest, project) or not os.path.isfile(dest):
            return False
        try:
            with open(dest, "rb") as installed, open(os.path.join(BIN_DIR, name), "rb") as template:
                if installed.read() != template.read():
                    return False
        except OSError:
            return False
    return True


def _editor_pid(project, running=None):
    matches = [row["pid"] for row in (list_running() if running is None else running)
               if row["projectPath"] == project]
    if len(matches) > 1:
        raise RequestError("Several Editor processes report this project; wait for startup/shutdown to finish")
    return matches[0] if matches else None


def bridge_ready(project, pid=None):
    pid = _editor_pid(project) if pid is None else pid
    hb = _read_json(os.path.join(project, "Library", "UALImport", "heartbeat.json"))
    if not pid or not hb or hb.get("pid") != pid or hb.get("status") != "ready" or hb.get("bridge_version") != BRIDGE_VERSION:
        return False
    try:
        updated = datetime.fromisoformat(hb["updated"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return 0 <= age < 10 and _pid_alive(pid) and bridge_installed(project)


def project_info(path, projects=None, running=None):
    project = _norm(path)
    version = project_version(project)
    if projects is None:
        try:
            projects = list_projects()
        except RequestError:
            projects = []  # Hub titles are optional; a browsed project need not be registered.
    title = next((row["title"] for row in projects if row["path"] == project), os.path.basename(project))
    pid = _editor_pid(project, running)
    return {"path": project, "title": title, "version": version, "open": pid is not None,
            "bridge_installed": bridge_installed(project), "bridge_ready": bridge_ready(project, pid) if pid else False}


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


def install_bridge(path):
    project = _norm(path)
    project_version(project)
    with _per_project_lock(project):
        dest_dir = os.path.join(project, "Assets", BRIDGE_SUBDIR)
        if not _inside(dest_dir, project):
            raise RequestError("Bridge destination escapes the project")
        contents = {}
        for name in (BRIDGE_NAME, BRIDGE_ASMDEF):
            dest = os.path.join(dest_dir, name)
            if not _inside(dest, project) or os.path.islink(dest):
                raise RequestError("Bridge destination is an unsafe symlink")
            with open(os.path.join(BIN_DIR, name), encoding="utf-8") as stream:
                contents[name] = stream.read()
            if os.path.exists(dest):
                with open(dest, encoding="utf-8") as stream:
                    if stream.read() != contents[name]:
                        raise RequestError("Refusing to overwrite a different existing file: " + dest)
        os.makedirs(dest_dir, exist_ok=True)
        for name, content in contents.items():
            dest = os.path.join(dest_dir, name)
            if not os.path.exists(dest):
                ia.write_atomic(dest, content)
    return project_info(project)


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
    for key in ("id", "repo", "root", "project", "mode"):
        if not isinstance(request.get(key), str) or not request[key].strip() or "\0" in request[key]:
            raise RequestError("Request requires a nonempty " + key)
    if request["mode"] not in ("closed", "live"):
        raise RequestError("Import mode must be closed or live")
    rows = request.get("packages")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PACKAGES:
        raise RequestError("Select between 1 and 1000 packages")
    fields = {key: request[key] for key in ("id", "mode")}
    fields.update({key: _norm(request[key]) for key in ("repo", "root", "project")})
    fields["cancel_file"] = _norm(request["cancel_file"]) if request.get("cancel_file") is not None else None
    fields["packages"] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("asset_key"), str) or not row["asset_key"].strip():
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
    _progress({"stage": "importing", "completed": completed, "total": len(results),
               "current_item": current, "results": results,
               "counts": {status: sum(row["status"] == status for row in results)
                          for status in ("pending", "preparing", "downloading", "ready",
                                         "importing", "imported", "failed", "cancelled")}})


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
    the import cursor, at most STAGE_CONCURRENCY copies in flight, each copy in a
    child process so cancellation terminates it without hanging. After the first
    failure no new staging starts, but copies already in flight still complete so
    an earlier package can never be blocked by a later failed one."""

    def __init__(self, fields, rows, staging):
        self.fields = fields
        self.rows = rows
        self.staging = staging
        self.mailbox = None      # receipts dir; failure receipts let the runner adopt them
        self.heartbeat = None    # touched while staging is active so the runner never
                                 # mistakes a slow preparation for a dead host
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
        """Advance the window past an imported package and drop its staged copy."""
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
                _emit(self.fields, self.rows, sum(r["status"] in ("imported", "failed") for r in self.rows), None)
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
                    # Never touch importing/imported: the import phase may already
                    # have consumed the staged copy and confirmed a receipt.
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
        # Called under self.lock from _run/_spawn. Only pre-import states may
        # transition here: once Unity is importing/imported the staged copy is
        # already consumed and a late staging outcome must not rewrite history.
        row = self.rows[j]
        if row["status"] not in ("pending", "preparing", "downloading"):
            return
        if receipt.get("ok"):
            row.update(status="ready", bytes_completed=receipt.get("bytes_total"), bytes_total=receipt.get("bytes_total"), error=None)
        else:
            row.update(status="failed", error=receipt.get("error") or "staging failed")
            self.spawn_stopped = True  # ordered imports stop on the first failure
            if self.mailbox:
                try:
                    ia.write_atomic(os.path.join(self.mailbox, "%d.json" % j),
                                    json.dumps({"index": j, "status": "failed", "error": row["error"]}))
                except OSError:
                    pass

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
                if self.heartbeat:
                    try:
                        ia.write_atomic(self.heartbeat, json.dumps({"updated": time.time()}))
                    except OSError:
                        pass
            except Exception as exc:  # the scheduler must never die silently
                with self.lock:
                    for j, (proc, dest, result) in list(self.procs.items()):
                        if self.rows[j]["status"] not in PREP_TERMINAL and _read_json(result) is None:
                            # Only fail children with no pending result; one that did
                            # finish is left for the normal adopt pass so a receipt
                            # never flips a failed row back to ready.
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
            _emit(self.fields, self.rows, sum(r["status"] in ("imported", "failed") for r in self.rows), None)
            self.tick.set()
            self.tick.clear()
            self.tick.wait(0.25)


def _deploy_runner(project):
    """Deploy the temporary runner without taking ownership of an existing folder."""
    dest_dir = os.path.join(project, "Assets", RUNNER_SUBDIR)
    if not _inside(dest_dir, project) or os.path.islink(dest_dir):
        raise RequestError("Runner destination escapes the project")
    if os.path.exists(dest_dir):
        raise RequestError("A %s directory already exists in the project; remove or rename it first" % RUNNER_SUBDIR)
    os.makedirs(dest_dir)
    try:
        for name in (RUNNER_NAME, RUNNER_ASMDEF):
            with open(os.path.join(BIN_DIR, name), encoding="utf-8") as stream:
                ia.write_atomic(os.path.join(dest_dir, name), stream.read())
    except Exception:
        _remove_runner(project)
        raise


def _remove_runner(project):
    """Delete only the exact files this tool deployed; never a user file that may
    have appeared alongside them."""
    dest_dir = os.path.join(project, "Assets", RUNNER_SUBDIR)
    for name in (RUNNER_NAME, RUNNER_ASMDEF):
        for path in (os.path.join(dest_dir, name), os.path.join(dest_dir, name + ".meta")):
            try:
                os.unlink(path)
            except OSError:
                pass
    removed = False
    try:
        os.rmdir(dest_dir)  # only succeeds when nothing user-owned remains
        removed = True
    except OSError:
        pass
    if removed:
        # Preserve the folder .meta (and its GUID) whenever user files keep the
        # directory alive; only clean the meta for a folder we fully removed.
        try:
            os.unlink(dest_dir + ".meta")
        except OSError:
            pass


def _prepare_batch_mailbox(project):
    mailbox = os.path.join(project, "Library", "UALImport")
    if not _inside(mailbox, project):
        raise RequestError("Bridge mailbox escapes the project")
    receipts = os.path.join(mailbox, "batch-receipts")
    shutil.rmtree(receipts, ignore_errors=True)
    os.makedirs(receipts, exist_ok=True)
    for name in ("batch.json", "batch-result.json", "batch-active.json", "batch.stop", "batch-staging.json"):
        try:
            os.unlink(os.path.join(mailbox, name))
        except OSError:
            pass
    return mailbox, receipts


def run_closed_batch(project, fields, results, staged, prep, editor):
    """Run one Editor without -quit; completion callbacks own the safe exit."""
    _deploy_runner(project)
    proc = None
    seen = 0
    exit_detail = ""
    try:
        mailbox, receipts = _prepare_batch_mailbox(project)
        prep.mailbox = receipts
        prep.heartbeat = os.path.join(mailbox, "batch-staging.json")
        ia.write_atomic(os.path.join(mailbox, "batch.json"), json.dumps({
            "id": fields["id"], "stop": os.path.join(mailbox, "batch.stop"),
            "stagingHeartbeat": prep.heartbeat, "stagingHeartbeatMaxAgeSeconds": 60,
            "timeoutSeconds": STAGE_FILE_TIMEOUT_SECONDS,
            "packages": [{"index": i, "package": path} for i, path in enumerate(staged)]}))
        prep.start()
        stop_sent = False
        with tempfile.TemporaryDirectory(prefix="ual-import-log-") as logs:
            log_path = os.path.join(logs, "unity.log")
            # `unity run` adds -quit, which exits before asynchronous imports finish.
            # Run the discovered Editor directly, isolated from terminal signals.
            # Only the runner exits it, at a confirmed safe package boundary.
            proc = subprocess.Popen([editor, "-batchmode", "-nographics", "-projectPath", project,
                                     "-logFile", log_path, "-executeMethod", RUNNER_METHOD],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            while proc.poll() is None:
                if not stop_sent and _cancelled(fields):
                    ia.write_atomic(os.path.join(mailbox, "batch.stop"), "")
                    stop_sent = True
                seen = _adopt_receipts(fields, results, receipts, seen, prep)
                time.sleep(0.5)
            seen = _adopt_receipts(fields, results, receipts, seen, prep)
            if seen < len(results):
                try:
                    with open(log_path, "rb") as stream:
                        stream.seek(0, os.SEEK_END)
                        stream.seek(max(0, stream.tell() - 4000))
                        exit_detail = stream.read().decode("utf-8", errors="replace").strip()
                except OSError:
                    pass
    finally:
        if proc is not None and proc.poll() is None:
            # Host errors must not remove files under an active Unity import.
            try:
                ia.write_atomic(os.path.join(mailbox, "batch.stop"), "")
            except OSError:
                pass
            proc.wait()
        _remove_runner(project)
    any_failed = any(row["status"] == "failed" for row in results[:seen])
    for row in results[seen:]:
        if row["status"] in ("pending", "preparing", "downloading", "ready", "importing"):
            if _cancelled(fields) or any_failed:
                row.update(status="cancelled", error=None)
            else:
                error = "Unity exited before this package's import was confirmed"
                row.update(status="failed", error=error + ("\n" + exit_detail if exit_detail else ""))
                any_failed = True
    return seen


def _adopt_receipts(fields, results, receipts, seen, prep):
    adopted = seen
    for i in range(seen, len(results)):
        receipt = _read_json(os.path.join(receipts, "%d.json" % i))
        if receipt is None:
            if i < len(results) and results[i]["status"] == "ready":
                results[i]["status"] = "importing"
                _emit(fields, results, sum(r["status"] in ("imported", "failed") for r in results), results[i]["file"])
            break
        row = results[i]
        row["status"] = receipt.get("status") or "failed"
        if row["status"] == "imported":
            row["bytes_completed"] = row["bytes_total"]
        if receipt.get("error"):
            row["error"] = receipt["error"]
        prep.consume(i)  # receipt confirmed: the staged copy may go
        adopted = i + 1
    if adopted > seen:
        _emit(fields, results, sum(r["status"] in ("imported", "failed") for r in results),
              results[min(adopted, len(results)) - 1]["file"])
    return adopted



def _wait_bridge_ready(project, pid, fields):
    deadline = time.monotonic() + BRIDGE_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _cancelled(fields) or not _pid_alive(pid):
            return False
        if bridge_ready(project, pid):
            return True
        time.sleep(0.5)
    return False


def _wait_live_done(project, req_id, pid):
    mailbox = os.path.join(project, "Library", "UALImport")
    while True:
        done = _read_json(os.path.join(mailbox, "done.json"))
        if done and done.get("id") == req_id and done.get("status") in ("imported", "failed"):
            # Keep locks until completion AND claim removal; scripts may still be reloading.
            claimed = any((_read_json(os.path.join(mailbox, name)) or {}).get("id") == req_id
                          for name in ("request.json", "active.json"))
            if not _pid_alive(pid) or (not claimed and bridge_ready(project, pid)):
                return done
        if not _pid_alive(pid):
            # Requests are PID-bound: a new Editor cannot execute this old request.
            for name in ("request.json", "active.json"):
                target = os.path.join(mailbox, name)
                if (_read_json(target) or {}).get("id") == req_id:
                    os.unlink(target)
            return {"id": req_id, "status": "failed", "error": "Editor exited before import completion was confirmed. Inspect the project before retrying."}
        # No unsafe timeout: an alive Editor may still be writing. Stop is honored
        # at the next package boundary, not by abandoning an outstanding request.
        time.sleep(0.5)


def _run_closed(fields, results, project, staging, prep, editor):
    staged = [prep.staged_path(i) for i in range(len(results))]
    seen = run_closed_batch(project, fields, results, staged, prep, editor)
    completed = sum(row["status"] in ("imported", "failed") for row in results)
    current = results[seen - 1]["file"] if seen else None
    _emit(fields, results, completed, current)
    return completed, current


def _run_live(fields, results, project, prep):
    completed = 0
    current = None
    for i, row in enumerate(fields["packages"]):
        if _cancelled(fields):
            break
        status = prep.await_ready(i)
        if status == "cancelled":
            break
        current = row["file"]
        try:
            if status == "failed":
                raise RequestError(prep.error(i) or "staging failed")
            package = prep.staged_path(i)
            pid = _editor_pid(project)
            if not pid or not bridge_installed(project):
                raise RequestError("Live import requires an open Editor and explicit bridge installation")
            if not _wait_bridge_ready(project, pid, fields):
                if _cancelled(fields):
                    break
                raise RequestError("Bridge is not ready. Focus Unity, refresh assets and resolve compile errors, then retry")
            if _cancelled(fields):
                break
            results[i]["status"] = "importing"
            _emit(fields, results, completed, current)
            mailbox = os.path.join(project, "Library", "UALImport")
            if not _inside(mailbox, project):
                raise RequestError("Bridge mailbox escapes the project")
            os.makedirs(mailbox, exist_ok=True)
            if any(os.path.exists(os.path.join(mailbox, name)) for name in ("request.json", "active.json")):
                raise RequestError("An unresolved bridge request exists; inspect Unity before retrying")
            req_id = fields["id"] + "-" + str(i)
            if (_read_json(os.path.join(mailbox, "done.json")) or {}).get("id") == req_id:
                raise RequestError("This request ID has already completed. Review the project and start a new queue")
            ia.write_atomic(os.path.join(mailbox, "request.json"), json.dumps({"id": req_id, "package": package, "editor_pid": pid}))
            done = _wait_live_done(project, req_id, pid)
            ok, error = done["status"] == "imported", done.get("error")
            results[i]["status"] = "imported" if ok else "failed"
            if not ok:
                results[i]["error"] = error or "Unity did not import this package"
            prep.consume(i)  # receipt confirmed: the staged copy may go
        except Exception as error:
            results[i]["status"] = "failed"
            results[i]["error"] = str(error)
        completed += 1
        _emit(fields, results, completed, current)
        if results[i]["status"] == "failed":
            break
    return completed, current


def run_import(fields):
    state_dir = ia.data_dir(fields["root"])
    results = [{"file": row["file"], "status": "pending", "bytes_completed": 0, "bytes_total": None}
               for row in fields["packages"]]
    completed = 0
    current = None
    project = fields["project"]
    with ia.state_write_lock(state_dir, "import", blocking=False), _per_project_lock(project), _library_lock(fields["root"]):
        members = _load_index_members(state_dir)
        version = project_version(project)
        editor = editor_executable(version)
        for row in fields["packages"]:
            if (row["asset_key"], row["file"]) not in members:
                raise RequestError("Archive no longer matches the library index: " + row["file"])
            _archive(fields["root"], row["file"])
        pid = _editor_pid(project)
        if fields["mode"] == "closed" and pid:
            results[0].update(status="failed", error="Project is open; use live mode or close it yourself")
            completed = 1
            current = results[0]["file"]
            _emit(fields, results, completed, current)
        else:
            # Staging lives in OS temp (guaranteed local): the library may sit on
            # cloud-synced storage and staged copies must never be uploaded.
            staging = tempfile.mkdtemp(prefix="ual-staging-")
            prep = _Preparation(fields, results, staging)
            try:
                if fields["mode"] == "closed":
                    completed, current = _run_closed(fields, results, project, staging, prep, editor)
                else:
                    prep.start()
                    completed, current = _run_live(fields, results, project, prep)
            finally:
                prep.stop()
                shutil.rmtree(staging, ignore_errors=True)
    failed = next((row for row in results if row["status"] == "failed"), None)
    cancelled = not failed and completed < len(results) and _cancelled(fields)
    if failed or cancelled:
        for row in results:
            if row["status"] in ("pending", "preparing", "downloading", "ready"):
                row["status"] = "cancelled"
    return {"status": "failed" if failed else "cancelled" if cancelled else "completed",
            "completed": completed, "total": len(results), "current_item": current,
            "results": results, "error": failed["error"] if failed else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("projects", help="List Hub projects as JSON")
    for command in ("inspect", "install"):
        commands.add_parser(command).add_argument("--project", required=True)
    commands.add_parser("run").add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "projects":
            result = list_projects()
        elif args.command == "inspect":
            result = project_info(args.project)
        elif args.command == "install":
            result = install_bridge(args.project)
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
