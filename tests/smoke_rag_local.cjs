// P5-04: local RAG path (rag_api + pgvector + llama.cpp bge-m3). Synthetic text only; no model or external call.
// Needs `make documents-up`. Run: docker exec -i kai-api-1 node < tests/smoke_rag_local.cjs
// Tokens are minted exactly like LibreChat's generateShortLivedToken (HS256, JWT_SECRET, { id }).
const assert = require("node:assert/strict");
const { randomUUID } = require("node:crypto");
const jwt = require("jsonwebtoken");
const RAG = process.env.KAI_SMOKE_RAG_URL || "http://rag_api:8000";
const token = (id) => jwt.sign({ id }, process.env.JWT_SECRET, { expiresIn: "5m" });
const A = `smoke-a-${randomUUID()}`, B = `smoke-b-${randomUUID()}`;
const fileId = randomUUID();
const text = [
  "Laporan internal fiktif untuk pengujian.",
  "Pendapatan cabang Bandung pada kuartal ketiga naik 12 persen karena produk tabungan baru.",
  "Kantor Surabaya menutup dua gerai pada bulan Agustus.",
  "Rapat evaluasi berikutnya dijadwalkan pada minggu kedua Desember.",
].join("\n\n");
const call = (path, who, init = {}) =>
  fetch(RAG + path, { ...init, headers: { Authorization: `Bearer ${token(who)}`, ...(init.headers || {}) }, signal: AbortSignal.timeout(120000) });
const query = async (who, extra = {}) => {
  const res = await call("/query", who, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: fileId, query: "Berapa kenaikan pendapatan di Bandung?", k: 2, ...extra }),
  });
  return { status: res.status, hits: res.ok ? await res.json() : [] };
};
async function main() {
  try {
    const form = new FormData();
    form.append("file_id", fileId);
    form.append("file", new Blob([text], { type: "text/plain" }), "laporan.txt");
    const started = Date.now();
    const embed = await call("/embed", A, { method: "POST", body: form });
    assert.equal(embed.status, 200, `embed: ${await embed.text()}`);
    const embedMs = Date.now() - started;

    const own = await query(A);
    assert.equal(own.status, 200);
    const top = own.hits[0]?.[0]?.page_content || "";
    assert.match(top, /Bandung/, "the most similar chunk answers the question");

    const other = await query(B);
    assert.equal(other.hits.length, 0, "B gets nothing from A's file");

    // Known upstream gap (P5-01): entity_id is caller-asserted. Record the behaviour so a guard can be proven later.
    const spoof = await query(B, { entity_id: A });
    console.log(`ok: embedded in ${embedMs} ms; A top hit is the Bandung chunk; B sees 0 hits; ` +
      `B with spoofed entity_id sees ${spoof.hits.length} hit(s)${spoof.hits.length ? " -> needs the server-side guard" : ""}`);
  } finally {
    await call("/documents", A, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify([fileId]) }).catch(() => {});
  }
}
main().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
