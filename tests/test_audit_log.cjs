// Admin data access log: admin-only, validated filters, names attached, and a safe empty result when the MCP is down.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createAuditLog } = require("../services/platform-api/audit_log.cjs");

const id = (c) => c.repeat(24);
const LOG = {
  version: 1, available: true, partial: false, total: 2, counts: { ok: 1, access_denied: 1 }, tools: ["execute_readonly_sql"],
  users: [id("b")],
  rows: [{ ts: "2026-11-09T10:05:00Z", user: id("b"), tool: "execute_readonly_sql", ch_user: "budi_ch", outcome: "access_denied", rows: null, duration_ms: 40 }],
};
function build({ log = LOG, allow = true } = {}) {
  const calls = [];
  process.env.KAI_PLATFORM_INTERNAL_TOKEN = "internal";
  const audit = createAuditLog({
    hasCapability: async () => allow,
    findUsers: async () => [{ _id: id("b"), name: "Budi", email: "budi@x" }],
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (log instanceof Error) throw log;
      return { ok: true, text: async () => JSON.stringify(log) };
    },
  });
  return { audit, calls };
}
const req = (query = {}) => ({ user: { id: id("a"), role: "ADMIN" }, query });

test("passes validated filters and attaches names", async () => {
  const { audit, calls } = build();
  const result = await audit.list(req({ days: "7", user: id("b"), outcome: "access_denied", tool: "execute_readonly_sql" }));
  assert.equal(calls[0].url, `http://clickhouse-mcp:8000/v1/internal/audit?days=7&user=${id("b")}&outcome=access_denied&tool=execute_readonly_sql`);
  assert.equal(calls[0].options.headers.Authorization, "Bearer internal");
  assert.equal(result.rows[0].name, "Budi");
  assert.deepEqual(result.users, [{ id: id("b"), name: "Budi", email: "budi@x" }]);
  assert.equal(result.days, 7);
});

test("rejects bad filters and non-admins", async () => {
  const { audit } = build();
  for (const query of [{ user: "x" }, { outcome: "drop" }, { tool: "A;B" }])
    await assert.rejects(audit.list(req(query)), (e) => e.status === 400);
  await assert.rejects(build({ allow: false }).audit.list(req()), (e) => e.status === 403);
  await assert.rejects(audit.list({ user: { id: "nope" } }), (e) => e.status === 401);
});

test("degrades to an empty, unavailable log", async () => {
  for (const log of [new Error("down"), { version: 1, available: false }, { version: 2 }]) {
    const result = await build({ log }).audit.list(req({ days: "999" }));
    assert.equal(result.available, false);
    assert.equal(result.days, 1);
  }
});
