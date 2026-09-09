const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

// Preserve existing libraries and preferences across the rename to Unity Asset
// Library: keep each environment on its historical userData directory.
app.setPath("userData", path.join(app.getPath("appData"), app.isPackaged ? "Unity Asset Index" : "unity-asset-index"));

let backendPort = 8765;
const SERVICE = "unity-asset-library";
const API_VERSION = 6;
const { resolvePort } = require("./port-recovery.cjs");
let backendProcess = null;
let ownsBackend = false;
let backendSession = null;
let backendGeneration = 0;
let quitting = false;
let quitReady = false;
let registered = false;
let startupPromise = Promise.resolve();
let saveQueue = Promise.resolve();
let transitionPromise = null;
const backendOutput = [];
let pendingPortChoice = null;
let importJob = null;
let importAdmission = false;
let importPromise = Promise.resolve();
let importCancelFile = null;
const storageState = {
  path: null,
  ready: false,
  needsSetup: true,
  error: null,
  canChange: true,
  busy: false,
  progress: null,
  portRecovery: null,
};
storageState.busy = true;

function cloneStorageState() {
  return { ...storageState, progress: storageState.progress ? { ...storageState.progress } : null };
}

function askPortChoice(request) {
  if (quitting) return Promise.resolve({ action: "cancel" });
  return new Promise((resolve) => {
    const id = crypto.randomUUID();
    storageState.portRecovery = { ...request, id, awaiting: true };
    pendingPortChoice = { id, resolve };
  });
}

function answerPortChoice(id, choice) {
  const request = storageState.portRecovery;
  if (!pendingPortChoice || pendingPortChoice.id !== id || !request) throw new Error("This port recovery request has expired.");
  const allowed = request.confirm ? ["cancel", "confirm-stop"] : ["cancel", "use", "stop"];
  if (!choice || !allowed.includes(choice.action) ||
      (choice.action === "use" && (!Number.isInteger(choice.port) || choice.port < 1 || choice.port > 65535)) ||
      (choice.action === "stop" && !request.owner)) throw new Error("Invalid port recovery choice.");
  const pending = pendingPortChoice;
  pendingPortChoice = null;
  storageState.portRecovery = { ...request, awaiting: false };
  pending.resolve(choice);
}

