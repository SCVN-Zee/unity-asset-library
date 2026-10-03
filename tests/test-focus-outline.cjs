// Run: npx electron tests/test-focus-outline.cjs
// Isolated renderer fixture: never opens the real library or writes its data.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { buildSync } = require('esbuild');
let win;
const evaluate = code => win.webContents.executeJavaScript(code);
async function frame() {
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
}
async function waitFor(expression) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(`Timed out: ${expression}`);
}
async function key(keyCode, modifiers = []) {
  win.webContents.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
  win.webContents.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
  await frame();
}
async function click(selector) {
  const point = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x: Math.round(r.x + Math.min(50, r.width / 2)), y: Math.round(r.y + r.height / 2)}; })()`);
  win.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
  win.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point });
  await waitFor('document.documentElement.dataset.focusVisible === "false"');
}
const outline = selector => evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(selector)})).outlineStyle`);
app.whenReady().then(async () => {
  let exitCode = 0;
  try {
    const errors = [];
    win = new BrowserWindow({ show: true, width: 900, height: 500 });
    win.webContents.on('console-message', (_event, details) => {
      if (details.level === 'error') errors.push(details.message);
    });
    const css = ['styles.css', 'components/FavoritePackages.css', 'components/react-bits/SpringCheck.css'].map(file => fs.readFileSync(path.join(__dirname, '../app/src', file), 'utf8')).join('\n');
    const script = buildSync({
      stdin: { contents: 'import React from "react"; import { createRoot } from "react-dom/client"; import { FocusVisibility } from "./app/src/components/focus-visibility"; function Fixture() { React.useEffect(() => { window.fixtureReady = true; }, []); return React.createElement(FocusVisibility); } createRoot(document.getElementById("root")).render(React.createElement(React.StrictMode, null, React.createElement(Fixture)));', resolveDir: path.join(__dirname, '..'), loader: 'tsx' },
      bundle: true, write: false, format: 'iife', platform: 'browser', define: { 'process.env.NODE_ENV': '"production"' },
    }).outputFiles[0].text;
    await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(`<style>${css}</style><div id="root"></div><details class="favorite-package-disclosure"><summary>Saved packages</summary></details><button>Next</button><input aria-label="Search"><label class="spring-check"><input type="checkbox" class="spring-check__input"><span class="spring-check__press">Select</span></label><script>${script}</script>`));
    await waitFor('window.fixtureReady === true');
    assert.equal(await evaluate('document.documentElement.dataset.focusVisible'), 'true', 'Initial virtual/keyboard focus remains visible');
    await click('summary');
    assert.equal(await evaluate('document.activeElement.tagName'), 'SUMMARY');
    assert.equal(await outline('summary'), 'none');
    await key('Meta');
    assert.equal(await evaluate('document.activeElement.matches(":focus-visible")'), true, 'Reproduce Chromium modifier-only focus-visible promotion');
    await key('Tab', ['meta']);
    assert.equal(await outline('summary'), 'none', 'Cmd+Tab must not outline the pointer-focused disclosure');
    await evaluate('window.dispatchEvent(new Event("blur")); window.dispatchEvent(new Event("focus"));');
    await frame();
    assert.equal(await outline('summary'), 'none', 'App focus restoration preserves pointer modality');
    fs.writeFileSync(path.join(os.tmpdir(), 'ual-focus-pointer.png'), (await win.webContents.capturePage()).toPNG());
    await key('Tab');
    assert.equal(await evaluate('document.activeElement.tagName'), 'BUTTON');
    await waitFor('document.documentElement.dataset.focusVisible === "true"');
    assert.equal(await outline('button'), 'solid', 'Tab retains a visible keyboard focus ring');
    await key('Tab', ['shift']);
    assert.equal(await evaluate('document.activeElement.tagName'), 'SUMMARY');
    assert.equal(await outline('summary'), 'solid', 'Shift+Tab outlines the disclosure');
    await key('Meta');
    assert.equal(await outline('summary'), 'solid', 'App switching does not clear genuine keyboard focus');
    const open = await evaluate('document.querySelector("details").open');
    await key('Space');
    assert.equal(await evaluate('document.querySelector("details").open'), !open, 'Keyboard activation is unchanged');
    fs.writeFileSync(path.join(os.tmpdir(), 'ual-focus-keyboard.png'), (await win.webContents.capturePage()).toPNG());
    await click('button');
    await key('Meta');
    assert.equal(await outline('button'), 'none', 'Buttons also ignore modifier-only focus rings');
    await key('Down');
    await waitFor('document.documentElement.dataset.focusVisible === "true"');
    assert.equal(await outline('button'), 'solid', 'Arrow navigation enables keyboard focus');
    await click('input[aria-label="Search"]');
    assert.equal(await outline('input[aria-label="Search"]'), 'solid', 'Pointer-focused text inputs keep their outline');
    await key('Meta');
    assert.equal(await outline('input[aria-label="Search"]'), 'solid');
    await key('Tab');
    assert.equal(await outline('.spring-check__press'), 'solid', 'Keyboard checkbox focus remains visible');
    await click('.spring-check__press');
    await key('Meta');
    assert.equal(await outline('.spring-check__press'), 'none', 'Wrapped checkbox focus also ignores app-switch modifiers');
    assert.deepEqual(errors, [], 'No renderer console errors');
    console.log('PASS: Cmd+Tab, focus restoration, Tab/Shift+Tab, arrows, activation, text inputs, and wrapped checkboxes');
  } catch (error) { console.error(error); exitCode = 1; }
  finally { app.exit(exitCode); }
});
