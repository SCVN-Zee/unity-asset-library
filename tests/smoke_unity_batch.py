"""Direct-install CLI smoke using disposable projects, never user projects.

python3 tests/smoke_unity_batch.py
python3 tests/smoke_unity_batch.py --editor /path/to/Unity --version 6000.3.15f1
The optional Editor is launched by this verification script, not the importer.
Installed files/GUIDs are verified; successful Unity compilation is not implied.
"""
import argparse
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import shutil
import tarfile
import tempfile
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor")
    parser.add_argument("--version", default="6000.3.15f1")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ual-direct-smoke-") as directory:
        base = Path(directory).resolve()
        project, library = base / "project", base / "library"
        for folder in (project / "Assets", project / "Packages", project / "ProjectSettings", library / ".data"):
            folder.mkdir(parents=True)
        (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: " + args.version + "\n")
        (project / "Packages/manifest.json").write_text('{"dependencies":{}}')
        packages = []
        for name in ("Closed", "Open" if args.editor else "Second"):
            guid = uuid.uuid4().hex
            filename = name + " v1.2.unitypackage"
            with tarfile.open(library / filename, "w:gz") as archive:
                for leaf, text in (("pathname", "Assets/" + name + ".txt"), ("asset", name + " installed"),
                                   ("asset.meta", "fileFormatVersion: 2\nguid: " + guid + "\n")):
                    data = text.encode()
                    info = tarfile.TarInfo(guid + "/" + leaf)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            packages.append({"asset_key": name, "file": filename, "guid": guid})
        (library / ".data/assets.json").write_text(json.dumps({"assets": [
            {"asset_key": p["asset_key"], "versions": [{"file": p["file"]}]} for p in packages]}))
        worker = [sys.executable, str(repo / "bin/import_packages.py"), "run", "--request", str(base / "request.json")]
        editor = None
        try:
            for index, row in enumerate(packages):
                if index and args.editor:
                    log = base / "unity.log"
                    editor = subprocess.Popen([args.editor, "-batchmode", "-nographics", "-projectPath", str(project), "-logFile", str(log)],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    deadline = time.monotonic() + 90
                    while time.monotonic() < deadline:
                        if editor.poll() is not None:
                            raise AssertionError(log.read_text()[-4000:])
                        if log.exists() and "Initial Refresh End" in log.read_text():
                            break
                        time.sleep(.5)
                    else:
                        raise AssertionError("Editor startup timed out")
                (base / "request.json").write_text(json.dumps({"id": "smoke", "repo": str(repo), "root": str(library),
                    "project": str(project), "packages": [{k: row[k] for k in ("asset_key", "file")}]}))
                result = subprocess.run(worker, capture_output=True, text=True, timeout=30, env={**os.environ, "PATH": "/nonexistent"})
                assert result.returncode == 0, result.stdout + result.stderr
                outcome = json.loads(result.stdout.splitlines()[-1])
                assert outcome["results"][0]["status"] == "installed", outcome
                assert (project / "Assets" / (row["asset_key"] + ".txt")).read_text() == row["asset_key"] + " installed"
                assert row["guid"] in (project / "Assets" / (row["asset_key"] + ".txt.meta")).read_text()
                if editor is not None:
                    assert editor.poll() is None, "Installer stopped Editor"
                print("PASS:", row["asset_key"], "scenario; real CLI, dotted filename, GUID preserved, no unity executable on PATH", flush=True)
            assert {p.name for p in (project / "Assets").iterdir()} == {
                row["asset_key"] + suffix for row in packages for suffix in (".txt", ".txt.meta")}
        finally:
            if editor is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(editor.pid, signal.SIGTERM)
                try:
                    editor.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(editor.pid, signal.SIGKILL)
                editor.wait()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        os.killpg(editor.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.1)
            # Detached Unity helpers can finish cache writes after Editor exit.
            for attempt in range(50):
                try:
                    shutil.rmtree(base)
                    break
                except OSError as error:
                    if error.errno != errno.ENOTEMPTY or attempt == 49:
                        raise
                    time.sleep(.1)


if __name__ == "__main__":
    main()
