// Run: npm run build && npx electron tests/test_renderer_browsing.cjs
// Isolated renderer fixture: never opens the real library or writes its data.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-browsing-'));
app.setPath('userData', path.join(temporary, 'profile'));
const fixtures = [
  { asset_key: 'audio-favorite', name: 'Forest Audio', author: 'Studio', category: { path: 'Audio/Ambient', levels: ['Audio', 'Ambient'] }, versions: [{ file: 'Audio/Forest v1.unitypackage', size_bytes: 1024 }] },
  { asset_key: 'audio-other', name: 'City Audio', author: 'Studio', category: { path: 'Audio/Ambient', levels: ['Audio', 'Ambient'] }, versions: [] },
  { asset_key: 'tools-favorite', name: 'Editor Tool', author: 'Studio', category: { path: 'Tools', levels: ['Tools'] }, versions: [] },
];
const preload = path.join(temporary, 'preload.cjs');
fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getAssets: async () => ({ assets: ${JSON.stringify(fixtures)} }),
  getState: async () => ({ pending_enrichment: 0, job: null, action: null, ready: true }),
  favorites: async () => ({ favorites: ['audio-favorite', 'tools-favorite'] }),
  getTags: async () => ({ tags: [], assignments: {} }),
  importProjects: async () => [],
  chooseImportProject: async () => null,
  inspectImportProject: async () => ({ path: '', title: '', version: '', open: false, bridge_installed: false, bridge_ready: false }),
  installImportBridge: async () => ({ path: '', title: '', version: '', open: false, bridge_installed: false, bridge_ready: false }),
  startImport: async () => { throw new Error('not wired in fixture'); },
  getImport: async () => null,
  stopImport: async () => { throw new Error('not wired in fixture'); }
});`);
let win;
const evaluate = (code) => win.webContents.executeJavaScript(code);
async function waitFor(expression) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function click(selector) {
  const bounds = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...bounds });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...bounds });
}
app.whenReady().then(async () => {
  try {
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    await waitFor('document.querySelector("img.brand-mark")?.naturalWidth > 0');
    await waitFor('document.querySelectorAll(".asset-card").length === 3');
    await click('.side-section .nav-row:nth-of-type(2)');
    await waitFor('document.querySelectorAll(".asset-card").length === 2');
    await click('.category-tree button[title="Audio"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 1');
    assert.equal(await evaluate('document.querySelector(".card-title").textContent'), 'Forest Audio');
    await click('[aria-label="Remove category filter"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 2');
    await click('[aria-label="List view"]');
    await waitFor('document.querySelectorAll(".list-row").length === 2');
    await click('[aria-label="Hide asset details"]');
    await waitFor('document.querySelector(".inspector").hidden');
    await click('[aria-label="Show asset details"]');
    await waitFor('!document.querySelector(".inspector").hidden');
    assert.equal(await evaluate('document.querySelector(".inspector-title h2").textContent'), 'Forest Audio');
    await evaluate('document.activeElement.blur(); document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }))');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Search library"');
    await win.webContents.insertText("aUdIo FoReSt");
    await waitFor('document.querySelectorAll(".list-row").length === 1');
    assert.equal(await evaluate('document.querySelector(".inspector-title h2").textContent'), "Forest Audio");
    win.setContentSize(600, 800);
    await win.reload();
    await waitFor('document.querySelectorAll(".list-row").length === 1');
    assert.equal(await evaluate(`document.querySelector('[aria-label="Search library"]').value`), "aUdIo FoReSt");
    await click('.list-row');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Close asset details"');
    await click('[aria-label="Close asset details"]');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Show asset details"');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
    console.log('PASS: combined filters, independent removal, list/detail coherence, search shortcut, compact detail focus, no horizontal overflow');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    if (win) win.destroy();
    await fs.promises.rm(temporary, { recursive: true, force: true });
    app.exit(process.exitCode || 0);
  }
});
