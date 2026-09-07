const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const temp = fs.mkdtempSync(path.join(os.tmpdir(), "uai-bootstrap-"));
(async () => {
try {
  const resources = path.join(temp, "resources");
  const template = path.join(resources, "uai-backend");
  fs.mkdirSync(path.join(template, "bin"), { recursive: true });
  fs.mkdirSync(path.join(template, "state"));
  fs.writeFileSync(path.join(template, "state", "cache.json"), '{"resolved":{}}');
  fs.writeFileSync(path.join(template, "state", "assets.json"), '"private inventory"');
  fs.writeFileSync(path.join(template, "config.json"), '"private config"');
  let selection;
  const app = {
    isPackaged: true,
    getPath: () => path.join(temp, "user"),
    whenReady: () => ({ then() {} }),
    on() {},
  };
  const context = vm.createContext({
    require: (name) => name === "electron"
      ? { app, dialog: { showOpenDialogSync: () => selection } }
      : require(name),
    process: { resourcesPath: resources },
    __dirname: path.join(temp, "dev", "electron"),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../electron/main.cjs"), "utf8"), context);
  const state = { service: "unity-asset-index", ready: true, csrf: "token" };
  context.fetch = async () => ({ ok: true, text: async () => JSON.stringify(state) });
  await assert.rejects(vm.runInContext("ensureBackend()", context), /incompatible.*restart/i);
  await assert.rejects(vm.runInContext('postJson("/api/resync/plan")', context), /incompatible.*restart/i);
  state.api_version = 2;
  await assert.rejects(vm.runInContext("ensureBackend()", context), /incompatible.*restart/i);
  state.api_version = 3;
  await assert.rejects(vm.runInContext("ensureBackend()", context), /incompatible.*restart/i);
  state.api_version = 4;
  await vm.runInContext("ensureBackend()", context);
  const prepare = () => vm.runInContext("preparePackagedBackend()", context);
  const root = path.join(temp, "user", "backend");
  assert.equal(prepare(), null);
  assert.equal(fs.existsSync(path.join(root, "config.json")), false);
  selection = [temp];
  assert.equal(prepare(), root);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "config.json"))).vault_root, temp);
  assert.equal(fs.existsSync(path.join(root, "state", "assets.json")), false);
  assert.equal(fs.readFileSync(path.join(root, "state", "cache.json"), "utf8"), '{"resolved":{}}');
  fs.writeFileSync(path.join(root, "state", "cache.json"), '"user cache"');
  selection = undefined;
  assert.equal(prepare(), root);
  assert.equal(fs.readFileSync(path.join(root, "state", "cache.json"), "utf8"), '"user cache"');
  app.isPackaged = false;
  assert.equal(prepare(), null);
  selection = [temp];
  assert.equal(prepare(), path.join(temp, "dev"));
  console.log("PASS: backend compatibility, cancel, per-user config, cache-only seed, cache preservation, fresh development setup");
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
})().catch((error) => { console.error(error); process.exitCode = 1; });
