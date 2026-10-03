#!/usr/bin/env node
const path = require('node:path');
const { validateRelease } = require('../../src/ci/release-identity.cjs');
const { publishRelease } = require('../../src/ci/release-publisher.cjs');
const config = require('./release-config.cjs');
async function main() {
  const root = path.resolve(__dirname, '../..');
  const release = validateRelease(root, config, process.argv[2]);
  const files = config.assets(release).map(file => path.resolve(root, file));
  await publishRelease({ ...release, files, notes: config.notes });
  console.log(`Published verified ${release.tag} to ${release.repository}`);
}
main().catch(error => {
  console.error(`publish-release: ${error.message}`);
  process.exitCode = 1;
});
