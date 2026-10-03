/** Draft-only GitHub publication. Copy with release-identity.cjs; inject gh for offline tests. */
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { validateRepository } = require('./release-identity.cjs');

function ghCommand(args) {
  return execFileSync('gh', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

function expectedAssets(files) {
  if (!files.length) throw new Error('release assets are required');
  const expected = new Map();
  for (const file of files) {
    const bytes = fs.readFileSync(file);
    if (!bytes.length) throw new Error(`empty release asset: ${file}`);
    const name = path.basename(file);
    if (expected.has(name)) throw new Error(`duplicate release asset: ${name}`);
    expected.set(name, { size: bytes.length, digest: `sha256:${createHash('sha256').update(bytes).digest('hex')}` });
  }
  return expected;
}

function assertAssets(assets, expected) {
  if (!Array.isArray(assets) || assets.length !== expected.size)
    throw new Error('release asset allowlist mismatch');
  const unseen = new Set(expected.keys());
  for (const asset of assets) {
    const local = expected.get(asset.name);
    if (!local || !unseen.delete(asset.name)) throw new Error(`unexpected release asset: ${asset.name}`);
    if (asset.digest !== local.digest || asset.size !== local.size)
      throw new Error(`asset read-back mismatch: ${asset.name}`);
  }
  if (unseen.size) throw new Error('missing release assets');
}

async function publishRelease({ repository, tag, channel, files, notes, gh = ghCommand,
    wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
  validateRepository(repository);
  if (!['stable', 'beta'].includes(channel)) throw new Error('channel must be stable or beta');
  if (!/^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(tag)) throw new Error('invalid release tag');
  const expected = expectedAssets(files); // Fail before creating or mutating a draft.
  const command = args => gh([...args, '--repo', repository]);
  const find = () => {
    // List failures are fatal, not confused with a missing tag.
    let pages;
    try {
      pages = JSON.parse(gh(['api', '--paginate', '--slurp', `repos/${repository}/releases?per_page=100`]));
    } catch (error) {
      throw new Error(`cannot read GitHub release list: ${error.message}`);
    }
    if (!Array.isArray(pages) || pages.some(page => !Array.isArray(page))) throw new Error('invalid release list');
    const matches = pages.flat().filter(release => release.tag_name === tag);
    if (matches.length > 1) throw new Error('multiple releases found for tag');
    return matches[0];
  };
  const assertDraft = release => {
    if (!release?.draft || release.prerelease !== (channel === 'beta'))
      throw new Error(`release must be a ${channel} draft; refusing published or wrong-channel assets`);
  };
  let release = find();
  if (release) assertDraft(release);
  else {
    const args = ['release', 'create', tag, '--verify-tag', '--draft', '--title', tag];
    if (channel === 'beta') args.push('--prerelease');
    args.push(...(notes ? ['--notes', notes] : ['--generate-notes']));
    command(args);
    for (const delay of [0, 1000, 2000, 4000, 8000]) {
      if (delay) await wait(delay);
      release = find();
      if (release) break;
    }
    assertDraft(release);
  }
  // Reruns may replace only expected files in an unpublished, same-channel draft.
  assertDraft(find());
  command(['release', 'upload', tag, ...files, '--clobber']);
  assertDraft(find());
  let assets;
  try {
    assets = JSON.parse(command(['release', 'view', tag, '--json', 'assets'])).assets;
  } catch (error) {
    throw new Error(`cannot read GitHub release assets: ${error.message}`);
  }
  assertAssets(assets, expected);
  command(['release', 'edit', tag, '--draft=false', `--prerelease=${channel === 'beta'}`,
    `--latest=${channel === 'stable'}`]);
}

module.exports = { expectedAssets, assertAssets, publishRelease };
