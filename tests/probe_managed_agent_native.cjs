/* Offline native SDK probe: synthetic DB/ACL/config and a local tool manager only.
 * Run inside the patched image with --network none; no Mongo/CH/model endpoint is used.
 * Native function bodies are evaluated unchanged from installed source, with I/O injected.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const { tool } = require("@librechat/agents/langchain/tools");
const {
  Constants,
  ResourceType,
  PermissionBits,
  isAssistantsEndpoint,
} = require("librechat-data-provider");
const { Providers, Constants: AgentConstants } = require("@librechat/agents");
const {
  normalizeServerName,
  normalizeJsonSchema,
  resolveJsonSchemaRefs,
} = require("@librechat/api");
const source = fs.readFileSync("/app/api/server/services/MCP.js", "utf8");
function nativeFunction(name) {
  const starts = [
    ...source.matchAll(new RegExp(`^(?:async )?function ${name}\\(`, "gm")),
  ].map((match) => match.index);
  assert.equal(starts.length, 1, `Unique native function ${name}`);
  const end = source.indexOf("\n}\n", starts[0]);
  assert.ok(end > starts[0]);
  return source.slice(starts[0], end + 3);
}
const ID = "agent_abcdefghijklmnopqrstu";
const key = `run_query${Constants.mcp_delimiter}clickhouse`;
function fixture() {
  const state = {
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
    users: {
      alice: { _id: "alice", role: "USER" },
      bob: { _id: "bob", role: "USER" },
      owner: { _id: "owner", role: "ADMIN" },
    },
    agent: {
      _id: "db-agent",
      id: ID,
      author: "owner",
      provider: "openAI",
      model: "approved",
      tools: [key],
      edges: [],
    },
    canView: true,
  };
  const sandbox = {
    module: { exports: {} },
    tool,
    Constants,
    ResourceType,
    PermissionBits,
    Providers,
    AgentConstants,
    normalizeServerName,
    normalizeJsonSchema,
    resolveJsonSchemaRefs,
    isAssistantsEndpoint,
    logger: { error() {}, debug() {} },
    checkAccessWithRequestCache: async ({ user }) => user.role === "ADMIN",
    PermissionTypes: { MCP_SERVERS: "MCP_SERVERS" },
    Permissions: { USE: "USE" },
    getAppConfigForUser: async () => state.config,
    db: {
      findUser: async ({ _id }, projection) => {
        assert.ok(projection.includes("kaiAccount.status"));
        return state.users[String(_id)];
      },
      getAgent: async ({ id }) => (state.agent?.id === id ? state.agent : null),
    },
    require(name) {
      if (name === "librechat-data-provider")
        return { ResourceType, PermissionBits };
      if (name === "/app/kai-platform/managed_agents.cjs") return require(name);
      if (name === "./PermissionService")
        return {
          checkPermission: async ({ userId, resourceId, requiredPermission }) =>
            state.canView &&
            ["alice", "bob"].includes(userId) &&
            resourceId === "db-agent" &&
            requiredPermission === PermissionBits.VIEW,
        };
      if (name === "~/server/services/Schedules")
        return { recordMCPToolAuthFailure: async () => {} };
      throw new Error(`Unexpected native dependency: ${name}`);
    },
    isDirectOpenIDBearerRecoveryEnabled: () => false,
    requiresEphemeralUserConnection: () => false,
    getLogStores: () => ({}),
    CacheKeys: { FLOWS: "flows" },
    getFlowStateManager: () => ({}),
    getMCPManager: () => ({
      callTool: async ({ user, serverName }) => {
        assert.equal(serverName, "clickhouse");
        return [`result-for-${user.id}`, {}];
      },
    }),
    createRunStepDeltaEmitter: () => () => {},
    createOAuthStart: () => () => {},
    createOAuthEnd: () => () => {},
    findToken() {
      throw new Error("Unexpected token read");
    },
    createToken() {},
    updateToken() {},
    deleteTokens() {},
    getGraphApiToken() {},
    exchangeOboToken() {},
    createOboTrustChecker: () => () => false,
    OpenIDReauthRequiredError: class extends Error {},
    MCPAuthenticationRefreshError: class extends Error {},
    MCPAuthenticationRejectedError: class extends Error {},
  };
  vm.runInNewContext(
    [
      "userCanUseMCPServers",
      "createMCPPermissionContext",
      "resolveToolCallUserId",
      "createToolInstance",
    ]
      .map(nativeFunction)
      .join("\n") +
      "\nmodule.exports = { createMCPPermissionContext, createToolInstance };",
    sandbox,
  );
  const { createMCPPermissionContext, createToolInstance } =
    sandbox.module.exports;
  function instance(
    id,
    agentId = ID,
    serverName = "clickhouse",
    serverToolName = "run_query",
  ) {
    const user = { id, role: state.users[id].role };
    const req = { user, config: structuredClone(state.config) };
    return createToolInstance({
      user,
      toolName: "run_query",
      serverName,
      serverToolName,
      mcpPermissionContext: createMCPPermissionContext(req, agentId),
      toolDefinition: {
        description: "offline probe",
        parameters: {
          type: "object",
          properties: { query: { type: "string" } },
          required: ["query"],
        },
      },
      provider: "openAI",
      serverConfig: {},
    });
  }
  const invoke = (instance, config = {}) =>
    instance.invoke(
      { query: "SELECT 1" },
      { metadata: { thread_id: "offline", run_id: "offline" }, ...config },
    );
  return { state, instance, invoke, createMCPPermissionContext };
}

test("native SDK tool authorizes two users and rechecks ACL and stored tools at invocation", async () => {
  const f = fixture();
  const alice = f.instance("alice");
  const bob = f.instance("bob");
  assert.equal(await f.invoke(alice), "result-for-alice");
  assert.equal(await f.invoke(bob), "result-for-bob");
  f.state.canView = false;
  await assert.rejects(f.invoke(alice), /Forbidden/);
  f.state.canView = true;
  f.state.agent.tools = [`list_tables${Constants.mcp_delimiter}clickhouse`];
  await assert.rejects(f.invoke(alice), /Forbidden/);
});
test("native SDK invocation rejects forged user, actor and server aliases", async () => {
  const f = fixture();
  const alice = f.instance("alice");
  await assert.rejects(
    f.invoke(alice, { configurable: { user: { id: "owner", role: "ADMIN" } } }),
    /Forbidden/,
  );
  await assert.rejects(
    f.invoke(alice, { metadata: { agentId: "other" } }),
    /Forbidden/,
  );
  await assert.rejects(
    f.invoke(f.instance("alice", ID, "Clickhouse")),
    /Forbidden/,
  );
  await assert.rejects(
    f.invoke(f.instance("alice", ID, "clickhouse", "clickhouse_run_query")),
    /Forbidden/,
  );
  await assert.rejects(f.invoke(f.instance("alice", "other")), /Forbidden/);
});
test("public native MCP permission remains role controlled", async () => {
  const f = fixture();
  const user = { id: "alice", role: "USER" };
  assert.equal(
    await f.createMCPPermissionContext({ user }).canUseServers(user),
    false,
  );
});
