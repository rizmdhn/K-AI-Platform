/* Offline spike of the bundled native permission helper, with synthetic dependency data only. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const {
  ResourceType,
  PermissionBits,
  hasPermissions,
  isEphemeralAgentId,
} = require("librechat-data-provider");

function fixture() {
  const state = {
    agent: {
      _id: "agent-db-id",
      author: "owner",
      tool_resources: { file_search: { file_ids: ["shared"] } },
    },
    permissions: PermissionBits.VIEW,
    files: [
      { file_id: "shared", user: "owner" },
      { file_id: "unrelated", user: "owner" },
    ],
  };
  const context = {
    module: { exports: {} },
    require(name) {
      if (name === "@librechat/data-schemas") return { logger: { error() {} } };
      if (name === "librechat-data-provider")
        return {
          ResourceType,
          PermissionBits,
          hasPermissions,
          isEphemeralAgentId,
        };
      if (name === "@librechat/api")
        return { getRemoteAgentPermissions: async () => state.permissions };
      if (name === "~/server/services/PermissionService")
        return {
          checkPermission: async ({ requiredPermission }) =>
            hasPermissions(state.permissions, requiredPermission),
          getEffectivePermissions: async () => state.permissions,
        };
      if (name === "~/models")
        return {
          getAgent: async () => state.agent,
          getFiles: async () => state.files,
        };
      throw Error("Unexpected dependency in native permission helper");
    },
  };
  const filename = "/app/api/server/services/Files/permissions.js";
  vm.runInNewContext(fs.readFileSync(filename, "utf8"), context, { filename });
  return { state, ...context.module.exports };
}

test("shared agent VIEW permits only attached files with existing owner metadata", async () => {
  const f = fixture();
  const access = await f.hasAccessToFilesViaAgent({
    userId: "reader",
    role: "USER",
    fileIds: ["shared", "unrelated", "missing"],
    agentId: "agent_fixture",
  });
  assert.equal(access.get("shared"), true);
  assert.equal(access.get("unrelated"), false);
  assert.equal(access.get("missing"), false);
});

test("known file IDs do not grant access without current agent VIEW", async () => {
  const f = fixture();
  f.state.permissions = 0;
  const files = await f.filterFilesByAgentAccess({
    files: f.state.files,
    userId: "reader",
    agentId: "agent_fixture",
  });
  assert.equal(files.length, 0);
});

test("fresh permission check observes unshare and detachment", async () => {
  const f = fixture();
  const args = {
    files: f.state.files,
    userId: "reader",
    agentId: "agent_fixture",
  };
  assert.equal((await f.filterFilesByAgentAccess(args)).length, 1);
  f.state.permissions = 0;
  assert.equal((await f.filterFilesByAgentAccess(args)).length, 0);
  f.state.permissions = PermissionBits.VIEW;
  f.state.agent.tool_resources.file_search.file_ids = [];
  assert.equal((await f.filterFilesByAgentAccess(args)).length, 0);
});

test("agent EDIT required for deletion in addition to VIEW", async () => {
  const f = fixture();
  const args = {
    userId: "reader",
    fileIds: ["shared"],
    agentId: "agent_fixture",
    isDelete: true,
  };
  assert.equal((await f.hasAccessToFilesViaAgent(args)).get("shared"), false);
  f.state.permissions = PermissionBits.VIEW | PermissionBits.EDIT;
  assert.equal((await f.hasAccessToFilesViaAgent(args)).get("shared"), true);
});

test("owned file remains accessible while other owners require agent access", async () => {
  const f = fixture();
  f.state.permissions = 0;
  const files = [{ file_id: "mine", user: "reader" }, ...f.state.files];
  const allowed = await f.filterFilesByAgentAccess({
    files,
    userId: "reader",
    agentId: "agent_fixture",
  });
  assert.equal(allowed.length, 1);
  assert.equal(allowed[0].file_id, "mine");
});

test("helper alone passes through missing agent context and is not a complete authorization boundary", async () => {
  const f = fixture();
  f.state.permissions = 0;
  assert.equal(
    await f.filterFilesByAgentAccess({
      files: f.state.files,
      userId: "reader",
    }),
    f.state.files,
  );
});
