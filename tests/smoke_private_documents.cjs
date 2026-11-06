// P5-02: per-user "Upload as Text" documents. A's upload is parsed to text and stays private from B.
// Temporary accounts only, removed afterwards. Never calls a model.
// Needs fileConfig.endpoints.agents.disabled: false (the deployed default since 6 Nov 2026).
// Run: docker exec -i kai-api-1 node < tests/smoke_private_documents.cjs
const assert = require("node:assert/strict");
const { randomBytes, randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");
const prefix = `kai-docs-${randomUUID()}`;
const marker = `kai-private-${randomBytes(6).toString("hex")}`;
const ids = [];
// uaParser rejects non-browser clients on upload routes.
// Object keys in the document bucket that belong to a user (P6-12).
const objectsOf = async (userId) => {
  const { S3Client, ListObjectsV2Command } = require("@aws-sdk/client-s3");
  const s3 = new S3Client({ endpoint: process.env.AWS_ENDPOINT_URL, region: process.env.AWS_REGION, forcePathStyle: true });
  const page = await s3.send(new ListObjectsV2Command({ Bucket: process.env.AWS_BUCKET_NAME }));
  return (page.Contents ?? []).filter((o) => o.Key.includes(userId)).length;
};
const UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
async function main() {
  await mongoose.connect(process.env.MONGO_URI, { autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 5000 });
  const User = mongoose.model("User"), File = mongoose.model("File");
  const base = `http://127.0.0.1:${process.env.PORT || 3080}`;
  const call = (path, token, init = {}) =>
    fetch(base + path, { ...init, headers: { Authorization: `Bearer ${token}`, "User-Agent": UA, ...(init.headers || {}) }, signal: AbortSignal.timeout(30000) });
  const users = {};
  try {
    for (const name of ["A", "B"]) {
      const password = randomBytes(24).toString("hex"), email = `${prefix}-${name}@example.invalid`;
      const u = await User.create({ name: `Temporary docs ${name}`, email, role: "USER", provider: "local", emailVerified: true, password: await bcrypt.hash(password, 10) });
      ids.push(u._id);
      // Signed like a native session token; avoids the login rate limiter on repeated runs.
      users[name] = { id: String(u._id), token: jwt.sign({ id: String(u._id) }, process.env.JWT_SECRET, { expiresIn: "5m" }) };
    }
    // Same request the chat composer sends for "Upload as Text" in the managed General agent.
    const base_ = await mongoose.connection.collection("configs").findOne({ principalType: "role", principalId: "__base__" });
    const agentId = base_?.overrides?.modelSpecs?.list?.find((spec) => spec.name === "kai-assistant")?.preset?.agent_id;
    assert.ok(agentId, "managed General agent is configured");
    const upload = (token, name, type, body) => {
      const form = new FormData();
      form.append("file_id", randomUUID());
      form.append("endpoint", "agents");
      form.append("tool_resource", "context");
      form.append("message_file", "true");
      form.append("agent_id", agentId);
      form.append("file", new Blob([body], { type }), name);
      return call("/api/files", token, { method: "POST", body: form });
    };

    const ok = await upload(users.A.token, "notes.txt", "text/plain", `Quarterly note ${marker}`);
    const okBody = await ok.text();
    assert.equal(ok.status, 200, `txt upload: ${okBody}`);
    const file = JSON.parse(okBody);
    const stored = await File.findOne({ file_id: file.file_id }).lean();
    assert.equal(String(stored.user), users.A.id, "owner is the uploader");
    assert.ok(stored.text?.includes(marker), "text extracted for context");
    // P6-12: documents live in RustFS object storage and download streams back through api.
    assert.equal(stored.source, "s3", "document stored in object storage");
    const own = await call(`/api/files/download/${users.A.id}/${file.file_id}`, users.A.token);
    assert.equal(own.status, 200, "owner download");
    assert.ok((await own.text()).includes(marker), "owner gets the original bytes");
    assert.equal(await objectsOf(users.A.id), 1, "one object in the bucket");
    // The UI first asks for a presigned URL; for internal RustFS that must be refused so it falls back
    // to the proxied download above (browsers cannot reach http://rustfs:9000).
    assert.equal((await call(`/api/files/download-url/${users.A.id}/${file.file_id}`, users.A.token)).status, 501, "no presigned URL");
    const direct = await call(`/api/files/download/${users.A.id}/${file.file_id}?direct=true`, users.A.token, { redirect: "manual" });
    assert.equal(direct.status, 200, "direct=true streams instead of redirecting to RustFS");

    const blocked = await upload(users.A.token, "tool.exe", "application/x-msdownload", "MZ");
    assert.ok(blocked.status >= 400, `disallowed type must be rejected, got ${blocked.status}`);

    const list = await (await call("/api/files", users.B.token)).text();
    assert.ok(!list.includes(file.file_id), "B must not list A's file");
    const dl = await call(`/api/files/download/${users.A.id}/${file.file_id}`, users.B.token);
    assert.ok(dl.status === 403 || dl.status === 404, `B download must be refused, got ${dl.status}`);
    const del = await call("/api/files", users.B.token, {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files: [{ file_id: file.file_id, filepath: stored.filepath }] }),
    });
    assert.ok(await File.exists({ file_id: file.file_id }), `B delete must not remove A's file (status ${del.status})`);
    console.log("ok: text upload parsed and owned by A; exe rejected; B cannot list, download or delete it");
  } finally {
    const files = await File.find({ user: { $in: ids } }, "file_id filepath").lean();
    if (users.A && files.length)
      await call("/api/files", users.A.token, {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: files.map((f) => ({ file_id: f.file_id, filepath: f.filepath })) }),
      }).catch(() => {});
    await File.deleteMany({ user: { $in: ids } });
    if (users.A) console.log(`objects left for A after owner delete: ${await objectsOf(users.A.id)}`);
    await User.deleteMany({ _id: { $in: ids } });
    await mongoose.disconnect();
  }
}
main().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
