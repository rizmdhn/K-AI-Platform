import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { installManagedAgents } from "../services/platform-api/install_managed_agents.mjs";

test("installer refuses unsupported native versions before changing files", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kai-managed-install-"));
  try {
    fs.mkdirSync(path.join(root, "api"), { recursive: true });
    fs.writeFileSync(
      path.join(root, "api/package.json"),
      JSON.stringify({ version: "future" }),
    );
    assert.throws(
      () => installManagedAgents(root),
      /Unsupported native managed-agent version/,
    );
    assert.equal(
      fs.readFileSync(path.join(root, "api/package.json"), "utf8"),
      '{"version":"future"}',
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("a later native loader seam mismatch leaves all earlier native files untouched", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "kai-managed-drift-"));
  const sources = {
    "package.json": '{"version":"v0.8.8"}',
    "server/services/MCP.js": `function createMCPPermissionContext(req) {
  return {
    canUseServers: (user = req?.user) => userCanUseMCPServers(user, req),
  };
}\nawait mcpPermissionContext.canUseServers(permissionUser)`,
    "server/services/ToolService.js":
      Array(2)
        .fill(
          `  const hasMCPTools = agent.tools?.some((tool) => tool?.includes(Constants.mcp_delimiter));
  const mcpPermissionContext = createMCPPermissionContext(req);
  const canUseMCP = hasMCPTools ? await mcpPermissionContext.canUseServers(req.user) : true;
return areToolsEnabled && canUseMCP;`,
        )
        .join("\n") +
      `\n        returnMetadata: true,
        mcpAvailableTools,`,
    "app/clients/tools/util/handleTools.js":
      "// native loader changed upstream",
  };
  try {
    for (const [name, source] of Object.entries(sources)) {
      const file = path.join(root, "api", name);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, source);
    }
    assert.throws(
      () => installManagedAgents(root),
      /Unexpected native managed-agent seam: native loader exact permissions/,
    );
    for (const [name, source] of Object.entries(sources)) {
      assert.equal(
        fs.readFileSync(path.join(root, "api", name), "utf8"),
        source,
      );
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
