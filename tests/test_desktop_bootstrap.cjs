const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ual-bootstrap-"));
(async () => {
  try {
    const resources = path.join(temp, "resources");
    fs.mkdirSync(path.join(resources, "ual-backend", "bin"), { recursive: true });
    const selected = path.join(temp, "library");
    fs.mkdirSync(selected);
    let pickerResult = { canceled: true, filePaths: [] };
    let quitCalls = 0;
    const revealed = [];
    const appPaths = { appData: temp, userData: path.join(temp, "Unity Asset Library") };
    const dialog = {
      showOpenDialog: async () => pickerResult,
    };
    const app = {
      isPackaged: true,
      getPath: (name) => appPaths[name],
      setPath: (name, value) => { appPaths[name] = value; },
      getAppPath: () => temp,
      whenReady: () => ({ then() {} }),
      on() {},
      quit: () => { quitCalls += 1; },
    };
    const handlers = {};
    const ipcMain = { handle: (channel, fn) => { handlers[channel] = fn; } };
    const context = vm.createContext({
      require: (name) => name === "electron"
        ? { app, dialog, ipcMain, BrowserWindow: class {}, shell: { openExternal() {}, showItemInFolder: (p) => (revealed.push(p), p) } }
        : name === "./port-recovery.cjs" ? require("../electron/port-recovery.cjs") : require(name),
      process: { ...process, resourcesPath: resources },
      __dirname: path.join(temp, "dev", "electron"),
      console, AbortSignal,
      setTimeout,
      clearTimeout,
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../electron/main.cjs"), "utf8"), context);

    // A compatible external backend is surfaced but never made mutable by this desktop shell.
    const externalState = {
      service: "unity-asset-library", api_version: 7, ready: true, csrf: "external-token",
      vault_root: selected, repo: temp, instance_id: "other-process",
    };
    context.fetch = async () => ({ ok: true, text: async () => JSON.stringify(externalState) });
    await vm.runInContext("bootstrapStorage()", context);
    const externalStorage = vm.runInContext("getStorage()", context);
    assert.equal(externalStorage.path, selected);
    assert.equal(externalStorage.ready, true);
    assert.equal(externalStorage.canChange, false);
    vm.runInContext("registerIpc()", context);
    // Invalid preference payloads must fail before contacting the backend.
    for (const bad of [null, "x", [], { theme: 5 }, { nope: "x" }, { action: { id: "a", kind: "bogus", phase: "plan" } }]) {
      await assert.rejects(async () => handlers["prefs:set"]({}, bad, selected), /Invalid preferences/);
    }
    await assert.rejects(async () => handlers["prefs:set"]({}, { theme: "dark" }, ""), /Invalid preferences/);
    assert.throws(() => handlers["prefs:set"]({}), /Invalid preferences/);
    const choicePromise = vm.runInContext("askPortChoice({ port: 8765, suggestion: 8766, owner: { pid: 123, identity: \"test\" }, detail: \"\", confirm: false })", context);
    const requestId = vm.runInContext("getStorage().portRecovery.id", context);
    const answer = (id, choice) => handlers["storage:port-choice"]({}, id, choice);
    assert.throws(() => answer("stale", { action: "cancel" }), /expired/);
    assert.throws(() => answer(requestId, { action: "confirm-stop" }), /Invalid/);
    assert.throws(() => answer(requestId, { action: "use", port: 65536 }), /Invalid/);
    answer(requestId, { action: "cancel" });
    assert.equal((await choicePromise).action, "cancel");
    assert.throws(() => answer(requestId, { action: "cancel" }), /expired/);
    vm.runInContext("storageState.portRecovery = null", context);
    // Invalid tag payloads must fail before contacting the backend.
    for (const change of [null, {}, { action: "constructor", tag: "ui" },
      { action: "rename", tag: "ui" }, { action: "assign", tag: "ui", asset_key: " " },
      { action: "delete", tag: "ui", extra: true }]) {
      assert.throws(() => handlers["backend:mutate-tags"]({}, change), /Invalid tag change/);
    }
    // Reveal containment: valid library file reveals, symlink escape and missing file are rejected.
    const reveal = (p) => handlers["shell:reveal-item"]({}, p);
    const insideFile = path.join(selected, "file.txt");
    fs.writeFileSync(insideFile, "x");
    assert.equal(reveal("file.txt"), fs.realpathSync(insideFile));
    const outsideFile = path.join(temp, "outside.txt");
    fs.writeFileSync(outsideFile, "x");
    const escapeLink = path.join(selected, "escape.txt");
    fs.symlinkSync(outsideFile, escapeLink);
    await assert.rejects(async () => reveal("escape.txt"), /outside the library/i);
    await assert.rejects(async () => reveal("ghost.txt"), /no longer on disk/i);
    assert.notEqual(reveal("file.txt"), escapeLink);

    // Native picker returns only a selected path and cancellation leaves storage untouched.
    pickerResult = { canceled: true, filePaths: [] };
    assert.equal(await vm.runInContext("chooseStorageFolder()", context), null);
    pickerResult = { canceled: false, filePaths: [selected] };
    assert.equal(await vm.runInContext("chooseStorageFolder()", context), selected);
    assert.equal(fs.existsSync(path.join(temp, "config.json")), false);
    assert.equal(fs.existsSync(path.join(temp, "Unity Asset Index", "backend", "config.json")), false);

    console.log("PASS: external backend immutability, picker cancellation and no config writes");
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
