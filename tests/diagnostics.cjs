// Verify diagnostics global listeners ignore unrelated plugins/Steam frames.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(require.resolve("../src/utils/diagnostics.ts"), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});

const listeners = new Map();
const flushed = [];
const api = {};

vm.runInNewContext(outputText, {
  exports: api,
  require: (name) => {
    assert.equal(name, "@decky/api");
    return {
      call: async (route, batch) => {
        if (route === "log_frontend") flushed.push(...batch);
        return {};
      },
    };
  },
  window: {
    addEventListener: (ev, fn) => listeners.set(ev, fn),
    removeEventListener: (ev, fn) => {
      if (listeners.get(ev) === fn) listeners.delete(ev);
    },
  },
  performance: { now: () => 0 },
  console: { info() {} },
  setInterval: () => 1,
  clearInterval: () => {},
  Date,
});

async function main() {
  const stop = api.startDiagnostics();
  const onError = listeners.get("error");
  const onRejection = listeners.get("unhandledrejection");
  assert.ok(onError && onRejection);

  // Third-party plugin and Steam UI errors must be ignored.
  onRejection({
    reason: {
      stack: "TypeError: Cannot read properties of undefined\n    at updateStyle (http://127.0.0.1:1337/plugins/SteamGridDB/dist/index.js:2677:41)",
    },
  });
  onRejection({
    reason: {
      stack: "Error: library failed\n    at foo (https://steamloopback.host/library.js:12:3)",
    },
  });
  onError({ message: "Script error.", filename: "", lineno: 0, error: null });

  // Decky Yomi errors must be recorded.
  onRejection({
    reason: {
      stack: "TypeError: own rejection\n    at run (http://127.0.0.1:1337/plugins/decky-yomi/dist/index.js:42:9)",
    },
  });
  onError({
    message: "own error",
    filename: "http://127.0.0.1:1337/plugins/decky-yomi/dist/index.js",
    lineno: 10,
    error: { stack: "Error: own error\n    at http://127.0.0.1:1337/plugins/decky-yomi/dist/index.js:10:1" },
  });

  stop();
  await new Promise((r) => setTimeout(r, 20));

  const parsed = flushed.map((line) => JSON.parse(line));
  assert.equal(parsed.some((e) => e.detail.includes("SteamGridDB")), false);
  assert.equal(parsed.some((e) => e.detail.includes("steamloopback.host")), false);
  assert.equal(parsed.some((e) => e.detail.includes("Script error.")), false);
  assert.equal(parsed.some((e) => e.event === "window.unhandledrejection" && e.detail.includes("own rejection")), true);
  assert.equal(parsed.some((e) => e.event === "window.error" && e.detail.includes("own error")), true);
  console.log("Diagnostics origin filtering tests passed.");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
