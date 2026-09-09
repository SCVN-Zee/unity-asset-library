const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-import-host-'));
const entry = path.resolve(__dirname, '../electron/main.cjs');
const localRequire = createRequire(entry);
(async () => {
  try {
    const project = path.join(root, 'Unity project');
    fs.mkdirSync(project);
    const handlers = {};
    const calls = [];
    let choice = 0;
    let finishWorker;
    const appEvents = {};
    let nativeQuits = 0;
    const state = { service: 'unity-asset-library', api_version: 6, ready: true, csrf: 'test', vault_root: root, repo: root, instance_id: 'fixture', job: null, action: null };
    const app = { isPackaged: false, getPath: () => root, setPath() {}, whenReady: () => ({ then() {} }), on: (name, handler) => { appEvents[name] = handler; }, quit: () => { nativeQuits++; } };
    const context = vm.createContext({
      require: (name) => name === "electron" ? { app, ipcMain: { handle: (name, fn) => { handlers[name] = fn; } }, dialog: { showMessageBox: async () => ({ response: choice }), showMessageBoxSync: () => choice } } : localRequire(name),
      __dirname: path.dirname(entry), process: { ...process, resourcesPath: root }, console, setTimeout, clearTimeout, AbortSignal,
      fetch: async () => ({ ok: true, text: async () => JSON.stringify(state) }),
      fakeCli: (command, extra, onProgress) => {
        calls.push(command);
        if (command !== 'run') return Promise.resolve({ path: project, bridge_installed: command === 'install' });
        onProgress({ completed: 1, results: [{ file: 'one.unitypackage', status: 'imported' }, { file: 'two.unitypackage', status: 'importing' }] });
        return new Promise((resolve, reject) => { finishWorker = { resolve, reject }; });
      },
    });
    vm.runInContext(fs.readFileSync(entry, 'utf8'), context);
    await vm.runInContext('bootstrapStorage()', context);
    vm.runInContext('registerIpc(); importCli = fakeCli', context);
    const request = { project, mode: 'closed', packages: [{ asset_key: 'one', file: 'one.unitypackage' }, { asset_key: 'two', file: 'two.unitypackage' }] };
    const start = () => handlers['import:start']({}, request);
    const job = start();
    assert.throws(start, /Wait for the import/);
    await assert.rejects(handlers['storage:save']({}, root), /Wait for the import/);
    await assert.rejects(handlers['backend:cleanup-plan']({}), /Wait for the import/);
    await new Promise(resolve => setImmediate(resolve));
    const snapshot = handlers['import:status']({});
    snapshot.results[0].status = 'failed';
    assert.equal(handlers['import:status']({}).results[0].status, 'imported');
    assert.throws(() => handlers['import:stop']({}, 'old-job'), /no longer available/);
    const stopping = handlers['import:stop']({}, job.id);
    assert.equal(stopping.status, 'running', 'Stop must not claim cancellation while Unity is still importing');
    assert.equal(stopping.stop_requested, true);
    const error = new Error('second package failed');
    error.outcome = { status: 'failed', completed: 1, results: [{ file: 'one.unitypackage', status: 'imported' }, { file: 'two.unitypackage', status: 'failed', error: 'archive invalid' }] };
    finishWorker.reject(error);
    await vm.runInContext('importPromise', context);
    const failed = handlers['import:status']({});
    assert.equal(failed.status, 'failed');
    assert.equal(failed.completed, 1);
    assert.equal(failed.results[1].error, 'archive invalid');
    assert.equal(vm.runInContext('importActive()', context), false);

    // A native confirmation cancellation must never install into a project.
    await handlers['import:install-bridge']({}, project);
    assert.equal(calls.at(-1), 'inspect');
    choice = 1;
    await handlers['import:install-bridge']({}, project);
    assert.equal(calls.at(-1), 'install');
    assert.throws(() => handlers['import:start']({}, { ...request, packages: [] }), /Choose packages/);
    assert.throws(() => handlers['import:start']({}, { ...request, mode: 'auto' }), /Choose packages/);
    vm.runInContext(`importCli = async () => { const error = new Error("archive no longer indexed"); error.outcome = { status: "failed", results: [], total: 0, completed: 0 }; throw error; }`, context);
    start();
    await vm.runInContext("importPromise", context);
    const preflight = handlers["import:status"]({});
    assert.equal(preflight.total, 2);
    assert.equal(preflight.results.length, 2);
    assert(preflight.results.every(row => row.status === "pending"), "Preflight failure must retain the unstarted reviewed selection");
    vm.runInContext("importCli = fakeCli", context);
    start();
    await new Promise(resolve => setImmediate(resolve));
    let prevented = 0;
    appEvents["before-quit"]({ preventDefault: () => { prevented++; } });
    appEvents["before-quit"]({ preventDefault: () => { prevented++; } });
    assert.equal(prevented, 2, "Repeated quit must not bypass an in-flight import");
    assert.equal(nativeQuits, 0);
    finishWorker.resolve({ status: "cancelled", completed: 1, results: [{ file: "one.unitypackage", status: "imported" }, { file: "two.unitypackage", status: "cancelled" }] });
    await vm.runInContext("importPromise", context);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(nativeQuits, 1);
    console.log('PASS: import admission, storage/mutation exclusion, safe stop, retained partial failure, explicit bridge consent');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
