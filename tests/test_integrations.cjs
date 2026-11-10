// Integration settings proxy: config capabilities, session editor id, only known form fields forwarded.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createIntegrations } = require("../services/platform-api/integrations.cjs");

const ADMIN = "a".repeat(24);
function build(caps = ["access:admin", "read:configs", "manage:configs"]) {
  const calls = [];
  process.env.KAI_PLATFORM_INTERNAL_TOKEN = "internal";
  const api = createIntegrations({
    hasCapability: async (_p, cap) => caps.includes(cap),
    fetchImpl: async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, text: async () => "{}" }; },
  });
  return { api, calls, req: (body, name = "openmetadata") => ({ user: { id: ADMIN, role: "ADMIN" }, params: { name }, body }) };
}

test("save sends the session editor and drops unknown or spoofed fields", async () => {
  const { api, calls, req } = build();
  await api.save(req({ url: "https://om", token: "t", updated_by: "b".repeat(24), extra: 1 }));
  const sent = JSON.parse(calls[0].init.body);
  assert.deepEqual(sent, { url: "https://om", token: "t", updated_by: ADMIN });
  assert.equal(calls[0].init.method, "PUT");
});

test("read-only config admins can view but not save or test; others cannot view", async () => {
  const { api, req } = build(["access:admin", "read:configs"]);
  await api.get(req());
  await assert.rejects(api.save(req({})), { status: 403 });
  await assert.rejects(api.test(req({})), { status: 403 });
  await assert.rejects(build(["access:admin", "read:users"]).api.get(req()), { status: 403 });
  await assert.rejects(build().api.get({ user: {} }), { status: 401 });
});

test("each section forwards only its own fields; unknown sections are 404", async () => {
  const { api, calls, req } = build();
  await api.save(req({ model: "m", api_key: "k", url: "https://om", max_memory_gb: 9 }, "batch_model"));
  assert.match(calls[0].url, /integrations\/batch_model$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { model: "m", api_key: "k", updated_by: ADMIN });
  await api.save(req({ max_memory_gb: 9, model: "m" }, "limits"));
  assert.deepEqual(JSON.parse(calls[1].init.body), { max_memory_gb: 9, updated_by: ADMIN });
  await assert.rejects(api.get(req({}, "../binding")), { status: 404 });
});
