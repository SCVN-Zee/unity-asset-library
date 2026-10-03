const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');

test('exact workflow names and ordered validation, README sync, build and publication', () => {
  const workflow = name => fs.readFileSync(path.join(root, `.github/workflows/${name}.yml`), 'utf8');
  const sync = workflow('sync-readme');
  assert.match(sync, /^name: Sync README\n/);
  assert.match(sync, /workflow_call:/);
  assert.match(sync, /if \[ ! -f README.md \] \|\| git diff --quiet -- README.md/);
  assert.match(sync, /git add -- README.md/);
  for (const channel of ['stable', 'beta']) {
    const text = workflow(`release-${channel}`);
    assert.match(text, new RegExp(`^name: Release ${channel === 'stable' ? 'Stable' : 'Beta'}\\n`));
    assert.match(text, new RegExp(`run: node scripts/ci/validate-release.cjs ${channel}`));
    assert.match(text, /sync-readme:[\s\S]*?needs: validate\n\s+uses: \.\/.github\/workflows\/sync-readme.yml/);
    assert.match(text, /release:[\s\S]*?needs: sync-readme/);
    const prepare = text.indexOf(`run: node scripts/ci/validate-release.cjs ${channel} --prepare`);
    assert.ok(prepare > text.indexOf('  release:'));
    assert.ok(prepare > text.indexOf('run: node scripts/ci/sync-readme-repository.cjs'));
    assert.ok(prepare < text.indexOf('name: Publish verified release'));
    assert.match(text, new RegExp(`run: node scripts/ci/publish-release.cjs ${channel}`));
    assert.match(text, /GH_REPO: \$\{\{ github.repository \}\}/);
    assert.doesNotMatch(text, /gh release|continue-on-error|if:.*always\(/);
    for (const action of text.matchAll(/uses: ([^\s]+)@([^\s]+)/g))
      assert.match(action[2], /^[a-f0-9]{40}$/, `unpinned action ${action[1]}`);
  }
});

test('copied CI commands prepare moved repository and skip absent README without other writes', t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'release-projection-'));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  for (const folder of ['src/ci', 'scripts/ci']) fs.cpSync(path.join(root, folder), path.join(temp, folder), { recursive: true });
  const pkg = { version: '1.2.3', private: true, build: {} };
  const lock = JSON.stringify({ version: pkg.version, packages: { '': { version: pkg.version } } });
  fs.writeFileSync(path.join(temp, 'package.json'), JSON.stringify(pkg));
  fs.writeFileSync(path.join(temp, 'package-lock.json'), lock);
  const env = { ...process.env, GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'new-owner/moved-project',
    GITHUB_REF_NAME: 'v1.2.3', RELEASE_REPOSITORY_PRIVATE: 'true' };
  const run = (script, args = [], overrides = {}) => spawnSync(process.execPath, [`scripts/ci/${script}.cjs`, ...args],
    { cwd: temp, encoding: 'utf8', env: { ...env, ...overrides } });
  const prepared = run('validate-release', ['stable', '--prepare']);
  assert.equal(prepared.status, 0, prepared.stderr);
  assert.match(prepared.stdout, /new-owner\/moved-project/);
  const config = require(path.join(root, 'scripts/ci/release-config.cjs'));
  if (config.appMetadata) {
    const updated = JSON.parse(fs.readFileSync(path.join(temp, 'package.json')));
    assert.equal(updated.releaseRepository, 'new-owner/moved-project');
    assert.equal(updated.build.publish.repo, 'moved-project');
  }
  const before = fs.readFileSync(path.join(temp, 'package.json'));
  assert.notEqual(run('validate-release', ['beta']).status, 0);
  assert.notEqual(run('validate-release', ['stable'], { GITHUB_REPOSITORY: 'bad' }).status, 0);
  assert.notEqual(run('validate-release', ['stable'], { GITHUB_REPOSITORY: '' }).status, 0);
  const skipped = run('sync-readme-repository');
  assert.equal(skipped.status, 0, skipped.stderr);
  assert.match(skipped.stdout, /absent; skipped/);
  assert.equal(fs.existsSync(path.join(temp, 'README.md')), false);
  assert.deepEqual(fs.readFileSync(path.join(temp, 'package.json')), before);
  assert.equal(fs.readFileSync(path.join(temp, 'package-lock.json'), 'utf8'), lock);
});
