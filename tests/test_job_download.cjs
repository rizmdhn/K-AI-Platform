const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createJobDownload, createFileDownload } = require("../services/platform-api/job_download.cjs");
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
test("create_document files: known types only, MCP file name kept, type from the extension", async () => {
  upstream.calls = [];
  const fetchImpl = async (url) => { upstream.calls.push({ url }); return { status: 200, headers: new Map([["content-disposition", 'attachment; filename="Laporan-Q3.xlsx"']]), arrayBuffer: async () => Buffer.from("x") }; };
  const r = res();
  await createFileDownload({ fetchImpl, token: () => "svc" })({ params: { file: `${JOB}.xlsx` }, query: { exp: EXP, sig: SIG } }, r);
  assert.equal(r.code, 200);
  assert.equal(upstream.calls[0].url, `http://clickhouse-mcp:8000/v1/files/${JOB}.xlsx?exp=${EXP}&sig=${SIG}`);
  assert.match(r.headers["Content-Type"], /spreadsheetml/);
  assert.equal(r.headers["Content-Disposition"], 'attachment; filename="Laporan-Q3.xlsx"');
  const bad = res();
  await createFileDownload({ fetchImpl })({ params: { file: `${JOB}.exe` }, query: { exp: EXP, sig: SIG } }, bad);
  assert.equal(bad.code, 400);
});
