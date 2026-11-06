// Temporary local Mongo/native HTTP fixtures only. Never calls a model or ClickHouse.
const assert = require("node:assert/strict");
const { randomBytes, randomUUID } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");
const prefix = `kai-native-${randomUUID()}`;
const ids = [];
let step = "start";
async function main() {
  await mongoose.connect(process.env.MONGO_URI, {
    autoIndex: false,
    autoCreate: false,
    serverSelectionTimeoutMS: 5000,
  });
  const User = mongoose.model("User"),
    collection = User.collection;
  const { createAccounts } = require("/app/kai-platform/accounts.cjs");
  const core = createAccounts({
    mongoose,
    bcrypt,
    hasCapability: async (actor) => actor.role === "ADMIN",
    origin: new URL(process.env.DOMAIN_CLIENT).origin,
  });
  const base = `http://127.0.0.1:${process.env.PORT || 3080}`;
  const call = (path, token, method = "GET", body) =>
    fetch(base + path, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(15000),
    });
  try {
    const sessions = {};
    for (const role of ["ADMIN", "USER"]) {
      const password = randomBytes(24).toString("hex"),
        email = `${prefix}-${role}@example.invalid`;
      const u = await User.create({
        name: "Temporary native account test",
        email,
        role,
        provider: "local",
        emailVerified: true,
        password: await bcrypt.hash(password, 10),
      });
      ids.push(u._id);
      const login = await call("/api/auth/login", null, "POST", {
        email,
        password,
      });
      assert.equal(login.status, 200);
      sessions[role] = (await login.json()).token;
    }
    const data = {
      name: "Temporary provisioned user",
      email: `${prefix}-new@example.invalid`,
      role: "USER",
      warehouse: {
        service: "fixture.example.invalid",
        username: prefix,
        confirmed: true,
      },
    };
    step = "authorization";
    assert.equal(
      (await call("/api/admin/users", null, "POST", data)).status,
      401,
    );
    assert.equal(
      (await call("/api/admin/users", sessions.USER, "POST", data)).status,
      403,
    );
    assert.equal(
      (
        await call("/api/admin/users", sessions.ADMIN, "POST", {
          ...data,
          password: "forbidden",
        })
      ).status,
      400,
    );
    step = "create";
    const response = await call(
      "/api/admin/users",
      sessions.ADMIN,
      "POST",
      data,
    );
    assert.equal(response.status, 200);
    const created = await response.json(),
      userId = created.account.id;
    ids.push(new mongoose.Types.ObjectId(userId));
    assert.equal(created.account.status, "pending");
    const fragment = new URLSearchParams(
        new URL(created.activationUrl).hash.slice(1),
      ),
      token = fragment.get("token");
    assert.ok(token && !new URL(created.activationUrl).search);
    const stored = await collection.findOne({ _id: ids.at(-1) });
    assert.ok(stored.kaiAccount.activationHash);
    assert.equal(stored.password, undefined);
    assert.ok(!JSON.stringify(stored).includes(token));
    assert.equal(
      (await call("/api/admin/users", sessions.ADMIN, "POST", data)).status,
      409,
    );
    assert.equal(
      (
        await call("/api/admin/users", sessions.ADMIN, "POST", {
          ...data,
          email: `${prefix}-duplicate@example.invalid`,
        })
      ).status,
      409,
    );
    step = "native password reset cannot strand invitation";
    assert.equal(await core.isPending(userId), true);
    const reset = await require("~/server/services/AuthService").resetPassword(
      userId,
      "invalid",
      "irrelevant-password",
    );
    assert.equal(reset.message, "Account activation required");
    step = "pending guard and privacy";
    const testBearer = jwt.sign({ id: userId }, process.env.JWT_SECRET, {
      expiresIn: "5m",
    });
    const pending = await call("/api/user", testBearer);
    assert.equal(pending.status, 403);
    assert.equal((await pending.json()).code, "ACCOUNT_ACTIVATION_REQUIRED");
    const listing = await call("/api/admin/users", sessions.ADMIN);
    assert.equal(listing.status, 200);
    const listedText = await listing.text();
    assert.ok(!listedText.includes("activationHash"));
    assert.ok(!listedText.includes(token));
    const defaultUser = await User.findById(userId).lean();
    assert.equal(defaultUser.kaiAccount, undefined);
    step = "reference bookkeeping";
    assert.equal(
      (
        await call(
          `/api/admin/users/${userId}/kai-account`,
          sessions.ADMIN,
          "PATCH",
          { warehouse: null, role: "ADMIN" },
        )
      ).status,
      400,
    );
    assert.equal(
      (
        await call(
          `/api/admin/users/${userId}/kai-account`,
          sessions.ADMIN,
          "PATCH",
          { warehouse: null },
        )
      ).status,
      200,
    );
    assert.equal(
      (await collection.findOne({ _id: ids.at(-1) })).kaiAccount.activationHash,
      stored.kaiAccount.activationHash,
    );
    step = "reissue invalidates";
    const reissue = await call(
      `/api/admin/users/${userId}/kai-activation`,
      sessions.ADMIN,
      "POST",
      {},
    );
    assert.equal(reissue.status, 200);
    const next = new URLSearchParams(
      new URL((await reissue.json()).activationUrl).hash.slice(1),
    );
    const password = randomBytes(24).toString("hex");
    await assert.rejects(
      core.activate({ userId, token, password, confirm_password: password }),
    );
    step = "single use race";
    const attempts = await Promise.allSettled(
      [0, 1].map(() =>
        core.activate({
          userId,
          token: next.get("token"),
          password,
          confirm_password: password,
        }),
      ),
    );
    assert.equal(attempts.filter((r) => r.status === "fulfilled").length, 1);
    const activated = await collection.findOne({ _id: ids.at(-1) });
    assert.equal(activated.kaiAccount.status, "active");
    assert.equal(activated.kaiAccount.activationHash, undefined);
    assert.ok(await bcrypt.compare(password, activated.password));
    assert.equal(
      (
        await call(
          `/api/admin/users/${userId}/kai-activation`,
          sessions.ADMIN,
          "POST",
          {},
        )
      ).status,
      409,
    );
    const login = await call("/api/auth/login", null, "POST", {
      email: data.email,
      password,
    });
    assert.equal(login.status, 200);
    const profile = await call("/api/user", (await login.json()).token);
    assert.equal(profile.status, 200);
    assert.ok(!(await profile.text()).includes("kaiAccount"));
    step = "expiry and tenant boundaries";
    const expResponse = await call("/api/admin/users", sessions.ADMIN, "POST", {
      ...data,
      email: `${prefix}-expired@example.invalid`,
      warehouse: null,
    });
    assert.equal(expResponse.status, 200);
    const expired = await expResponse.json();
    ids.push(new mongoose.Types.ObjectId(expired.account.id));
    await collection.updateOne(
      { _id: ids.at(-1) },
      { $set: { "kaiAccount.activationExpiresAt": new Date(0) } },
    );
    const expToken = new URLSearchParams(
      new URL(expired.activationUrl).hash.slice(1),
    ).get("token");
    await assert.rejects(
      core.activate({
        userId: expired.account.id,
        token: expToken,
        password,
        confirm_password: password,
      }),
    );
    const delegated = createAccounts({
      mongoose,
      bcrypt,
      hasCapability: async () => true,
      origin: new URL(process.env.DOMAIN_CLIENT).origin,
    });
    await assert.rejects(
      delegated.create({
        user: { id: String(ids[1]), role: "ADMIN" },
        body: { ...data, role: "ADMIN", warehouse: null },
      }),
      (e) => e.status === 403,
    );
    await assert.rejects(
      delegated.update({
        user: { id: String(ids[1]), role: "ADMIN" },
        params: { id: String(ids[0]) },
        body: { warehouse: null },
      }),
      (e) => e.status === 403,
    );
    const foreignId = new mongoose.Types.ObjectId();
    ids.push(foreignId);
    await collection.insertOne({
      _id: foreignId,
      name: "Foreign fixture",
      email: `${prefix}-foreign@example.invalid`,
      role: "USER",
      tenantId: "fixture-other-tenant",
    });
    await assert.rejects(
      core.read({
        user: { id: String(ids[0]) },
        params: { id: String(foreignId) },
      }),
      (e) => e.status === 404,
    );
    step = "activation headers";
    const page = await fetch(base + "/activate-account");
    assert.equal(page.status, 200);
    assert.equal(page.headers.get("referrer-policy"), "same-origin");
    assert.equal(page.headers.get("cache-control"), "no-store");
    assert.match(
      page.headers.get("content-security-policy"),
      /frame-ancestors 'none'/,
    );
    console.log(
      "Native Users HTTP/Mongo checks passed: authorization, strict input, duplicates, pending denial, private metadata, reference update, reissue, atomic single-use, native login and activation headers.",
    );
  } finally {
    const leftovers = await collection
      .find({ email: { $regex: `^${prefix}` } }, { projection: { _id: 1 } })
      .toArray();
    const all = [...ids, ...leftovers.map((u) => u._id)];
    await mongoose.model("Session").deleteMany({ user: { $in: all } });
    await collection.deleteMany({ _id: { $in: all } });
    assert.equal(
      await collection.countDocuments({ email: { $regex: `^${prefix}` } }),
      0,
    );
    await mongoose.disconnect();
    console.log("Temporary account/session fixtures removed.");
  }
}
main().then(
  () => process.exit(0),
  (e) => {
    console.error(
      `Native account smoke failed at ${step}: ${e.name}${e.actual !== undefined ? ` (status/count ${e.actual}, expected ${e.expected})` : ""}. No credentials printed.`,
    );
    process.exit(1);
  },
);
