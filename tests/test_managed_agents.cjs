const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  createManagedAgentPermissionContext,
} = require("../services/platform-api/managed_agents.cjs");
const ID = "agent_abcdefghijklmnopqrstu";
const GENERAL_ID = "agent_123456789012345678901";
const KEY = "run_query_mcp_clickhouse";
function fixture() {
  const state = {
    agent: {
      id: ID,
      _id: "resource-1",
      author: "owner",
      provider: "openAI",
      model: "approved",
      tools: [KEY],
      edges: [],
      tenantId: null,
    },
    users: {
      alice: { _id: "alice", role: "USER" },
      bob: { _id: "bob", role: "USER" },
      owner: { _id: "owner", role: "ADMIN" },
    },
    acl: new Set(["alice", "bob"]),
    config: {
      modelSpecs: {
        list: [
          {
            name: "kai-assistant",
            preset: { endpoint: "openAI", model: "approved" },
          },
          { name: "kai-ch", preset: { endpoint: "agents", agent_id: ID } },
        ],
      },
    },
  };
  const dependencies = {
    delimiter: "_mcp_",
    ResourceType: { AGENT: "agent" },
    PermissionBits: { VIEW: 1 },
    getAgent: async ({ id, tenantId }) =>
      state.agent?.id === id && (state.agent.tenantId ?? null) === tenantId
        ? state.agent
        : null,
    findUser: async ({ _id }, projection) => {
      assert.ok(projection.includes("kaiAccount.status"));
      return state.users[String(_id)] ?? null;
    },
    checkPermission: async (query) =>
      query.resourceType === "agent" &&
      query.requiredPermission === 1 &&
      query.resourceId === "resource-1" &&
      query.role === state.users[query.userId]?.role &&
      state.acl.has(query.userId),
    getConfig: async () => state.config,
    normalCanUseServers: async (user) => user?.role === "ADMIN",
  };
  const context = (id = "alice", agentId = ID) =>
    createManagedAgentPermissionContext(
      {
        user: {
          id,
          role: state.users[id]?.role,
          tenantId: state.users[id]?.tenantId,
        },
        config: structuredClone(state.config),
      },
      agentId,
      dependencies,
    );
  return { state, dependencies, context };
}
function persistedFixture() {
  const f = fixture();
  f.state.general = {
    id: GENERAL_ID,
    _id: "general-resource",
    author: "general-owner",
    provider: "openAI",
    model: "approved",
    tools: [],
    tenantId: null,
  };
  f.state.users["general-owner"] = { _id: "general-owner", role: "ADMIN" };
  f.state.config.modelSpecs.list[0].preset = {
    endpoint: "agents",
    agent_id: GENERAL_ID,
  };
  f.dependencies.getAgent = async ({ id, tenantId }) =>
    [f.state.agent, f.state.general].find(
      (agent) => agent?.id === id && (agent.tenantId ?? null) === tenantId,
    ) ?? null;
  return f;
}
test("persisted general baseline permits the aligned CH agent with independent active ADMIN owners", async () => {
  const f = persistedFixture();
  assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), true);
  delete f.state.general.tools;
  assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), true);
  // P2-10: file_search on the General agent must not break the CH agent's grant.
  f.state.general.tools = ["file_search"];
  assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), true);
});
test("persisted general baseline rejects malformed identity, owner, tenant, tools and alternate paths", async () => {
  for (const mutate of [
    (s) => (s.config.modelSpecs.list[0].preset.agent_id = "general"),
    (s) => (s.config.modelSpecs.list[0].preset.agent_id = ID),
    (s) => (s.general.id = "general"),
    (s) => delete s.general._id,
    (s) => (s.general.tenantId = "other"),
    (s) => (s.general.provider = ""),
    (s) => (s.general.model = ""),
    (s) => (s.general.tools = [KEY]),
    (s) => (s.general.tools = ["web_search"]),
    (s) => (s.general.tools = ["file_search", KEY]),
    (s) => (s.general.tools = null),
    (s) => (s.general.agent_ids = [ID]),
    (s) => (s.general.edges = [{ to: ID }]),
    (s) => (s.general.connected_agents = [ID]),
    (s) => (s.general.connectedAgents = [ID]),
    (s) => (s.general.handoffs = [ID]),
    (s) => (s.general.subagents = { agent_ids: [ID] }),
    (s) => (s.general.actions = ["action"]),
    (s) => delete s.general.author,
    (s) => delete s.users["general-owner"],
    (s) => (s.users["general-owner"].role = "USER"),
    (s) => (s.users["general-owner"].active = false),
    (s) => (s.users["general-owner"].kaiAccount = { status: "pending" }),
    (s) => (s.users["general-owner"].tenantId = "other"),
  ]) {
    const f = persistedFixture();
    assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), true);
    mutate(f.state);
    assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), false);
  }
});
test("persisted general provider, model, owner and tools are revalidated across calls", async () => {
  for (const mutate of [
    (s) => (s.general.provider = "other"),
    (s) => (s.general.model = "new-model"),
    (s) => (s.general.tools = [KEY]),
    (s) => (s.users["general-owner"].active = false),
  ]) {
    const f = persistedFixture();
    const c = f.context();
    assert.equal(await c.canUseServers({ id: "alice" }, KEY), true);
    mutate(f.state);
    assert.equal(await c.canUseServers({ id: "alice" }, KEY), false);
  }
  const f = persistedFixture();
  const c = f.context();
  assert.equal(await c.canUseServers({ id: "alice" }, KEY), true);
  f.state.general.model = "new-model";
  assert.equal(await c.canUseServers({ id: "alice" }, KEY), false);
  f.state.agent.model = "new-model";
  assert.equal(await c.canUseServers({ id: "alice" }, KEY), true);
});
test("Alice and Bob reuse the same approved agent with their own authorization", async () => {
  const { context } = fixture();
  assert.equal(
    await context("alice").canUseServers({ id: "alice", role: "USER" }, KEY),
    true,
  );
  assert.equal(
    await context("bob").canUseServers({ id: "bob", role: "USER" }, KEY),
    true,
  );
});
test("each of the ten exact approved ClickHouse tool keys can be selected", async () => {
  const f = fixture();
  f.state.agent.tools = [
    "list_databases_mcp_clickhouse",
    "list_tables_mcp_clickhouse",
    "run_query_mcp_clickhouse",
    "get_table_schema_mcp_clickhouse",
    "execute_readonly_sql_mcp_clickhouse",
    "search_database_metadata_mcp_clickhouse",
    "search_rows_by_meaning_mcp_clickhouse",
    "suggest_comment_sql_mcp_clickhouse",
    "start_row_labeling_mcp_clickhouse",
    "get_row_labeling_job_mcp_clickhouse",
  ];
  const c = f.context();
  for (const key of f.state.agent.tools) {
    assert.equal(
      await c.canUseServers({ id: "alice", role: "USER" }, key),
      true,
    );
  }
});
test("every call rechecks ACL, stored tool membership, model and approval", async () => {
  for (const revoke of [
    (s) => s.acl.delete("alice"),
    (s) => (s.agent.tools = ["list_tables_mcp_clickhouse"]),
    (s) => (s.agent.model = "changed"),
    (s) => s.config.modelSpecs.list.pop(),
    (s) =>
      (s.config.modelSpecs.list[1].preset.agent_id =
        "agent_123456789012345678901"),
  ]) {
    const { state, context } = fixture();
    const c = context();
    assert.equal(
      await c.canUseServers({ id: "alice", role: "USER" }, KEY),
      true,
    );
    revoke(state);
    assert.equal(
      await c.canUseServers({ id: "alice", role: "USER" }, KEY),
      false,
    );
  }
});
test("invalid agent, owner, actor or config never grants tool access", async () => {
  for (const mutate of [
    (s) => s.acl.clear(),
    (s) => delete s.users.owner,
    (s) => (s.users.owner.role = "USER"),
    (s) => (s.users.owner.kaiAccount = { status: "pending" }),
    (s) => (s.users.owner.kaiAccount = { status: "deleted" }),
    (s) => (s.users.alice.kaiAccount = { status: "pending" }),
    (s) => delete s.users.alice,
    (s) => (s.agent.provider = "other"),
    (s) => (s.agent.tools = []),
    (s) => s.agent.tools.push("shell"),
    (s) => s.agent.tools.push("run_query_mcp_other"),
    (s) => (s.agent.tools = [{ name: KEY }]),
    (s) => (s.agent.tenantId = "other"),
    (s) => (s.users.owner.tenantId = "other"),
    (s) => (s.users.alice.tenantId = "other"),
    (s) => (s.agent.agent_ids = [ID]),
    (s) => (s.agent.edges = [{ to: "other" }]),
    (s) => (s.agent.connected_agents = ["other"]),
    (s) => (s.agent.subagents = { agent_ids: ["other"] }),
    (s) => (s.agent.actions = ["action"]),
    (s) => s.config.modelSpecs.list.push(s.config.modelSpecs.list[1]),
    (s) => delete s.config.modelSpecs.list[0].preset.model,
  ]) {
    const { state, context } = fixture();
    const c = context();
    mutate(state);
    assert.equal(
      await c.canUseServers({ id: "alice", role: "USER" }, KEY),
      false,
    );
  }
});
test("no capability probe, alias, wrong server, runtime identity or different agent inherits the grant", async () => {
  const { context } = fixture();
  const c = context();
  for (const key of [
    undefined,
    "run_query_mcp_ClickHouse",
    "run_query_mcp_other",
    "clickhouse_run_query_mcp_clickhouse",
  ]) {
    assert.equal(
      await c.canUseServers({ id: "alice", role: "USER" }, key),
      false,
    );
  }
  assert.equal(
    await c.canUseServers({ id: "owner", role: "ADMIN" }, KEY),
    false,
  );
  assert.equal(
    await c.canUseServers({ id: "alice", role: "USER" }, KEY, "other"),
    false,
  );
  assert.equal(
    await c.canUseServers({ id: "alice", role: "USER" }, KEY, ID, "Clickhouse"),
    false,
  );
  assert.equal(
    await c.canUseServers(
      { id: "alice", role: "USER" },
      KEY,
      ID,
      "clickhouse",
      "clickhouse_run_query",
    ),
    false,
  );
  assert.equal(
    await context("alice", "agent_123456789012345678901").canUseServers(
      { id: "alice", role: "USER" },
      KEY,
    ),
    false,
  );
});
test("public discovery and other agents retain normal role checks; approved agent fails closed for ADMIN", async () => {
  const { state, context } = fixture();
  const publicContext = createManagedAgentPermissionContext(
    { user: { id: "alice", role: "USER" } },
    undefined,
    fixture().dependencies,
  );
  assert.equal(
    await publicContext.canUseServers({ id: "alice", role: "USER" }, KEY),
    false,
  );
  assert.equal(
    await context("owner", "other").canUseServers(
      { id: "owner", role: "ADMIN" },
      KEY,
    ),
    true,
  );
  state.agent.model = "bad";
  assert.equal(
    await context("owner").canUseServers({ id: "owner", role: "ADMIN" }, KEY),
    false,
  );
  state.config.modelSpecs.list.pop();
  assert.equal(
    await context().canUseServers({ id: "alice", role: "USER" }, KEY),
    false,
  );
});
test("dependency failures deny without surfacing diagnostics", async () => {
  const f = fixture();
  f.dependencies.getAgent = async () => {
    throw new Error("private");
  };
  assert.equal(
    await f.context().canUseServers({ id: "alice", role: "USER" }, KEY),
    false,
  );
});
test("shared resolver returns only a fresh validated configured agent for the target tenant", async () => {
  const {
    resolveManagedAgent,
  } = require("../services/platform-api/managed_agents.cjs");
  const f = fixture();
  assert.equal(
    (await resolveManagedAgent(f.state.config, null, f.dependencies))?.id,
    ID,
  );
  f.state.users.owner.role = "USER";
  assert.equal(
    await resolveManagedAgent(f.state.config, null, f.dependencies),
    null,
  );
  f.state.users.owner.role = "ADMIN";
  assert.equal(
    await resolveManagedAgent(f.state.config, "other", f.dependencies),
    null,
  );
});
test("tenant-scoped users can use the same approved agent only within their captured tenant", async () => {
  const f = fixture();
  f.state.agent.tenantId = "tenant-one";
  for (const user of Object.values(f.state.users)) user.tenantId = "tenant-one";
  const c = f.context();
  assert.equal(
    await c.canUseServers(
      { id: "alice", role: "USER", tenantId: "tenant-one" },
      KEY,
    ),
    true,
  );
  assert.equal(
    await c.canUseServers(
      { id: "alice", role: "USER", tenantId: "tenant-two" },
      KEY,
    ),
    false,
  );
  f.state.users.alice.tenantId = "tenant-two";
  assert.equal(
    await c.canUseServers(
      { id: "alice", role: "USER", tenantId: "tenant-one" },
      KEY,
    ),
    false,
  );
});
test("an explicitly named CH model may differ from General, but the provider never may", async () => {
  const previous = process.env.KAI_CH_AGENT_MODEL;
  process.env.KAI_CH_AGENT_MODEL = "pro-model";
  try {
    const f = persistedFixture();
    f.state.agent.model = "pro-model";
    assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), true);
    f.state.agent.model = "approved"; // General's model is no longer the CH pin
    assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), false);
    f.state.agent.model = "pro-model";
    f.state.agent.provider = "other-provider";
    assert.equal(await f.context().canUseServers({ id: "alice" }, KEY), false);
  } finally {
    if (previous === undefined) delete process.env.KAI_CH_AGENT_MODEL;
    else process.env.KAI_CH_AGENT_MODEL = previous;
  }
});
