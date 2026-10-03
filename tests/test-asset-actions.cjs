const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { hydrateAsset } = require('../electron/asset-actions.cjs');

(async () => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ual-asset-actions-'));
  const root = path.join(temporary, 'library');
  fs.mkdirSync(root);
  const asset = { asset_key: 'cloud', store: 'https://assetstore.unity.com/packages/test', versions: [
    { file: 'cloud.unitypackage', availability: 'cloud_only' },
    { file: 'local.zip', availability: 'local' },
    { file: 'missing.zip', availability: 'missing' },
  ] };
  try {
    fs.writeFileSync(path.join(root, 'cloud.unitypackage'), Buffer.alloc(150000, 42));
    // Local/missing entries intentionally do not exist: only cloud entries are read.
    assert.deepEqual(await hydrateAsset(root, asset, new AbortController().signal), { downloaded: 1 });
    assert.equal(fs.readFileSync(path.join(root, 'cloud.unitypackage')).length, 150000);
    assert.deepEqual(fs.readdirSync(root), ['cloud.unitypackage'], 'Hydration creates no copies or project files');
    assert.deepEqual(await hydrateAsset(root, { versions: [asset.versions[1]] }, new AbortController().signal), { downloaded: 0 });
    const hydrate = file => hydrateAsset(root, { versions: [{ file, availability: 'cloud_only' }] }, new AbortController().signal);
    fs.writeFileSync(path.join(temporary, 'outside.zip'), 'private');
    fs.symlinkSync(path.join(temporary, 'outside.zip'), path.join(root, 'escape.zip'));
    await assert.rejects(hydrate('../outside.zip'), /outside the library/);
    await assert.rejects(hydrate('escape.zip'), /outside the library/);
    await assert.rejects(hydrate(path.join(root, 'cloud.unitypackage')), /Invalid archive path/);
    await assert.rejects(hydrate('bad\0.zip'), /Invalid archive path/);
    await assert.rejects(hydrate('missing.zip'), /ENOENT/);
    await assert.rejects(hydrate('.'), /outside the library/);
    fs.mkdirSync(path.join(root, 'folder.zip'));
    await assert.rejects(hydrate('folder.zip'), /no longer a file/);
    const controller = new AbortController();
    controller.abort(new Error('cancelled'));
    await assert.rejects(hydrateAsset(root, asset, controller.signal), /cancelled/);
    console.log('PASS: cloud-only streaming, no copies, containment, invalid paths, missing files and cancellation');
  } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
})().catch(error => { console.error(error); process.exitCode = 1; });
