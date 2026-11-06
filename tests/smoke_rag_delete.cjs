// P5-06: deleting a document removes its chunks from pgvector, using LibreChat's own uploadVectors/deleteVectors.
// Needs `make documents-up`. Synthetic text, no model call. Prints chunk counts read back through rag_api.
// Run: docker exec -i kai-api-1 node < tests/smoke_rag_delete.cjs
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
process.env.RAG_API_URL ||= "http://rag_api:8000";
const { uploadVectors, deleteVectors } = require("/app/api/server/services/Files/VectorDB/crud.js");
const { generateShortLivedToken } = require("@librechat/api");

const A = `smoke-a-${randomUUID()}`, B = `smoke-b-${randomUUID()}`;
const reqFor = (id) => ({ user: { id }, config: {} });
// Chunks the owner (or the owner entity) can still reach, read through rag_api's scoped /documents route.
const chunks = async (owner, fileId, entity) => {
  const url = `${process.env.RAG_API_URL}/documents?ids=${fileId}${entity ? `&entity_id=${entity}` : ""}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${generateShortLivedToken(owner)}` } });
  return res.ok ? (await res.json()).length : 0;
};
const embed = async (owner, entity_id) => {
  const file_id = randomUUID();
  const p = path.join(os.tmpdir(), `${file_id}.txt`);
  fs.writeFileSync(p, "Dokumen fiktif.\n\nCabang Medan membuka layanan baru pada bulan Mei.\n\nTidak ada data nyata.");
  try {
    const res = await uploadVectors({ req: reqFor(owner), file: { path: p, originalname: "fiktif.txt", mimetype: "text/plain" }, file_id, entity_id });
    assert.ok(res.embedded, "embedded");
  } finally { fs.rmSync(p, { force: true }); }
  return file_id;
};

async function main() {
  // 1. Own file: delete removes every chunk.
  const own = await embed(A);
  const before = await chunks(A, own);
  assert.ok(before > 0, "chunks stored");
  await deleteVectors(reqFor(A), { file_id: own, embedded: true });
  assert.equal(await chunks(A, own), 0, "owner delete leaves no chunks");

  // 2. Another user cannot delete it (404 is swallowed by LibreChat; chunks must survive).
  const victim = await embed(A);
  await deleteVectors(reqFor(B), { file_id: victim, embedded: true });
  assert.ok((await chunks(A, victim)) > 0, "B's delete must not remove A's chunks");
  await deleteVectors(reqFor(A), { file_id: victim, embedded: true });

  // 3. Agent-scoped file (entity_id): LibreChat's delete sends no entity_id (P5-01 finding). Record the outcome.
  const entity = `agent_smoke${randomUUID().replace(/-/g, "").slice(0, 12)}`;
  const shared = await embed(A, entity);
  await deleteVectors(reqFor(A), { file_id: shared, embedded: true });
  const orphan = await chunks(A, shared, entity);
  if (orphan) {
    // Cleanup with the entity scope so the smoke leaves nothing behind.
    await fetch(`${process.env.RAG_API_URL}/documents?entity_id=${entity}`, {
      method: "DELETE", body: JSON.stringify([shared]),
      headers: { Authorization: `Bearer ${generateShortLivedToken(A)}`, "Content-Type": "application/json" },
    });
  }
  console.log(`ok: own file ${before} chunk(s) -> 0 after delete; other user's delete removed nothing; ` +
    `agent-scoped file after LibreChat delete: ${orphan} orphan chunk(s)${orphan ? " (cleaned by smoke; needs entity-aware delete before shared agents)" : ""}`);
}
main().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
