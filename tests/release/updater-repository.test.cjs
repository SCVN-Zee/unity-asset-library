const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const file = path.resolve(__dirname, '../../electron/updates.cjs');
const nativeRequire = createRequire(file);
function load(repository) {
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    module, exports: module.exports, __dirname: path.dirname(file), Buffer, AbortSignal, console,
    process: { ...process, env: { ...process.env, GITHUB_REPOSITORY: 'attacker/runtime' } },
    require: name => name === '../package.json' ? { releaseRepository: repository } : nativeRequire(name),
  }, { filename: file });
  return module.exports;
}

test('shipped updater trusts moved build metadata, not runtime GitHub environment or old owner', () => {
  const updates = load('new-owner/moved-library');
  const name = 'unity-asset-library-1.2.3-arm64.zip';
  const release = { tag_name: 'v1.2.3', draft: false, prerelease: false, assets: [{ name,
    state: 'uploaded', digest: `sha256:${'a'.repeat(64)}`, size: 123,
    browser_download_url: `https://github.com/new-owner/moved-library/releases/download/v1.2.3/${name}` }] };
  assert.equal(updates.REPO, 'new-owner/moved-library');
  assert.equal(updates.selectRelease(release, '1.2.2').url, release.assets[0].browser_download_url);
  for (const repository of ['attacker/runtime', 'SCVN-Zee/unity-asset-library']) {
    const redirected = { ...release, assets: [{ ...release.assets[0],
      browser_download_url: `https://github.com/${repository}/releases/download/v1.2.3/${name}` }] };
    assert.throws(() => updates.selectRelease(redirected, '1.2.2'), /invalid/);
  }
  for (const repository of [undefined, 'owner/..', 'owner/app?token'])
    assert.throws(() => load(repository), /Invalid packaged/);
});
