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
test("smtp test needs manage:configs and forwards only the recipient; mail never throws", async () => {
  const { api, calls, req } = build();
  await api.testSmtp(req({ to: "a@b.co", host: "evil" }, "smtp"));
  assert.match(calls[0].url, /integrations\/smtp\/test$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { to: "a@b.co" });
  await assert.rejects(build(["access:admin", "read:configs"]).api.testSmtp(req({ to: "a@b.co" }, "smtp")), (e) => e.status === 403);
  assert.deepEqual(await api.mail("a@b.co", "s", "t"), { sent: true });
  assert.match(calls.at(-1).url, /\/v1\/internal\/mail$/);
  for (const [status, configured] of [[409, false], [400, true], [503, true]]) {
    const off = createIntegrations({ hasCapability: async () => true, fetchImpl: async () => ({ ok: false, status, text: async () => JSON.stringify({ message: "SMTP belum dikonfigurasi." }) }) });
    const result = await off.mail("a@b.co", "s", "t");
    assert.equal(result.sent, false);
    assert.equal(result.configured, configured);
  }
});

test("semantic layer: session editor id, known fields only, write needs manage:configs, ids are UUIDs", async () => {
  const { api, calls } = build();
  const id = "0b6f1d2e-3c4a-4b5c-8d9e-0f1a2b3c4d5e";
  const req = (body, params = {}) => ({ user: { id: ADMIN, role: "ADMIN" }, params, body });
  await api.semanticCreate(req({ kind: "metric", name: "omzet", expression: "sum(x)", updated_by: "b".repeat(24), source: "catalog" }));
  assert.match(calls[0].url, /internal\/semantic$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { kind: "metric", name: "omzet", expression: "sum(x)", updated_by: ADMIN });
  await api.semanticUpdate(req({ status: "approved" }, { id }));
  assert.equal(calls[1].url.endsWith(`/semantic/${id}`), true);
  assert.equal(calls[1].init.method, "PUT");
  await api.semanticDelete(req({}, { id }));
  assert.equal(calls[2].init.method, "DELETE");
  await assert.rejects(api.semanticUpdate(req({}, { id: "../integrations/smtp" })), { status: 404 });
  const viewer = build(["access:admin", "read:configs"]);
  await viewer.api.semanticList(req());
  await assert.rejects(viewer.api.semanticCreate(req({ kind: "metric" })), { status: 403 });
  await assert.rejects(viewer.api.semanticDelete(req({}, { id })), { status: 403 });
});

test("semantic paging, tables and bulk: only listed query keys pass, the admin id is server-set, queue start needs a list", async () => {
  const { api, calls } = build();
  const req = (extra) => ({ user: { id: ADMIN, role: "ADMIN" }, params: {}, ...extra });
  await api.semanticList(req({ query: { status: "draft", size: "50", sort: "name", evil: "x", q: ["a", "b"] } }));
  assert.match(calls[0].url, /internal\/semantic\?status=draft&sort=name&size=50$/);
  await api.semanticTables(req({ query: { database: "gold_mart", page: "2", user: "c".repeat(24) } }));
  assert.match(calls[1].url, new RegExp(`/semantic/tables\\?database=gold_mart&page=2&user=${ADMIN}$`));
  await api.semanticBulk(req({ body: { status: "approved", ids: ["x"], updated_by: "c".repeat(24) } }));
  assert.deepEqual(JSON.parse(calls[2].init.body), { status: "approved", updated_by: ADMIN, ids: ["x"] });
  await api.semanticDraftStart(req({ body: { databases: ["kai_demo", "gold_mart"] } }));
  assert.deepEqual(JSON.parse(calls[3].init.body), { databases: ["kai_demo", "gold_mart"], updated_by: ADMIN });
  await assert.rejects(api.semanticDraftStart(req({ body: { database: "kai_demo" } })), { status: 400 });
  const viewer = build(["access:admin", "read:configs"]);
  await assert.rejects(viewer.api.semanticBulk(req({ body: { status: "approved", ids: ["x"] } })), { status: 403 });
  await assert.rejects(viewer.api.semanticTables(req({ query: { database: "x" } })), { status: 403 });
});

test("local models: viewers read state; actions need manage:configs and only known actions/models are forwarded", async () => {
  const { api, calls } = build();
  const req = (body, params = {}) => ({ user: { id: ADMIN, role: "ADMIN" }, params, body });
  await api.localModelAction(req({ model: "Qwen2.5-Coder-7B-Instruct-Q4_K_M", path: "/etc" }, { action: "download" }));
  assert.match(calls[0].url, /semantic\/models\/download$/);
  assert.deepEqual(JSON.parse(calls[0].init.body), { model: "Qwen2.5-Coder-7B-Instruct-Q4_K_M" });
  await api.localModelAction(req({}, { action: "pause" }));
  assert.equal(calls[1].init.method, "DELETE");
  await assert.rejects(api.localModelAction(req({ model: "x" }, { action: "shell" })), { status: 404 });
  await assert.rejects(api.localModelAction(req({}, { action: "delete" })), { status: 400 });
  const viewer = build(["access:admin", "read:configs"]);
  await viewer.api.localModels(req());
  await assert.rejects(viewer.api.localModelAction(req({ model: "x" }, { action: "activate" })), { status: 403 });
});
