const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const http = require("node:http");
const crypto = require("node:crypto");
const { REPO, selectRelease, downloadVerified, extractBundle } = require("../electron/updates.cjs");

function makeZip(es) {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  const crc = (b) => {
      let c = 0xffffffff;
      for (const x of b) c = t[(c ^ x) & 255] ^ (c >>> 8);
      return (c ^ 0xffffffff) >>> 0;
    },
    ls = [],
    cs = [];
  let off = 0;
  for (const e of es) {
    const n = Buffer.from(e.name),
      b = Buffer.from(e.body),
      c = crc(b),
      l = Buffer.alloc(30 + n.length);
    l.writeUInt32LE(0x04034b50);
    l.writeUInt16LE(20, 4);
    l.writeUInt16LE(0x800, 6);
    l.writeUInt32LE(c, 14);
    l.writeUInt32LE(b.length, 18);
    l.writeUInt32LE(b.length, 22);
    l.writeUInt16LE(n.length, 26);
    n.copy(l, 30);
    ls.push(l, b);
    const x = Buffer.alloc(46 + n.length);
    x.writeUInt32LE(0x02014b50);
    x.writeUInt16LE(0x0314, 4);
    x.writeUInt16LE(20, 6);
    x.writeUInt16LE(0x800, 8);
    x.writeUInt32LE(c, 16);
    x.writeUInt32LE(b.length, 20);
    x.writeUInt32LE(b.length, 24);
    x.writeUInt16LE(n.length, 28);
    x.writeUInt32LE(((e.mode ?? 0o100644) << 16) >>> 0, 38);
    x.writeUInt32LE(off, 42);
    n.copy(x, 46);
    cs.push(x);
    off += l.length + b.length;
  }
  const cb = Buffer.concat(cs),
    end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50);
  end.writeUInt16LE(es.length, 8);
  end.writeUInt16LE(es.length, 10);
  end.writeUInt32LE(cb.length, 12);
  end.writeUInt32LE(off, 16);
  return Buffer.concat([...ls, cb, end]);
}

(async () => {
  const temp = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "ual-updates-test-")));
  const payload = Buffer.from("release artifact bytes");
  const server = http.createServer((_request, response) => response.end(payload));
  try {
    const release = { tag_name: "v0.3.0", draft: false, prerelease: false, assets: [{
      name: "unity-asset-library-0.3.0-arm64.zip", state: "uploaded", size: payload.length,
      digest: `sha256:${crypto.createHash("sha256").update(payload).digest("hex")}`,
      browser_download_url: `https://github.com/${REPO}/releases/download/v0.3.0/unity-asset-library-0.3.0-arm64.zip`,
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

    const bundle = "Unity Asset Library.app";
    const entry = (name, body = "bad", mode = 0o100644) => ({ name: bundle + "/" + name, body, mode });
    const goodZip = path.join(temp, "valid.zip");
    await fs.writeFile(goodZip, makeZip([
      entry("Contents/Frameworks/Current", "Versions/A", 0o120777),
      entry("Contents/Frameworks/Versions/A/data", "framework bytes"),
      entry("Contents/MacOS/probe", "executable bytes", 0o100755),
      { name: "__MACOSX/._metadata", body: "ignored" },
    ]));
    const extracted = await extractBundle(goodZip, path.join(temp, "valid"));
    assert.equal(await fs.readFile(path.join(extracted, "Contents/Frameworks/Current/data"), "utf8"), "framework bytes");
    assert.equal(await fs.readlink(path.join(extracted, "Contents/Frameworks/Current")), "Versions/A");
    assert.equal((await fs.stat(path.join(extracted, "Contents/MacOS/probe"))).mode & 0o777, 0o755);
    const victim = path.join(temp, "victim");
    await fs.mkdir(victim);
    await fs.writeFile(path.join(victim, "sentinel"), "preserve me");
    const invalid = [
      [{ name: "../escape", body: "bad" }],
      [{ name: victim + "/escape", body: "bad" }],
      [{ name: "Other.app/payload", body: "bad" }],
      [entry("Contents/Resources/line" + String.fromCharCode(10) + "break")],
      [entry("Contents/Resources/item"), entry("Contents/Resources/ITEM")],
      [entry("Contents/Resources/caf" + String.fromCharCode(233)), entry("Contents/Resources/cafe" + String.fromCharCode(769))],
      [entry("Contents/Resources/pipe", "", 0o010644)],
      [entry("Contents/Resources/link", victim, 0o120777)],
      [entry("Contents/Resources/link", "../../../../victim", 0o120777)],
      [entry("Contents/Resources/link", "x".repeat(4097), 0o120777)],
      [entry("flat/", "", 0o040755), entry("a/b/link", "../../flat", 0o120777),
        entry("a/b/c/jump", "../link/../../../victim", 0o120777), entry("a/b/c/jump/payload", ".", 0o120777)],
    ];
    for (const [index, entries] of invalid.entries()) {
      const zip = path.join(temp, "invalid-" + index + ".zip");
      await fs.writeFile(zip, makeZip(entries));
      await assert.rejects(extractBundle(zip, path.join(temp, "out-" + index)));
      assert.equal(await fs.readFile(path.join(victim, "sentinel"), "utf8"), "preserve me");
      assert.deepEqual(await fs.readdir(victim), ["sentinel"]);
    }
    await assert.rejects(fs.access(path.join(temp, "escape")), { code: "ENOENT" });
    console.log("PASS: framework symlinks and executable permissions preserved; unsafe paths, duplicate names, special files and escaping link chains rejected");
  } finally {
    await new Promise((resolve) => server.close(resolve));
    await fs.rm(temp, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
