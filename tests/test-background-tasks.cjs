// Run after npm run build: npx electron tests/test-background-tasks.cjs
// Isolated task snapshots; never opens or changes the real asset library.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-task-dropdown-'));
app.setPath('userData', path.join(temp, 'profile'));
const action = { id: 'sync', kind: 'resync', phase: 'plan', status: 'completed', stage: 'scan', total: 10, completed: 10 };
const enrichment = { id: 'enrichment', kind: 'enrich', status: 'completed', remaining: 2, stage: 'enrich', total: 8, completed: 8 };
const imported = { id: 'import', kind: 'import', status: 'failed', error: 'Archive unavailable', total: 1, completed: 0, results: [] };
const packages = { id: 'packages', kind: 'packages', status: 'running', total: 1, completed: 0, project: '/fixture/project', results: [{ id: 'math', label: 'Math', status: 'installing' }] };
const preload = path.join(temp, 'preload.cjs');
fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('ual', {
  getStorage: async () => ({ path: '/fixture', ready: true, canChange: true, busy: false }),
  getState: async () => ({ ready: true, vault_root: '/fixture', pending_enrichment: 2, action: ${JSON.stringify(action)}, job: ${JSON.stringify(enrichment)} }),
  getAction: async () => ({ action: ${JSON.stringify(action)} }),
  getAssets: async () => ({ assets: [] }), favorites: async () => ({ favorites: [] }),
  getTags: async () => ({ tags: [], assignments: {} }),
  getPreferences: async () => ({ theme: 'dark' }), setPreferences: async prefs => prefs,
  getImport: async () => (${JSON.stringify(imported)}),
  getPackageFavorites: async () => ({ packages: [] }),
  getPackageInstall: async () => JSON.parse(localStorage.getItem('fixture:packages') || ${JSON.stringify(JSON.stringify(packages))})
});`);
let win;
const evaluate = code => win.webContents.executeJavaScript(code);
async function waitFor(code) {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (await evaluate(code)) return;
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('Timed out: ' + code);
}
async function click(selector) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: 'nearest' })`);
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const point = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point });
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}
app.whenReady().then(async () => {
  let exitCode = 0;
  try {
    win = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { preload, contextIsolation: true, sandbox: true } });
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'));
    await waitFor(`document.querySelector('.task-indicator')?.getAttribute('aria-label').includes('4 total')`);
    assert.equal(await evaluate(`document.querySelector('.task-indicator').textContent.trim()`), '', 'Task trigger is icon-only');
    assert.equal(await evaluate(`!!document.querySelector('.task-indicator .task-spinner')`), true, 'Running tasks use a basic spinner');
    assert.equal(await evaluate(`Boolean(document.querySelector('.task-indicator').compareDocumentPosition(document.querySelector('[aria-label="Keyboard shortcuts"]')) & Node.DOCUMENT_POSITION_FOLLOWING)`), true, 'Task spinner is left of keyboard guide');
    assert.equal(await evaluate(`!!document.querySelector('.task-label, .task-count, .task-indicator .lucide-chevron-down, .task-indicator .lattice-loader')`), false, 'Task trigger has no label, count, chevron or decorative loader');
    win.webContents.debugger.attach('1.3');
    await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('.task-spinner')).animationName`), 'none', 'Spinner honors reduced motion');
    await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia', { features: [] });
    win.webContents.debugger.detach();
    assert.equal(await evaluate(`document.querySelectorAll('.topbar .task-indicator').length`), 1);
    await click('.task-indicator');
    await waitFor(`!!document.querySelector('#background-tasks .favorite-package-progress')`);
    assert.equal(await evaluate(`document.querySelectorAll('#background-tasks .progress-panel').length`), 3);
    await evaluate('document.documentElement.classList.add("native-titlebar")');
    const gap = await evaluate(`(() => { const search = document.querySelector('.search-wrap').getBoundingClientRect(); const button = document.querySelector('.task-indicator').getBoundingClientRect(); const x = Math.round((search.right + button.left) / 2); const y = Math.round(button.top + button.height / 2); const hit = document.elementFromPoint(x, y); return { x, y, hit: hit?.className, backdropRegion: getComputedStyle(document.querySelector('.task-backdrop')).getPropertyValue('-webkit-app-region'), headerRegion: getComputedStyle(document.querySelector('.topbar')).getPropertyValue('-webkit-app-region') }; })()`);
    console.log('Outside header-gap probe:', JSON.stringify(gap));
    assert.equal(gap.backdropRegion, 'no-drag', 'Open backdrop must opt out of native titlebar drag hit testing');
    win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, x: gap.x, y: gap.y });
    win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, x: gap.x, y: gap.y });
    await waitFor(`!document.querySelector('#background-tasks')`);
    await click('.task-indicator');
    await waitFor(`!!document.querySelector('#background-tasks')`);
    await click('#background-tasks > .progress-heading');
    assert.equal(await evaluate(`!!document.querySelector('#background-tasks')`), true, 'Clicks inside keep the task dropdown open');
    const text = await evaluate(`document.querySelector('#background-tasks').textContent`);
    for (const label of ['Resync', 'Enrich', 'Import', 'Installing packages', 'Archive unavailable', '2 assets still need enrichment']) assert(text.includes(label), label);
    assert.equal(await evaluate(`document.querySelectorAll('.content-column .progress-panel, .content-column .favorite-package-progress').length`), 0);
    for (const [width, height] of [[800, 600], [1200, 800], [1440, 900]]) {
      win.setSize(width, height);
      await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
      assert.equal(await evaluate(`(() => { const r = document.querySelector('.task-details').getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1; })()`), true, `Dropdown fits ${width}x${height}`);
    }
    await fs.promises.writeFile(path.join(os.tmpdir(), 'ual-background-tasks.png'), (await win.webContents.capturePage()).toPNG());
    await click('[aria-label="Dismiss Resync · Preview result"]');
    await click('[aria-label="Dismiss Enrich result"]');
    await click('[aria-label="Dismiss Import result"]');
    await waitFor(`document.querySelector('.task-indicator')?.getAttribute('aria-label').includes('1 total')`);
    await evaluate(`localStorage.setItem('fixture:packages', JSON.stringify(${JSON.stringify({ ...packages, status: 'completed', completed: 1, results: [{ id: 'math', label: 'Math', status: 'installed' }] })}))`);
    await waitFor(`!!document.querySelector('[aria-label="Dismiss package installation result"]')`);
    await click('[aria-label="Dismiss package installation result"]');
    await waitFor(`!document.querySelector('.task-indicator') && !document.querySelector('.task-details')`);
    await waitFor(`document.activeElement.getAttribute('aria-label') === 'Keyboard shortcuts'`);
    console.log('PASS: basic spinner before keyboard guide, reduced motion, icon-only trigger, all task sources, live completion, dismissal hides empty trigger/popover, focus fallback and viewport bounds');
    console.log('Screenshot: ' + path.join(os.tmpdir(), 'ual-background-tasks.png'));
  } catch (error) { console.error(error); exitCode = 1; }
  finally { await fs.promises.rm(temp, { recursive: true, force: true }); app.exit(exitCode); }
});
