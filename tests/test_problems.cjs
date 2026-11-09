// P6-10: the admin problem list combines account state, CH agent ACL and the MCP audit summary, and degrades safely.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createProblems } = require("../services/platform-api/problems.cjs");

const id = (c) => c.repeat(24);
const USERS = [
  { id: id("a"), name: "Ani", email: "ani@x", status: "active", cleanupPending: false },    // agent, no binding
  { id: id("b"), name: "Budi", email: "budi@x", status: "active", cleanupPending: false },  // login rejected + denials
  { id: id("c"), name: "Citra", email: "citra@x", status: "disabled", cleanupPending: true },
  { id: id("d"), name: "Dewi", email: "dewi@x", status: "active", cleanupPending: false },  // healthy
  { id: id("e"), name: "Eko", email: "eko@x", status: "active", cleanupPending: false },    // rejected earlier, ok since
  { id: id("f"), name: "Owner", email: "owner@x", status: "existing", cleanupPending: false },
];
const MCP = {
  version: 1, audit_available: true, database_available: true, calls_24h: 30, errors_24h: 3,
  active_subjects: [id("b"), id("d"), id("e")],
  checks: { binding_dsn: true, keyring: true, warehouse_target: true },
  users: {
    [id("b")]: { last_ok: "2026-11-09T08:00:00Z", last_error: { outcome: "login_rejected", at: "2026-11-09T09:00:00Z" }, errors: 6, denied: 5 },
    [id("e")]: { last_ok: "2026-11-09T10:00:00Z", last_error: { outcome: "login_rejected", at: "2026-11-09T09:00:00Z" }, errors: 1, denied: 0 },
  },
};
const ACL = [id("a"), id("b"), id("d"), id("f")].map((u) => ({ principalType: "user", principalId: u, permBits: 1 }));

function build({ mcp = MCP, acl = ACL, agent = { _id: "res", author: id("f") }, token = "internal" } = {}) {
  const calls = [];
  process.env.KAI_PLATFORM_INTERNAL_TOKEN = token;
  const problems = createProblems({
    accounts: { problemUsers: async () => ({ actor: { id: id("f"), tenantId: null }, users: USERS }) },
    managedAgent: async () => (agent instanceof Error ? Promise.reject(agent) : agent),
    aclEntries: async () => acl,
    VIEW: 1, USER: "user",
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (mcp instanceof Error) throw mcp;
      return { ok: true, text: async () => JSON.stringify(mcp) };
    },
    now: () => new Date("2026-11-09T12:00:00Z"),
  });
  return { problems, calls };
}
const codes = (result) => Object.fromEntries(result.accounts.map((a) => [a.name, a.issues.map((i) => i.code)]));

test("flags each problem account and nothing for healthy ones", async () => {
  const { problems, calls } = build();
  const result = await problems.list({});
  assert.deepEqual(codes(result), {
    Ani: ["agent_without_binding"],
    Budi: ["ch_login_rejected", "many_denials"],
    Citra: ["cleanup_pending"],
  });
  assert.deepEqual(result.system, []);
  assert.equal(calls[0].url, "http://clickhouse-mcp:8000/v1/internal/problems");
  assert.equal(calls[0].options.headers.Authorization, "Bearer internal");
  assert.equal(calls[0].options.redirect, "error");
});

test("system issues: MCP down, broad sharing, config, error rate", async () => {
  let result = await build({ mcp: new Error("down") }).problems.list({});
  assert.deepEqual(result.system.map((s) => s.code), ["mcp_unreachable"]);
  assert.deepEqual(codes(result), { Citra: ["cleanup_pending"] }, "without MCP data no binding/login claims are made");

  result = await build({
    mcp: { ...MCP, database_available: false, audit_available: false, calls_24h: 20, errors_24h: 5, checks: { binding_dsn: true, keyring: false } },
    acl: [...ACL, { principalType: "role", principalId: "USER", permBits: 1 }],
  }).problems.list({});
  assert.deepEqual(result.system.map((s) => s.code), ["agent_shared_broadly", "audit_unavailable", "metadata_db_unavailable", "config_missing", "high_error_rate"]);
  assert.equal(result.system[3].check, "keyring");
  assert.ok(!codes(result).Ani, "unknown binding state is not reported as missing");

  result = await build({ agent: new Error("tampered") }).problems.list({});
  assert.deepEqual(result.system.map((s) => s.code), ["agent_unavailable"]);
  result = await build({ token: "" }).problems.list({});
  assert.deepEqual(result.system.map((s) => s.code), ["mcp_unreachable"]);
});

test("ignores a non-view ACL entry and an unexpected MCP payload", async () => {
  const result = await build({ acl: [{ principalType: "user", principalId: id("a"), permBits: 0 }], mcp: { version: 2 } }).problems.list({});
  assert.deepEqual(result.system.map((s) => s.code), ["mcp_unreachable"]);
  assert.ok(!codes(result).Ani);
});
