const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-asset-host-'));
const entry = path.resolve(__dirname, '../electron/main.cjs');
const localRequire = createRequire(entry);
(async () => {
  try {
    const handlers = {};
    const events = {};
    let finishHydration;
    let hydrationSignal;
    const asset = { asset_key: 'cloud', versions: [{ file: 'cloud.unitypackage', availability: 'cloud_only' }] };
    const state = { job: null, action: null };
    const app = { isPackaged: false, getPath: () => temporary, setPath() {}, whenReady: () => ({ then() {} }), on: (event, fn) => { events[event] = fn; }, quit() {} };
    const context = vm.createContext({
      require: name => name === 'electron' ? {
        app, ipcMain: { handle: (name, fn) => { handlers[name] = fn; } },
      } : name === './asset-actions.cjs' ? {
        hydrateAsset: async (root, selected, signal) => {
          assert.equal(root, temporary); assert.equal(selected.asset_key, 'cloud');
          hydrationSignal = signal;
          return new Promise((resolve, reject) => { finishHydration = { resolve, reject }; signal.addEventListener('abort', () => reject(signal.reason), { once: true }); });
        },
      } : localRequire(name),
      __dirname: path.dirname(entry), process: { ...process, resourcesPath: temporary }, console, setTimeout, clearTimeout, AbortSignal, AbortController,
      fixtureRoot: temporary,
      fakeBackend: async endpoint => endpoint === '/api/assets' ? { assets: [asset] } : endpoint === '/api/favorites' ? { favorites: ['cloud'] } : state,
    });
    vm.runInContext(fs.readFileSync(entry, 'utf8'), context);
    vm.runInContext('backendSession = { vaultRoot: fixtureRoot }; storageState.busy = false; backendRequest = fakeBackend; registerIpc()', context);
    assert.equal(handlers['assets:context-menu'], undefined, 'Menu rendering belongs to the styled renderer');
    assert.throws(() => handlers['assets:download']({}, ''), /indexed asset/);
    const download = handlers['assets:download']({}, 'cloud');
    assert.throws(() => handlers['assets:download']({}, 'cloud'), /Wait for the package download/);
    assert.throws(() => handlers['import:start']({}, {}), /Wait for the package download/);
    await assert.rejects(handlers['backend:cleanup-plan']({}), /Wait for the package download/);
    await assert.rejects(handlers['storage:save']({}, temporary), /Wait for the package download/);
    await new Promise(resolve => setImmediate(resolve));
    assert(hydrationSignal, 'Download uses an abortable stream');
    finishHydration.resolve({ downloaded: 1 });
    assert.equal((await download).downloaded, 1);
    assert.equal(vm.runInContext('downloadController', context), null);
    await assert.rejects(handlers['assets:download']({}, 'stale'), /no longer indexed/);
    assert.equal(vm.runInContext('downloadController', context), null, 'Failed preflight releases admission');
    state.job = { status: 'running' };
    await assert.rejects(handlers['assets:download']({}, 'cloud'), /library operation/);
    state.job = null;
    const failing = handlers['assets:download']({}, 'cloud');
    await new Promise(resolve => setImmediate(resolve));
    finishHydration.reject(new Error('Cloud provider offline'));
    await assert.rejects(failing, /Cloud provider offline/);
    assert.equal(vm.runInContext('downloadController', context), null);
    const closing = handlers['assets:download']({}, 'cloud');
    await new Promise(resolve => setImmediate(resolve));
    let prevented = false;
    events['before-quit']({ preventDefault: () => { prevented = true; } });
    await assert.rejects(closing, /application is closing/);
    assert.equal(prevented, true);
    assert.equal(hydrationSignal.aborted, true);
    console.log('PASS: indexed identity, download admission, conflicting mutation guards, failure cleanup and quit cancellation');
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
