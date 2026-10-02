const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFile, spawn } = require("node:child_process");
const { promisify } = require("node:util");
const { Readable, Transform } = require("node:stream");
const { pipeline } = require("node:stream/promises");
const semver = require("semver");
const run = promisify(execFile);
const REPO = "SCVN-Zee/unity-asset-library";
const API = `https://api.github.com/repos/${REPO}/releases/latest`;
const BUNDLE = "Unity Asset Library.app";

function selectRelease(release, currentVersion) {
  if (release.draft || release.prerelease) return null;
  const version = release.tag_name?.replace(/^v/, "");
  if (!semver.valid(version) || semver.prerelease(version) || !semver.gt(version, currentVersion)) return null;
  const name = `unity-asset-library-${version}-arm64.zip`;
  const asset = release.assets?.find((entry) => entry.name === name && entry.state === "uploaded");
  if (!asset || !/^sha256:[a-f0-9]{64}$/.test(asset.digest || "")) {
    throw new Error("The release has no verified arm64 ZIP. Try again after the release is complete.");
  }
  const expectedUrl = `https://github.com/${REPO}/releases/download/${release.tag_name}/${name}`;
  if (asset.browser_download_url !== expectedUrl || !Number.isSafeInteger(asset.size) || asset.size <= 0) {
    throw new Error("The release asset metadata is invalid.");
  }
  return { version, url: expectedUrl, digest: asset.digest.slice(7), size: asset.size };
}

async function downloadVerified(release, destination, onProgress = () => {}, signal) {
  const response = await fetch(release.url, { signal: AbortSignal.any([AbortSignal.timeout(10 * 60 * 1000), ...(signal ? [signal] : [])]) });
  if (!response.ok || !response.body) throw new Error(`Download failed (HTTP ${response.status}).`);
  const hash = crypto.createHash("sha256");
  let bytes = 0;
  const meter = new Transform({ transform(chunk, _encoding, callback) {
    bytes += chunk.length;
    if (bytes > release.size) return callback(new Error("Download exceeds the release size."));
    hash.update(chunk);
    onProgress(Math.floor(bytes / release.size * 100));
    callback(null, chunk);
  } });
  try {
    await pipeline(Readable.fromWeb(response.body), meter, fs.createWriteStream(destination, { flags: "wx", mode: 0o600 }));
    if (bytes !== release.size || hash.digest("hex") !== release.digest) {
      throw new Error("Update checksum mismatch. The download was discarded.");
    }
  } catch (error) {
    await fsp.rm(destination, { force: true });
    throw error;
  }
}

async function stageUpdate(release, target, onProgress, signal) {
  if (target.includes("/AppTranslocation/") || !target.endsWith(".app")) {
    throw new Error("Move Unity Asset Library to Applications before updating.");
  }
  if ((await fsp.lstat(target)).isSymbolicLink()) throw new Error("Cannot update a symlinked application.");
  await fsp.access(target, fs.constants.W_OK);
  const workspace = await fsp.realpath(await fsp.mkdtemp(path.join(path.dirname(target), ".ual-update-")));
  await fsp.chmod(workspace, 0o700);
  try {
    const zip = path.join(workspace, "update.zip");
    await downloadVerified(release, zip, onProgress, signal);
    const { stdout: entries } = await run("/usr/bin/unzip", ["-Z", "-1", zip], { maxBuffer: 16 * 1024 * 1024 });
    if (entries.split("\n").filter(Boolean).some((entry) => entry.startsWith("/") || entry.split("/").includes(".."))) {
      throw new Error("Unsafe paths in update archive.");
    }
    await run("/usr/bin/ditto", ["-x", "-k", zip, workspace]);
    const bundle = path.join(workspace, BUNDLE);
    const root = await fsp.realpath(bundle);
    if (root !== bundle) throw new Error("Update bundle must not be a symlink.");
    async function checkLinks(directory) {
      for (const entry of await fsp.readdir(directory, { withFileTypes: true })) {
        const file = path.join(directory, entry.name);
        if (entry.isSymbolicLink()) {
          const resolved = await fsp.realpath(file);
          if (!resolved.startsWith(root + path.sep)) throw new Error("Update contains an external symlink.");
        } else if (entry.isDirectory()) await checkLinks(file);
      }
    }
    await checkLinks(bundle);
    const plist = path.join(bundle, "Contents", "Info.plist");
    for (const [key, expected] of [["CFBundleIdentifier", "com.unityassetlibrary.viewer"], ["CFBundleShortVersionString", release.version]]) {
      const { stdout } = await run("/usr/bin/plutil", ["-extract", key, "raw", "-o", "-", plist]);
      if (stdout.trim() !== expected) throw new Error(`Update ${key} does not match the release.`);
    }
    await run("/usr/bin/codesign", ["--verify", "--deep", "--strict", bundle]);
    await fsp.rm(zip);
    return workspace;
  } catch (error) {
    await fsp.rm(workspace, { recursive: true, force: true });
    throw error;
  }
}

