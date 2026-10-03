#!/usr/bin/env node
const path = require('node:path');
const { syncReadmeFile } = require('../../src/ci/release-readme.cjs');
try {
  console.log(syncReadmeFile(path.resolve(__dirname, '../..'), process.env.GITHUB_REPOSITORY));
} catch (error) {
  console.error(`sync-readme-repository: ${error.message}`);
  process.exitCode = 1;
}
