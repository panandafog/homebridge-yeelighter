const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");
const { createRequire } = require("node:module");
const { test } = require("node:test");
const dist = process.env.YEELIGHTER_TEST_DIST || path.resolve(__dirname, "../dist");
const localRequire = createRequire(path.join(dist, "discovery.js"));

test("cached discovery survives lost multicast and initial unicast; close cancels retries", () => {
  let time = 0;
  let id = 0;
  const tasks = new Map();
  const sends = [];
  const detected = new Set();
  function timer(fn, delay, repeat) {
    const handle = { id: ++id, unref() {} };
    tasks.set(handle.id, { handle, fn, at: time + delay, delay, repeat });
    return handle;
  }
  function advance(target) {
    while (true) {
      const next = [...tasks.values()].filter((task) => task.at <= target).sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      time = next.at;
      if (next.repeat) next.at += next.delay;
      else tasks.delete(next.handle.id);
      next.fn();
    }
    time = target;
  }
  const socket = new EventEmitter();
  socket.bind = (port, callback) => {
    assert.equal(port, 1982);
    callback();
    socket.emit("listening");
  };
  socket.setBroadcast = socket.setMulticastTTL = socket.addMembership = () => {};
  socket.send = (buffer, offset, length, port, host, callback) => {
    assert.equal(port, 1982);
    assert(buffer.toString().includes("ST: wifi_bulb"));
    sends.push({ time, host });
    callback?.();
    // Multicast is always lost. Direct queries only succeed after the first try.
    if (host !== "239.255.255.250" && time > 0) {
      socket.emit(
        "message",
        Buffer.from(
          `HTTP/1.1 200 OK\r\nid: 0x0000000000000001\r\nlocation: yeelight://${host}:55443\r\nmodel: mono\r\n\r\n`
        )
      );
    }
  };
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(dist, "discovery.js"), "utf8"), {
    module,
    exports: module.exports,
    Buffer,
    console,
    require: (name) => (name === "node:dgram" ? { createSocket: () => socket } : localRequire(name)),
    setTimeout: (fn, delay) => timer(fn, delay, false),
    setInterval: (fn, delay) => timer(fn, delay, true),
    clearTimeout: (handle) => tasks.delete(handle.id),
    clearInterval: (handle) => tasks.delete(handle.id)
  });
  const discovery = new module.exports.Discovery();
  discovery.addTarget("127.0.0.2");
  discovery.addTarget("127.0.0.3");
  discovery.addTarget("127.0.0.2");
  discovery.addTarget(undefined);
  discovery.addTarget("invalid-ip");
  discovery.on("didDiscoverDevice", (info) => detected.add(info.host));
  discovery.listen();
  assert.equal(sends.length, 3, "one multicast and two unique valid targets");
  assert.equal(detected.size, 0);
  advance(5000);
  assert.deepEqual([...detected].sort(), ["127.0.0.2", "127.0.0.3"]);
  advance(15000);
  assert.equal(sends.length, 12);
  advance(60000);
  assert.equal(sends.length, 15, "periodic rediscovery");
  socket.emit("close");
  advance(180000);
  assert.equal(sends.length, 15, "no sends after close");
  assert.equal(tasks.size, 0);
});

test("platform registers cached addresses once without starting extra sockets", () => {
  class Discovery extends EventEmitter {
    targets = [];
    addTarget(host) {
      this.targets.push(host);
    }
  }
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(dist, "platform.js"), "utf8"), {
    module,
    exports: module.exports,
    require: (name) => (name === "./discovery" ? { Discovery } : localRequire(name))
  });
  const noop = () => {};
  const hap = createRequire(require.resolve("homebridge"))("hap-nodejs");
  const platform = new module.exports.YeelighterPlatform(
    { debug: noop, info: noop, warn: noop },
    {},
    { hap, on: noop }
  );
  const accessory = { UUID: "cached", displayName: "cached", context: { device: { host: "192.0.2.1" } } };
  platform.configureAccessory(accessory);
  platform.configureAccessory(accessory);
  assert.deepEqual(platform.agent.targets, ["192.0.2.1"]);
});
