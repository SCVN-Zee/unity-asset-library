const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { validateRepository, repositoryIdentity, validateRelease, prepareRepository } = require('../../src/ci/release-identity.cjs');
const { syncReadmeRepository, syncReadmeFile } = require('../../src/ci/release-readme.cjs');
const { publishRelease, expectedAssets } = require('../../src/ci/release-publisher.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'portable-release-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

test('CI identity is authoritative, invalid CI fails closed, local ignores ambient CI repo', () => {
  const pkg = { repository: { url: 'https://github.com/local/project.git' } };
  assert.equal(repositoryIdentity(pkg, {}, { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'moved/project' }), 'moved/project');
  assert.equal(repositoryIdentity(pkg, {}, { GITHUB_REPOSITORY: 'ambient/project' }), 'local/project');
  assert.throws(() => repositoryIdentity(pkg, {}, { GITHUB_ACTIONS: 'true' }), /required/);
  for (const value of ['', '../repo', 'owner/..', 'owner/repo?token', 'owner/repo/extra'])
    assert.throws(() => validateRepository(value), /invalid/);
});

test('tag, channel, lock and private visibility validation precede build metadata mutation', t => {
  const root = fixture(t);
  const pkg = { version: '1.2.3', private: true, build: {}, repository: 'https://github.com/local/project.git' };
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify(pkg));
  fs.writeFileSync(path.join(root, 'package-lock.json'), JSON.stringify({ version: pkg.version, packages: { '': { version: pkg.version } } }));
  const env = { GITHUB_ACTIONS: 'true', GITHUB_REPOSITORY: 'moved/app', GITHUB_REF_NAME: 'v1.2.3' };
  const config = { appMetadata: true };
  const release = validateRelease(root, config, 'stable', env);
  prepareRepository(root, config, release);
  const prepared = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
  assert.equal(prepared.releaseRepository, 'moved/app');
  assert.deepEqual(prepared.build.publish, { provider: 'github', owner: 'moved', repo: 'app', publishAutoUpdate: false });
  assert.throws(() => validateRelease(root, config, 'beta', env), /not beta/);
  assert.throws(() => validateRelease(root, config, 'stable', { ...env, GITHUB_REF_NAME: 'v9.9.9' }), /tag/);
  assert.throws(() => validateRelease(root, { ...config, requirePrivateRepository: true }, 'stable', env), /private/);
  fs.writeFileSync(path.join(root, 'package-lock.json'), '{broken');
  assert.throws(() => validateRelease(root, config, 'stable', env), /package metadata/);
});

test('README works for desktop links and install commands, is idempotent and skips absence', t => {
  const text = 'https://github.com/old/app/releases/latest old/app old/app-extra https://github.com/foreign/tool\n';
  const after = syncReadmeRepository(text, 'new/moved');
  assert.equal(after, text.replaceAll('old/app/releases', 'new/moved/releases').replace(' old/app ', ' new/moved '));
  assert.equal(syncReadmeRepository(after, 'new/moved'), after);
  const root = fixture(t);
  assert.match(syncReadmeFile(root, 'new/moved'), /absent; skipped/);
  assert.equal(fs.existsSync(path.join(root, 'README.md')), false);
  assert.throws(() => syncReadmeRepository('no release link', 'new/moved'), /no repository/);
});

function publisher(t, channel = 'stable') {
  const root = fixture(t);
  const files = ['app.dmg', 'app.zip'].map(name => path.join(root, name));
  files.forEach(file => fs.writeFileSync(file, 'verified payload'));
  let state;
  const calls = [];
  const assets = [...expectedAssets(files)].map(([name, meta]) => ({ name, ...meta }));
  const options = { repository: 'moved/app', tag: channel === 'beta' ? 'v1.2.3-beta.1' : 'v1.2.3', channel, files,
    wait: async () => {}, gh: args => {
      calls.push(args);
      if (args[0] === 'api') return JSON.stringify([[...(state ? [state] : [])]]);
      if (args[1] === 'create') state = { tag_name: options.tag, draft: true, prerelease: channel === 'beta' };
      if (args[1] === 'view') return JSON.stringify({ assets });
      return '';
    } };
  return { options, calls, assets, setState: value => { state = value; } };
}

for (const channel of ['stable', 'beta']) test(`${channel} publishes only after digest read-back with explicit destination`, async t => {
  const f = publisher(t, channel);
  await publishRelease(f.options);
  const actions = f.calls.filter(args => args[0] === 'release');
  assert.deepEqual(actions.map(args => args[1]), ['create', 'upload', 'view', 'edit']);
  actions.forEach(args => assert.deepEqual(args.slice(-2), ['--repo', 'moved/app']));
  assert.ok(actions[0].includes('--verify-tag'));
  assert.ok(actions.at(-1).includes(`--latest=${channel === 'stable'}`));
  assert.ok(actions.at(-1).includes(`--prerelease=${channel === 'beta'}`));
});

test('published and wrong-channel drafts cannot upload', async t => {
  for (const state of [{ draft: false, prerelease: false }, { draft: true, prerelease: true }]) {
    const f = publisher(t);
    f.setState({ tag_name: f.options.tag, ...state });
    await assert.rejects(publishRelease(f.options), /draft/);
    assert.equal(f.calls.some(args => args[1] === 'upload'), false);
  }
});

test('missing/empty assets and API errors cannot create a draft', async t => {
  const f = publisher(t);
  fs.writeFileSync(f.options.files[0], '');
  await assert.rejects(publishRelease(f.options), /empty/);
  assert.equal(f.calls.length, 0);
  fs.writeFileSync(f.options.files[0], 'payload');
  await assert.rejects(publishRelease({ ...f.options, gh: () => { throw Error('API denied'); } }), /release list/);
  await assert.rejects(publishRelease({ ...f.options, gh: () => '{broken' }), /release list/);
});

test('draft visibility retries are bounded and upload failures never publish', async t => {
  const f = publisher(t);
  const delegate = f.options.gh;
  let created = false, hidden = 0, waits = 0;
  const gh = args => {
    if (args[1] === 'create') created = true;
    if (args[0] === 'api' && created && hidden++ < 2) return '[[]]';
    return delegate(args);
  };
  await publishRelease({ ...f.options, gh, wait: async () => { waits++; } });
  assert.equal(waits, 2);
  const failed = publisher(t);
  await assert.rejects(publishRelease({ ...failed.options, gh: args => {
    if (args[1] === 'upload') throw Error('upload interrupted');
    return failed.options.gh(args);
  } }), /upload interrupted/);
  assert.equal(failed.calls.some(args => args[1] === 'edit'), false);
  const invisible = publisher(t);
  await assert.rejects(publishRelease({ ...invisible.options, gh: args => {
    if (args[0] === 'api') return '[[]]';
    return invisible.options.gh(args);
  } }), /draft/);
  assert.equal(invisible.calls.some(args => args[1] === 'upload'), false);
});

test('missing, extra, duplicate and mismatched read-back assets cannot publish', async t => {
  for (const change of [a => a.pop(), a => a.push({ name: 'extra' }), a => { a[1] = a[0]; },
    a => { a[0].digest = 'sha256:wrong'; }, a => { a[0].size++; }]) {
    const f = publisher(t);
    change(f.assets);
    await assert.rejects(publishRelease(f.options), /asset/);
    assert.equal(f.calls.some(args => args[1] === 'edit'), false);
  }
});
