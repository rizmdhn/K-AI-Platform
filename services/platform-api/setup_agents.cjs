#!/usr/bin/env node
// Operator-only entrypoint; importing it does not initialize native services or connect.
const fs = require('node:fs');
const BACKUP = '/tmp/kai-agent-model-specs-before.json';
const fail = () => new Error('Managed agent setup failed. Check owner, capabilities and native configuration.');
const active = user => user && !user.deleted && !user.deletedAt && !user.disabled && user.active !== false && ['active', 'existing'].includes(user.kaiAccount?.status ?? 'existing');
function parseArgs(args) {
  let apply = false, mode, owner;
  for (let i = 0; i < args.length; i++) {
    if (['--check', '--apply'].includes(args[i]) && !mode) {
      mode = args[i]; apply = mode === '--apply';
    } else if (args[i] === '--owner' && owner === undefined && /^[a-f0-9]{24}$/.test(args[i + 1] ?? '')) owner = args[++i];
    else throw fail();
  }
  return { apply, owner };
}
const summarize = agent => ({ id: agent.id, name: agent.name, provider: agent.provider, model: agent.model, tools: agent.tools ?? [] });
function createAdapter({ db, mongoose, permissions, capabilities, configService, api, native, nanoid, brand, beforeWrite }) {
  const { ResourceType, PrincipalType, AccessRoleIds, Constants } = native;
  const chTools = ['list_databases', 'list_tables', 'run_query', 'get_table_schema', 'execute_readonly_sql', 'search_database_metadata', 'search_rows_by_meaning', 'suggest_comment_sql', 'start_row_labeling', 'get_row_labeling_job'].map(name => `${name}${Constants.mcp_delimiter}clickhouse`);
  const getConfig = actor => configService.getAppConfig({ tenantId: actor.tenantId, failClosed: true, skipRuntimeAugmentation: true });
  const handlers = api.createAdminConfigHandlers({
    ...Object.fromEntries(['listAllConfigs', 'findConfigByPrincipal', 'upsertConfig', 'patchConfigFields', 'tombstoneConfigField', 'unsetConfigField', 'deleteConfig', 'toggleConfigActive'].map(key => [key, db[key]])),
    ...Object.fromEntries(['hasAnyConfigReadAccess', 'getReadableConfigSections', 'hasConfigCapability', 'hasCapability'].map(key => [key, capabilities[key]])),
    getAppConfig: configService.getAppConfig, invalidateConfigCaches: configService.invalidateConfigCaches,
  });
  async function grant(agent, actor, principalType, principalId, resourceType, accessRoleId) {
    const role = await db.findRoleByIdentifier(accessRoleId);
    if (!role || role.resourceType !== resourceType) throw fail();
    const queryPrincipalId = principalType === PrincipalType.USER ? new mongoose.Types.ObjectId(principalId) : principalId;
    const entries = await db.findEntriesByPrincipalsAndResource([{ principalType, principalId: queryPrincipalId }], resourceType, agent._id);
    if (!Array.isArray(entries)) throw fail();
    if (entries.some(entry => String(entry.roleId) === String(role._id) || (entry.permBits & role.permBits) === role.permBits)) return;
    // Do not replace any incomparable existing role with a narrower one.
    if (entries.some(entry => (entry.permBits & role.permBits) !== entry.permBits)) throw fail();
    await beforeWrite();
    if (!await permissions.grantPermission({ principalType, principalId, resourceType, resourceId: agent._id, accessRoleId, grantedBy: actor.id })) throw fail();
  }
  return {
    findUser: id => db.findUser({ _id: id }), hasCapability: capabilities.hasCapability, getConfig,
    getAgent: id => typeof id === 'string' ? db.getAgent({ id }) : null,
    findCandidates: (name, author, tenantId) => mongoose.model('Agent').find({ name, author, tenantId: tenantId ?? null }).limit(2).lean(),
    async createAgent(data, actor, config) {
      const { author, tenantId, ...input } = data;
      const allowed = chTools;
      if (!Array.isArray(input.tools) || input.tools.some(tool => !allowed.includes(tool) && tool !== 'file_search')) throw fail();
      const mcp = input.tools.some(tool => allowed.includes(tool));
      // Config-source proof only: no MCP registry, discovery, connection or metadata call.
      if (mcp && !config.mcpConfig?.clickhouse) throw fail();
      const parsed = api.agentCreateSchema.parse(input);
      await beforeWrite();
      return db.createAgent({ ...parsed, id: `agent_${nanoid()}`, author: actor.id, ...(actor.tenantId ? { tenantId: actor.tenantId } : {}), mcpServerNames: mcp ? ['clickhouse'] : [] });
    },
    // Only the managed upgrade fields: tools ([file_search] for General, P2-10; the full CH set), provider+model,
    // or the presentation/guardrail fields (validated by the native update schema).
    async updateAgent(agent, changes, actor) {
      const keys = Object.keys(changes ?? {});
      // Provider/model move (inference switch, e.g. Gemini on Vertex): both together, plain strings.
      if (keys.length === 2 && keys.includes('provider') && keys.includes('model')) {
        if (![changes.provider, changes.model].every(v => typeof v === 'string' && /^[A-Za-z0-9][A-Za-z0-9 ._:\/@-]{0,199}$/.test(v))) throw fail();
        await beforeWrite();
        return db.updateAgent({ id: agent.id }, changes, { updatingUserId: actor.id });
      }
      const shown = ['name', 'description', 'instructions', 'conversation_starters', 'support_contact'];
      if (keys.length && keys.every(key => shown.includes(key))) {
        if (typeof changes.instructions === 'string' && changes.instructions.length > 8000) throw fail();
        const parsed = api.agentUpdateSchema.parse(changes);
        if (JSON.stringify(Object.keys(parsed).sort()) !== JSON.stringify([...keys].sort())) throw fail();
        await beforeWrite();
        return db.updateAgent({ id: agent.id }, parsed, { updatingUserId: actor.id });
      }
      if (keys.length !== 1 || keys[0] !== 'tools') throw fail();
      if (keys[0] === 'tools' && ![['file_search'], chTools].some(set => JSON.stringify([...changes.tools ?? []].sort()) === JSON.stringify([...set].sort()))) throw fail();
      await beforeWrite();
      return db.updateAgent({ id: agent.id }, changes, { updatingUserId: actor.id });
    },
    async validateWarehouseAccess(agent, actor, { registered }) {
      for (const resourceType of [ResourceType.AGENT, ResourceType.REMOTE_AGENT]) {
        const entries = await db.findEntriesByResource(resourceType, agent._id);
        if (!Array.isArray(entries)) throw fail();
        const ownerRole = await db.findRoleByIdentifier(resourceType === ResourceType.AGENT ? AccessRoleIds.AGENT_OWNER : AccessRoleIds.REMOTE_AGENT_OWNER);
        if (!ownerRole) throw fail();
        for (const entry of entries) {
          if (entry.principalType !== PrincipalType.USER || entry.inheritedFrom) throw fail();
          if (String(entry.principalId) === actor.id) {
            if (String(entry.roleId) !== String(ownerRole._id)) throw fail();
          } else if (!registered) throw fail();
        }
      }
    },
    async ensureOwners(agent, actor) {
      await grant(agent, actor, PrincipalType.USER, actor.id, ResourceType.AGENT, AccessRoleIds.AGENT_OWNER);
      await grant(agent, actor, PrincipalType.USER, actor.id, ResourceType.REMOTE_AGENT, AccessRoleIds.REMOTE_AGENT_OWNER);
    },
    async ensureGeneralView(agent, actor) {
      for (const role of ['USER', 'ADMIN']) await grant(agent, actor, PrincipalType.ROLE, role, ResourceType.AGENT, AccessRoleIds.AGENT_VIEWER);
    },
    async patchSpecs(list, actor) {
      const existing = await db.findConfigByPrincipal('role', '__base__', { includeInactive: true });
      if (existing && existing.active === false) throw fail();
      await beforeWrite();
      let status = 200, sent = false;
      const res = { status(code) { status = code; return this; }, json() { sent = true; return this; } };
      await handlers.patchConfigField({ user: actor, config: await getConfig(actor), tenantId: actor.tenantId, params: { principalType: 'role', principalId: '__base__' }, body: { entries: [{ fieldPath: 'modelSpecs.list', value: list }] } }, res);
      if (!sent || status < 200 || status >= 300) throw fail();
      await configService.invalidateConfigCaches(actor.tenantId);
      if (JSON.stringify((await getConfig(actor)).modelSpecs?.list) !== JSON.stringify(list)) throw fail();
    },
    delimiter: Constants.mcp_delimiter, brand,
    target: { provider: process.env.KAI_AGENT_PROVIDER, generalModel: process.env.KAI_GENERAL_AGENT_MODEL, chModel: process.env.KAI_CH_AGENT_MODEL },
  };
}
async function run(options, runtime) {
  const { mongoose, capabilities, tenantStorage } = runtime;
  let owner;
  if (options.owner) owner = await runtime.db.findUser({ _id: options.owner });
  else {
    const admins = await mongoose.model('User').find({ role: 'ADMIN' }).select('_id role tenantId deleted deletedAt disabled active kaiAccount.status').lean();
    const eligible = admins.filter(active);
    if (eligible.length !== 1) throw fail();
    owner = eligible[0];
  }
  if (!active(owner) || owner.role !== 'ADMIN') throw fail();
  const actor = { id: String(owner._id), role: owner.role, tenantId: owner.tenantId };
  const execute = async () => {
    for (const cap of ['access:admin', 'manage:agents', 'manage:configs']) if (!await capabilities.hasCapability(actor, cap)) throw fail();
    let changed = false, original;
    const deps = createAdapter({ ...runtime, beforeWrite: async () => {
      if (!options.apply) throw fail();
      if (!changed) {
        // Exclusive creation prevents a repeated run from replacing the original snapshot.
        try { fs.writeFileSync(BACKUP, JSON.stringify(original, null, 2), { mode: 0o600, flag: 'wx' }); }
        catch (error) {
          if (error.code !== 'EEXIST') throw fail();
          const existing = fs.lstatSync(BACKUP);
          if (!existing.isFile() || existing.nlink !== 1 || existing.uid !== process.getuid() || (existing.mode & 0o777) !== 0o600) throw fail();
        }
      }
      changed = true;
    } });
    const config = await deps.getConfig(actor);
    original = config.modelSpecs?.list;
    if (!Array.isArray(original)) throw fail();
    const existingBase = await runtime.db.findConfigByPrincipal('role', '__base__', { includeInactive: true });
    if (existingBase?.active === false) throw fail();
    if (options.apply) {
      const result = await require('./managed_agent_setup.cjs').provisionManagedAgents(actor.id, deps);
      return { changed, agents: [summarize(result.general), summarize(result.warehouse)] };
    }
    const agents = [];
    for (const spec of original.filter(spec => ['kai-assistant', 'kai-ch'].includes(spec.name))) {
      const agent = spec.preset?.endpoint === 'agents' ? await deps.getAgent(spec.preset.agent_id) : null;
      agents.push(agent ? summarize(agent) : { id: null, name: spec.label ?? spec.name, provider: spec.preset?.endpoint ?? null, model: spec.preset?.model ?? null, tools: [] });
    }
    return { changed: false, agents };
  };
  return actor.tenantId ? tenantStorage.run({ ...tenantStorage.getStore(), tenantId: actor.tenantId }, execute) : execute();
}
async function main(args) {
  const options = parseArgs(args);
  require('module-alias').addAlias('~', '/app/api');
  const mongoose = require('mongoose');
  // Prevent implicit index/collection writes in the default read-only mode.
  mongoose.set('autoIndex', false); mongoose.set('autoCreate', false);
  const schemas = require('@librechat/data-schemas');
  schemas.logger.silent = true;
  schemas.createModels(mongoose);
  if (!process.env.MONGO_URI) throw fail();
  try {
    await mongoose.connect(process.env.MONGO_URI, { autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 10000 });
    const result = await run(options, { mongoose, db: require('~/models'), permissions: require('~/server/services/PermissionService'), capabilities: require('~/server/middleware/roles/capabilities'), configService: require('~/server/services/Config'), api: require('@librechat/api'), native: require('librechat-data-provider'), nanoid: require('nanoid').nanoid, tenantStorage: schemas.tenantStorage, brand: process.env.APP_TITLE || 'K-AI platform' });
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } finally { await mongoose.disconnect(); }
}
if (require.main === module) {
  // Native libraries can log configuration errors; expose only the sanitized CLI result.
  console.log = console.info = console.warn = console.error = () => {};
  main(process.argv.slice(2)).then(() => process.exit(0), () => { process.stderr.write(`${fail().message}\n`); process.exit(1); });
}
module.exports = { parseArgs, createAdapter, run };