async function launchInstaller({ target, workspace, userData, pid = process.pid }) {
  const directory = path.join(userData, "updates");
  await fsp.mkdir(directory, { recursive: true, mode: 0o700 });
  const helper = path.join(directory, "install-update.sh");
  await fsp.copyFile(path.join(__dirname, "install-update.sh"), helper);
  const log = path.join(directory, "install.log");
  await fsp.writeFile(log, "Installing update\n", { mode: 0o600 });
  const child = spawn("/bin/sh", [helper, String(pid), target, workspace, log], { detached: true, stdio: "ignore" });
  await new Promise((resolve, reject) => { child.once("spawn", resolve); child.once("error", reject); });
  child.unref();
}

function createUpdates({ app, dialog, Menu, requestRestart }) {
  let busy = false;
  let closing = false;
  let staging = null;
  const cancellation = new AbortController();
  let prepared = null;
  let installing = false;
  let label = "Check for Updates…";
  const supported = app.isPackaged && process.platform === "darwin" && process.arch === "arm64";
  const target = supported ? path.resolve(app.getAppPath(), "../../..") : null;
  const showError = (error) => dialog.showMessageBox({ type: "error", title: "Update failed", message: error.message,
    detail: "Your installed app and library have not been replaced. You can retry from the app menu." });
  function refreshMenu() {
    if (closing) return;
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { label: app.name, submenu: [{ role: "about" }, { type: "separator" },
        { label, enabled: !busy && !installing, click: () => { void check(true); } },
        { type: "separator" }, { role: "services" }, { role: "hide" }, { role: "hideOthers" }, { role: "unhide" },
        { type: "separator" }, { role: "quit" }] },
      { role: "editMenu" }, { role: "viewMenu" }, { role: "windowMenu" },
    ]));
  }
  async function offerRestart() {
    const { response } = await dialog.showMessageBox({ type: "info", title: "Update ready",
      message: `Version ${prepared.version} is ready to install.`,
      detail: "Restart to update. Active library work will finish before the app is replaced. Your library and preferences stay in place.",
      buttons: ["Restart and Install", "Later"], defaultId: 1, cancelId: 1, noLink: true });
    if (response === 0 && !closing) requestRestart();
  }
  async function check(manual = false) {
    if (busy || installing || closing) return;
    if (!supported) {
      if (manual) await dialog.showMessageBox({ type: "info", title: "Updates unavailable",
        message: "Auto-updates are available in installed macOS Apple Silicon builds." });
      return;
    }
    busy = true; label = "Checking for Updates…"; refreshMenu();
    try {
      if (prepared) return await offerRestart();
      const response = await fetch(API, { headers: { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" },
        signal: AbortSignal.any([AbortSignal.timeout(30_000), cancellation.signal]) });
      if (!response.ok) throw new Error(`GitHub update check failed (HTTP ${response.status}).`);
      const release = selectRelease(await response.json(), app.getVersion());
      if (!release) {
        if (manual) await dialog.showMessageBox({ type: "info", title: "No update available", message: `You are running version ${app.getVersion()}. No newer stable release is available.` });
        return;
      }
      const { response: choice } = await dialog.showMessageBox({ type: "info", title: "Update available",
        message: `Download Unity Asset Library ${release.version}?`,
        detail: "The update comes from GitHub Releases. Installation requires a confirmed restart.",
        buttons: ["Download Update", "Later"], defaultId: 0, cancelId: 1, noLink: true });
      if (choice !== 0 || closing) return;
      label = "Downloading Update…"; refreshMenu();
      staging = stageUpdate(release, target, (percent) => {
        const next = `Downloading Update… ${percent}%`;
        if (next !== label) { label = next; refreshMenu(); }
      }, cancellation.signal);
      const workspace = await staging;
      prepared = { workspace, version: release.version };
      if (!closing) await offerRestart();
    } catch (error) {
      // Background network outages are logged; explicit checks and accepted downloads surface errors.
      console.error("Update failed:", error);
      if (!closing && (manual || label.startsWith("Downloading"))) await showError(error);
    } finally {
      busy = false; label = prepared ? "Restart and Install Update…" : "Check for Updates…"; refreshMenu();
    }
  }
  refreshMenu();
  return {
    check,
    async install() {
      if (!prepared) return false;
      await launchInstaller({ target, workspace: prepared.workspace, userData: app.getPath("userData") });
      installing = true; refreshMenu();
      return true;
    },
    async discard() {
      closing = true;
      cancellation.abort();
      if (staging) await staging.catch(() => {});
      if (prepared && !installing) await fsp.rm(prepared.workspace, { recursive: true, force: true });
    },
  };
}

module.exports = { createUpdates, selectRelease, downloadVerified, stageUpdate, launchInstaller };
