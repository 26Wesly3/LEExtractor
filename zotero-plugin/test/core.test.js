const test = require("node:test");
const assert = require("node:assert");
const core = require("../lib/core.js");
const client = require("../lib/client.js");

test("discoverText sends only title and DOI, bounded", () => {
  const text = core.discoverText([{ title: "Attention is all you need", DOI: "10.1/abc" }, { title: "" }]);
  assert.strictEqual(text, "Attention is all you need; doi:10.1/abc");
  assert.ok(core.discoverText([{ title: "x".repeat(5000) }]).length <= 3000);
});

test("selectedMetadata never reads attachments or paths and bounds the abstract", () => {
  const item = { key: "K1", getField: (f) => ({ title: "T", DOI: "10.2/x", date: "2024-05", abstractNote: "a".repeat(3000) })[f] };
  const [row] = core.selectedMetadata([item]);
  assert.deepStrictEqual(Object.keys(row).sort(), ["DOI", "abstract", "key", "title", "year"]);
  assert.strictEqual(row.year, "2024");
  assert.strictEqual(row.abstract.length, 1500);
});

test("idempotency keys are 16 to 128 characters", () => {
  const key = core.idempotencyKey("proj");
  assert.ok(key.length >= 16 && key.length <= 128);
});

test("terminal states match the v3.2 job states", () => {
  assert.ok(core.isTerminal("succeeded"));
  assert.ok(!core.isTerminal("running"));
});

test("client sends CSRF on mutating calls and surfaces the server error envelope", async () => {
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/session")) return { ok: true, text: async () => JSON.stringify({ csrf_token: "tok" }) };
    return { ok: false, status: 403, text: async () => JSON.stringify({ error: { code: "csrf", message: "denied" } }) };
  };
  const api = client.makeClient("http://127.0.0.1:8000/api/v3", fakeFetch);
  await api.session();
  await assert.rejects(api.createProject("n", "k".repeat(16)), (e) => e.code === "csrf" && e.status === 403);
  assert.strictEqual(calls[1].init.headers["X-CSRF-Token"], "tok");
  assert.strictEqual(calls[1].init.credentials, "include");
});
