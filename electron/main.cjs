const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

// Preserve existing libraries and preferences across the rename to Unity Asset
// Library: keep each environment on its historical userData directory.
app.setPath("userData", path.join(app.getPath("appData"), app.isPackaged ? "Unity Asset Index" : "unity-asset-index"));

const PORT = 8765;
const SERVICE = "unity-asset-library";
const API_VERSION = 5;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const INCOMPATIBLE_BACKEND = `An incompatible backend is listening on port ${PORT}. Stop that Python server and restart the desktop app.`;
let backendProcess = null;
let ownsBackend = false;
let backendSession = null;
let backendGeneration = 0;
let quitting = false;
let registered = false;
let startupPromise = Promise.resolve();
let saveQueue = Promise.resolve();
let transitionPromise = null;
const backendOutput = [];
const storageState = {
  path: null,
  ready: false,
  needsSetup: true,
  error: null,
  canChange: true,
  busy: false,
  progress: null,
};
storageState.busy = true;

function cloneStorageState() {
  return { ...storageState, progress: storageState.progress ? { ...storageState.progress } : null };
}

function canonicalPath(value) {
  if (typeof value !== "string" || !value) return null;
  try { return fs.realpathSync(value); } catch { return path.resolve(value); }
}

function pathsEqual(left, right) {
  const a = canonicalPath(left);
  const b = canonicalPath(right);
  return Boolean(a && b && a === b);
}

function isViewerState(value, expected = {}) {
  return Boolean(
    value &&
      value.service === SERVICE &&
      value.api_version === API_VERSION &&
      value.ready === true &&
      typeof value.csrf === "string" &&
      value.csrf.length > 0 &&
      typeof value.vault_root === "string" &&
      typeof value.repo === "string" &&
      typeof value.instance_id === "string" &&
      value.instance_id.length > 0 &&
      (!expected.repo || pathsEqual(value.repo, expected.repo)) &&
      (!expected.vaultRoot || pathsEqual(value.vault_root, expected.vaultRoot)) &&
      (!expected.instanceId || value.instance_id === expected.instanceId),
  );
}

