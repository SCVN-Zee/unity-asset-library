const net = require("node:net");
const { promisify } = require("node:util");
const execFile = promisify(require("node:child_process").execFile);

async function probePort(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", (error) => error.code === "EADDRINUSE" ? resolve(null) : reject(error));
    server.listen(port, "127.0.0.1", () => {
      const selected = server.address().port;
      server.close((error) => error ? reject(error) : resolve(selected));
    });
  });
}

async function listenerIdentity(port) {
  // The desktop distribution targets macOS. Unknown ownership always fails closed.
  const { stdout } = await execFile("/usr/sbin/lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"], { timeout: 2000 });
  const pids = [...new Set(stdout.trim().split(/\s+/))];
  if (pids.length !== 1 || !/^[1-9]\d*$/.test(pids[0])) throw new Error("Cannot identify a single listening process.");
  const pid = Number(pids[0]);
  if (pid === process.pid || pid === process.ppid) throw new Error("Cannot stop this application or its launcher.");
  const result = await execFile("/bin/ps", ["-p", String(pid), "-o", "lstart=,comm="], { timeout: 2000, env: { ...process.env, LC_ALL: "C" } });
  const identity = result.stdout.trim();
  if (!identity) throw new Error("The listening process has exited.");
  // ps lstart uses a fixed 24-character C-locale date; keep it for identity checks, not the UI.
  const name = require("node:path").basename(identity.slice(24).trim());
  return { pid, identity, name };
}

async function resolvePort(port, ask) {
  let detail = "";
  while (!await probePort(port)) {
    let owner = null;
    try { owner = await listenerIdentity(port); } catch { /* Keep the non-destructive option available. */ }
    const suggestion = await probePort(0);
    const choice = await ask({ port, suggestion, owner, detail, confirm: false });
    detail = "";
    if (choice.action === "use") {
      const selected = choice.port;
      if (!Number.isInteger(selected) || selected < 1 || selected > 65535) {
        detail = "Enter a whole-number port from 1 to 65535.";
        continue;
      }
      try {
        if (await probePort(selected)) return selected;
        detail = `Port ${selected} is also in use. Choose another port.`;
      } catch (error) { detail = `Cannot use port ${selected}: ${error.message}`; }
      continue;
    }
    if (choice.action === "cancel") throw new Error("Connection cancelled. Select Retry when you’re ready.");
    if (!owner || choice.action !== "stop") throw new Error("Invalid port recovery action.");
    const confirmation = await ask({ port, suggestion, owner, detail: "", confirm: true });
    if (confirmation.action !== "confirm-stop") continue;
    try {
      const current = await listenerIdentity(port);
      if (current.pid !== owner.pid || current.identity !== owner.identity) throw new Error("The listening process changed. Review the new process before stopping it.");
      process.kill(owner.pid, "SIGTERM");
      for (let attempt = 0; attempt < 50; attempt++) {
        if (await probePort(port)) return port;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      detail = "The port is still occupied after requesting termination. Choose another port or cancel. No force-kill was sent.";
    } catch (error) { detail = `Could not stop the process: ${error.message}`; }
  }
  return port;
}

module.exports = { probePort, resolvePort };
