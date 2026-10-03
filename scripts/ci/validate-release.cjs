#!/usr/bin/env node
const path = require('node:path');
const { validateRelease, prepareRepository } = require('../../src/ci/release-identity.cjs');
const config = require('./release-config.cjs');
try {
  const [channel, option] = process.argv.slice(2);
  if (option && option !== '--prepare') throw new Error('unknown option');
  const root = path.resolve(__dirname, '../..');
  const release = validateRelease(root, config, channel);
  if (option) prepareRepository(root, config, release);
  console.log(`Validated ${release.tag} (${channel}) for ${release.repository}`);
} catch (error) {
  console.error(`validate-release: ${error.message}`);
  process.exitCode = 1;
}
