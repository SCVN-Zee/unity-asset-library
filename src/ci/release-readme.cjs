/** Own-repository README synchronization, independent of installation command style. */
const fs = require('node:fs');
const path = require('node:path');
const { validateRepository } = require('./release-identity.cjs');

function syncReadmeRepository(text, repository) {
  validateRepository(repository);
  const match = /gh release download[^\n]*--repo (?:github\.com\/)?([A-Za-z0-9-]+\/[A-Za-z0-9_.-]+)/.exec(text) ||
    /https:\/\/github\.com\/([A-Za-z0-9-]+\/[A-Za-z0-9_.-]+)\/releases(?:[\s/#?)\]]|$)/.exec(text);
  if (!match) throw new Error('README has no repository release link or install command');
  const previous = validateRepository(match[1]);
  const escaped = previous.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return text.replace(new RegExp(`(^|[^A-Za-z0-9_.-])${escaped}(?=$|[^A-Za-z0-9_.-])`, 'g'),
    (_whole, prefix) => prefix + repository);
}

function syncReadmeFile(root, repository) {
  validateRepository(repository);
  const file = path.join(root, 'README.md');
  if (!fs.existsSync(file)) return 'README absent; skipped';
  const before = fs.readFileSync(file, 'utf8');
  const after = syncReadmeRepository(before, repository);
  if (after !== before) fs.writeFileSync(file, after);
  return after === before ? 'README repository already current' : 'README repository updated';
}

module.exports = { syncReadmeRepository, syncReadmeFile };
