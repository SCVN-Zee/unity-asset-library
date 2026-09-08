const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
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
  // Evaluate the shipped entry point without opening a window or touching user data.
  const app = {
    isPackaged: true, getPath: () => temp, setPath() {},
    whenReady: () => ({ then() {} }), on() {},
  };
  const context = vm.createContext({
    require: (name) => name === "electron" ? { app } : require(name),
    process: { ...process, env: { PATH: "/nonexistent", HOME: temp }, resourcesPath: resources },
    __dirname: path.join(resources, "electron"),
  });
  vm.runInContext(asar.extractFile(archive, "electron/main.cjs").toString(), context);
  const python = vm.runInContext("pythonCommand()", context);
  assert.equal(python, path.join(resources, "python", "bin", "python3"));
  const storage = await vm.runInContext(
    'runStorageCli(storageArgs(preparePackagedBackend(), "package-smoke", "status"))', context);
  assert.equal(storage.needsSetup, true);
  assert(!fs.readdirSync(path.join(resources, "python"), { recursive: true })
    .some((name) => name.endsWith(".pyc")), "Launcher must not write bytecode into the signed runtime");
  const result = execFileSync(python, ["-c", `
import json, os, pathlib, subprocess, sys, threading
sys.path.insert(0, sys.argv[1])
import requests, storage, server
repo = pathlib.Path(sys.argv[2]) / "workspace"
vault = pathlib.Path(sys.argv[2]) / "empty-library"
vault.mkdir()
configured = storage.configure(str(repo), str(vault), "package-smoke")
assert configured["ready"], configured
# Enrichment subprocesses must inherit the relocated interpreter and dependencies.
subprocess.run([sys.executable, "-c", "import requests, ssl, fcntl"], check=True)
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
    assert state["api_version"] == 5 and state["ready"], state
    assert state["instance_id"] == "package-smoke", state
    assert os.path.realpath(state["vault_root"]) == os.path.realpath(vault), state
    print("PASS: shipped launcher, bundled Python + requests, child interpreter, configured backend HTTP identity")
finally:
    httpd.shutdown()
    thread.join()
    httpd.server_close()
    service.shutdown()
`, path.join(resources, "ual-backend", "bin"), temp], {
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
