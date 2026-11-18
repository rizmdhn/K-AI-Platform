// Private, per-agent MCP authorization. Public MCP role/discovery checks remain native.
const TOOL_NAMES = [
  "list_databases",
  "list_tables",
  "run_query",
  "get_table_schema",
  "execute_readonly_sql",
  "search_database_metadata",
  "search_rows_by_meaning",
  "suggest_comment_sql",
  "start_row_labeling",
  "get_row_labeling_job",
];
const USER_FIELDS =
  "_id role tenantId kaiAccount.status deleted deletedAt disabled active";
const idString = (value) => (value == null ? null : String(value));
const tenant = (value) => (value == null ? null : String(value));
const nativeAgentId = (value) =>
  typeof value === "string" && /^agent_[A-Za-z0-9_-]{21}$/.test(value);
const populated = (value) =>
  value != null &&
  !(Array.isArray(value) && value.length === 0) &&
  !(typeof value === "object" && Object.keys(value).length === 0);
const active = (user) =>
  !!user &&
  !user.deleted &&
  !user.deletedAt &&
  !user.disabled &&
  user.active !== false &&
  ["existing", "active"].includes(user.kaiAccount?.status ?? "existing");

function readApproval(config) {
  const specs = config?.modelSpecs?.list;
  if (!Array.isArray(specs)) return null;
  const approved = specs.filter((spec) => spec?.name === "kai-ch");
  const baseline = specs.filter((spec) => spec?.name === "kai-assistant");
  if (approved.length !== 1 || baseline.length !== 1) return null;
  const agentId = approved[0]?.preset?.agent_id;
  const preset = baseline[0]?.preset;
  if (
    approved[0]?.preset?.endpoint !== "agents" ||
    !nativeAgentId(agentId) ||
    typeof preset?.endpoint !== "string" ||
    !preset.endpoint
  )
    return null;
  if (preset.endpoint === "agents") {
    if (!nativeAgentId(preset.agent_id) || preset.agent_id === agentId)
      return null;
    return { agentId, generalAgentId: preset.agent_id };
  }
  if (typeof preset.model !== "string" || !preset.model) return null;
  return { agentId, provider: preset.endpoint, model: preset.model };
}

function namesAgent(config, agentId) {
  return (
    Array.isArray(config?.modelSpecs?.list) &&
    config.modelSpecs.list.some(
      (spec) => spec?.name === "kai-ch" && spec?.preset?.agent_id === agentId,
    )
  );
}

// Models an admin approved for a custom endpoint (librechat.yaml or an Admin Panel override, merged in config).
function endpointModels(config, provider) {
  const endpoint = (Array.isArray(config?.endpoints?.custom) ? config.endpoints.custom : [])
    .find((entry) => entry?.name === provider);
  const listed = endpoint?.models?.default;
  return Array.isArray(listed) ? listed.filter((model) => typeof model === "string" && model.trim()) : [];
}

