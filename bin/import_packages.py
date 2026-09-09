#!/usr/bin/env python3
"""Import indexed .unitypackage files, in order, into a closed or live Unity project.

Commands: projects; inspect --project PATH; install --project PATH;
          run --request REQUEST.json
Request: {id, repo, root, project, mode: "closed"|"live",
          packages: [{asset_key, file}], cancel_file?}
`file` is library-relative. `repo/state/assets.json` is the authoritative index.
Output: UL_PROGRESS JSON lines followed by one terminal JSON line. Exit 0 means
completed/cancelled, 1 package failure, 2 preflight failure. Creating cancel_file
or sending SIGINT/SIGTERM stops after the current package, never mid-import.
Live mode requires explicit bridge installation and a loaded, idle Editor.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import cleanup_versions as cv
import index_assets as ia
from progress import json_progress

BIN_DIR = os.path.dirname(os.path.abspath(__file__))
BRIDGE_SUBDIR = "UnityAssetLibraryImport"
BRIDGE_NAME = "UnityAssetLibraryImport.cs"
BRIDGE_ASMDEF = "UnityAssetLibraryImport.asmdef"
BRIDGE_VERSION = "1"
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


def list_editors():
    data = run_unity_json(["editors", "--installed", "--json"])
    if not isinstance(data, list):
        raise RequestError("Cannot determine installed Unity versions")
    return [row["version"] for row in data if isinstance(row, dict) and isinstance(row.get("version"), str)]


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


def _emit(fields, results, completed, current):
    json_progress({"stage": "importing", "completed": completed, "total": len(results),
                   "current_item": current, "results": results,
                   "counts": {status: sum(row["status"] == status for row in results)
                              for status in ("imported", "failed", "cancelled")}})


def run_closed_import(project, package):
    # Isolate terminal signals: stopping the worker must not interrupt Unity mid-write.
    with tempfile.TemporaryDirectory(prefix="ual-import-log-") as logs:
        log_path = os.path.join(logs, "unity.log")
        proc = subprocess.run([unity_bin(), "--non-interactive", "run", project, "--",
                               "-logFile", log_path, "-importPackage", package],
                              capture_output=True, text=True, start_new_session=True)
        if proc.returncode == 0:
            return True, None
        detail = (proc.stderr or proc.stdout)[-2000:]
        try:
            with open(log_path, "rb") as stream:
                stream.seek(max(0, os.fstat(stream.fileno()).st_size - 4000))
                detail += "\n" + stream.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        return False, "Unity import failed (exit %s): %s" % (proc.returncode, detail)


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


def run_import(fields):
    state_dir = os.path.join(fields["repo"], "state")
    results = [{"file": row["file"], "status": "pending"} for row in fields["packages"]]
    completed = 0
    current = None
    project = fields["project"]
    with ia.state_write_lock(state_dir, "import", blocking=False), _per_project_lock(project), _library_lock(fields["root"]):
        members = _load_index_members(state_dir)
        version = project_version(project)
        if version not in list_editors():
            raise RequestError("Required Unity Editor is not installed: " + version)
        for row in fields["packages"]:
            if (row["asset_key"], row["file"]) not in members:
                raise RequestError("Archive no longer matches the library index: " + row["file"])
            _archive(fields["root"], row["file"])
        for i, row in enumerate(fields["packages"]):
            if _cancelled(fields):
                break
            current = row["file"]
            try:
                package = _archive(fields["root"], current)
                pid = _editor_pid(project)
                if fields["mode"] == "closed" and pid:
                    raise RequestError("Project is open; use live mode or close it yourself")
                if fields["mode"] == "live":
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
                if fields["mode"] == "closed":
                    ok, error = run_closed_import(project, package)
                else:
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
            except Exception as error:
                results[i]["status"] = "failed"
                results[i]["error"] = str(error)
            completed += 1
            _emit(fields, results, completed, current)
            if results[i]["status"] == "failed":
                break
    failed = next((row for row in results if row["status"] == "failed"), None)
    cancelled = not failed and completed < len(results) and _cancelled(fields)
    if cancelled:
        for row in results:
            if row["status"] == "pending":
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
