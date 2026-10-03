/** Copyable release helpers: Node stdlib only; project policy lives in release-config.cjs. */
const fs = require('node:fs');
const path = require('node:path');

function validateRepository(value) {
  if (typeof value !== 'string' ||
      !/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_.-]+$/.test(value) ||
      ['.', '..'].includes(value.split('/')[1]))
    throw new Error('invalid GitHub repository (expected owner/repo)');
  return value;
}

function repositoryIdentity(pkg, config, env = process.env) {
  if (env.GITHUB_ACTIONS === 'true') {
    if (!env.GITHUB_REPOSITORY) throw new Error('GITHUB_REPOSITORY is required in GitHub Actions');
    return validateRepository(env.GITHUB_REPOSITORY);
  }
  const url = typeof pkg.repository === 'string' ? pkg.repository : pkg.repository?.url;
  const match = /^https:\/\/github\.com\/([^/]+\/[^/]+?)(?:\.git)?$/.exec(url || '');
  return validateRepository(match ? match[1] : config.repository);
}

function validateRelease(root, config, channel, env = process.env) {
  if (!['stable', 'beta'].includes(channel)) throw new Error('channel must be stable or beta');
  let pkg, lock;
  try {
    pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json')));
    lock = JSON.parse(fs.readFileSync(path.join(root, 'package-lock.json')));
  } catch (error) {
    throw new Error(`cannot read release package metadata: ${error.message}`);
  }
  const core = '(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)\\.(?:0|[1-9]\\d*)';
  const suffix = config.betaPattern || '(?:[0-9A-Za-z-]+)(?:\\.[0-9A-Za-z-]+)*';
  const pattern = new RegExp(`^${core}${channel === 'beta' ? `-${suffix}` : ''}$`);
  if (!pattern.test(pkg.version)) throw new Error(`version '${pkg.version}' is not ${channel}`);
  if (env.GITHUB_REF_NAME !== `v${pkg.version}`) throw new Error('release tag must equal v plus package version');
  if (lock.version !== pkg.version || lock.packages?.['']?.version !== pkg.version)
    throw new Error('package-lock versions must both equal package version');
  const repository = repositoryIdentity(pkg, config, env);
  if (config.requirePrivateRepository && pkg.private === true &&
      env.GITHUB_ACTIONS === 'true' && env.RELEASE_REPOSITORY_PRIVATE !== 'true')
    throw new Error('private package requires a private release repository');
  return { pkg, version: pkg.version, tag: env.GITHUB_REF_NAME, repository, channel };
}

// Build metadata is fixed in the packaged app, never taken from its runtime environment.
function prepareRepository(root, config, release) {
  if (!config.appMetadata) return;
  const file = path.join(root, 'package.json');
  const pkg = release.pkg;
  pkg.releaseRepository = release.repository;
  if (pkg.build) {
    const [owner, repo] = release.repository.split('/');
    pkg.build.publish = { provider: 'github', owner, repo, publishAutoUpdate: false };
  }
  const before = fs.readFileSync(file, 'utf8');
  const after = JSON.stringify(pkg, null, 2) + '\n';
  if (before !== after) fs.writeFileSync(file, after);
}

module.exports = { validateRepository, repositoryIdentity, validateRelease, prepareRepository };
