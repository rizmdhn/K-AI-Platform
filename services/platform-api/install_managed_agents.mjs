import fs from "node:fs";
import path from "node:path";

function replace(source, before, after, count, seam) {
  if (source.split(before).length - 1 !== count)
    throw new Error(`Unexpected native managed-agent seam: ${seam}`);
  return source.split(before).join(after);
}

export function installManagedAgents(root = "/app") {
  const nativeVersion = JSON.parse(
    fs.readFileSync(path.join(root, "api/package.json"), "utf8"),
  ).version;
  if (nativeVersion !== "v0.8.8")
    throw new Error("Unsupported native managed-agent version");
  const files = {
    mcp: path.join(root, "api/server/services/MCP.js"),
    tools: path.join(root, "api/server/services/ToolService.js"),
    loader: path.join(root, "api/app/clients/tools/util/handleTools.js"),
  };
  // Compute and validate every patch before writing any native source.
  let mcp = fs.readFileSync(files.mcp, "utf8");
  let tools = fs.readFileSync(files.tools, "utf8");
  let loader = fs.readFileSync(files.loader, "utf8");
  mcp = replace(
    mcp,
    `function createMCPPermissionContext(req) {
  return {
    canUseServers: (user = req?.user) => userCanUseMCPServers(user, req),
  };
}`,
    `function createMCPPermissionContext(req, agentId) {
  const { ResourceType, PermissionBits } = require('librechat-data-provider');
  return require('/app/kai-platform/managed_agents.cjs').createManagedAgentPermissionContext(req, agentId, {
    normalCanUseServers: userCanUseMCPServers,
    getConfig: (user) => getAppConfigForUser(user.id, user),
    getAgent: db.getAgent,
    findUser: db.findUser,
    checkPermission: require('./PermissionService').checkPermission,
    ResourceType,
    PermissionBits,
    delimiter: Constants.mcp_delimiter,
  });
}`,
    1,
    "MCP context factory",
  );
  mcp = replace(
    mcp,
    "await mcpPermissionContext.canUseServers(permissionUser)",
    "await mcpPermissionContext.canUseServers(permissionUser, normalizedToolKey, config?.metadata?.agent_id ?? config?.metadata?.agentId ?? config?.configurable?.agent_id, serverName, serverToolName)",
    1,
    "MCP invocation permission",
  );
  tools = replace(
    tools,
    `  const hasMCPTools = agent.tools?.some((tool) => tool?.includes(Constants.mcp_delimiter));
  const mcpPermissionContext = createMCPPermissionContext(req);
  const canUseMCP = hasMCPTools ? await mcpPermissionContext.canUseServers(req.user) : true;`,
    `  const mcpPermissionContext = createMCPPermissionContext(req, agent.id);
  const allowedMCPTools = new Set((await Promise.all(
    agent.tools.filter((key) => key?.includes(Constants.mcp_delimiter)).map(async (key) =>
      await mcpPermissionContext.canUseServers(req.user, key) ? key : null),
  )).filter(Boolean));`,
    2,
    "agent definition/traditional exact tool gates",
  );
  tools = replace(
    tools,
    "return areToolsEnabled && canUseMCP;",
    "return areToolsEnabled && allowedMCPTools.has(tool);",
    2,
    "agent tool membership filters",
  );
  tools = replace(
    tools,
    `        returnMetadata: true,
        mcpAvailableTools,`,
    `        returnMetadata: true,
        mcpPermissionContext: createMCPPermissionContext(req, agent?.id),
        mcpAvailableTools,`,
    1,
    "event-driven executing agent context",
  );
  loader = replace(
    loader,
    `  const canUseMCP = hasMCPTools
    ? await mcpPermissionContext.canUseServers(options.req?.user)
    : true;`,
    `  const allowedMCPTools = new Set((await Promise.all(
    tools.filter((key) => key && mcpToolPattern.test(key)).map(async (key) =>
      await mcpPermissionContext.canUseServers(options.req?.user, key) ? key : null),
  )).filter(Boolean));
  const hasAnyAllowedMCP = allowedMCPTools.size > 0;`,
    1,
    "native loader exact permissions",
  );
  loader = replace(
    loader,
    "if (hasMCPTools && canUseMCP) {",
    "if (hasMCPTools && hasAnyAllowedMCP) {",
    1,
    "native loader context priming",
  );
  loader = replace(
    loader,
    "if (!canUseMCP) {",
    "if (!allowedMCPTools.has(tool)) {",
    1,
    "native loader exact tool construction",
  );
  fs.writeFileSync(files.mcp, mcp);
  fs.writeFileSync(files.tools, tools);
  fs.writeFileSync(files.loader, loader);
}
