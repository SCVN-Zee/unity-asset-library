const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const PORT = 8765;
const SERVICE = "unity-asset-index";
const BASE_URL = `http://127.0.0.1:${PORT}`;
const INCOMPATIBLE_BACKEND = `An incompatible backend is listening on port ${PORT}. Stop that Python server and restart the desktop app.`;
let backendProcess = null;
let ownsBackend = false;
let quitting = false;
const backendOutput = [];

function isViewerState(value) {
  return Boolean(
    value &&
      value.service === SERVICE &&
      value.api_version === 3 &&
      value.ready === true &&
      typeof value.csrf === "string" &&
      value.csrf.length > 0,
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
    throw new Error(value.error === "plan_changed" ? "The library changed. Run the action again to review a fresh plan before applying." : value.detail || value.error || `Backend request failed (${response.status}).`);
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
  const template = path.join(process.resourcesPath, "uai-backend");
  // Keep per-user configuration and inventory out of application resources.
  const root = app.isPackaged
    ? path.join(app.getPath("userData"), "backend")
    : path.resolve(__dirname, "..");
  const configPath = path.join(root, "config.json");
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  if (app.isPackaged) {
    fs.cpSync(path.join(template, "bin"), path.join(root, "bin"), { recursive: true });
  }
  if (!fs.existsSync(configPath)) {
    const folders = dialog.showOpenDialogSync({
      title: "Choose your Unity asset folder",
      properties: ["openDirectory"],
    });
    if (!folders?.length) return null;
    fs.writeFileSync(configPath, `${JSON.stringify({ vault_root: folders[0], output_dir: "." }, null, 2)}\n`);
  }
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  if (config.output_dir !== ".") {
    config.output_dir = ".";
    fs.writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`);
  }
  const cachePath = path.join(root, "state", "cache.json");
  if (app.isPackaged && !fs.existsSync(cachePath)) {
    fs.copyFileSync(path.join(template, "state", "cache.json"), cachePath);
  }
  return root;
}

function pythonCommand() {
  return process.env.UAI_PYTHON || (process.platform === "win32" ? "python" : "python3");
}

async function ensureBackend() {
  let existing;
  try {
    existing = await readState();
  } catch {
    // No viewer is listening yet. Start our owned backend below.
  }
  if (existing) {
    if (isViewerState(existing)) return;
    throw new Error(INCOMPATIBLE_BACKEND);
  }

  const root = preparePackagedBackend();
  if (!root) return false;
  const script = path.join(root, "bin", "server.py");
  backendOutput.length = 0;
  const child = spawn(pythonCommand(), [script, "--port", String(PORT)], {
    cwd: root,
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

  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (child.exitCode !== null) break;
    try {
      const state = await readState();
      if (isViewerState(state)) return;
    } catch {
      // The server may still be importing the backend modules.
    }
    await delay(100);
  }

  const detail = backendOutput.slice(-20).join("\n") || "The Python backend did not become ready.";
  await stopOwnedBackend();
  throw new Error(detail);
}

async function postJson(endpoint, body = {}) {
  const state = await readState();
  if (!isViewerState(state)) throw new Error(INCOMPATIBLE_BACKEND);
  return requestJson(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, csrf: state.csrf }),
  });
}

function registerIpc() {
  ipcMain.handle("backend:state", () => readState());
  ipcMain.handle("backend:assets", () => requestJson("/api/assets"));
  ipcMain.handle("backend:resync", () => postJson("/api/resync/plan"));
  ipcMain.handle("backend:resync-apply", (_event, planHash) => postJson("/api/resync/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:cleanup-plan", () => postJson("/api/cleanup/plan"));
  ipcMain.handle("backend:cleanup-apply", (_event, planHash) => postJson("/api/cleanup/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:organize-plan", () => postJson("/api/organize/plan"));
  ipcMain.handle("backend:organize-apply", (_event, planHash) => postJson("/api/organize/apply", { plan_hash: planHash }));
  ipcMain.handle("backend:enrich-start", () => postJson("/api/enrich/start"));
  ipcMain.handle("backend:enrich-cancel", () => postJson("/api/enrich/cancel"));
  ipcMain.handle("backend:job", (_event, jobId) => requestJson(`/api/job/${encodeURIComponent(jobId)}`));
  ipcMain.handle("shell:open-external", (_event, url) => {
    const parsed = new URL(url);
    if (!["https:", "http:"].includes(parsed.protocol)) throw new Error("Only web links can be opened.");
    return shell.openExternal(parsed.toString());
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1480,
    height: 960,
    minWidth: 980,
    minHeight: 680,
    backgroundColor: "#111214",
    title: "Unity Asset Index",
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

app.whenReady().then(async () => {
  registerIpc();
  try {
    if (await ensureBackend() === false) {
      app.quit();
      return;
    }
    createWindow();
  } catch (error) {
    dialog.showErrorBox("Unity Asset Index could not start", String(error.message || error));
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (quitting) return;
  event.preventDefault();
  quitting = true;
  void stopOwnedBackend().finally(() => app.quit());
});

app.on("window-all-closed", () => {
  if (!quitting) app.quit();
});
