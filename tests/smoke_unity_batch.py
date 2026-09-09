"""Manual integration gate: python3 tests/smoke_unity_batch.py 2022.3.16f1.
Requires that installed Editor and the unity CLI. Never opens a user project.
Exercises the staging window, script compilation, cleanup and the first reopen.
Failed fixtures remain in the printed temporary directory for diagnosis.
"""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
import import_packages as ip


def main(version):
    editor = ip.editor_executable(version)
    base = Path(tempfile.mkdtemp(prefix="ual-batch-smoke-")).resolve()
    print("Fixture:", base, flush=True)
    root, project = base / "library", base / "project"
    (root / ".data").mkdir(parents=True)
    for folder in ("Assets", "ProjectSettings", "Packages"):
        (project / folder).mkdir(parents=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: " + version + "\n")
    (project / "Packages/manifest.json").write_text('{"dependencies":{"com.unity.modules.jsonserialize":"1.0.0"}}')
    expected = {"Before0.txt": "before zero", "Before1.txt": "before one",
                "ReloadProof.cs": "public static class ReloadProof { public static int Value = 42; }\n",
                "After0.txt": "after zero", "After1.txt": "after one"}
    assets, packages = [], []
    for index, (name, content) in enumerate(expected.items()):
        guid, file = uuid.uuid4().hex, str(index) + ".unitypackage"
        with tarfile.open(root / file, "w:gz") as archive:
            for entry, text in (("pathname", "Assets/" + name), ("asset", content),
                                ("asset.meta", "fileFormatVersion: 2\nguid: " + guid + "\n")):
                data = text.encode()
                info = tarfile.TarInfo(guid + "/" + entry)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        assets.append({"asset_key": str(index), "versions": [{"file": file}]})
        packages.append({"asset_key": str(index), "file": file})
    (root / ".data/assets.json").write_text(json.dumps({"assets": assets}))
    request = base / "request.json"
    request.write_text(json.dumps({"id": "batch-smoke", "repo": str(REPO), "root": str(root),
                                   "project": str(project), "mode": "closed", "packages": packages,
                                   "cancel_file": str(base / "cancel")}))
    with (base / "worker.log").open("w") as log:
        result = subprocess.run([sys.executable, str(REPO / "bin/import_packages.py"), "run", "--request", str(request)],
                                stdout=log, stderr=subprocess.STDOUT)
    output = (base / "worker.log").read_text()
    assert result.returncode == 0, output[-5000:]
    terminal = json.loads(output.splitlines()[-1])
    assert terminal["status"] == "completed" and [row["status"] for row in terminal["results"]] == ["imported"] * 5, terminal
    for name, content in expected.items():
        assert (project / "Assets" / name).read_text() == content, name
    assert not (project / "Assets/UnityAssetLibraryBatch").exists()
    batch = json.loads((project / "Library/UALImport/batch.json").read_text())
    assert all(not Path(entry["package"]).parent.exists() for entry in batch["packages"])
    assert (project / "Library/ScriptAssemblies/Assembly-CSharp.dll").is_file()
    log_path = base / "reopen.log"
    subprocess.run([editor, "-batchmode", "-nographics", "-quit", "-projectPath", str(project), "-logFile", str(log_path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
    log = log_path.read_text()
    assert "Initial Refresh End" in log and "error CS" not in log, log[-5000:]
    shutil.rmtree(base)
    print("PASS:", version, "five ordered imports, compiled C# package, staging cleanup and clean first reopen")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 tests/smoke_unity_batch.py INSTALLED_UNITY_VERSION")
    main(sys.argv[1])
