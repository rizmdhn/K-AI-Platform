// Admin account flows against the running local stack: reset link, role change, name/email edit, agent-owner guard.
// Temporary Mongo fixtures only; never calls a model or ClickHouse. Run: docker exec -i kai-api-1 node < tests/smoke_account_admin_flows.cjs
const assert = require("node:assert/strict");
const { randomBytes, randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const bcrypt = require("bcryptjs");
const prefix = `kai-flows-${randomUUID()}`;
const ids = [];
let step = "start", restoreOwner = null;
async function main() {
  await mongoose.connect(process.env.MONGO_URI, { autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 5000 });
  const collection = mongoose.model("User").collection;
  const { createAccounts } = require("/app/kai-platform/accounts.cjs");
  const core = createAccounts({ mongoose, bcrypt, hasCapability: async (a) => a.role === "ADMIN", origin: new URL(process.env.DOMAIN_CLIENT).origin });
  const base = `http://127.0.0.1:${process.env.PORT || 3080}`;
  const call = (path, token, method = "GET", body) => fetch(base + path, {
    method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(15000),
  });
  const login = (email, password) => call("/api/auth/login", null, "POST", { email, password });
  // LibreChat allows ~7 logins per 5 min per IP; only admin, user and the renamed-email check log in over HTTP.
  const hashOf = async (id) => (await collection.findOne({ _id: new mongoose.Types.ObjectId(id) }, { projection: { password: 1 } })).password;
  const make = async (key, role, withLogin = true) => {
    const password = randomBytes(24).toString("hex"), email = `${prefix}-${key}@example.invalid`;
    const u = await mongoose.model("User").create({ name: `Flow ${key}`, email, role, provider: "local", emailVerified: true, password: await bcrypt.hash(password, 10) });
    ids.push(u._id);
    if (!withLogin) return { id: String(u._id), email, password };
    const res = await login(email, password);
    assert.equal(res.status, 200, `login ${key}`);
    return { id: String(u._id), email, password, token: (await res.json()).token };
  };
  try {
    const admin = await make("admin", "ADMIN"), user = await make("user", "USER"), target = await make("target", "USER", false);

    step = "reset link authorization";
    assert.equal((await call(`/api/admin/users/${target.id}/kai-password-reset`, user.token, "POST", {})).status, 403);
    const issued = await call(`/api/admin/users/${target.id}/kai-password-reset`, admin.token, "POST", {});
    assert.equal(issued.status, 200);
    const token = new URLSearchParams(new URL((await issued.json()).activationUrl).hash.slice(1)).get("token");
    step = "old password still works until the link is used";
    assert.ok(await bcrypt.compare(target.password, await hashOf(target.id)));
    step = "reset consumes once and logs out";
    const fresh = randomBytes(16).toString("hex");
    await core.activate({ userId: target.id, token, password: fresh, confirm_password: fresh });
    await assert.rejects(core.activate({ userId: target.id, token, password: fresh, confirm_password: fresh }));
    assert.equal(await mongoose.model("Session").countDocuments({ user: new mongoose.Types.ObjectId(target.id) }), 0);
    assert.ok(!(await bcrypt.compare(target.password, await hashOf(target.id))));
    assert.ok(await bcrypt.compare(fresh, await hashOf(target.id)));
    step = "reset refused for pending accounts";
    const pending = await call("/api/admin/users", admin.token, "POST", { name: "Pending flow", email: `${prefix}-pending@example.invalid`, role: "USER", warehouse: null });
    assert.equal(pending.status, 200);
    const pendingId = (await pending.json()).account.id;
    ids.push(new mongoose.Types.ObjectId(pendingId));
    assert.equal((await call(`/api/admin/users/${pendingId}/kai-password-reset`, admin.token, "POST", {})).status, 409);

    step = "role change";
    assert.equal((await call(`/api/admin/users/${target.id}/kai-role`, user.token, "PATCH", { role: "ADMIN" })).status, 403);
    const promoted = await call(`/api/admin/users/${target.id}/kai-role`, admin.token, "PATCH", { role: "ADMIN" });
    assert.equal(promoted.status, 200);
    assert.equal((await promoted.json()).role, "ADMIN");
    assert.equal((await call(`/api/admin/users/${target.id}/kai-role`, admin.token, "PATCH", { role: "USER" })).status, 200);
    assert.equal((await call(`/api/admin/users/${admin.id}/kai-role`, admin.token, "PATCH", { role: "USER" })).status, 409);
    assert.equal((await call(`/api/admin/users/${target.id}/kai-role`, admin.token, "PATCH", { role: "ROOT" })).status, 400);

    step = "name and email edit";
    const renamed = `${prefix}-renamed@example.invalid`;
    const edit = await call(`/api/admin/users/${target.id}/kai-profile`, admin.token, "PATCH", { name: "Renamed flow", email: renamed.toUpperCase() });
    assert.equal(edit.status, 200);
    assert.equal((await edit.json()).email, renamed);
    assert.equal((await login(renamed, fresh)).status, 200);
    const dup = await call(`/api/admin/users/${target.id}/kai-profile`, admin.token, "PATCH", { name: "x", email: user.email });
    assert.equal(dup.status, 409);
    assert.match((await dup.json()).message, /sudah terdaftar/);
    assert.equal((await call(`/api/admin/users/${target.id}/kai-profile`, admin.token, "PATCH", { name: "x", email: "bad" })).status, 400);
    assert.equal((await call(`/api/admin/users/${target.id}/kai-profile`, user.token, "PATCH", { name: "x", email: renamed })).status, 403);

    step = "disabled account can free its email for a new account";
    assert.equal((await call(`/api/admin/users/${target.id}/kai-disable`, admin.token, "POST", {})).status, 200);
    assert.equal((await call(`/api/admin/users/${target.id}/kai-profile`, admin.token, "PATCH", { name: "Old", email: `${prefix}-old@invalid.local` })).status, 200);
    const again = await call("/api/admin/users", admin.token, "POST", { name: "Reused", email: renamed, role: "USER", warehouse: null });
    assert.equal(again.status, 200);
    ids.push(new mongoose.Types.ObjectId((await again.json()).account.id));

    step = "released CH username can be prepared for another user (no ClickHouse contact)";
    const status0 = await (await call(`/api/admin/users/${user.id}/kai-warehouse`, admin.token)).json();
    if (!status0?.service?.host) console.log("Warehouse enrollment not configured locally; release checked by unit/SQL tests only.");
    else {
      const chName = `${prefix}-ch`, ref = { warehouse: { service: status0.service.host, username: chName, confirmed: true } };
      const holder = await call("/api/admin/users", admin.token, "POST", { name: "CH holder", email: `${prefix}-holder@example.invalid`, role: "USER", warehouse: ref.warehouse });
      assert.equal(holder.status, 200);
      const holderId = (await holder.json()).account.id;
      ids.push(new mongoose.Types.ObjectId(holderId));
      assert.equal((await call(`/api/admin/users/${holderId}/kai-warehouse/prepare`, admin.token, "POST", {})).status, 200);
      assert.equal((await call(`/api/admin/users/${holderId}/kai-warehouse/release`, admin.token, "POST", {})).status, 409, "release before revoke");
      assert.equal((await call(`/api/admin/users/${holderId}/kai-disable`, admin.token, "POST", {})).status, 200);
      assert.equal((await call(`/api/admin/users/${user.id}/kai-account`, admin.token, "PATCH", ref)).status, 409, "username still held");
      const released = await call(`/api/admin/users/${holderId}/kai-warehouse/release`, admin.token, "POST", {});
      assert.equal(released.status, 200, await released.clone().text());
      assert.equal((await released.json()).state, "revoked");
      assert.equal((await (await call(`/api/admin/users/${holderId}/kai-account`, admin.token)).json()).warehouse, null);
      assert.equal((await call(`/api/admin/users/${user.id}/kai-account`, admin.token, "PATCH", ref)).status, 200);
      const prepared = await call(`/api/admin/users/${user.id}/kai-warehouse/prepare`, admin.token, "POST", {});
      assert.equal(prepared.status, 200, await prepared.clone().text());
      assert.equal((await prepared.json()).state, "ready");
      await call(`/api/admin/users/${user.id}/kai-disable`, admin.token, "POST", {});
    }

    step = "managed agent owner cannot be disabled or demoted";
    const config = await require("~/server/services/Config").getAppConfig({ role: "ADMIN" }).catch(() => null);
    const agentIds = (config?.modelSpecs?.list ?? []).filter((s) => ["kai-assistant", "kai-ch"].includes(s?.name) && s?.preset?.endpoint === "agents").map((s) => s.preset.agent_id);
    const agent = agentIds.length ? await mongoose.model("Agent").findOne({ id: { $in: agentIds } }, { author: 1 }).lean() : null;
    if (!agent) console.log("No managed agent configured locally; owner guard checked by unit tests only.");
    else {
      const ownerId = String(agent.author);
      const before = await collection.findOne({ _id: agent.author }, { projection: { role: 1, kaiAccount: 1 } });
      restoreOwner = { _id: agent.author, role: before.role, kaiAccount: before.kaiAccount };
      const refused = await call(`/api/admin/users/${ownerId}/kai-disable`, admin.token, "POST", {});
      assert.equal(refused.status, 409);
      assert.match((await refused.json()).message, /pemilik chatbot/);
      assert.equal((await call(`/api/admin/users/${ownerId}/kai-role`, admin.token, "PATCH", { role: "USER" })).status, 409);
      const after = await collection.findOne({ _id: agent.author }, { projection: { role: 1, "kaiAccount.status": 1 } });
      assert.equal(after.role, before.role);
      assert.equal(after.kaiAccount?.status, before.kaiAccount?.status);
      restoreOwner = null;
    }
    console.log("Admin account flow checks passed: reset link, role change, name/email edit, email reuse, agent-owner guard.");
  } finally {
    if (restoreOwner) await collection.updateOne({ _id: restoreOwner._id }, { $set: { role: restoreOwner.role, ...(restoreOwner.kaiAccount ? { kaiAccount: restoreOwner.kaiAccount } : {}) } });
    const leftovers = await collection.find({ email: { $regex: `^${prefix}` } }, { projection: { _id: 1 } }).toArray();
    const all = [...ids, ...leftovers.map((u) => u._id)];
    await mongoose.model("Session").deleteMany({ user: { $in: all } });
    await collection.deleteMany({ _id: { $in: all } });
    await mongoose.disconnect();
    console.log("Temporary account/session fixtures removed.");
  }
}
main().then(() => process.exit(0), (e) => { console.error(`Failed at ${step}: ${e.message}`); process.exit(1); });
