// P1-12: an Admin Panel override of the Ollama key is encrypted at rest, redacted on read and used at runtime.
// Writes only a temporary user-scoped override (never __base__) and removes it. Never calls a model.
// Run: docker exec -i kai-api-1 node < tests/smoke_ollama_key_override.cjs
const assert = require("node:assert/strict");
const { randomBytes, randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const bcrypt = require("bcryptjs");
const prefix = `kai-ollama-key-${randomUUID()}`;
const key = `kai-smoke-${randomBytes(12).toString("hex")}`;
const ids = [];
async function main() {
  await mongoose.connect(process.env.MONGO_URI, { autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 5000 });
  const User = mongoose.model("User");
  const base = `http://127.0.0.1:${process.env.PORT || 3080}`;
  const call = (path, token, method = "GET", body) =>
    fetch(base + path, {
      method,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(15000),
    });
  const users = {};
  try {
    for (const role of ["ADMIN", "USER"]) {
      const password = randomBytes(24).toString("hex"), email = `${prefix}-${role}@example.invalid`;
      const u = await User.create({ name: "Temporary key override test", email, role, provider: "local", emailVerified: true, password: await bcrypt.hash(password, 10) });
      ids.push(u._id);
      const login = await call("/api/auth/login", null, "POST", { email, password });
      assert.equal(login.status, 200, "login");
      users[role] = { id: String(u._id), token: (await login.json()).token };
    }
    const path = `/api/admin/config/user/${users.USER.id}`;
    const write = await call(`${path}/fields`, users.ADMIN.token, "PATCH", {
      entries: [{ fieldPath: "endpoints.custom", value: [{ name: "Ollama Cloud", apiKey: key }] }],
    });
    assert.ok(write.status >= 200 && write.status < 300, `write override: ${write.status} ${await write.text()}`);

    const read = await (await call(path, users.ADMIN.token)).text();
    assert.ok(!read.includes(key), "admin read must redact the key");
    const stored = await mongoose.connection.collection("configs").findOne({ principalId: users.USER.id });
    const storedKey = JSON.stringify(stored).match(/"apiKey":"([^"]*)"/)?.[1];
    assert.match(storedKey ?? "", /^v3:/, "key must be encrypted at rest");

    const { getAppConfig } = require("/app/api/server/services/Config/app.js");
    const { getCustomEndpointConfig } = require("@librechat/api");
    const resolved = (userId, role) => getAppConfig({ userId, role, refresh: true })
      .then((appConfig) => getCustomEndpointConfig({ endpoint: "Ollama Cloud", appConfig }));
    const own = await resolved(users.USER.id, "USER");
    assert.equal(own.apiKey, key, "runtime must use the override key");
    assert.equal(own.baseURL, "https://ollama.com/v1/", "merge by name keeps the YAML entry");
    assert.notEqual((await resolved(users.ADMIN.id, "ADMIN")).apiKey, key, "other principals keep the base key");
    console.log("ok: override encrypted, redacted, merged by name and resolved at runtime");
  } finally {
    if (users.ADMIN && users.USER)
      await call(`/api/admin/config/user/${users.USER.id}`, users.ADMIN.token, "DELETE").catch(() => {});
    await mongoose.connection.collection("configs").deleteMany({ principalId: { $in: ids.map(String) } });
    await User.deleteMany({ _id: { $in: ids } });
    await mongoose.disconnect();
  }
}
main().then(() => process.exit(0), (error) => { console.error(error); process.exit(1); });