async function requestJson(endpoint, options = {}) {
  const response = await fetch(`${BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let value = {};
  try {
    value = text ? JSON.parse(text) : {};
  } catch {
    throw new Error("The Python backend returned invalid JSON.");
  }
  if (!response.ok) {
    if (value.error === "plan_changed") {
      const error = new Error("The library changed. Run the action again to review a fresh plan before applying.");
      error.code = value.error; error.status = response.status; throw error;
    }
    const error = new Error(value.detail || value.error || `Backend request failed (${response.status}).`);
    error.code = value.error; error.status = response.status; throw error;
  }
  return value;
}

async function readState() {
  return requestJson("/api/state");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function appendBackendOutput(label, chunk) {
  const lines = String(chunk).split(/\r?\n/).filter(Boolean);
  backendOutput.push(...lines.map((line) => `[${label}] ${line}`));
  if (backendOutput.length > 80) backendOutput.splice(0, backendOutput.length - 80);
}

function signalProcessGroup(child, signal) {
  if (!child || child.exitCode !== null) return;
  if (process.platform !== "win32" && child.pid) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch {
      // Fall back to the direct child when process groups are unavailable.
    }
  }
  try { child.kill(signal); } catch { /* already exited */ }
}

function waitForBackendExit(child, timeout) {
  if (!child || child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.removeListener("close", onClose);
      resolve(false);
    }, timeout);
    const onClose = () => {
      clearTimeout(timer);
      resolve(true);
    };
    child.once("close", onClose);
  });
}

async function stopOwnedBackend() {
  const child = backendProcess;
  backendProcess = null;
  ownsBackend = false;
  backendSession = null;
  backendGeneration += 1;
  if (!child || child.exitCode !== null) return;

  try { child.kill("SIGTERM"); } catch { /* already exited */ }
  if (await waitForBackendExit(child, 5000)) return;
  appendBackendOutput("electron", "backend did not exit after SIGTERM; terminating process group");
  signalProcessGroup(child, "SIGTERM");
  if (await waitForBackendExit(child, 2000)) return;
  appendBackendOutput("electron", "backend process group did not exit; killing process group");
  signalProcessGroup(child, "SIGKILL");
  await waitForBackendExit(child, 2000);
}

function preparePackagedBackend() {
  const template = path.join(process.resourcesPath, "ual-backend");
  const root = app.isPackaged
    ? path.join(app.getPath("userData"), "backend")
    : path.resolve(__dirname, "..");
  fs.mkdirSync(root, { recursive: true });
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  if (app.isPackaged && fs.existsSync(path.join(template, "bin"))) {
    fs.cpSync(path.join(template, "bin"), path.join(root, "bin"), { recursive: true });
  }
  return root;
}

function pythonCommand() {
  return process.env.UAL_PYTHON || (process.platform === "win32" ? "python" : "python3");
}

function newInstanceId() {
  return crypto.randomUUID();
}

function parseStorageOutcome(value) {
  if (!value || typeof value !== "object") throw new Error("Storage backend returned an invalid outcome.");
  if (value.path !== null && typeof value.path !== "string") throw new Error("Storage backend returned an invalid path.");
  if (typeof value.ready !== "boolean" || typeof value.needsSetup !== "boolean") {
    throw new Error("Storage backend returned an invalid readiness state.");
  }
  if (value.needsIndex !== undefined && typeof value.needsIndex !== "boolean") {
    throw new Error("Storage backend returned an invalid index state.");
  }
  if (value.error !== null && typeof value.error !== "string") throw new Error("Storage backend returned an invalid error.");
  return { path: value.path, ready: value.ready, needsSetup: value.needsSetup, needsIndex: value.needsIndex === true, error: value.error };
}

function runStorageCli(args, onProgress) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(pythonCommand(), args, {
        cwd: args[args.indexOf("--repo") + 1],
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      reject(error);
      return;
    }
    let stdout = "";
    let stderr = "";
    let outcome = null;
    const consume = (chunk) => {
      stdout += String(chunk);
      const lines = stdout.split(/\r?\n/);
      stdout = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        if (line.startsWith("UL_PROGRESS ")) {
          try {
            const progress = JSON.parse(line.slice("UL_PROGRESS ".length));
            if (onProgress) onProgress(progress);
            // Ignore non-protocol output; the final outcome remains authoritative.
          } catch {
          }
          continue;
        }
        try { outcome = parseStorageOutcome(JSON.parse(line)); } catch { /* diagnostics below */ }
      }
    };
    child.stdout.on("data", consume);
    child.stderr.on("data", (chunk) => { stderr += String(chunk); appendBackendOutput("storage", chunk); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (stdout.trim()) {
        try { outcome = parseStorageOutcome(JSON.parse(stdout.trim())); } catch { /* report below */ }
      }
      if (code !== 0 || !outcome) {
        const detail = outcome?.error || stderr.trim() || `Storage configuration failed (exit ${code ?? "unknown"}).`;
        reject(new Error(detail));
        return;
      }
      resolve(outcome);
    });
  });
}

function storageArgs(root, instanceId, command, extra = []) {
  return [path.join(root, "bin", "storage.py"), "--repo", root, "--instance-id", instanceId, command, ...extra];
}

function applyStorageOutcome(outcome, canChange = true) {
  storageState.path = outcome.path;
  storageState.ready = outcome.ready;
  storageState.needsSetup = outcome.needsSetup;
  storageState.error = outcome.error;
  storageState.canChange = canChange;
}

function setStorageError(error, options = {}) {
  storageState.error = String(error?.message || error);
  storageState.ready = false;
  storageState.busy = Boolean(options.busy);
  if (options.path !== undefined) storageState.path = options.path;
  if (options.canChange !== undefined) storageState.canChange = options.canChange;
  if (options.needsSetup !== undefined) storageState.needsSetup = options.needsSetup;
}

function assertOwnState(state, expected) {
  if (!isViewerState(state, expected)) throw new Error("The Python backend identity changed during startup.");
}

async function startOwnedBackend(root, vaultRoot) {
  const instanceId = newInstanceId();
  const script = path.join(root, "bin", "server.py");
  backendOutput.length = 0;
  const child = spawn(pythonCommand(), [script, "--repo", root, "--port", String(PORT), "--instance-id", instanceId], {
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32",
  });
  backendProcess = child;
  ownsBackend = true;
  child.stdout.on("data", (chunk) => appendBackendOutput("stdout", chunk));
  child.stderr.on("data", (chunk) => appendBackendOutput("stderr", chunk));
  child.on("error", (error) => appendBackendOutput("electron", error.message));
  child.on("close", (code, signal) => appendBackendOutput("electron", `backend exited code=${code} signal=${signal || "none"}`));

  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (child.exitCode !== null) break;
    try {
      const state = await readState();
      if (isViewerState(state, { repo: root, vaultRoot, instanceId })) {
        backendSession = { root, vaultRoot: state.vault_root, instanceId, generation: ++backendGeneration };
        storageState.path = state.vault_root;
        storageState.ready = true;
        storageState.needsSetup = false;
        storageState.error = null;
        storageState.canChange = true;
        return state;
      }
    } catch {
      // The server may still be importing the backend modules.
    }
    await delay(100);
  }

  const detail = backendOutput.slice(-20).join("\n") || "The Python backend did not become ready.";
  await stopOwnedBackend();
  throw new Error(detail);
}

async function reuseExternalBackend(state) {
  if (!isViewerState(state)) throw new Error(INCOMPATIBLE_BACKEND);
  backendSession = { root: state.repo, vaultRoot: state.vault_root, instanceId: state.instance_id, generation: ++backendGeneration, external: true };
  ownsBackend = false;
  storageState.path = state.vault_root;
  storageState.ready = true;
  storageState.needsSetup = false;
  storageState.error = null;
  storageState.canChange = false;
  return state;
}
async function configureAndStart(root, vaultRoot, instanceId = newInstanceId(), onConfigured) {
  const outcome = await runStorageCli(storageArgs(root, instanceId, "configure", ["--root", vaultRoot, "--progress-json"]), (progress) => {
    storageState.progress = progress;
  });
  const configuredPath = canonicalPath(outcome.path);
  if (!outcome.ready || !configuredPath || !pathsEqual(configuredPath, vaultRoot)) {
    throw new Error(outcome.error || "Storage backend did not activate the selected folder.");
  }
  if (onConfigured) onConfigured();
  return startOwnedBackend(root, configuredPath);
}

async function bootstrapStorage() {
  let root;
  storageState.busy = true;
  try {
    root = preparePackagedBackend();
    let existing;
    try { existing = await readState(); } catch { existing = null; }
    if (existing) {
      if (isViewerState(existing)) return reuseExternalBackend(existing);
      throw new Error(INCOMPATIBLE_BACKEND);
    }

    const status = parseStorageOutcome(await runStorageCli(storageArgs(root, newInstanceId(), "status")));
    applyStorageOutcome(status, true);
    if (status.needsSetup || !status.path) return null;
    if (!status.ready) {
      if (!status.needsIndex) return null;
      storageState.progress = null;
      await configureAndStart(root, status.path);
    } else {
      await startOwnedBackend(root, status.path);
    }
    return backendSession;
  } catch (error) {
    const incompatible = String(error?.message || error) === INCOMPATIBLE_BACKEND;
    setStorageError(error, { canChange: !incompatible && !backendSession?.external, needsSetup: false });
    return null;
  } finally {
    storageState.busy = false;
    storageState.progress = null;
  }
}

async function prepareRestart() {
  const session = backendSession;
  if (!session || !ownsBackend) return;
  const state = await readState();
  assertOwnState(state, session);
  await requestJson("/api/storage/prepare-restart", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csrf: state.csrf }),
  });
}

async function recoverPrevious(root, previousPath, reconfigure) {
  if (!previousPath) return null;
  try {
    if (reconfigure) await configureAndStart(root, previousPath);
    else await startOwnedBackend(root, previousPath);
    return backendSession;
  } catch (error) {
    setStorageError(new Error(`Storage change failed and previous library could not be restored: ${error.message}`), { path: previousPath, canChange: true, needsSetup: false });
    return null;
  }
}

async function performSaveStorage(selectedPath) {
  if (typeof selectedPath !== "string" || !selectedPath.trim()) throw new Error("Choose a library folder before saving.");
  if (!storageState.canChange) throw new Error("The active backend is external and cannot be reconfigured from this window.");
  storageState.busy = true;
  storageState.progress = null;
  try {
    await startupPromise.catch(() => {});
    if (!storageState.canChange) throw new Error("The active backend is external and cannot be reconfigured from this window.");
    const root = preparePackagedBackend();
    const previousPath = storageState.path;
    const previousSession = backendSession && ownsBackend ? { ...backendSession } : null;
    storageState.error = null;
    transitionPromise = (async () => {
      let previousStopped = false;
      let replacementConfigured = false;
      backendGeneration += 1;
      try {
        if (previousSession) {
          await prepareRestart();
          await stopOwnedBackend();
          previousStopped = true;
        }
        backendSession = null;
        storageState.ready = false;
        await configureAndStart(root, selectedPath, newInstanceId(), () => { replacementConfigured = true; });
        storageState.path = backendSession.vaultRoot;
        storageState.ready = true;
        storageState.needsSetup = false;
        storageState.error = null;
        const result = cloneStorageState();
        result.busy = false;
        result.progress = null;
        return result;
      } catch (error) {
        if ((previousStopped || !previousSession) && (backendProcess || ownsBackend)) await stopOwnedBackend();
        let restored = null;
        if (previousStopped) restored = await recoverPrevious(root, previousPath, replacementConfigured);
        else if (previousSession) {
          backendSession = previousSession;
          ownsBackend = true;
          restored = previousSession;
        }
        if (restored) {
          storageState.path = restored.vaultRoot;
          storageState.ready = true;
          storageState.needsSetup = false;
        } else {
          storageState.ready = false;
          storageState.path = previousPath || null;
        }
        storageState.error = String(error.message || error);
        throw error;
      } finally {
        transitionPromise = null;
      }
    })();
    return await transitionPromise;
  } finally {
    if (!transitionPromise) {
      storageState.busy = false;
      storageState.progress = null;
    }
  }
}
function getStorage() {
  return cloneStorageState();
}

function chooseStorageFolder(currentPath) {
  const properties = ["openDirectory", "createDirectory"];
  const options = { title: "Choose your Unity asset folder", properties };
  if (typeof currentPath === "string" && currentPath) options.defaultPath = currentPath;
  return dialog.showOpenDialog(options).then((result) => result.canceled || !result.filePaths?.length ? null : result.filePaths[0]);
}

function saveStorage(selectedPath) {
  const run = saveQueue.then(() => performSaveStorage(selectedPath));
  saveQueue = run.catch(() => {});
  return run;
}

function assertBackendRequestAllowed() {
  if (transitionPromise || storageState.busy) throw new Error("Storage transition in progress.");
  const session = backendSession;
  if (!session) throw new Error(storageState.error || "The library backend is not ready.");
  return { session, generation: backendGeneration };
}

async function backendRequest(endpoint, options = {}) {
  const guard = assertBackendRequestAllowed();
  const value = await requestJson(endpoint, options);
  if (endpoint === "/api/state") assertOwnState(value, guard.session);
  if (guard.generation !== backendGeneration || backendSession !== guard.session) {
    throw new Error("The library changed while the request was running; retry against the active library.");
  }
  return value;
}

async function postJson(endpoint, body = {}) {
  const guard = assertBackendRequestAllowed();
  const state = await readState();
  assertOwnState(state, guard.session);
  const value = await requestJson(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, csrf: state.csrf }),
  });
  if (guard.generation !== backendGeneration || backendSession !== guard.session) {
    throw new Error("The library changed while the request was running; retry against the active library.");
  }
  return value;
}

function registerIpc() {
  if (registered) return;
  ipcMain.handle("backend:assets", () => backendRequest("/api/assets"));
  ipcMain.handle("backend:favorites", () => backendRequest("/api/favorites"));
  ipcMain.handle("backend:set-favorite", (_event, assetKey, favorite) => {
    if (typeof assetKey !== "string" || !assetKey || typeof favorite !== "boolean") {
      throw new Error("setFavorite expects an asset key string and a boolean.");
    }
    return postJson("/api/favorites", { asset_key: assetKey, favorite });
  });
  ipcMain.handle("backend:state", () => backendRequest("/api/state"));
  ipcMain.handle("backend:resync", () => postJson("/api/resync/plan"));
  ipcMain.handle("backend:resync-apply", (_event, planHash) => postJson("/api/resync/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:cleanup-plan", () => postJson("/api/cleanup/plan"));
  ipcMain.handle("backend:cleanup-apply", (_event, planHash) => postJson("/api/cleanup/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:organize-plan", () => postJson("/api/organize/plan"));
  ipcMain.handle("backend:organize-apply", (_event, planHash) => postJson("/api/organize/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:enrich-start", () => postJson("/api/enrich/start"));
  ipcMain.handle("backend:enrich-cancel", () => postJson("/api/enrich/cancel"));
  ipcMain.handle("backend:job", (_event, jobId) => backendRequest(`/api/job/${encodeURIComponent(jobId)}`));
  ipcMain.handle("backend:action", (_event, actionId) => backendRequest(`/api/action/${encodeURIComponent(actionId)}`));
  ipcMain.handle("storage:get", () => getStorage());
  ipcMain.handle("storage:choose-folder", (_event, currentPath) => chooseStorageFolder(currentPath));
  ipcMain.handle("storage:save", (_event, selectedPath) => saveStorage(selectedPath));
  ipcMain.handle("shell:open-external", (_event, url) => {
    const parsed = new URL(url);
    if (!["https:", "http:"].includes(parsed.protocol)) throw new Error("Only web links can be opened.");
    return shell.openExternal(parsed.toString());
  });
  ipcMain.handle("shell:reveal-item", (_event, filePath) => {
    const vaultRoot = backendSession ? backendSession.vaultRoot : null;
    if (typeof filePath !== "string" || !filePath || !vaultRoot) throw new Error("No library folder is connected.");
    const root = canonicalPath(vaultRoot);
    let target;
    try { target = fs.realpathSync(path.resolve(root, filePath)); } catch { throw new Error("This file is no longer on disk."); }
    // realpath resolves symlinks, so a link escaping the library fails the containment check below.
    if (target !== root && !target.startsWith(root + path.sep)) throw new Error("This file is outside the library folder.");
    return shell.showItemInFolder(target);
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 980,
    minHeight: 680,
    backgroundColor: "#111214",
    title: "Unity Asset Library",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) window.loadURL(devUrl);
  else window.loadFile(path.join(app.getAppPath(), "dist", "renderer", "index.html"));
}

app.whenReady().then(() => {
  registerIpc();
  createWindow();
  startupPromise = bootstrapStorage();
});

app.on("before-quit", (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  void Promise.allSettled([startupPromise, saveQueue, transitionPromise || Promise.resolve()]).then(() => stopOwnedBackend()).finally(() => app.quit());
});

app.on("window-all-closed", () => {
  if (!quitting) app.quit();
});