async function resolveManagedAgent(
  config,
  tenantId,
  // Both agents always share one provider (one data destination). The CH agent may use another model of
  // that provider only when the operator names it (.env, e.g. Gemini Pro vs Flash) or an admin lists it for
  // that endpoint (Admin Panel).
  { getAgent, findUser, delimiter, chModel = process.env.KAI_CH_AGENT_MODEL },
) {
  try {
    const approval = readApproval(config);
    if (!approval || typeof delimiter !== "string" || !delimiter) return null;
    const expectedTenant = tenant(tenantId);
    // Both records and their ADMIN owners are read again for every decision.
    const validOwnedAgent = async (agent, expectedId) => {
      if (
        !agent ||
        agent.id !== expectedId ||
        !idString(agent._id)?.trim() ||
        tenant(agent.tenantId) !== expectedTenant ||
        !agent.author
      )
        return false;
      // No graph, handoff, spawn, action or alternate tool path can share this grant.
      for (const field of [
        "agent_ids",
        "edges",
        "connected_agents",
        "connectedAgents",
        "handoffs",
        "subagents",
        "actions",
      ]) {
        if (populated(agent[field])) return false;
      }
      const owner = await findUser({ _id: agent.author }, USER_FIELDS);
      return (
        active(owner) &&
        idString(owner._id) === idString(agent.author) &&
        owner.role === "ADMIN" &&
        tenant(owner.tenantId) === expectedTenant
      );
    };
    if (approval.generalAgentId) {
      const general = await getAgent({
        id: approval.generalAgentId,
        tenantId: expectedTenant,
      });
      if (
        !(await validOwnedAgent(general, approval.generalAgentId)) ||
        typeof general.provider !== "string" ||
        !general.provider.trim() ||
        typeof general.model !== "string" ||
        !general.model.trim() ||
        // The General agent may only have file_search (P2-10): never an MCP tool.
        (general.tools !== undefined &&
          (!Array.isArray(general.tools) ||
            general.tools.some((key) => key !== "file_search")))
      )
        return null;
      approval.provider = general.provider;
      approval.model = general.model;
    }
    const allowed = new Set(
      TOOL_NAMES.map((name) => `${name}${delimiter}clickhouse`),
    );
    const agent = await getAgent({
      id: approval.agentId,
      tenantId: expectedTenant,
    });
    if (
      !(await validOwnedAgent(agent, approval.agentId)) ||
      agent.provider !== approval.provider ||
      ![chModel || approval.model, ...endpointModels(config, approval.provider)].includes(agent.model) ||
      !Array.isArray(agent.tools) ||
      !agent.tools.length ||
      !agent.tools.every((key) => typeof key === "string" && allowed.has(key))
    )
      return null;
    return agent;
  } catch {
    return null;
  }
}

function createManagedAgentPermissionContext(req, agentId, dependencies) {
  const {
    normalCanUseServers,
    getConfig,
    getAgent,
    findUser,
    checkPermission,
    ResourceType,
    PermissionBits,
    delimiter,
  } = dependencies;
  if (agentId == null || !namesAgent(req?.config, agentId))
    return {
      canUseServers: (user = req?.user) => normalCanUseServers(user, req),
    };
  const capturedId = idString(req?.user?.id);
  const capturedTenant = tenant(req?.user?.tenantId ?? req?.tenantId);
  // Retain scope after a global config edit removes or changes the approved entry.
  const allowed = new Set(
    TOOL_NAMES.map((name) => `${name}${delimiter}clickhouse`),
  );
  return {
    async canUseServers(
      user = req?.user,
      toolKey,
      runtimeAgentId = agentId,
      serverName = "clickhouse",
      serverToolName,
    ) {
      try {
        if (
          !capturedId ||
          idString(user?.id) !== capturedId ||
          runtimeAgentId !== agentId ||
          serverName !== "clickhouse" ||
          tenant(user?.tenantId) !== capturedTenant
        )
          return false;
        const currentUser = await findUser({ _id: capturedId }, USER_FIELDS);
        if (
          !active(currentUser) ||
          idString(currentUser._id) !== capturedId ||
          tenant(currentUser.tenantId) !== capturedTenant
        )
          return false;
        const config = await getConfig({ ...currentUser, id: capturedId });
        const approval = readApproval(config);
        if (approval?.agentId !== agentId) return false;
        if (
          typeof delimiter !== "string" ||
          !delimiter ||
          typeof toolKey !== "string" ||
          !allowed.has(toolKey)
        )
          return false;
        if (
          serverToolName != null &&
          `${serverToolName}${delimiter}clickhouse` !== toolKey
        )
          return false;
        const agent = await resolveManagedAgent(config, capturedTenant, {
          getAgent,
          findUser,
          delimiter,
        });
        if (!agent || agent.id !== agentId || !agent.tools.includes(toolKey))
          return false;
        return (
          (await checkPermission({
            userId: capturedId,
            role: currentUser.role,
            resourceType: ResourceType.AGENT,
            resourceId: agent._id,
            requiredPermission: PermissionBits.VIEW,
          })) === true
        );
      } catch {
        return false;
      }
    },
  };
}

module.exports = { createManagedAgentPermissionContext, resolveManagedAgent, endpointModels };
