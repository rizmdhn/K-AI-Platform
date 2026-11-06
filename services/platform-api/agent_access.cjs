// Native ACL writes stay behind PermissionService; configuration supplies the only agent ID.
function createAgentAccess(deps) {
  const {
    agentContext,
    getBinding,
    getAgent,
    findUser,
    markCleanupPending,
    hasCapability,
    getConfig,
    resolveManagedAgent,
    getUserPrincipals,
    findEntriesByPrincipalsAndResource,
    checkPermission,
    grantPermission,
    bulkUpdateResourcePermissions,
    ResourceType,
    PrincipalType,
    PermissionBits,
    AccessRoleIds,
  } = deps;
  const fail = (status) =>
    Object.assign(new Error("Akses agent tidak tersedia."), { status });
  const tenant = (value) => (value == null ? null : String(value));
  const bindingRequired = () => Object.assign(new Error("Isi kredensial CH dulu"), {status: 409, code: "BINDING_REQUIRED"});
  const uuid = (value) => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
  const activeTarget = (target) => ["active", "existing"].includes(target.status);
  async function binding(req, target) {
    if (!activeTarget(target)) return {eligible: false};
    if (typeof getBinding !== "function") throw fail(503);
    const receipt = await getBinding({...req,headers:req.headers, body: {}});
    if (receipt?.version !== 1 || typeof receipt.eligible !== "boolean" ||
        (receipt.eligible ? !uuid(receipt.binding_id) || !uuid(receipt.revision) : receipt.binding_id !== null || receipt.revision !== null)) throw fail(503);
    return receipt;
  }
  async function canonicalAgent(config, target) {
    if (!config || typeof config !== "object") throw fail(503);
    const specs = config?.modelSpecs?.list?.filter((spec) => spec?.name === "kai-ch") ?? [];
    if (!specs.length) return null;
    const id = specs[0]?.preset?.agent_id;
    if (specs.length !== 1 || specs[0]?.preset?.endpoint !== "agents" || typeof id !== "string" || !/^agent_[A-Za-z0-9_-]{21}$/.test(id)) throw fail(503);
    const agent = await getAgent({id, tenantId: tenant(target.tenantId)});
    if (!agent || agent.id !== id || !agent._id || tenant(agent.tenantId) !== tenant(target.tenantId)) throw fail(503);
    return agent;
  }
  async function context(req, write) {
    const { actor, target } = await agentContext(req, write);
    if (
      !actor?.id ||
      !target?.id ||
      tenant(actor.tenantId) !== tenant(target.tenantId) ||
      (write && target.role === "ADMIN" && actor.role !== "ADMIN")
    )
      throw fail(403);
    const canManage =
      (await hasCapability(actor, "manage:agents")) === true &&
      (await hasCapability(actor, "manage:users")) === true &&
      (target.role !== "ADMIN" || actor.role === "ADMIN");
    if (write && !canManage) throw fail(403);
    const config = await getConfig(target);
    const specs =
      config?.modelSpecs?.list?.filter((spec) => spec?.name === "kai-ch") ?? [];
    if (!specs.length) return { actor, target, canManage, agent: null };
    if (specs.length !== 1 || specs[0]?.preset?.endpoint !== "agents")
      throw fail(503);
    const agent = await resolveManagedAgent(config, tenant(target.tenantId));
    if (!agent || agent.id !== specs[0].preset.agent_id || !agent._id)
      throw fail(503);
    return { actor, target, canManage, agent };
  }
  async function snapshot(ctx, receipt = {eligible: false}) {
    const { target, agent, canManage } = ctx;
    const empty = {
      configured: false,
      binding_active: receipt.eligible,
      agent: null,
      direct_assignment: false,
      effective_access: false,
      inherited_access: false,
      can_manage: canManage,
      can_remove_direct: false,
    };
    if (!agent) return { result: empty, direct: [] };
    const principals = await getUserPrincipals({
      userId: target.id,
      role: target.role,
    });
    const entries = await findEntriesByPrincipalsAndResource(
      principals,
      ResourceType.AGENT,
      agent._id,
    );
    if (!Array.isArray(entries)) throw fail(503);
    const direct = entries.filter(
      (entry) =>
        entry.principalType === PrincipalType.USER &&
        String(entry.principalId) === String(target.id) &&
        !entry.inheritedFrom,
    );
    const userEntries = entries.filter(
      (entry) =>
        entry.principalType === PrincipalType.USER &&
        String(entry.principalId) === String(target.id),
    );
    const view = (entry) =>
      (entry.permBits & PermissionBits.VIEW) === PermissionBits.VIEW;
    const effective = await checkPermission({
      userId: target.id,
      role: target.role,
      resourceType: ResourceType.AGENT,
      resourceId: agent._id,
      requiredPermission: PermissionBits.VIEW,
    });
    if (typeof effective !== "boolean") throw fail(503);
    return {
      direct,
      userEntries,
      result: {
        configured: true,
        binding_active: receipt.eligible,
        agent: {
          id: agent.id,
          name:
            typeof agent.name === "string" ? agent.name : "Agent ClickHouse",
          provider: agent.provider,
          model: agent.model,
          tools: agent.tools,
        },
        direct_assignment: direct.some(view),
        effective_access: effective,
        inherited_access:
          String(agent.author) === String(target.id) ||
          entries.some((entry) => !direct.includes(entry) && view(entry)),
        can_manage: canManage,
        can_remove_direct:
          canManage &&
          direct.length > 0 &&
          userEntries.length === direct.length &&
          direct.every((entry) => entry.permBits === PermissionBits.VIEW) &&
          String(agent.author) !== String(target.id),
      },
    };
  }
  async function run(req, write) {
    try {
      if (
        write &&
        (!req.body ||
          Object.keys(req.body).sort().join(",") !== "agent_id,assigned" ||
          typeof req.body.agent_id !== "string" ||
          typeof req.body.assigned !== "boolean")
      )
        throw fail(400);
      const ctx = await context(req, write);
      const receipt = ctx.agent ? await binding(req, ctx.target) : {eligible: false};
      const before = await snapshot(ctx, receipt);
      if (!write) return before.result;
      if (!ctx.agent || req.body.agent_id !== ctx.agent.id) throw fail(400);
      if (req.body.assigned && !receipt.eligible) throw bindingRequired();
      // Native standalone ACL writes have no transaction: concurrent native role edits can race this check.
      const params = {
        resourceType: ResourceType.AGENT,
        resourceId: ctx.agent._id,
        grantedBy: ctx.actor.id,
      };
      if (req.body.assigned && !before.result.direct_assignment) {
        // Preserve any existing non-viewer entry rather than replacing its native role.
        if (before.userEntries.length) throw fail(409);
        const granted = await grantPermission({
          ...params,
          principalType: PrincipalType.USER,
          principalId: ctx.target.id,
          accessRoleId: AccessRoleIds.AGENT_VIEWER,
        });
        if (!granted) throw fail(503);
        try {
          const afterBinding = await binding(req, ctx.target);
          const fresh = await context(req, true);
          if (!activeTarget(fresh.target) || !afterBinding.eligible || afterBinding.binding_id !== receipt.binding_id ||
              afterBinding.revision !== receipt.revision || String(fresh.agent?._id) !== String(ctx.agent._id)) throw bindingRequired();
          return (await snapshot(fresh, afterBinding)).result;
        } catch {
          // Bulk revocation is safe only while this request's new viewer is still the sole user ACL.
          await cleanupContext(ctx);
          throw bindingRequired();
        }
      } else if (!req.body.assigned && before.direct.length) {
        if (!before.result.can_remove_direct) throw fail(409);
        const result = await bulkUpdateResourcePermissions({
          ...params,
          updatedPrincipals: [],
          revokedPrincipals: [{ type: PrincipalType.USER, id: ctx.target.id }],
        });
        if (!result || !Array.isArray(result.errors) || result.errors.length)
          throw fail(503);
      }
      const fresh = await context(req, false);
      return (await snapshot(fresh, fresh.agent ? await binding(req, fresh.target) : {eligible:false})).result;
    } catch (error) {
      if (error?.message === "Isi kredensial CH dulu" && error.status === 409) throw bindingRequired();
      throw fail([400, 403, 409].includes(error?.status) ? error.status : 503);
    }
  }
  async function pending(target, value) {
    if (typeof markCleanupPending !== "function") {
      if (value) throw fail(503);
      return;
    }
    await markCleanupPending(target.id, value);
  }
  async function cleanupContext(ctx) {
    let complete = false;
    try {
      if (!ctx.agent) complete = true;
      else {
        const before = await snapshot(ctx);
        if (!before.userEntries.length && String(ctx.agent.author) !== String(ctx.target.id)) complete = true;
        else if (before.result.can_remove_direct) {
          const result = await bulkUpdateResourcePermissions({
            resourceType: ResourceType.AGENT, resourceId: ctx.agent._id, grantedBy: ctx.actor.id,
            updatedPrincipals: [], revokedPrincipals: [{type: PrincipalType.USER, id: ctx.target.id}],
          });
          if (result && Array.isArray(result.errors) && !result.errors.length) {
            const after = await snapshot(ctx);
            complete = !after.userEntries.length;
          }
        }
      }
    } catch { complete = false; }
    await pending(ctx.target, !complete);
    return {cleanup_complete: complete};
  }
  async function revokeManaged(req) {
    // Cleanup needs user administration authority, but must not depend on a live model or owner.
    let ctx;
    try {
      const {actor, target} = await agentContext({...req,headers:req.headers,body:{}}, false);
      if (!actor?.id || !target?.id || tenant(actor.tenantId) !== tenant(target.tenantId) ||
          (target.role === "ADMIN" && actor.role !== "ADMIN") || await hasCapability(actor,"manage:users") !== true) throw fail(403);
      ctx = {actor,target,canManage:true,agent:null};
      ctx.agent = await canonicalAgent(await getConfig(target),target);
      return await cleanupContext(ctx);
    } catch (error) {
      if (error?.status === 403) throw fail(403);
      if (!ctx) throw fail(503);
      await pending(ctx.target,true);
      return {cleanup_complete:false};
    }
  }
  async function guardNativeShare(req, res, next) {
    if (![ResourceType.AGENT, ResourceType.REMOTE_AGENT].includes(req.params?.resourceType)) return next();
    try {
      const actor = await findUser({_id:req.user?.id}, "_id role tenantId kaiAccount.status deleted deletedAt disabled active");
      if (!actor || String(actor._id) !== req.user?.id || tenant(actor.tenantId) !== tenant(req.user?.tenantId) || actor.deleted || actor.deletedAt || actor.disabled || actor.active === false || !["active","existing"].includes(actor.kaiAccount?.status ?? "existing")) throw fail(403);
      const target = {...actor,id:String(actor._id)};
      const agent = await canonicalAgent(await getConfig(target),target);
      if (agent && String(agent._id) === req.params.resourceId) return res.status(409).json({message:"Atur akses chatbot CH melalui detail pengguna."});
      return next();
    } catch (error) {
      return res.status(error?.status === 403 ? 403 : 503).json({message:"Akses agent tidak tersedia."});
    }
  }
  return { get: (req) => run(req, false), set: (req) => run(req, true), revokeManaged, guardNativeShare };
}
module.exports = { createAgentAccess };
