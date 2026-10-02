const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { execFile, spawn } = require("node:child_process");
const { promisify } = require("node:util");
const { stageUpdate, launchInstaller } = require("../electron/updates.cjs");
const run = promisify(execFile);

async function waitFor(predicate) {
  for (let i = 0; i < 100; i++) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Installer did not reach the expected state.");
}
(async () => {
  if (process.platform !== "darwin") return;
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "ual-install-test-")));
  const marker = path.join(root, "launched");
  const source = path.join(root, "probe.c");
  const target = path.join(root, "Installed.app");
  const userData = path.join(root, "user-data");
  const log = path.join(userData, "updates", "install.log");
  let oldProcess;
  let server;
  try {
    await fs.mkdir(userData);
    await fs.writeFile(path.join(userData, "library"), "keep this data");
    await fs.writeFile(source, `#include <stdio.h>\nint main(){FILE*f=fopen("${marker}","w");fputs(VERSION,f);fclose(f);return 0;}\n`);
    async function bundle(directory, version, arch = "arm64") {
      const contents = path.join(directory, "Contents");
      await fs.mkdir(path.join(contents, "MacOS"), { recursive: true });
      await fs.writeFile(path.join(contents, "Info.plist"), `<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.unityassetlibrary.viewer</string><key>CFBundleExecutable</key><string>probe</string><key>CFBundleName</key><string>Installer Test</string><key>CFBundlePackageType</key><string>APPL</string><key>CFBundleShortVersionString</key><string>${version}</string></dict></plist>`);
      await run("/usr/bin/clang", [source, "-arch", arch, `-DVERSION="${version}"`, "-o", path.join(contents, "MacOS", "probe")]);
      await run("/usr/bin/codesign", ["--force", "--sign", "-", directory]);
    }
    const version = async (directory) => (await run("/usr/bin/plutil", ["-extract", "CFBundleShortVersionString", "raw", "-o", "-", path.join(directory, "Contents", "Info.plist")])).stdout.trim();
    const text = async (file) => { try { return await fs.readFile(file, "utf8"); } catch (error) { if (error.code === "ENOENT") return ""; throw error; } };
    await bundle(target, "1.0.0");
    const input = path.join(root, "input", "Unity Asset Library.app");
    const archive = path.join(root, "update.zip");
    let payload;
    server = require("node:http").createServer((_request, response) => response.end(payload));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    async function packageRelease(arch) {
      await fs.rm(input, { recursive: true, force: true });
      await fs.rm(archive, { force: true });
      await bundle(input, "1.1.0", arch);
      await fs.mkdir(path.join(input, "Contents", "Resources"));
      await fs.writeFile(path.join(input, "Contents", "Resources", "data"), "linked resource");
      await fs.symlink("data", path.join(input, "Contents", "Resources", "current"));
      await run("/usr/bin/codesign", ["--force", "--sign", "-", input]);
      await run("/usr/bin/ditto", ["-c", "-k", "--sequesterRsrc", "--keepParent", input, archive]);
      payload = await fs.readFile(archive);
      return { version: "1.1.0", size: payload.length, digest: require("node:crypto").createHash("sha256").update(payload).digest("hex"),
        url: "http://127.0.0.1:" + server.address().port + "/update.zip" };
    }
    await assert.rejects(stageUpdate(await packageRelease("x86_64"), target), /arm64/);
    assert.equal(await version(target), "1.0.0");
    assert.deepEqual((await fs.readdir(root)).filter((name) => name.startsWith(".ual-update-")), []);
    console.log("PASS: signed x86-only update rejected before replacement; failed staging removed");
    const workspace = await stageUpdate(await packageRelease("arm64"), target);
    assert.equal(await fs.readFile(path.join(workspace, "Unity Asset Library.app", "Contents", "Resources", "current"), "utf8"), "linked resource");
    await assert.rejects(fs.access(path.join(workspace, "update.zip")), { code: "ENOENT" });
    console.log("PASS: real HTTP ZIP download, safe extraction, arm64 and signature verification preserve signed bundle links");
    oldProcess = spawn("/bin/sleep", ["30"]);
    await new Promise((resolve, reject) => { oldProcess.once("spawn", resolve); oldProcess.once("error", reject); });
    const exited = new Promise((resolve) => oldProcess.once("exit", resolve));
    await launchInstaller({ target, workspace, userData, pid: oldProcess.pid });
    await waitFor(async () => (await text(log)).includes("Waiting for application"));
    assert.equal(await version(target), "1.0.0");
    assert.equal(await text(marker), "");
    oldProcess.kill();
    await exited;
    await waitFor(async () => await text(marker) === "1.1.0");
    assert.equal(await version(target), "1.1.0");
    assert.equal(await version(path.join(workspace, "previous.app")), "1.0.0");
    assert.equal(await text(path.join(userData, "library")), "keep this data");
    console.log("PASS: installer waits for old process exit, replaces bundle, launches new executable and preserves user data");

    const failed = await fs.mkdtemp(path.join(root, ".ual-update-"));
    const badBundle = path.join(failed, "Unity Asset Library.app");
    await bundle(badBundle, "1.2.0");
    await fs.chmod(path.join(badBundle, "Contents", "MacOS", "probe"), 0o644);
    await run("/usr/bin/codesign", ["--verify", "--deep", "--strict", badBundle]);
    await fs.rm(marker);
    await launchInstaller({ target, workspace: failed, userData, pid: oldProcess.pid });
    await waitFor(async () => (await text(log)).includes("Update failed"));
    await waitFor(async () => await text(marker) === "1.1.0");
    assert.equal(await version(target), "1.1.0");
    assert.equal(await text(path.join(userData, "library")), "keep this data");
    console.log("PASS: LaunchServices failure after replacement restores and relaunches the previous app");
  } finally {
    if (oldProcess && oldProcess.exitCode === null) oldProcess.kill();
    if (server?.listening) await new Promise((resolve) => server.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
