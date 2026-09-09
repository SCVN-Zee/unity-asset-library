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
  { asset_key: 'audio-other', name: 'City Audio', author: 'Studio', category: { path: 'Audio/Ambient', levels: ['Audio', 'Ambient'] }, versions: [{ file: 'City.unitypackage' }] },
  { asset_key: 'tools-favorite', name: 'Editor Tool', author: 'Studio', category: { path: 'Tools', levels: ['Tools'] }, versions: [{ file: 'Editor.unitypackage' }] },
  { asset_key: 'docs', name: 'Documentation', author: 'Studio', versions: [{ file: 'Docs.zip' }] },
];
const preload = path.join(temporary, 'preload.cjs');
fs.writeFileSync(preload, `const favorites = new Set(['audio-favorite', 'tools-favorite']); require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getState: async () => ({ pending_enrichment: 0, job: null, action: null, ready: true, vault_root: '/fixture' }),
  getAssets: async () => ({ assets: ${JSON.stringify(fixtures)} }),
  favorites: async () => ({ favorites: [...favorites] }),
  setFavorite: async (key, enabled) => { if (enabled) favorites.add(key); else favorites.delete(key); return { favorites: [...favorites] }; },
  getTags: async () => ({ tags: [], assignments: {} }),
  getPreferences: async () => { try { return JSON.parse(localStorage.getItem('ual:prefs') || '{}'); } catch { return {}; } },
  setPreferences: async (prefs) => { localStorage.setItem('ual:prefs', JSON.stringify(prefs)); return prefs; },
  getLegacyLibraryRoot: async () => null,
  importProjects: async () => [],
  chooseImportProject: async () => null,
  inspectImportProject: async () => ({ path: '', title: '', version: '', open: false, bridge_installed: false, bridge_ready: false }),
  installImportBridge: async () => ({ path: '', title: '', version: '', open: false, bridge_installed: false, bridge_ready: false }),
  startImport: async () => { throw new Error('not wired in fixture'); },
  getImport: async () => JSON.parse(localStorage.getItem("ual:test-import") || "null"),
  stopImport: async () => { throw new Error('not wired in fixture'); }
});`);
let win;
let exitCode = 0;
const evaluate = (code) => win.webContents.executeJavaScript(code).catch(error => { throw new Error(`Renderer evaluation failed: ${code}`, { cause: error }); });
async function waitFor(expression) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function click(selector, modifiers = []) {
  const bounds = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, modifiers, ...bounds });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, modifiers, ...bounds });
}
async function selection(names) {
  const expected = JSON.stringify([...names].sort());
  await waitFor(`JSON.stringify([...document.querySelectorAll('.import-check input:checked')].map(e => e.closest('.asset-cell').querySelector('.asset-card,.list-row').title).sort()) === ${JSON.stringify(expected)}`);
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.asset-card.selected,.list-row.selected')].map(e => e.title).sort()"), [...names].sort());
  assert.equal(await evaluate("Number(document.querySelector('.import-controls .primary')?.textContent.match(/\\d+/)?.[0] || 0)"), names.length);
}
app.whenReady().then(async () => {
  try {
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    await waitFor('document.querySelector("img.brand-mark")?.naturalWidth > 0');
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
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
    await win.webContents.insertText('aUdIo FoReSt');
    await waitFor('document.querySelectorAll(".list-row").length === 1');
    assert.equal(await evaluate('document.querySelector(".inspector-title h2").textContent'), 'Forest Audio');
    win.setContentSize(600, 800);
    await win.reload();
    await waitFor('document.querySelectorAll(".list-row").length === 1');
    assert.equal(await evaluate(`document.querySelector('[aria-label="Search library"]').value`), 'aUdIo FoReSt');
    await click('.list-row');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Close asset details"');
    await click('[aria-label="Close asset details"]');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Show asset details"');
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true);
    console.log('PASS: combined filters, independent removal, list/detail coherence, search shortcut, compact detail focus, no horizontal overflow');

    win.setContentSize(1200, 800);
    await evaluate('localStorage.clear()');
    await win.reload();
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    for (const view of ['Grid', 'List']) {
      await click(`[aria-label="${view} view"]`);
      await waitFor(`document.querySelectorAll('${view === 'Grid' ? '.asset-card' : '.list-row'}').length === 4`);
      await click('[title="City Audio"]'); await selection(['City Audio']);
      await click('[title="Forest Audio"]', ['meta']); await selection(['City Audio', 'Forest Audio']);
      await click('[title="City Audio"]', ['meta']); await selection(['Forest Audio']);
      await click('[title="City Audio"]'); await selection(['City Audio']);
      await click('[title="Forest Audio"]', ['shift']); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await click('[title="Editor Tool"]', ['shift']); await selection(['City Audio', 'Editor Tool']);
      await click('[title="Forest Audio"]'); await selection(['Forest Audio']);
      await click('[title="City Audio"]', ['shift']); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await click('[title="City Audio"]'); await selection(['City Audio']);
      await click('[title="Forest Audio"]', ['meta']); await selection(['City Audio', 'Forest Audio']);
      await click('[title="Editor Tool"]', ['meta', 'shift']); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await click('[aria-label="Select City Audio for import"]'); await selection(['Editor Tool', 'Forest Audio']);
      await click('[aria-label="Select Forest Audio for import"]', ['shift']); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await click('[title="Documentation"]'); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await click('.asset-cell [aria-label="Remove Forest Audio from favorites"]'); await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await waitFor('!!document.querySelector(\'[aria-label="Add Forest Audio to favorites"]\')');
      await click('.asset-cell [aria-label="Add Forest Audio to favorites"]');
      await click('[title="City Audio"]'); await selection(['City Audio']);
      await evaluate('(() => { const sort = document.querySelector(\'[aria-label="Sort assets"]\'); sort.value = sort.value === "name" ? "author" : "name"; sort.dispatchEvent(new Event("change", { bubbles: true })); })()');
      await click('[title="Forest Audio"]', ['shift']); await selection(['Forest Audio']);
      await click('[title="City Audio"]'); await selection(['City Audio']);
      await evaluate('document.querySelector(\'[aria-label="Search library"]\').focus()');
      await win.webContents.insertText('Audio');
      await waitFor('document.querySelectorAll(".asset-cell").length === 2');
      await click('[title="Forest Audio"]', ['shift']); await selection(['Forest Audio']);
      await click('[aria-label="Clear search"]');
      await waitFor('document.querySelectorAll(".asset-cell").length === 4');
    }
    console.log('PASS: grid/list Cmd toggles, forward/backward/union ranges, checkbox parity, ineligible skipping, favorite isolation and filter/sort anchor reset');
    await evaluate(`localStorage.setItem("ual:test-import", JSON.stringify({ id: "preparation", kind: "import", status: "running", project: "/fixture/project", mode: "closed", completed: 0, total: 1, results: [{ file: "Cloud.unitypackage", status: "downloading", bytes_completed: 50, bytes_total: 100 }] }))`);
    await win.reload();
    await waitFor(`!!document.querySelector('[aria-label="Open import progress"]')`);
    assert.equal(await evaluate(`document.querySelector(".task-details").hidden`), true);
    win.webContents.focus();
    await evaluate(`document.querySelector(".topbar .task-indicator").focus()`);
    await waitFor(`!document.querySelector(".task-details").hidden`);
    await evaluate(`document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))`);
    await waitFor(`document.querySelector(".task-details").hidden`);
    const taskBounds = await evaluate(`(() => { const r = document.querySelector(".task-indicator").getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
    win.webContents.sendInputEvent({ type: "mouseMove", ...taskBounds });
    await waitFor(`!document.querySelector(".task-details").hidden`);
    await click('[aria-label="Open import progress"]');
    await waitFor(`!!document.querySelector('.import-dialog')`);
    await click(".import-dialog .dialog-foot .quiet");
    await evaluate(`localStorage.setItem("ual:test-import", JSON.stringify({ id: "preparation", kind: "import", status: "failed", error: "Package could not be imported", completed: 0, total: 1, results: [] }))`);
    await waitFor(`!!document.querySelector('.task-indicator[data-status="failed"]')`);
    await click(".task-indicator");
    await waitFor(`!document.querySelector(".task-details").hidden`);
    assert.equal(await evaluate(`document.querySelector(".task-details .progress-error").textContent.trim()`), "Package could not be imported");
    await click('[aria-label="Dismiss Import result"]');
    await waitFor(`!document.querySelector(".task-indicator")`);
    console.log("PASS: task hover, focus, Escape, review import, failure details and dismissal");
  } catch (error) {
    console.error(error);
    exitCode = 1;
  } finally {
    await fs.promises.rm(temporary, { recursive: true, force: true });
    app.exit(exitCode);
  }
});
