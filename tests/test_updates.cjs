const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const crypto = require("node:crypto");
const { selectRelease, downloadVerified } = require("../electron/updates.cjs");

(async () => {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "ual-updates-test-"));
  const payload = Buffer.from("release artifact bytes");
  const server = http.createServer((_request, response) => response.end(payload));
  try {
    const release = { tag_name: "v0.3.0", draft: false, prerelease: false, assets: [{
      name: "unity-asset-library-0.3.0-arm64.zip", state: "uploaded", size: payload.length,
      digest: `sha256:${crypto.createHash("sha256").update(payload).digest("hex")}`,
      browser_download_url: "https://github.com/SCVN-Zee/unity-asset-library/releases/download/v0.3.0/unity-asset-library-0.3.0-arm64.zip",
    }] };
    assert.equal(selectRelease(release, "0.2.6").version, "0.3.0");
    assert.equal(selectRelease(release, "0.3.0"), null);
    assert.equal(selectRelease(release, "0.4.0"), null);
    assert.equal(selectRelease({ ...release, prerelease: true }, "0.2.6"), null);
    assert.equal(selectRelease({ ...release, draft: true }, "0.2.6"), null);
    assert.throws(() => selectRelease({ ...release, assets: [] }, "0.2.6"), /verified arm64 ZIP/);
    assert.throws(() => selectRelease({ ...release, assets: [{ ...release.assets[0], digest: null }] }, "0.2.6"), /verified arm64 ZIP/);
    assert.throws(() => selectRelease({ ...release, assets: [{ ...release.assets[0], browser_download_url: "https://example.com/update.zip" }] }, "0.2.6"), /invalid/);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const asset = { ...selectRelease(release, "0.2.6"), url: `http://127.0.0.1:${server.address().port}/artifact` };
    const destination = path.join(temp, "update.zip");
    await downloadVerified(asset, destination);
    assert.deepEqual(await fs.readFile(destination), payload);
    await fs.rm(destination);
    await assert.rejects(downloadVerified({ ...asset, digest: "0".repeat(64) }, destination), /checksum mismatch/);
    await assert.rejects(fs.access(destination), { code: "ENOENT" });
    await assert.rejects(downloadVerified({ ...asset, size: payload.length - 1 }, destination), /exceeds/);
    await assert.rejects(fs.access(destination), { code: "ENOENT" });
    await assert.rejects(downloadVerified({ ...asset, size: payload.length + 1 }, destination), /checksum mismatch/);
    await assert.rejects(fs.access(destination), { code: "ENOENT" });
    console.log("PASS: stable release selection; missing/untrusted assets rejected; real download integrity and size failures discard artifacts");
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await fs.rm(temp, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
