const fs = require("node:fs");
const path = require("node:path");

async function hydrateAsset(root, asset, signal) {
  root = await fs.promises.realpath(root);
  const versions = (asset.versions || []).filter(version => version.file && version.availability === "cloud_only");
  // Validate every path before reading any placeholder. Never write a second copy.
  const targets = await Promise.all(versions.map(async version => {
    if (typeof version.file !== "string" || path.isAbsolute(version.file) || version.file.includes("\0")) throw new Error("Invalid archive path.");
    const target = await fs.promises.realpath(path.resolve(root, version.file));
    if (!target.startsWith(root + path.sep)) throw new Error("This file is outside the library folder.");
    const info = await fs.promises.stat(target);
    if (!info.isFile()) throw new Error("This archive is no longer a file.");
    return { target, size: info.size };
  }));
  for (const { target, size } of targets) {
    signal.throwIfAborted();
    let bytes = 0;
    const stream = fs.createReadStream(target, { signal, flags: fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW });
    for await (const chunk of stream) bytes += chunk.length;
    if (bytes !== size) throw new Error("The archive changed during download. Refresh and try again.");
  }
  return { downloaded: targets.length };
}

module.exports = { hydrateAsset };
