const assert = require("node:assert/strict");
const net = require("node:net");
const path = require("node:path");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const dist = process.env.YEELIGHTER_TEST_DIST || path.resolve(__dirname, "../dist");
const { Device, EMPTY_DEVICEINFO } = require(path.join(dist, "yeedevice.js"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(condition, message) {
  const deadline = Date.now() + 8000;
  while (!condition()) {
    assert(Date.now() < deadline, message);
    await wait(10);
  }
}
function serverFixture(onConnection) {
  const sockets = new Set();
  const server = net.createServer((socket) => {
    sockets.add(socket);
    socket.on("error", () => {});
    socket.on("close", () => sockets.delete(socket));
    onConnection(socket);
  });
  return {
    server,
    listen: () => new Promise((resolve) => server.listen(0, "127.0.0.1", resolve)),
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
    }
  };
}

test("transport recovery", { concurrency: true, timeout: 12000 }, async (t) => {
  const modes = [
    "FIN",
    "RST",
    "EHOSTUNREACH",
    "duplicate-errors",
    "shutdown",
    "rediscovery",
    "idempotence",
    "stale-socket"
  ];
  await Promise.all(
    modes.map((mode) =>
      t.test(mode, async () => {
        let accepts = 0;
        const fixture = serverFixture((socket) => {
          accepts++;
          if (accepts === 1 && ["FIN", "RST", "shutdown", "rediscovery"].includes(mode)) {
            setTimeout(() => (mode === "RST" ? socket.resetAndDestroy() : socket.end()), 40);
          }
        });
        let device;
        try {
          await fixture.listen();
          device = new Device({ ...EMPTY_DEVICEINFO, host: "127.0.0.1", port: fixture.server.address().port });
          device.connect();
          await waitFor(() => device.connected && accepts === 1, "initial connection");
          const oldSocket = device.socket;
          if (mode === "EHOSTUNREACH") {
            // Inject this OS error after a real connection; loopback itself stays reachable.
            oldSocket.emit(
              "error",
              Object.assign(new Error("connect EHOSTUNREACH 127.0.0.1"), { code: "EHOSTUNREACH" })
            );
          }
          if (["duplicate-errors", "stale-socket"].includes(mode)) {
            device.socketClosed(new Error("network failure"));
            if (mode === "duplicate-errors") device.socketClosed(new Error("duplicate failure"));
          }
          if (["shutdown", "rediscovery"].includes(mode)) {
            await waitFor(() => !device.connected, "peer close");
            if (mode === "shutdown") device.disconnect(true);
            else device.reconnect();
          }
          if (mode === "idempotence") {
            device.connect();
            device.connect();
            device.reconnect();
          }
          // Wait beyond the retry interval, including when rediscovery connects early.
          await wait(5400);
          const expected = ["shutdown", "idempotence"].includes(mode) ? 1 : 2;
          assert.equal(accepts, expected, "no missing or duplicate connection");
          assert.equal(device.connected, mode !== "shutdown");
          assert.equal(!!device.retryTimer, false, "no leftover retry");
          if (mode === "stale-socket") {
            const current = device.socket;
            oldSocket.emit("data", Buffer.from("incomplete old response"));
            oldSocket.emit("error", new Error("late error"));
            oldSocket.emit("end");
            oldSocket.emit("close");
            assert.equal(device.socket, current);
            assert.equal(device.connected, true);
            assert.equal(device.rest, "");
            assert.equal(!!device.retryTimer, false);
          }
        } finally {
          device?.disconnect(true);
          await fixture.close();
        }
      })
    )
  );
});

test("heartbeat recovers from a silent connection and resumes polling", { timeout: 12000 }, async () => {
  const hap = createRequire(require.resolve("homebridge"))("hap-nodejs");
  const { YeeAccessory, TRACKED_ATTRIBUTES } = require(path.join(dist, "yeeaccessory.js"));
  const { EMPTY_ATTRIBUTES } = require(path.join(dist, "lightservice.js"));
  let accepts = 0;
  let requests = 0;
  const fixture = serverFixture((socket) => {
    const connection = ++accepts;
    let rest = "";
    let localRequests = 0;
    socket.on("data", (data) => {
      rest += data.toString();
      let end;
      while ((end = rest.indexOf("\r\n")) >= 0) {
        const command = JSON.parse(rest.slice(0, end));
        rest = rest.slice(end + 2);
        requests++;
        localRequests++;
        // Respond once, then leave TCP open without answering any more requests.
        if (connection === 1 && localRequests > 1) continue;
        const result = TRACKED_ATTRIBUTES.map((key) =>
          typeof EMPTY_ATTRIBUTES[key] === "boolean" ? "off" : typeof EMPTY_ATTRIBUTES[key] === "number" ? "0" : ""
        );
        socket.write(JSON.stringify({ id: command.id, result }) + "\r\n");
      }
    });
  });
  let device;
  let light;
  try {
    await fixture.listen();
    const info = {
      ...EMPTY_DEVICEINFO,
      id: "0x0000000000000012",
      model: "mono",
      support: "get_prop set_power set_bright",
      trackedAttributes: TRACKED_ATTRIBUTES,
      fw_ver: "test",
      host: "127.0.0.1",
      port: fixture.server.address().port
    };
    device = new Device(info);
    const accessory = new hap.Accessory("Recovery test", hap.uuid.generate(info.id));
    accessory.context = { device: info };
    const noop = () => {};
    const platform = {
      Service: hap.Service,
      Characteristic: hap.Characteristic,
      AdaptiveLightingController: hap.AdaptiveLightingController,
      config: { interval: 50, timeout: 30 },
      api: { hap, updatePlatformAccessories: noop },
      log: { info: noop, warn: noop, error: noop, debug: noop }
    };
    light = new YeeAccessory(platform, device, accessory);
    await waitFor(() => accepts === 2 && light.connected && requests > 3, "heartbeat must reconnect");
    const previous = requests;
    await wait(180);
    assert(requests > previous, "polling must continue after recovery");
    assert.equal(accepts, 2, "healthy replacement connection remains stable");
    assert.equal(device.connected, true);
  } finally {
    device?.disconnect(true);
    if (light?.interval) clearInterval(light.interval);
    await fixture.close();
  }
});