async function ensureBackendPort() {
  try { backendPort = await resolvePort(backendPort, askPortChoice); } finally {
    pendingPortChoice = null;
    storageState.portRecovery = null;
  }
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
  const response = await fetch(`http://127.0.0.1:${backendPort}${endpoint}`, {
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
  return requestJson("/api/state", { signal: AbortSignal.timeout(1500) });
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
  return process.env.UAL_PYTHON || (app.isPackaged
    ? path.join(process.resourcesPath, "python", "bin", "python3")
    : (process.platform === "win32" ? "python" : "python3"));
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

function runPythonCli(args, onProgress, cwd = args[args.indexOf("--repo") + 1]) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn(pythonCommand(), args, {
        cwd,
        env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONDONTWRITEBYTECODE: "1" },
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
        try { outcome = JSON.parse(line); } catch { /* diagnostics below */ }
      }
    };
    child.stdout.on("data", consume);
    child.stderr.on("data", (chunk) => { stderr = (stderr + String(chunk)).slice(-16000); appendBackendOutput("python", chunk); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (stdout.trim()) {
        try { outcome = JSON.parse(stdout.trim()); } catch { /* report below */ }
      }
      if (code !== 0 || !outcome) {
        const detail = outcome?.error || stderr.trim() || `Python command failed (exit ${code ?? "unknown"}).`;
        const error = new Error(detail);
        error.outcome = outcome;
        reject(error);
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
  while (true) {
    await ensureBackendPort();
    try { return await launchOwnedBackend(root, vaultRoot); } catch (error) {
      // A listener can win the race between probing and Python binding.
      if (!String(error.message).includes(`server: cannot bind 127.0.0.1:${backendPort}`)) throw error;
    }
  }
}

async function launchOwnedBackend(root, vaultRoot) {
  const instanceId = newInstanceId();
  const script = path.join(root, "bin", "server.py");
  backendOutput.length = 0;
  const child = spawn(pythonCommand(), [script, "--repo", root, "--port", String(backendPort), "--instance-id", instanceId], {
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONDONTWRITEBYTECODE: "1" },
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
  if (!isViewerState(state)) throw new Error("The external backend is incompatible.");
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
  await ensureBackendPort();
  const outcome = parseStorageOutcome(await runPythonCli(storageArgs(root, instanceId, "configure", ["--root", vaultRoot, "--progress-json"]), (progress) => {
    storageState.progress = progress;
  }));
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
    if (isViewerState(existing)) return reuseExternalBackend(existing);
    await ensureBackendPort();
    const status = parseStorageOutcome(await runPythonCli(storageArgs(root, newInstanceId(), "status")));
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
    setStorageError(error, { canChange: !backendSession?.external, needsSetup: false });
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
  assertImportIdle();
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
  assertImportIdle();
  const state = await readState();
  assertOwnState(state, guard.session);
  assertImportIdle();
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

function importActive() {
  return importAdmission || Boolean(importJob && ["queued", "running"].includes(importJob.status));
}

function assertImportIdle() {
  if (importActive()) throw new Error("Wait for the import to finish, or stop after the current package.");
}

function importSnapshot() {
  return importJob ? JSON.parse(JSON.stringify(importJob)) : null;
}

function importCli(command, extra = [], onProgress) {
  const root = app.isPackaged ? path.join(process.resourcesPath, "ual-backend") : path.resolve(__dirname, "..");
  return runPythonCli([path.join(root, "bin", "import_packages.py"), command, ...extra], onProgress, root);
}

function importProjectPath(value) {
  if (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) throw new Error("Choose an absolute Unity project path.");
  return fs.realpathSync(value);
}

async function installImportBridge(projectPath) {
  assertImportIdle();
  assertBackendRequestAllowed();
  const project = importProjectPath(projectPath);
  importAdmission = true;
  try {
    const choice = await dialog.showMessageBox({
      type: "warning", title: "Install Unity import bridge?",
      message: "Install the Editor-only import bridge in this project?",
      detail: project + "\n\nAdds an Editor-only script and assembly definition under Assets/UnityAssetLibraryImport. Unity will compile and reload scripts. No packages are imported yet.",
      buttons: ["Cancel", "Install bridge"], defaultId: 0, cancelId: 0, noLink: true,
    });
    return await importCli(choice.response === 1 ? "install" : "inspect", ["--project", project]);
  } finally {
    importAdmission = false;
  }
}

function updateImportProgress(event) {
  if (!event || typeof event !== "object" || !importJob) return;
  for (const key of ["stage", "completed", "total", "current_item", "counts", "results"]) {
    if (Object.hasOwn(event, key)) importJob[key] = event[key];
  }
}

async function executeImport(request, guard) {
  let temp;
  try {
    const state = await backendRequest("/api/state");
    if (guard.generation !== backendGeneration || guard.session !== backendSession) throw new Error("The library changed. Review your selection again.");
    if ([state.job, state.action].some((job) => job && ["queued", "running"].includes(job.status))) throw new Error("Wait for the active library operation before importing.");
    temp = fs.mkdtempSync(path.join(os.tmpdir(), "ual-import-"));
    importCancelFile = path.join(temp, "cancel");
    const manifest = path.join(temp, "request.json");
    fs.writeFileSync(manifest, JSON.stringify({ ...request, id: importJob.id, repo: guard.session.root, root: guard.session.vaultRoot, cancel_file: importCancelFile }), { mode: 0o600 });
    if (importJob.stop_requested) fs.writeFileSync(importCancelFile, "stop");
    importJob.status = "running";
    importJob.stage = "preflight";
    const outcome = await importCli("run", ["--request", manifest], updateImportProgress);
    if (!outcome || !["completed", "failed", "cancelled"].includes(outcome.status)) throw new Error("The import worker ended without a terminal result. Check the project before retrying.");
    updateImportProgress(outcome);
    importJob.status = outcome.status;
    importJob.error = outcome.error || null;
  } catch (error) {
    if (error.outcome?.results?.length) updateImportProgress(error.outcome);
    importJob.status = "failed";
    importJob.error = String(error.message || error);
  } finally {
    importJob.finished_at = Date.now() / 1000;
    importCancelFile = null;
    if (temp) fs.rmSync(temp, { recursive: true, force: true });
  }
}

function startImport(request) {
  assertImportIdle();
  if (quitting) throw new Error("The application is quitting.");
  const guard = assertBackendRequestAllowed();
  if (!request || !["closed", "live"].includes(request.mode) || !Array.isArray(request.packages) || !request.packages.length || request.packages.length > 1000 ||
      Object.keys(request).some((key) => !["project", "mode", "packages"].includes(key)) ||
      request.packages.some((item) => !item || typeof item.asset_key !== "string" || !item.asset_key.trim() || typeof item.file !== "string" || !item.file.trim() || Object.keys(item).some((key) => !["asset_key", "file"].includes(key)))) throw new Error("Choose packages, exact archive versions and a target project before importing.");
  const project = importProjectPath(request.project);
  // Freeze the reviewed selection; the worker validates it against the index under lock.
  const packages = request.packages.map(({ asset_key, file }) => ({ asset_key, file }));
  importJob = { id: crypto.randomUUID(), kind: "import", status: "queued", stage: "queued", project, mode: request.mode,
    completed: 0, total: packages.length, current_item: null, counts: {}, started_at: Date.now() / 1000,
    results: packages.map(({ file }) => ({ file, status: "pending" })), error: null, stop_requested: false };
  importPromise = executeImport({ project, mode: request.mode, packages }, guard);
  return importSnapshot();
}

function stopImport(id) {
  if (!importJob || importJob.id !== id) throw new Error("This import job is no longer available.");
  if (["queued", "running"].includes(importJob.status)) {
    importJob.stop_requested = true;
    if (importCancelFile) fs.writeFileSync(importCancelFile, "stop", { mode: 0o600 });
  }
  return importSnapshot();
}


function registerIpc() {
  if (registered) return;
  ipcMain.handle("import:projects", () => importCli("projects"));
  ipcMain.handle("import:choose-project", async () => {
    const result = await dialog.showOpenDialog({ title: "Choose Unity project", properties: ["openDirectory"] });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("import:inspect", (_event, project) => importCli("inspect", ["--project", importProjectPath(project)]));
  ipcMain.handle("import:install-bridge", (_event, project) => installImportBridge(project));
  ipcMain.handle("import:start", (_event, request) => startImport(request));
  ipcMain.handle("import:status", () => importSnapshot());
  ipcMain.handle("import:stop", (_event, id) => stopImport(id));
  ipcMain.handle("backend:assets", () => backendRequest("/api/assets"));
  ipcMain.handle("backend:tags", () => backendRequest("/api/tags"));
  ipcMain.handle("backend:mutate-tags", (_event, change) => {
    const fields = { create: ["action", "tag"], rename: ["action", "tag", "new_tag"], delete: ["action", "tag"], assign: ["action", "tag", "asset_key"], remove: ["action", "tag", "asset_key"] };
    const allowed = change && Object.hasOwn(fields, change.action) ? fields[change.action] : null;
    if (!allowed || Object.keys(change).length !== allowed.length ||
        !allowed.every((key) => typeof change[key] === "string" && change[key].trim())) {
      throw new Error("Invalid tag change.");
    }
    return postJson("/api/tags", change);
  });
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
  ipcMain.handle("storage:port-choice", (_event, id, choice) => answerPortChoice(id, choice));
  ipcMain.handle("storage:retry", () => {
    if (storageState.busy || transitionPromise || backendSession || quitting) throw new Error("The backend cannot be retried right now.");
    startupPromise = bootstrapStorage();
  });
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
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: { x: 16, y: 24 },
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.on("close", (event) => {
    if (!quitReady && importActive()) {
      event.preventDefault();
      app.quit();
    }
  });

  const devUrl = process.env.ELECTRON_RENDERER_URL;
  if (devUrl) window.loadURL(devUrl);
  else window.loadFile(path.join(app.getAppPath(), "dist", "renderer", "index.html"));
}

app.whenReady().then(() => {
  if (!app.isPackaged && app.dock) {
    app.dock.setIcon(path.join(app.getAppPath(), "assets", "icons", "icon.png"));
  }
  registerIpc();
  createWindow();
  startupPromise = bootstrapStorage();
});

app.on("before-quit", (event) => {
  if (quitReady) return;
  event.preventDefault();
  if (quitting || importAdmission) return;
  if (importActive()) {
    const choice = dialog.showMessageBoxSync({ type: "warning", title: "Import in progress",
      message: "Stop the queue and quit after the current package finishes?",
      detail: "Unity will not be interrupted mid-import. Already imported assets remain in the project.",
      buttons: ["Keep importing", "Stop after current and quit"], defaultId: 0, cancelId: 0, noLink: true });
    if (choice !== 1) return;
    stopImport(importJob.id);
  }
  quitting = true;
  if (pendingPortChoice) answerPortChoice(pendingPortChoice.id, { action: "cancel" });
  void Promise.allSettled([startupPromise, saveQueue, importPromise, transitionPromise || Promise.resolve()]).then(() => stopOwnedBackend()).finally(() => { quitReady = true; app.quit(); });
});

app.on("window-all-closed", () => {
  if (!quitting) app.quit();
});
