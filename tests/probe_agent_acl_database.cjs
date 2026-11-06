// Disposable Mongo only. Configuration is synthetic; all identity/ACL reads and writes are native.
const assert = require("node:assert/strict");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
require("@librechat/data-schemas").createModels(mongoose);
const db = require("~/models");
const permissions = require("~/server/services/PermissionService");
const { hasCapability } = require("~/server/middleware/roles/capabilities");
const native = require("librechat-data-provider");
const { createAccounts } = require("/app/kai-platform/accounts.cjs");
const { createAgentAccess } = require("/app/kai-platform/agent_access.cjs");
const {
  resolveManagedAgent,
  createManagedAgentPermissionContext,
} = require("/app/kai-platform/managed_agents.cjs");

(async () => {
  await mongoose.connect("mongodb://%2Fsocket%2Fmongodb-27017.sock", {
    dbName: "fixture",
    serverSelectionTimeoutMS: 5000,
  });
  await db.seedDatabase();
  const users = [];
  for (const [name, role] of [
    ["owner", "ADMIN"],
    ["alice", "USER"],
    ["bob", "USER"],
  ]) {
    users.push(
      await mongoose
        .model("User")
        .create({
          name,
          role,
          email: `${name}@example.invalid`,
          provider: "local",
          emailVerified: true,
        }),
    );
  }
  const [owner, alice, bob] = users;
  const key = `run_query${native.Constants.mcp_delimiter}clickhouse`;
  const agent = await db.createAgent({
    id: "agent_abcdefghijklmnopqrstu",
    name: "Fixture CH",
    author: owner._id,
    provider: "openAI",
    model: "fixture",
    tools: [key],
  });
  const config = {
    modelSpecs: {
      list: [
        {
          name: "kai-assistant",
          preset: { endpoint: "openAI", model: "fixture" },
        },
        { name: "kai-ch", preset: { endpoint: "agents", agent_id: agent.id } },
      ],
    },
  };
  const accounts = createAccounts({
    mongoose,
    hasCapability,
    bcrypt: require("bcryptjs"),
    origin: "http://localhost",
  });
  const resolverDeps = {
    getAgent: db.getAgent,
    findUser: db.findUser,
    delimiter: native.Constants.mcp_delimiter,
  };
  const access = createAgentAccess({
    agentContext: accounts.agentContext,
    hasCapability,
    getConfig: async () => config,
    resolveManagedAgent: (c, t) => resolveManagedAgent(c, t, resolverDeps),
    getUserPrincipals: db.getUserPrincipals,
    findEntriesByPrincipalsAndResource: db.findEntriesByPrincipalsAndResource,
    ...permissions,
    ...native,
  });
  const request = {
    user: { id: String(owner._id) },
    params: { id: String(alice._id) },
    body: { agent_id: agent.id, assigned: true },
  };
  assert.equal((await access.get(request)).effective_access, false);
  assert.equal((await access.set(request)).direct_assignment, true);
  const contextFor = (user) =>
    createManagedAgentPermissionContext(
      { user: { id: String(user._id), role: user.role }, config },
      agent.id,
      {
        ...resolverDeps,
        normalCanUseServers: async () => false,
        getConfig: async () => config,
        checkPermission: permissions.checkPermission,
        ResourceType: native.ResourceType,
        PermissionBits: native.PermissionBits,
      },
    );
  const aliceContext = contextFor(alice),
    bobContext = contextFor(bob);
  assert.equal(await aliceContext.canUseServers(undefined, key), true);
  assert.equal(await bobContext.canUseServers(undefined, key), false);
  request.body.assigned = false;
  assert.equal((await access.set(request)).effective_access, false);
  assert.equal(await aliceContext.canUseServers(undefined, key), false);
  request.user = { id: String(bob._id) };
  request.body.assigned = true;
  await assert.rejects(access.set(request));
  console.log(
    "Native Mongo: ADMIN assignment, Alice allowed/Bob denied, fresh revocation, USER write denied.",
  );
})()
  .then(() => mongoose.disconnect())
  .then(() => process.exit(0))
  .catch(async (error) => {
    console.error(error.name, error.message);
    await mongoose.disconnect();
    process.exit(1);
  });
