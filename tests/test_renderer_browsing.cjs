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
fixtures[1].store = 'https://assetstore.unity.com/packages/city-audio';
const preload = path.join(temporary, 'preload.cjs');
fs.writeFileSync(preload, `const favorites = new Set(['audio-favorite', 'tools-favorite']); require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getState: async () => ({ pending_enrichment: 0, job: null, action: null, ready: true, vault_root: '/fixture' }),
  getAssets: async () => ({ assets: JSON.parse(localStorage.getItem("ual:test-assets") || ${JSON.stringify(JSON.stringify(fixtures))}) }),
  openExternal: async url => { localStorage.setItem('ual:test-open-url', url); },
  revealItem: async file => { localStorage.setItem('ual:test-reveal-file', file); },
  downloadAsset: async key => {
    localStorage.setItem('ual:test-download-key', key);
    await new Promise(resolve => { const timer = setInterval(() => { if (localStorage.getItem('ual:test-download-release')) { clearInterval(timer); resolve(); } }, 10); });
    if (localStorage.getItem('ual:test-download-fail')) throw new Error('Cloud provider offline');
    const assets = JSON.parse(${JSON.stringify(JSON.stringify(fixtures))});
    assets.find(asset => asset.asset_key === key).versions.forEach(version => { version.availability = 'local'; });
    localStorage.setItem('ual:test-assets', JSON.stringify(assets));
    return { downloaded: 1 };
  },
  favorites: async () => ({ favorites: [...favorites] }),
  setFavorite: async (key, enabled) => { if (enabled) favorites.add(key); else favorites.delete(key); return { favorites: [...favorites] }; },
  getTags: async () => ({ tags: [], assignments: {} }),
  getPreferences: async () => { try { return JSON.parse(localStorage.getItem('ual:prefs') || '{}'); } catch { return {}; } },
  setPreferences: async (prefs) => { localStorage.setItem('ual:prefs', JSON.stringify(prefs)); return prefs; },
  getLegacyLibraryRoot: async () => null,
  getPackageFavorites: async () => ({ packages: JSON.parse(localStorage.getItem("ual:test-package-favorites") || "[]") }),
  getPackageInstall: async () => JSON.parse(localStorage.getItem("ual:test-package-job") || "null"),
  importProjects: async () => [{ path: "/fixture/project", title: "Fixture", version: "6000.3.15f1" }],
  chooseImportProject: async () => null,
  inspectImportProject: async (path) => {
    if (localStorage.getItem("ual:test-delay-inspection")) {
      localStorage.setItem("ual:test-inspection-started", "true");
      await new Promise(resolve => {
        const timer = setInterval(() => {
          if (localStorage.getItem("ual:test-release-inspection")) { clearInterval(timer); resolve(); }
        }, 10);
      });
      localStorage.setItem("ual:test-inspection-finished", "true");
    }
    return { path, title: "Fixture", version: "6000.3.15f1" };
  },
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
async function click(selector, modifiers = [], button = 'left') {
  if (selector.startsWith('[role=menuitem]')) await waitFor('document.querySelector(".asset-context-menu") && !document.querySelector(".asset-context-menu").getAnimations({ subtree: true }).some(animation => animation.playState === "running")');
  const bounds = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button, clickCount: 1, modifiers, ...bounds });
  win.webContents.sendInputEvent({ type: 'mouseUp', button, clickCount: 1, modifiers, ...bounds });
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}
async function key(keyCode, modifiers = []) {
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
  // Chromium activates a native button on Enter's character event.
  if (keyCode === 'Enter') win.webContents.sendInputEvent({ type: 'char', keyCode: '\r', modifiers });
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
    assert.equal(await evaluate('!!document.querySelector(".task-indicator")'), false, 'Task indicator is hidden when there are no tasks');
    for (const view of ['Grid', 'List']) {
      await click('[aria-label="' + view + ' view"]');
      await click('[aria-label="Select City Audio for import"]');
      await selection(['City Audio']);
      await click('[data-asset-key=audio-favorite]', [], 'right');
      await waitFor('document.querySelector("[role=menu]") && document.activeElement.dataset.action === "details"');
      assert.equal(await evaluate('!!document.querySelector(".asset-context-menu__heading, .asset-context-menu__footer")'), false, 'Context menu contains action rows only');
      assert.equal(await evaluate('getComputedStyle(document.querySelector(".asset-context-menu")).width'), '224px', 'Menu uses compact width');
      assert.equal(await evaluate('getComputedStyle(document.querySelector(".asset-context-menu__surface")).paddingTop'), '4px', 'Menu uses compact outer padding');
      assert.deepEqual(await evaluate('(() => { const s = getComputedStyle(document.querySelector(".asset-context-menu__item")); return [s.paddingTop, s.paddingLeft]; })()'), ['4px', '8px'], 'Action rows use compact padding');
      await selection(['City Audio']);
      assert.equal(await evaluate('document.querySelector("[role=menuitem][data-action=store]").getAttribute("aria-disabled")'), 'true');
      await key('Down');
      await waitFor('document.activeElement.dataset.action === "favorite"');
      await key('Down');
      await waitFor('document.activeElement.dataset.action === "reveal"');
      await key('End');
      await waitFor('document.activeElement.dataset.action === "download"');
      await key('Escape');
      await waitFor('!document.querySelector("[role=menu]") && document.activeElement.dataset.assetKey === "audio-favorite"');
      await selection(['City Audio']);
      await key('F10', ['shift']);
      await waitFor('!!document.querySelector("[role=menu]")');
      assert.equal(await evaluate('document.querySelector(".asset-context-menu").hasAttribute("data-instant")'), true, 'Keyboard menu opens without motion');
      await key('Tab');
      await waitFor('!document.querySelector("[role=menu]")');
      await click('[data-asset-key=audio-favorite]', [], 'right');
      if (view === 'Grid') {
        for (const theme of ['dark', 'light']) {
          await evaluate(`document.documentElement.dataset.theme = ${JSON.stringify(theme)}`);
          await waitFor('!document.querySelector(".asset-context-menu").getAnimations({ subtree: true }).some(animation => animation.playState === "running")');
          await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
          const rect = await evaluate('(() => { const r = document.querySelector(".asset-context-menu").getBoundingClientRect(); return { x: Math.floor(r.x), y: Math.floor(r.y), width: Math.ceil(r.width), height: Math.ceil(r.height) }; })()');
          await win.webContents.capturePage(rect);
          fs.writeFileSync(path.join(os.tmpdir(), 'ual-asset-menu-' + theme + '.png'), (await win.webContents.capturePage(rect)).toPNG());
        }
        await evaluate('document.documentElement.dataset.theme = "system"');
        win.webContents.debugger.attach('1.3');
        await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
        await waitFor('document.querySelector(".asset-context-menu").hasAttribute("data-instant")');
        await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [] });
        win.webContents.debugger.detach();
      }
      await click('[role=menuitem][data-action=details]');
      await waitFor('document.activeElement.getAttribute("aria-label") === "Close asset details"');
      assert.equal(await evaluate('document.querySelector(".inspector-title h2").textContent'), 'Forest Audio');
      await click('[aria-label="Close asset details"]');
      await selection(['City Audio']);
      await click('[data-asset-key=audio-other]', [], 'right');
      assert.equal(await evaluate('!!document.querySelector("[role=menuitem][data-action=download]")'), false, 'Local package has no download action');
      await click('[role=menuitem][data-action=favorite]');
      await waitFor(`document.querySelector('[aria-label="Remove City Audio from favorites"]')`);
      await click('[data-asset-key=audio-other]', [], 'right');
      await click('[role=menuitem][data-action=favorite]');
      await waitFor(`document.querySelector('[aria-label="Add City Audio to favorites"]')`);
      await click('[data-asset-key=audio-other]', [], 'right');
      await click('[role=menuitem][data-action=store]');
      await waitFor('localStorage.getItem("ual:test-open-url") === "https://assetstore.unity.com/packages/city-audio"');
      await click('[data-asset-key=audio-other]', [], 'right');
      await click('[role=menuitem][data-action=reveal]');
      await waitFor('localStorage.getItem("ual:test-reveal-file") === "City.unitypackage"');
      await evaluate('document.querySelector("[data-asset-key=audio-favorite]").dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: innerWidth - 1, clientY: innerHeight - 1 }))');
      await waitFor('!!document.querySelector("[role=menu]")');
      assert.equal(await evaluate('(() => { const r = document.querySelector(".asset-context-menu").getBoundingClientRect(); return r.left >= 8 && r.top >= 8 && r.right <= innerWidth - 7 && r.bottom <= innerHeight - 7; })()'), true, 'Menu stays inside viewport');
      await click('[aria-label="Search library"]');
      await waitFor('!document.querySelector("[role=menu]")');
      await selection(['City Audio']);
      await click('[aria-label="Deselect all packages"]');
    }
    await click('[aria-label="Grid view"]');
    await click('[data-asset-key=audio-favorite]', [], 'right');
    await click('[role=menuitem][data-action=download]');
    await waitFor('localStorage.getItem("ual:test-download-key") === "audio-favorite" && document.querySelector(".task-indicator").dataset.status === "running"');
    assert.equal(await evaluate('!!document.querySelector(".browser .download-status")'), false, 'Downloads never occupy the browser content');
    await click('.task-indicator');
    await waitFor('document.querySelector("#background-tasks .download-status")?.textContent.includes("Downloading")');
    assert.equal(await evaluate(`document.querySelector('[aria-label="Select all packages in current results"]').disabled`), true, 'Download blocks importing');
    assert.equal(await evaluate('localStorage.getItem("ual:test-request")'), null, 'Download never imports');
    await key('Escape');
    await waitFor('!document.querySelector(".task-details")');
    await click('[data-asset-key=audio-favorite]', [], 'right');
    assert.equal(await evaluate('document.querySelector("[role=menuitem][data-action=download]").getAttribute("aria-disabled")'), 'true', 'Repeated downloads are disabled');
    await key('Escape');
    await evaluate('localStorage.setItem("ual:test-download-release", "true")');
    await waitFor('document.querySelector(".task-indicator").dataset.status === "completed"');
    await click('.task-indicator');
    await waitFor('document.querySelector("#background-tasks .download-status")?.textContent.includes("Downloaded 1 package")');
    assert.equal(await evaluate('document.querySelector("[data-asset-key=audio-favorite] .availability").dataset.availability'), 'local', 'Download refreshes availability');
    await click('[aria-label="Dismiss download status"]');
    await evaluate('localStorage.clear()');
    await win.reload();
    await waitFor('document.querySelectorAll(".asset-card").length === 4');
    await evaluate('localStorage.setItem("ual:test-download-fail", "true"); localStorage.setItem("ual:test-download-release", "true")');
    await click('[data-asset-key=audio-favorite]', [], 'right');
    await click('[role=menuitem][data-action=download]');
    await waitFor('document.querySelector(".task-indicator").dataset.status === "failed"');
    await click('.task-indicator');
    await waitFor('document.querySelector("#background-tasks").textContent.includes("Cloud provider offline")');
    assert.equal(await evaluate(`document.querySelector('[aria-label="Select all packages in current results"]').disabled`), false, 'Failure releases download busy state');
    console.log('PASS: React Bits styled menu, grid/list right-click and Shift-F10, keyboard/disabled actions, Escape/Tab/outside dismissal, viewport bounds, preserved selection, downloads and no Unity import');
    // Explicit scoped run for context-menu work; default execution still runs every browsing regression.
    if (process.env.UAL_CONTEXT_MENU_ONLY === '1') return;
    await evaluate('localStorage.clear()');
    await win.reload();
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
      await selection([]);
      assert.equal(await evaluate('document.querySelector(".inspector").hidden'), false, 'Escape keeps asset details open');
      await key('Escape');
      await waitFor('document.activeElement.title === "Forest Audio" && document.querySelector(".inspector").hidden');
      await key('Space');
      await selection(['Forest Audio']);
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
    await key('Escape');
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
    await waitFor(`document.querySelector('.task-indicator')?.dataset.status === 'running'`);
    assert.equal(await evaluate(`!!document.querySelector('.task-details')`), false);
    win.webContents.focus();
    await evaluate(`document.querySelector('.topbar .task-indicator').focus()`);
    await key('Enter');
    await waitFor(`!!document.querySelector('.task-details')`);
    await key('Escape');
    await waitFor(`!document.querySelector('.task-details')`);
    assert.equal(await evaluate(`document.activeElement.classList.contains('task-indicator')`), true, 'Escape restores trigger focus');
    await click('.task-indicator');
    await waitFor(`!!document.querySelector('[aria-label="Open import progress"]')`);
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
    await waitFor(`!!document.querySelector(".task-details")`);

    await click('[aria-label="Dismiss Import result"]');
    await waitFor(`!document.querySelector('.task-indicator') && !document.querySelector('.task-details')`);
    await waitFor(`document.activeElement.getAttribute('aria-label') === 'Keyboard shortcuts'`);
    console.log("PASS: unified task dropdown, keyboard opening, Escape/focus return, review import, failure details, dismissal and empty state");
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
    await waitFor(`!!document.querySelector('.task-details')`);
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
    await waitFor(`!!document.querySelector('.task-details')`);
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
    const packageJob = { id: "saved-install", kind: "packages", status: "running", project: "/fixture/project", completed: 0, total: 1, results: [{ id: "math", label: "Math", source: "com.unity.mathematics", status: "installing" }] };
    await evaluate('localStorage.setItem("ual:test-package-job", ' + JSON.stringify(JSON.stringify(packageJob)) + ')');
    await win.reload();
    await waitFor('document.querySelector(".task-indicator")?.dataset.status === "running"');
    await click('.task-indicator');
    await waitFor('document.querySelector("#background-tasks .favorite-package-progress")?.textContent.includes("Installing packages")');
    assert.equal(await evaluate('!!document.querySelector(".browser .favorite-package-progress")'), false);
    await key('Escape');
    await click('[aria-label="Open Settings"]');
    await waitFor(`document.querySelector('[aria-label="Browse for library folder"]')?.disabled === true`);
    packageJob.status = "completed";
    packageJob.completed = 1;
    packageJob.results[0].status = "installed";
    await evaluate('localStorage.setItem("ual:test-package-job", ' + JSON.stringify(JSON.stringify(packageJob)) + ')');
    await waitFor(`document.querySelector('[aria-label="Browse for library folder"]')?.disabled === false`);
    await click('.storage-topbar .button');
    await click('.task-indicator');
    await waitFor('document.querySelector("#background-tasks .favorite-package-progress")?.textContent.includes("Package installation complete")');
    await click('[aria-label="Dismiss package installation result"]');
    await waitFor('!document.querySelector(".task-indicator") && !document.querySelector(".task-details")');
    console.log("PASS: saved-package polling survives Settings navigation and releases library-change guards");
    await evaluate('localStorage.setItem("ual:test-package-favorites", JSON.stringify([{ id: "math", label: "Math", kind: "registry", source: "com.unity.mathematics", version: "1.3.2" }])); localStorage.setItem("ual:prefs", JSON.stringify({ filters: { quick: "favorites" } })); localStorage.setItem("ual:test-delay-inspection", "true")');
    await win.reload();
    await waitFor('document.querySelector(".favorite-package-disclosure") && !document.querySelector(".favorite-package-disclosure").open');
    await click(".favorite-package-disclosure summary");
    await waitFor('document.querySelector(".favorite-packages-list")?.textContent.includes("Math")');
    await click(".favorite-packages-heading .primary");
    await waitFor(`!!document.querySelector('[role="combobox"][aria-label="Target Unity project"]')`);
    await chooseOption("Target Unity project", "Fixture");
    await waitFor('localStorage.getItem("ual:test-inspection-started") === "true"');
    await key("Escape");
    await waitFor(`!document.querySelector('[role="combobox"][aria-label="Target Unity project"]')`);
    await click(".favorite-packages-heading .primary");
    await waitFor(`!!document.querySelector('[role="combobox"][aria-label="Target Unity project"]')`);
    await evaluate('localStorage.setItem("ual:test-release-inspection", "true")');
    await waitFor('localStorage.getItem("ual:test-inspection-finished") === "true"');
    await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
    assert.equal(await evaluate('document.querySelector(".favorite-package-dialog .dialog-footer .primary").disabled'), true);
    await chooseOption("Target Unity project", "Fixture");
    await waitFor('document.querySelector(".favorite-package-dialog .dialog-footer .primary").disabled === false');
    console.log("PASS: cancelled project inspection cannot select a target in a new saved-package review");
  } catch (error) {
    console.error(error);
    console.error('Renderer failure state:', await evaluate('({ menu: document.querySelector("[role=menu]")?.textContent, active: document.activeElement?.outerHTML?.slice(0, 250), downloadKey: localStorage.getItem("ual:test-download-key"), status: document.querySelector(".download-status")?.textContent, errors: [...document.querySelectorAll("[role=alert]")].map(element => element.textContent) })').catch(() => null));
    exitCode = 1;
  } finally {
    await fs.promises.rm(temporary, { recursive: true, force: true });
    app.exit(exitCode);
  }
});
