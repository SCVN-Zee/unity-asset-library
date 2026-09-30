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
  { asset_key: 'audio-favorite', name: 'Forest Audio', author: 'Studio', category: { path: 'Audio/Ambient/Nature', levels: ['Audio', 'Ambient', 'Nature'] }, versions: [{ file: 'Audio/Forest v1.unitypackage', size_bytes: 1024 }] },
  { asset_key: 'audio-other', name: 'City Audio', author: 'Studio', category: { path: 'Audio/Ambient', levels: ['Audio', 'Ambient'] }, versions: [{ file: 'City.unitypackage' }] },
  { asset_key: 'tools-favorite', name: 'Editor Tool', author: 'Studio', category: { path: 'Tools', levels: ['Tools'] }, versions: [{ file: 'Editor.unitypackage' }] },
  { asset_key: 'docs', name: 'Documentation', author: 'Studio', versions: [{ file: 'Docs.zip' }] },
];
fixtures[0].versions[0].availability = "cloud_only";
fixtures[1].versions[0].availability = "local";
const preload = path.join(temporary, 'preload.cjs');
fs.writeFileSync(preload, `const favorites = new Set(['audio-favorite', 'tools-favorite']); require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getState: async () => ({ pending_enrichment: 0, job: null, action: null, ready: true, vault_root: '/fixture' }),
  getAssets: async () => ({ assets: JSON.parse(localStorage.getItem("ual:test-assets") || ${JSON.stringify(JSON.stringify(fixtures))}) }),
  favorites: async () => ({ favorites: [...favorites] }),
  setFavorite: async (key, enabled) => { if (enabled) favorites.add(key); else favorites.delete(key); return { favorites: [...favorites] }; },
  getTags: async () => ({ tags: [], assignments: {} }),
  getPreferences: async () => { try { return JSON.parse(localStorage.getItem('ual:prefs') || '{}'); } catch { return {}; } },
  setPreferences: async (prefs) => { localStorage.setItem('ual:prefs', JSON.stringify(prefs)); return prefs; },
  getLegacyLibraryRoot: async () => null,
  importProjects: async () => [{ path: "/fixture/project", title: "Fixture", version: "6000.3.15f1" }],
  chooseImportProject: async () => null,
  inspectImportProject: async (path) => ({ path, title: "Fixture", version: "6000.3.15f1" }),
  startImport: async (request) => {
    localStorage.setItem("ual:test-request", JSON.stringify(request));
    return { id: "fixture-install", kind: "import", status: "completed", project: request.project, completed: request.packages.length, total: request.packages.length, results: request.packages.map(({ file }) => ({ file, status: "installed", ...(request.overwrite ? { backup_path: "/fixture/project/.ual-import-backups/fixture" } : {}) })) };
  },
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
async function key(keyCode, modifiers = []) {
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
  win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}
async function chooseOption(label, text) {
  const selector = '[role="combobox"][aria-label="' + label + '"]';
  await click(selector);
  await waitFor('document.querySelector(' + JSON.stringify(selector) + ').getAttribute("aria-expanded") === "true"');
  const id = await evaluate('Array.from(document.querySelectorAll("[role=listbox] [role=option]")).find(e => e.textContent.includes(' + JSON.stringify(text) + '))?.id');
  assert(id, 'Option is available: ' + text);
  await evaluate('document.getElementById(' + JSON.stringify(id) + ').scrollIntoView({ block: "nearest" })');
  await click('[id="' + id + '"]');
  await waitFor('!document.querySelector("[role=listbox]")');
}
async function selection(names) {
  const expected = JSON.stringify([...names].sort());
  await waitFor(`JSON.stringify([...document.querySelectorAll('.import-check input:checked')].map(e => e.closest('.asset-cell').querySelector('.asset-card,.list-row').title).sort()) === ${JSON.stringify(expected)}`);
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.asset-card.selected,.list-row.selected')].map(e => e.title).sort()"), [...names].sort());
}
app.whenReady().then(async () => {
  try {
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    await waitFor('document.querySelector("img.brand-mark")?.naturalWidth > 0');
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    for (const view of ['Grid', 'List']) {
      await click('[aria-label="' + view + ' view"]');
      await waitFor('document.querySelectorAll("' + (view === 'Grid' ? '.asset-card' : '.list-row') + '").length === 4');
      await evaluate('document.querySelector("[data-asset-key=audio-other]").focus()');
      await key('A', ['meta']);
      await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
      await key('A', ['meta', 'shift']);
      await selection([]);
      await key('Space');
      await selection(['City Audio']);
      await key(view === 'Grid' ? 'Right' : 'Down');
      await waitFor('document.activeElement.title === "Documentation"');
      await selection(['City Audio']);
      await key(view === 'Grid' ? 'Right' : 'Down', ['shift']);
      await waitFor('document.activeElement.title === "Editor Tool"');
      await selection(['City Audio', 'Editor Tool']);
      await key(view === 'Grid' ? 'Left' : 'Up', ['shift']);
      await selection(['City Audio']);
      await key('End');
      await waitFor('document.activeElement.title === "Forest Audio"');
      await selection(['City Audio']);
      await key('Enter');
      await waitFor('document.activeElement.getAttribute("aria-label") === "Close asset details"');
      await selection(['City Audio']);
      await key('Escape');
      await waitFor('document.activeElement.title === "Forest Audio" && document.querySelector(".inspector").hidden');
      await key('I', ['meta', 'shift']);
      await waitFor('!!document.querySelector(".import-dialog") && !!document.activeElement.closest(".import-dialog")');
      assert.equal(await evaluate('localStorage.getItem("ual:test-request")'), null, 'Review shortcut never imports');
      await key('K', ['meta']);
      assert.equal(await evaluate('document.activeElement.closest(".import-dialog") !== null'), true, 'Search shortcut cannot escape a modal');
      await key('Escape');
      await waitFor('!document.querySelector(".import-dialog") && document.activeElement.title === "Forest Audio"');
      await key('A', ['meta', 'shift']);
      await selection([]);
    }
    await click('[aria-label="Grid view"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    await evaluate('document.querySelector("[data-asset-key=audio-other]").focus()');
    await key('Space');
    await selection(['City Audio']);
    await click('[aria-label="Library filters"] button[title="Favorites"]');
    await waitFor('document.querySelectorAll("[data-asset-key]").length === 2');
    await evaluate(`document.querySelector('[aria-label="Library filters"] button[title="Favorites"]').focus()`);
    await key('A', ['control']);
    await selection(['Editor Tool', 'Forest Audio']);
    assert.equal(await evaluate('document.querySelector(".import-selection-count").textContent'), '3 selected · 1 outside filters');
    await key('A', ['control', 'shift']);
    await selection([]);
    await evaluate('document.activeElement.blur()');
    await key('A', ['meta']);
    await selection(['Editor Tool', 'Forest Audio']);
    assert.equal(await evaluate('window.getSelection().toString()'), '', 'Background select-all does not select page text');
    await key('A', ['meta', 'shift']);
    await selection([]);
    await key('K', ['meta']);
    await waitFor('document.activeElement.getAttribute("aria-label") === "Search library"');
    await win.webContents.insertText('Audio');
    await waitFor('document.querySelectorAll("[data-asset-key]").length === 1');
    await key('A', ['meta']);
    assert.equal(await evaluate('(() => { const e = new KeyboardEvent("keydown", { key:"a", metaKey:true, bubbles:true, cancelable:true }); document.activeElement.dispatchEvent(e); return e.defaultPrevented; })()'), false, 'Native select-all is not intercepted in search');
    await selection([]);
    await key('K', ['meta']);
    win.webContents.selectAll();
    await key('Backspace');
    await waitFor('document.querySelectorAll("[data-asset-key]").length === 2');
    await evaluate('document.querySelector("[data-asset-key]").focus()');
    await key('/', ['meta']);
    await waitFor('!!document.querySelector(".shortcuts-dialog")');
    await key('Tab');
    assert.equal(await evaluate('document.activeElement.closest(".shortcuts-dialog") !== null'), true, 'Help traps focus');
    await key('Escape');
    await waitFor('!document.querySelector(".shortcuts-dialog") && !!document.activeElement.dataset.assetKey');
    await key(',', ['meta']);
    await waitFor('document.activeElement.id === "storage-path"');
    await click('.storage-shell header button');
    await waitFor('document.activeElement.getAttribute("aria-label") === "Search library"');
    await click('[aria-label="Remove library filter"]');
    await waitFor('document.querySelectorAll("[data-asset-key]").length === 4');
    console.log('PASS: keyboard navigation, range selection, native text editing, scoped select-all, modal isolation, review-only import, help and settings');
    await evaluate('localStorage.clear()');
    await win.reload();
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    await click('[aria-label="Expand Audio"]');
    assert.equal(await evaluate('document.querySelectorAll(".asset-card").length'), 4, 'Expanding does not filter');
    assert.equal(await evaluate(`!!document.querySelector('.category-tree [title="Nature"]').closest("[inert]")`), true);
    await waitFor("document.querySelector('[aria-label=\"Collapse Audio\"]')?.getAttribute(\"aria-expanded\") === \"true\"");
    await waitFor('!document.querySelector(".category-tree").getAnimations({ subtree: true }).some(a => a.playState === "running")');
    await click('[aria-label="Expand Ambient"]');
    await waitFor("document.querySelector('[aria-label=\"Collapse Ambient\"]')?.getAttribute(\"aria-expanded\") === \"true\"");
    await waitFor('!document.querySelector(".category-tree").getAnimations({ subtree: true }).some(a => a.playState === "running")');
    await click('.category-tree [title="Nature"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 1');
    assert.equal(await evaluate('document.querySelector(".card-title").textContent'), 'Forest Audio');
    await click('[aria-label="Remove category filter"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    assert.equal(await evaluate(`document.querySelector('.category-tree [aria-current="true"]').title`), 'All categories');
    await click('[aria-label="Collapse Audio"]');
    await waitFor("!!document.querySelector('.category-tree [title=\"Nature\"]').closest(\"[inert]\")");
    console.log('PASS: recursive branch expansion, leaf filtering, controlled reset and collapsed descendants');
    await click('[aria-label="Select City Audio for import"]');
    await selection(["City Audio"]);
    await click('[aria-label="Library filters"] button[title="Favorites"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 2');
    await click('[aria-label="Select Forest Audio for import"]');
    await selection(["Forest Audio"]);
    await click('[aria-label="Select all packages in current results"]');
    await selection(["Editor Tool", "Forest Audio"]);
    assert.equal(await evaluate('document.querySelector(".import-selection-count").textContent'), "3 selected · 1 outside filters");
    await click('[aria-label="Remove library filter"]');
    await selection(["City Audio", "Editor Tool", "Forest Audio"]);
    assert.equal(await evaluate(`document.querySelector('[aria-label="Select all packages in current results"]').disabled`), true);
    await click('[aria-label="Deselect all packages"]');
    await selection([]);
    await click('[aria-label="Select all packages in current results"]');
    await selection(['City Audio', 'Editor Tool', 'Forest Audio']);
    await click('[aria-label="Deselect all packages"]');
    await selection([]);
    assert.equal(await evaluate(`document.querySelector('[title="Forest Audio"] .availability').getAttribute("data-availability")`), "cloud_only");
    assert.equal(await evaluate(`document.querySelector('[title="City Audio"] .availability').getAttribute("data-availability")`), "local");
    await click('[aria-label="Library filters"] button[title="Favorites"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 2');
    await click('.category-tree button[title="Audio"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 1');
    assert.equal(await evaluate('document.querySelector(".card-title").textContent'), 'Forest Audio');
    await click('[aria-label="Remove category filter"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 2');
    await click('[aria-label="List view"]');
    await waitFor('document.querySelectorAll(".list-row").length === 2');
    await click('[aria-label="Close asset details"]');
    await waitFor('document.querySelector(".inspector").hidden');
    await click('.list-row[title="Forest Audio"]');
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
    await waitFor('document.activeElement.matches(".list-row")');
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
      await chooseOption('Sort assets', view === 'Grid' ? 'Author' : 'Name');
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
    const refreshed = structuredClone(fixtures);
    refreshed[0].versions[0].file = "Audio/Forest v2.unitypackage";
    await evaluate(`localStorage.setItem("ual:test-assets", ${JSON.stringify(JSON.stringify(refreshed))})`);
    await click('[aria-label="Import 1 selected package"]');
    await waitFor(`!!document.querySelector('[role="combobox"][aria-label="Archive version for Forest Audio"]')`);
    await click('[role="combobox"][aria-label="Unity Hub project"]');
    await waitFor("document.querySelector('[role=\"combobox\"][aria-label=\"Unity Hub project\"]')?.getAttribute(\"aria-expanded\") === \"true\"");
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' });
    win.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' });
    await waitFor('!document.querySelector("[role=listbox]")');
    assert.equal(await evaluate('!!document.querySelector(".import-dialog")'), true, 'Escape closes select without closing import');
    assert.equal(await evaluate('document.activeElement.getAttribute("aria-label")'), 'Unity Hub project');
    await chooseOption('Unity Hub project', 'Fixture');
    await waitFor(`!!document.querySelector(".import-project-info")`);
    assert.equal(await evaluate(`document.querySelector('.import-dialog .dialog-foot .primary').disabled`), true);
    await chooseOption('Archive version for Forest Audio', 'Forest v2.unitypackage');
    await waitFor(`!document.querySelector('.import-dialog .dialog-foot .primary').disabled`);
    await click(".import-overwrite input");
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`!!document.querySelector('[data-status="installed"]')`);
    assert.equal((await evaluate(`JSON.parse(localStorage.getItem("ual:test-request"))`)).overwrite, false);
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`!document.querySelector('.import-dialog')`);
    await click('[aria-label="Import 1 selected package"]');
    await waitFor(`!!document.querySelector('.import-overwrite input')`);
    await click('.import-dialog .dialog-foot .quiet');
    await waitFor(`!document.querySelector('.import-dialog')`);
    console.log("PASS: explicit archive selection and replacement opt-out without Editor readiness");
    await evaluate(`localStorage.setItem("ual:test-import", JSON.stringify({ id: "preparation", kind: "import", status: "running", project: "/fixture/project", completed: 0, total: 1, results: [{ file: "Cloud.unitypackage", status: "downloading", bytes_completed: 50, bytes_total: 100 }] }))`);
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
    await waitFor(`!!document.querySelector('.import-dialog [role="progressbar"]')`);
    const progress = await evaluate(`(() => { const bar = document.querySelector('.import-dialog [role="progressbar"]'); return { value: Number(bar.getAttribute("aria-valuenow")), max: Number(bar.getAttribute("aria-valuemax")), width: bar.getBoundingClientRect().width, fill: bar.firstElementChild.getBoundingClientRect().width }; })()`);
    assert.equal(progress.value, 50);
    assert.equal(progress.max, 100);
    assert(progress.width > 40, "Preparation progress must be visible, not collapsed by status cell CSS");
    assert(Math.abs(progress.fill / progress.width - 0.5) < 0.01);
    console.log("PASS: cloud/local badges and visible proportional preparation progress");
    await click(".import-dialog .dialog-foot .quiet");
    refreshed[0].versions[0].availability = "local";
    await evaluate(`localStorage.setItem("ual:test-assets", ${JSON.stringify(JSON.stringify(refreshed))})`);
    await evaluate(`localStorage.setItem("ual:test-import", JSON.stringify({ id: "preparation", kind: "import", status: "failed", error: "Package could not be imported", completed: 0, total: 1, results: [] }))`);
    await waitFor(`!!document.querySelector('.task-indicator[data-status="failed"]')`);
    await waitFor(`!!document.querySelector('[title="Forest Audio"] [data-availability="local"]')`);
    console.log("PASS: terminal import refreshes hydrated archive availability");
    await click(".task-indicator");
    await waitFor(`!document.querySelector(".task-details").hidden`);

    await click('[aria-label="Dismiss Import result"]');
    await waitFor(`!document.querySelector(".task-indicator")`);
    console.log("PASS: task hover, focus, Escape, review import, failure details and dismissal");
    const batch = Array.from({ length: 14 }, (_, i) => ({ asset_key: `batch-${i}`, name: `Editor package ${String(i + 1).padStart(2, "0")}`, versions: [{ file: `Editor package ${i + 1} v1.2.3.unitypackage`, availability: "local" }] }));
    await evaluate(`localStorage.clear(); localStorage.setItem("ual:test-assets", ${JSON.stringify(JSON.stringify(batch))})`);
    await win.reload();
    await waitFor(`document.querySelectorAll(".asset-cell").length === 14`);
    await click('[aria-label="Select all packages in current results"]');
    await waitFor(`!!document.querySelector(".import-controls .primary")`);
    await click(`.import-controls .primary`);
    await waitFor(`document.querySelectorAll(".import-packages li").length === 14`);
    await click(".import-section-heading button[aria-pressed]");
    await waitFor(`!!document.querySelector('[aria-label="Move Editor package 01 down"]')`);
    await click(`[aria-label="Move Editor package 01 down"]`);
    await waitFor(`document.querySelector(".import-package-main strong").textContent === "Editor package 02"`);
    await chooseOption('Unity Hub project', 'Fixture');
    await waitFor(`!document.querySelector(".import-dialog .primary").disabled`);
    for (const [width, height] of [[1200, 800], [983, 700], [390, 700]]) {
      win.setContentSize(width, height);
      await waitFor(`innerWidth === ${width}`);
      assert(await evaluate(`(() => { const dialog = document.querySelector(".import-dialog"); const header = dialog.querySelector(".dialog-top").getBoundingClientRect(); const footer = dialog.querySelector(".dialog-foot").getBoundingClientRect(); const warning = dialog.querySelector(".import-warning").getBoundingClientRect(); return header.top >= 0 && footer.top >= 0 && footer.bottom <= innerHeight && warning.top >= 0 && warning.bottom <= innerHeight && dialog.scrollWidth <= dialog.clientWidth && dialog.scrollHeight <= dialog.clientHeight; })()`), "Header, replacement warning and actions stay visible with only the body scrolling");
      await evaluate(`document.querySelector(".import-body").scrollTop = 10000`);
      assert(await evaluate(`document.querySelector(".import-dialog .dialog-foot").getBoundingClientRect().bottom <= innerHeight`));
    }
    console.log("PASS: 14-package reorder, project selection, fixed import actions at desktop and mobile sizes");
    win.setContentSize(1200, 800);
    await evaluate(`document.querySelector(".import-body").scrollTop = 0`);
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`document.querySelectorAll('[data-status="installed"]').length === 14`);
    assert.equal((await evaluate(`JSON.parse(localStorage.getItem("ual:test-request"))`)).overwrite, true);
    assert.equal(await evaluate(`document.querySelectorAll(".import-backup").length`), 14);
    console.log("PASS: replacement with backups and installed results");

    // A job-level preflight error retains pending rows; recovery must not filter them away.
    const preflight = { id: "preflight-review", kind: "import", status: "failed", project: "/fixture/project", overwrite: false, error: "Archive no longer indexed", completed: 0, total: 2, results: [batch[1], batch[0]].map(asset => ({ file: asset.versions[0].file, status: "pending" })) };
    await evaluate(`localStorage.setItem("ual:test-import", ${JSON.stringify(JSON.stringify(preflight))})`);
    await win.reload();
    await waitFor(`!!document.querySelector('.task-indicator[data-status="failed"]')`);
    await click(".task-indicator");
    await waitFor(`!document.querySelector('.task-details').hidden`);
    await click('[aria-label="Open import progress"]');
    await waitFor(`!!document.querySelector('.import-dialog .import-inline-error')`);
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`document.querySelectorAll('.import-packages > li').length === 2 && !document.querySelector('.import-dialog .primary').disabled`);
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('.import-packages .import-package-main > strong')].map(e => e.textContent)`), [batch[1].name, batch[0].name]);
    assert.equal(await evaluate(`document.querySelector('.import-overwrite input').checked`), false, "Review retains the original replacement choice");
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`!!document.querySelector('.import-successes')`);
    const reviewed = await evaluate(`JSON.parse(localStorage.getItem('ual:test-request'))`);
    assert.deepEqual(reviewed.packages.map(row => row.file), preflight.results.map(row => row.file));
    assert.equal(reviewed.project, preflight.project);
    console.log("PASS: preflight recovery preserves ordered selection, target and replacement policy");

    const partial = { ...preflight, id: "partial-review", total: 3, completed: 1, results: [
      { file: batch[0].versions[0].file, status: "installed" },
      { file: batch[1].versions[0].file, status: "failed", error: "Existing asset differs" },
      { file: batch[2].versions[0].file, status: "cancelled" },
    ] };
    await evaluate(`localStorage.setItem("ual:test-import", ${JSON.stringify(JSON.stringify(partial))})`);
    await win.reload();
    await waitFor(`!!document.querySelector('.task-indicator[data-status="failed"]')`);
    await click(".task-indicator");
    await waitFor(`!document.querySelector('.task-details').hidden`);
    await click('[aria-label="Open import progress"]');
    await waitFor(`!!document.querySelector('.import-successes')`);
    assert.equal(await evaluate(`document.querySelector('.import-successes').open`), false);
    await click(".import-dialog .dialog-foot .primary");
    await waitFor(`document.querySelectorAll('.import-packages > li').length === 2`);
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('.import-packages .import-package-main > strong')].map(e => e.textContent)`), [batch[1].name, batch[2].name]);
    console.log("PASS: partial recovery excludes installed packages and retains cancelled packages");
    const shrinking = structuredClone(fixtures);
    shrinking[1].category = { path: 'Audio/Music', levels: ['Audio', 'Music'] };
    await evaluate('localStorage.clear(); localStorage.setItem("ual:test-assets", ' + JSON.stringify(JSON.stringify(shrinking)) + ')');
    await win.reload();
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    await click('[aria-label="Expand Audio"]');
    await waitFor('!!document.querySelector(\'[aria-label="Collapse Audio"]\')');
    await waitFor('!document.querySelector(".category-tree").getAnimations({ subtree: true }).some(a => a.playState === "running")');
    await click('[title="Forest Audio"]');
    await selection(['Forest Audio']);
    await evaluate('localStorage.setItem("ual:test-assets", ' + JSON.stringify(JSON.stringify(shrinking.filter(asset => asset.asset_key !== 'audio-other'))) + ')');
    await click('[aria-label="Import 1 selected package"]');
    await waitFor('document.querySelectorAll(".asset-card").length === 3');
    assert.equal(await evaluate('!!document.querySelector(\'.category-tree [title="Music"]\')'), false);
    await click('.import-dialog .dialog-foot .quiet');
    await waitFor('!document.querySelector(".import-dialog")');
    assert.equal(await evaluate('document.querySelector(\'[aria-label="Collapse Audio"]\').getAttribute("aria-expanded")'), 'true');
    console.log('PASS: catalog refresh safely removes a child from an expanded category');
  } catch (error) {
    console.error(error);
    exitCode = 1;
  } finally {
    await fs.promises.rm(temporary, { recursive: true, force: true });
    app.exit(exitCode);
  }
});
