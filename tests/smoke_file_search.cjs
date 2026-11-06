// P2-10: "Upload for File Search" on the managed General agent. A's document is embedded locally (bge-m3,
// vectordb), found by A's file_search, never by B's (even with A's file_id), and its chunks go on delete.
// Temporary accounts only, removed afterwards. Never calls a chat model. Needs make documents-up.
// Run: docker exec -i kai-api-1 node < tests/smoke_file_search.cjs
const assert = require("node:assert/strict");
const { randomBytes, randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const jwt = require("jsonwebtoken");
const { generateShortLivedToken } = require("@librechat/api");
const { createFileSearchTool } = require("/app/api/app/clients/tools/util/fileSearch.js");
const prefix = `kai-fs-${randomUUID()}`;
const marker = `kode-${randomBytes(4).toString("hex")}`;
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const ids = [];
// Chunks of a file reachable by its owner, read back through rag_api's user-scoped route.
const chunks = async (owner, fileId) => {
  const res = await fetch(`${process.env.RAG_API_URL}/documents?ids=${fileId}`, { headers: { Authorization: `Bearer ${generateShortLivedToken(owner)}` } });
  return res.ok ? (await res.json()).length : 0;
};
const search = async (userId, file) =>
  String(await (await createFileSearchTool({ userId, files: [{ file_id: file.file_id, filename: file.filename }] })).invoke({ query: "kode proyek cabang Medan" }));

async function main() {
  assert.ok(process.env.RAG_API_URL, "RAG_API_URL set (KAI_RAG_API_URL)");
  await mongoose.connect(process.env.MONGO_URI, { autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 5000 });
  const User = mongoose.model("User"), File = mongoose.model("File");
  const call = (path, token, init = {}) =>
    fetch(`http://127.0.0.1:${process.env.PORT || 3080}${path}`, { ...init, headers: { Authorization: `Bearer ${token}`, "User-Agent": UA, ...(init.headers || {}) }, signal: AbortSignal.timeout(120000) });
  const users = {};
  try {
    for (const name of ["A", "B"]) {
      const u = await User.create({ name: `Temporary file search ${name}`, email: `${prefix}-${name}@example.invalid`, role: "USER", provider: "local", emailVerified: true });
      ids.push(u._id);
      users[name] = { id: String(u._id), token: jwt.sign({ id: String(u._id) }, process.env.JWT_SECRET, { expiresIn: "5m" }) };
    }
    const base = await mongoose.connection.collection("configs").findOne({ principalType: "role", principalId: "__base__" });
    const agentId = base?.overrides?.modelSpecs?.list?.find((s) => s.name === "kai-assistant")?.preset?.agent_id;
    assert.ok(agentId, "managed General agent is configured");
    // Same request the chat composer sends for "Upload for File Search".
    const form = new FormData();
    for (const [k, v] of Object.entries({ file_id: randomUUID(), endpoint: "agents", tool_resource: "file_search", message_file: "true", agent_id: agentId })) form.append(k, v);
    form.append("file", new Blob([`Dokumen fiktif.\n\nCabang Medan memakai kode proyek ${marker}.\n\nTidak ada data nyata.`], { type: "text/plain" }), "fiktif.txt");
    const res = await call("/api/files", users.A.token, { method: "POST", body: form });
    const body = await res.text();
    assert.equal(res.status, 200, `upload: ${body}`);
    const file = JSON.parse(body);
    const stored = await File.findOne({ file_id: file.file_id }).lean();
    assert.equal(String(stored.user), users.A.id, "owner is the uploader");
    assert.equal(stored.embedded, true, "embedded in vectordb");
    assert.equal(stored.source, "s3", "original in object storage");
    const before = await chunks(users.A.id, file.file_id);
    assert.ok(before > 0, "chunks stored for A");
    assert.ok((await search(users.A.id, file)).includes(marker), "A's file_search finds the passage");
    assert.ok(!(await search(users.B.id, file)).includes(marker), "B's file_search with A's file_id returns nothing (SEC-13)");
    assert.equal(await chunks(users.B.id, file.file_id), 0, "rag_api hides A's chunks from B");

    const del = await call("/api/files", users.A.token, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ files: [{ file_id: file.file_id, filepath: stored.filepath, embedded: true }] }) });
    assert.equal(del.status, 200, "owner delete");
    assert.equal(await chunks(users.A.id, file.file_id), 0, "delete removes every chunk");
    console.log(`ok: embedded ${before} chunk(s), A finds it, B gets nothing with A's file_id, delete leaves 0 chunks`);
  } finally {
    await File.deleteMany({ user: { $in: ids } });
    await User.deleteMany({ _id: { $in: ids } });
    await mongoose.disconnect();
  }
}
main().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
