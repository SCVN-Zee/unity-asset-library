const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

// Command-Tab reads the native bundle name, not BrowserWindow.title or app.name.
// electron-builder handles packaged apps; npm reinstalls reset this dev bundle.
if (process.platform === "darwin") {
  const executable = require("electron");
  const name = require("../package.json").build.productName;
  const bundle = path.resolve(executable, "../../..");
  const namedBundle = path.join(path.dirname(bundle), `${name}.app`);
  if (bundle !== namedBundle) fs.renameSync(bundle, namedBundle);
  // Dock/Command-Tab use the .app directory name even when Info.plist is renamed.
  fs.writeFileSync(path.join(path.dirname(require.resolve("electron")), "path.txt"),
    path.join(`${name}.app`, "Contents", "MacOS", path.basename(executable)));
  const plist = path.join(namedBundle, "Contents", "Info.plist");
  for (const key of ["CFBundleName", "CFBundleDisplayName"]) {
    execFileSync("/usr/bin/plutil", ["-replace", key, "-string", name, plist]);
    assert.equal(execFileSync("/usr/bin/plutil", ["-extract", key, "raw", "-o", "-", plist], {
      encoding: "utf8",
    }).trim(), name);
  }
  // Give Launch Services a distinct identity instead of its cached Electron label.
  execFileSync("/usr/bin/plutil", ["-replace", "CFBundleIdentifier", "-string",
    `${require("../package.json").build.appId}.dev`, plist]);
}
