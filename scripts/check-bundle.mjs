// Smoke-test the built entry with the globals supplied by Decky Loader.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../dist/index.js', import.meta.url), 'utf8');
assert(!/\bDECKY_API\b/.test(source), 'Bundle must not require an invented API global');
let connected = false;
globalThis.window = {
  addEventListener() {},
  removeEventListener() {},
  __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit: {
    connect(version, name) {
      assert.equal(name, 'Decky Yomi');
      connected = true;
      return {
        _version: version,
        routerHook: {
          addGlobalComponent() {},
          removeGlobalComponent() {},
        },
        call: () => Promise.resolve({}),
      };
    },
  },
};
globalThis.SP_REACT = { Component: class {}, createContext: () => ({}) };
globalThis.SP_JSX = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
globalThis.DFL = { staticClasses: { Title: 'title' } };
const { default: plugin } = await import('../dist/index.js');
assert(connected);
assert.equal(typeof plugin, 'function');
const instance = plugin();
assert.equal(instance.name, 'Decky Yomi');
instance.onDismount();
console.log('Decky bundle initialization smoke test passed (mock loader globals).');
