const db = require("~/models");
const permissions = require("~/server/services/PermissionService");
const { getAppConfig } = require("~/server/services/Config");
const { hasCapability } = require("~/server/middleware/roles/capabilities");
const {
  ResourceType,
  PrincipalType,
  PermissionBits,
  AccessRoleIds,
  Constants,
} = require("librechat-data-provider");
const accounts = require("./runtime.cjs");
const mongoose = require("mongoose");
const warehouse = require("./warehouse_proxy.cjs").createWarehouseProxy({accounts});
const { resolveManagedAgent } = require("./managed_agents.cjs");

module.exports = require("./agent_access.cjs").createAgentAccess({
  agentContext: accounts.agentContext,
  getBinding: (req) => warehouse.eligibility({...req,headers:req.headers, body:{}}),
  getAgent: db.getAgent,
  findUser: db.findUser,
  markCleanupPending: async (targetId, pending) => {
    if (!mongoose.isValidObjectId(targetId) || typeof pending !== "boolean") throw new Error("Invalid cleanup state");
    const result = await mongoose.model("User").collection.updateOne(
      {_id: new mongoose.Types.ObjectId(targetId)},
      {$set:{"kaiAccount.chAclCleanupPending":pending}},
    );
    if (result.matchedCount !== 1) throw new Error("Cleanup state unavailable");
  },
  hasCapability,
  getConfig: (target) =>
    getAppConfig({
      role: target.role,
      tenantId: target.tenantId,
      userId: target.id,
    }),
  resolveManagedAgent: (config, tenantId) =>
    resolveManagedAgent(config, tenantId, {
      getAgent: db.getAgent,
      findUser: db.findUser,
      delimiter: Constants.mcp_delimiter,
    }),
  getUserPrincipals: db.getUserPrincipals,
  findEntriesByPrincipalsAndResource: db.findEntriesByPrincipalsAndResource,
  checkPermission: permissions.checkPermission,
  grantPermission: permissions.grantPermission,
  bulkUpdateResourcePermissions: permissions.bulkUpdateResourcePermissions,
  ResourceType,
  PrincipalType,
  PermissionBits,
  AccessRoleIds,
});
