const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { once } = require("node:events");
const { probePort, resolvePort } = require("../electron/port-recovery.cjs");

(async () => {
  const child = spawn(process.execPath, ["-e", `
    const server = require('node:net').createServer();
    server.listen(0, '127.0.0.1', () => console.log(server.address().port));
    process.on('SIGTERM', () => { if (!process.env.IGNORE_TERM) server.close(() => process.exit()); });
  `], { env: { ...process.env, IGNORE_TERM: "1" }, stdio: ["ignore", "pipe", "inherit"] });
  try {
    const port = Number(String((await once(child.stdout, "data"))[0]).trim());
    assert.equal(await probePort(port), null);
    let choices = [];
    const ask = async (request) => {
      const choice = choices.shift();
      assert.ok(choice, "Unexpected recovery request");
      return choice(request);
    };
    const cancel = () => ({ action: "cancel" });
    const stop = (request) => { assert.equal(request.owner.pid, child.pid); return { action: "stop" }; };
    choices = [cancel];
    await assert.rejects(resolvePort(port, ask), /cancelled/);
    process.kill(child.pid, 0);

    // Invalid input and occupied replacement ports remain recoverable.
    const available = await probePort(0);
    choices = [() => ({ action: "use", port: 0 }), (request) => { assert.match(request.detail, /whole-number/); return { action: "use", port }; }, (request) => { assert.match(request.detail, /also in use/); return { action: "use", port: available }; }];
    assert.equal(await resolvePort(port, ask), available);
    process.kill(child.pid, 0);

    // Declining the destructive confirmation never signals the listener.
    choices = [stop, cancel, cancel];
    await assert.rejects(resolvePort(port, ask), /cancelled/);
    assert.equal(await probePort(port), null);

    // An uncooperative process is not force-killed and another port remains available.
    choices = [stop, (request) => { assert.equal(request.confirm, true); return { action: "confirm-stop" }; }, (request) => { assert.match(request.detail, /No force-kill/); return { action: "use", port: available }; }];
    assert.equal(await resolvePort(port, ask), available);
    process.kill(child.pid, 0);
    assert.equal(choices.length, 0);
    console.log("PASS: real occupied listener, cancellation, invalid/busy replacement, declined stop, failed graceful termination and alternate port");
  } finally {
    const exited = once(child, "exit");
    child.kill("SIGKILL"); // Test-owned listener deliberately ignores SIGTERM.
    await exited;
  }

  const cooperative = spawn(process.execPath, ["-e", "require('node:net').createServer().listen(0, '127.0.0.1', function() { console.log(this.address().port); })"], { stdio: ["ignore", "pipe", "inherit"] });
  try {
    const port = Number(String((await once(cooperative.stdout, "data"))[0]).trim());
    const exited = once(cooperative, "exit");
    const ask = async (request) => {
      assert.equal(request.owner.pid, cooperative.pid);
      return { action: request.confirm ? "confirm-stop" : "stop" };
    };
    assert.equal(await resolvePort(port, ask), port);
    assert.equal((await exited)[1], "SIGTERM");
    assert.equal(await probePort(port), port);
    console.log("PASS: confirmed stop releases original port using SIGTERM");
  } finally { if (cooperative.exitCode === null && cooperative.signalCode === null) cooperative.kill(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
