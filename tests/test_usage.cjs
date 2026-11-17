// Admin token usage: permission, per-user/model/day totals from transactions, cost only when prices are set.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createUsage } = require("../services/platform-api/usage.cjs");

const ADMIN = "a".repeat(24), U1 = "b".repeat(24), U2 = "c".repeat(24);
const ROWS = [
  { _id: { user: U1, model: "gemini", type: "prompt", day: "2026-11-01" }, tokens: 2_000_000, calls: 4 },
  { _id: { user: U1, model: "gemini", type: "completion", day: "2026-11-01" }, tokens: 500_000, calls: 4 },
  { _id: { user: U2, model: "gemini", type: "prompt", day: "2026-11-02" }, tokens: 1_000_000, calls: 1 },
  { _id: { user: U2, model: "gemini", type: "completion", day: "2026-11-02" }, tokens: 100_000, calls: 1 },
];
function build({ caps = ["access:admin", "read:users"], prices = {} } = {}) {
  const seen = {};
  const api = createUsage({
    hasCapability: async (_p, cap) => caps.includes(cap),
    aggregate: async (pipeline) => {
      if (pipeline[0].$match.createdAt.$lt) { seen.previous = pipeline; return [{ _id: "prompt", tokens: 1_000_000, calls: 2 }, { _id: "completion", tokens: 200_000, calls: 2 }]; }
      seen.pipeline = pipeline; return ROWS;
    },
    findUsers: async (ids) => ids.map((id) => ({ _id: id, name: id === U1 ? "Ana" : "Budi", email: `${id}@x` })),
    pricing: async () => prices,
    now: () => new Date("2026-11-17T00:00:00Z"),
  });
  return { api, seen, req: (days) => ({ user: { id: ADMIN, role: "ADMIN" }, query: { days } }) };
}

test("totals per user, model and day; calls counted once per model call", async () => {
  const { api, seen, req } = build();
  const r = await api.summary(req("7"));
  assert.equal(r.days, 7);
  assert.deepEqual([r.total.prompt, r.total.completion, r.total.calls], [3_000_000, 600_000, 5]);
  assert.deepEqual(r.users.map((u) => [u.name, u.prompt + u.completion]), [["Ana", 2_500_000], ["Budi", 1_100_000]]);
  assert.equal(r.daily.length, 7);  // every day of the period, empty ones included
  assert.deepEqual([r.daily[0].day, r.daily.at(-1).day], ["2026-11-11", "2026-11-17"]);
  assert.equal(r.total.usd, null);
  assert.deepEqual([r.previous.prompt, r.previous.completion, r.previous.calls], [1_000_000, 200_000, 2]);
  assert.equal(seen.previous[0].$match.createdAt.$lt.toISOString(), "2026-11-10T17:00:00.000Z");
  assert.equal(r.pricing.priced, false);
  // 2026-11-11 00:00 Asia/Jakarta (UTC+7)
  assert.equal(seen.pipeline[0].$match.createdAt.$gte.toISOString(), "2026-11-10T17:00:00.000Z");
});

test("cost uses saved USD prices per 1M tokens", async () => {
  const { api, req } = build({ prices: { input_usd_per_million: "0.3", output_usd_per_million: "2.5", usd_to_idr: "16000" } });
  const r = await api.summary(req("30"));
  assert.ok(Math.abs(r.total.usd - (3 * 0.3 + 0.6 * 2.5)) < 1e-9);
  assert.equal(r.pricing.usd_to_idr, 16000);
});

test("24 hours are bucketed per hour, filling empty hours", async () => {
  const { api, seen, req } = build();
  const r = await api.summary(req("1"));
  assert.equal(r.bucket, "hour");
  assert.equal(r.daily.length, 24);
  // now = 2026-11-17 07:00 Jakarta, so the first bucket is 2026-11-16 08:00
  assert.deepEqual([r.daily[0].day, r.daily.at(-1).day], ["2026-11-16 08", "2026-11-17 07"]);
  assert.equal(seen.pipeline[0].$match.createdAt.$gte.toISOString(), "2026-11-16T01:00:00.000Z");
  assert.equal(seen.pipeline[1].$group._id.day.$dateToString.format, "%Y-%m-%d %H");
});

test("unknown period falls back to 30 days; non-admins are refused", async () => {
  assert.equal((await build().api.summary(build().req("999"))).days, 30);
  const { api, req } = build({ caps: ["access:admin"] });
  await assert.rejects(api.summary(req("7")), { status: 403 });
});
