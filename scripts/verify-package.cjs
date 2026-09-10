const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire } = require("node:module");
const { execFileSync } = require("node:child_process");
const asar = require("@electron/asar");

const bundle = path.resolve(process.argv[2] || "release/mac-arm64/Unity Asset Library.app");
const resources = path.join(bundle, "Contents", "Resources");
const archive = path.join(resources, "app.asar");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ual-package-"));
(async () => {
try {
  execFileSync("/usr/bin/codesign", ["--verify", "--deep", "--strict", bundle]);
  assert(asar.extractFile(archive, "dist/renderer/index.html").includes(Buffer.from("<html")));
  const unpacked = path.join(temp, "app");
  asar.extractAll(archive, unpacked);
  const entry = path.join(unpacked, "electron", "main.cjs");
  const packagedRequire = createRequire(entry);
  // Evaluate the shipped entry point without opening a window or touching user data.
  const handlers = {};
  const app = {
    isPackaged: true, getPath: () => temp, setPath() {},
    whenReady: () => ({ then() {} }), on() {},
  };
  const context = vm.createContext({
    require: (name) => name === "electron" ? { app, ipcMain: { handle: (name, fn) => { handlers[name] = fn; } } } : packagedRequire(name),
    __dirname: path.dirname(entry),
    process: { ...process, resourcesPath: resources, env: { ...process.env, UAL_PYTHON: undefined } },
  });
  vm.runInContext(fs.readFileSync(entry, "utf8"), context);
  const python = vm.runInContext("pythonCommand()", context);
  assert.equal(python, path.join(resources, "python", "bin", "python3"));
  const storage = await vm.runInContext(
    'runPythonCli(storageArgs(preparePackagedBackend(), "package-smoke", "status"))', context);
  assert.equal(storage.needsSetup, true);
  const importWorker = path.join(resources, "ual-backend", "bin", "import_packages.py");
  execFileSync(python, [importWorker, "--help"], { env: { PATH: "/nonexistent", HOME: temp, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" } });
  assert(!fs.readdirSync(path.join(resources, "python"), { recursive: true })
    .some((name) => name.endsWith(".pyc")), "Launcher must not write bytecode into the signed runtime");
  const result = execFileSync(python, ["-c", `
import io, json, os, pathlib, subprocess, sys, tarfile, threading
sys.path.insert(0, sys.argv[1])
import requests, storage, server
repo = pathlib.Path(sys.argv[2]) / "workspace"
vault = pathlib.Path(sys.argv[2]) / "empty-library"
vault.mkdir()
configured = storage.configure(str(repo), str(vault), "package-smoke")
assert configured["ready"], configured
# Enrichment subprocesses must inherit the relocated interpreter and dependencies.
subprocess.run([sys.executable, "-c", "import requests, ssl, fcntl"], check=True)
# Exercise the shipped importer, not just its help/parser, without any Unity CLI.
project = pathlib.Path(sys.argv[2]) / "unity-project"
(project / "Assets").mkdir(parents=True)
(project / "ProjectSettings").mkdir()
(project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.15f1\\n")
guid = "0123456789abcdef0123456789abcdef"
file = "Native v1.2.unitypackage"
meta = "fileFormatVersion: 2\\nguid: " + guid + "\\n"
with tarfile.open(vault / file, "w:gz") as archive:
    for leaf, text in (("pathname", "Assets/Proof.txt\\n00"), ("asset", "installed bytes"), ("asset.meta", meta)):
        data = text.encode()
        entry = tarfile.TarInfo(guid + "/" + leaf)
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
(vault / ".data/assets.json").write_text(json.dumps({"assets": [{"asset_key": "proof", "versions": [{"file": file}]}]}))
request = pathlib.Path(sys.argv[2]) / "import.json"
request.write_text(json.dumps({"id": "bundle-smoke", "repo": str(repo), "root": str(vault), "project": str(project), "packages": [{"asset_key": "proof", "file": file}]}))
result = subprocess.run([sys.executable, str(pathlib.Path(sys.argv[1]) / "import_packages.py"), "run", "--request", str(request)], capture_output=True, text=True, timeout=30)
assert result.returncode == 0, result.stdout + result.stderr
assert json.loads(result.stdout.splitlines()[-1])["results"][0]["status"] == "installed"
assert (project / "Assets/Proof.txt").read_text() == "installed bytes"
assert (project / "Assets/Proof.txt.meta").read_text() == meta
assert not (project / "Library").exists()
print("PASS: shipped direct installer, native pathname trailer, content and GUID preservation without Editor")
service = server.ViewerService(repo=str(repo), instance_id="package-smoke")
service.acquire_instance_lock()
httpd = server._Server(("127.0.0.1", 0), server.Handler, service)
thread = threading.Thread(target=httpd.serve_forever)
thread.start()
try:
    response = requests.get(f"http://127.0.0.1:{httpd.server_port}/api/state", timeout=10)
    response.raise_for_status()
    state = response.json()
    assert state["service"] == "unity-asset-library", state
    assert state["api_version"] == int(sys.argv[3]) and state["ready"], state
    assert state["instance_id"] == "package-smoke", state
    assert os.path.realpath(state["vault_root"]) == os.path.realpath(vault), state
    print("PASS: shipped launcher, bundled Python + requests, child interpreter, configured backend HTTP identity")
finally:
    httpd.shutdown()
    thread.join()
    httpd.server_close()
    service.shutdown()
`, path.join(resources, "ual-backend", "bin"), temp, String(vm.runInContext("API_VERSION", context))], {
    cwd: temp,
    env: { PATH: "/nonexistent", HOME: temp, PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1" },
    timeout: 30000,
    encoding: "utf8",
  });
  console.log(result.trim());
  execFileSync("/usr/bin/codesign", ["--verify", "--deep", "--strict", bundle]);
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
})().catch((error) => { console.error(error); process.exitCode = 1; });
