// Exercise the same selection and RPC payload used by both React views.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

const source = fs.readFileSync(require.resolve("../src/utils/vocabulary.ts"), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
let payload;
let saved = false;
async function call(route, ...args) {
  assert.equal(route, "save_word");
  payload = args;
  return { saved, error: saved ? undefined : "Database unavailable" };
}
const api = {};
vm.runInNewContext(outputText, {
  exports: api,
  require: name => {
    assert.equal(name, "./diagnostics");
    return { call };
  },
});

async function main() {
  const { result, surface, headword, reading } = JSON.parse(fs.readFileSync(0, "utf8"));
  const word = result.words.find(w => w.word.surface === surface);
  const choice = api.entryChoices(word).find(c => c.entry.headword === headword && c.entry.reading === reading);
  assert.ok(choice, "Requested dictionary entry must be selectable");
  await assert.rejects(api.saveChoice(choice, result.raw_text), /Database unavailable/);
  saved = true;
  await api.saveChoice(choice, result.raw_text);
  process.stdout.write(JSON.stringify(payload));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
