const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createJobDownload } = require("../services/platform-api/job_download.cjs");
const JOB = "a".repeat(32), SIG = "b".repeat(64), EXP = "1999999999";
function res() {
  const r = { headers: {}, set(k, v) { this.headers[k] = v; return this; }, status(c) { this.code = c; return this; },
    json(b) { this.body = b; return this; }, send(b) { this.body = b; return this; } };
  return r;
}
const upstream = (status, text = "id,label,reason\n") => async (url, init) => {
  upstream.calls.push({ url, init });
  return { status, body: { cancel: async () => {} }, arrayBuffer: async () => Buffer.from(text) };
};
upstream.calls = [];
test("forwards a well-formed signed link to MCP with the service bearer and returns CSV", async () => {
  upstream.calls = [];
  const r = res();
  await createJobDownload({ fetchImpl: upstream(200), token: () => "svc" })({ params: { file: `${JOB}.csv` }, query: { exp: EXP, sig: SIG } }, r);
  assert.equal(r.code, 200);
  assert.match(r.headers["Content-Type"], /text\/csv/);
  assert.equal(upstream.calls[0].url, `http://clickhouse-mcp:8000/v1/jobs/${JOB}.csv?exp=${EXP}&sig=${SIG}`);
  assert.equal(upstream.calls[0].init.headers.Authorization, "Bearer svc");
});
test("malformed links never reach MCP; expired links come back as 403", async () => {
  upstream.calls = [];
  for (const [file, query] of [["../x.csv", { exp: EXP, sig: SIG }], [`${JOB}.csv`, { exp: "1", sig: SIG }],
                               [`${JOB}.csv`, { exp: EXP, sig: "zz" }], [`${JOB}.csv`, { exp: [EXP], sig: SIG }]]) {
    const r = res();
    await createJobDownload({ fetchImpl: upstream(200) })({ params: { file }, query }, r);
    assert.equal(r.code, 400, file);
  }
  assert.equal(upstream.calls.length, 0);
  const r = res();
  await createJobDownload({ fetchImpl: upstream(403) })({ params: { file: `${JOB}.csv` }, query: { exp: EXP, sig: SIG } }, r);
  assert.equal(r.code, 403);
});
