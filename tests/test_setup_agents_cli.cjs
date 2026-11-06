const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseArgs, createAdapter, run } = require('../services/platform-api/setup_agents.cjs');
const owner = { _id: '1234567890abcdef12345678', role: 'ADMIN' };
function fixture() {
  const calls = [];
  const state = { entries: [], base: null, config: { modelSpecs: { list: [{ name: 'kai-assistant', preset: { endpoint: 'openAI', model: 'fixture' } }] }, mcpConfig: { clickhouse: {} } } };
  const runtime = {
    db: { findUser: async () => owner, findConfigByPrincipal: async () => state.base, findRoleByIdentifier: async id => ({ _id: id, resourceType: id.startsWith('remote') ? 'remote' : 'agent', permBits: id.includes('owner') ? 15 : 1 }), findEntriesByResource: async () => state.entries, findEntriesByPrincipalsAndResource: async () => state.entries, createAgent: async data => { calls.push(['create', data]); return data; } },
    mongoose: { Types: { ObjectId: class { constructor(value) { this.value = value; } } }, model: () => ({ find: () => ({ select: () => ({ lean: async () => [owner] }) }) }) },
    permissions: { grantPermission: async data => { calls.push(['grant', data]); return {}; } },
    capabilities: { hasCapability: async () => true },
    configService: { getAppConfig: async options => { assert.equal(options.role, undefined); assert.equal(options.userId, undefined); return state.config; }, invalidateConfigCaches: async () => calls.push(['invalidate']) },
    api: { agentCreateSchema: { parse: data => { assert.equal(data.author, undefined); assert.equal(data.tenantId, undefined); return data; } }, agentUpdateSchema: { parse: data => Object.fromEntries(Object.entries(data).filter(([k]) => ['name', 'description', 'instructions', 'conversation_starters', 'support_contact'].includes(k))) }, createAdminConfigHandlers: () => ({ patchConfigField: async (req, res) => { calls.push(['patch', req]); state.config.modelSpecs.list = req.body.entries[0].value; res.status(200).json({}); } }) },
    native: { Constants: { mcp_delimiter: '__' }, ResourceType: { AGENT: 'agent', REMOTE_AGENT: 'remote' }, PrincipalType: { USER: 'user', ROLE: 'role' }, AccessRoleIds: { AGENT_OWNER: 'owner', REMOTE_AGENT_OWNER: 'remote-owner', AGENT_VIEWER: 'viewer' } },
    nanoid: () => 'abcdefghijklmnopqrstu', brand: 'Fixture', beforeWrite: async () => calls.push(['write']),
    tenantStorage: { getStore: () => ({}), run: (scope, fn) => fn() },
  };
  return { runtime, state, calls, deps: createAdapter(runtime) };
}
test('default is check and unknown, duplicate or malformed arguments fail closed', () => {
  assert.deepEqual(parseArgs([]), { apply: false, owner: undefined });
  assert.equal(parseArgs(['--apply', '--owner', owner._id]).apply, true);
  for (const args of [['--bad'], ['--owner', 'wrong'], ['--check', '--apply'], ['--owner', owner._id, '--owner', owner._id]]) assert.throws(() => parseArgs(args));
});
test('check only reads and emits limited curated fields', async () => {
  const f = fixture();
  assert.deepEqual(await run(parseArgs([]), f.runtime), { changed: false, agents: [{ id: null, name: 'kai-assistant', provider: 'openAI', model: 'fixture', tools: [] }] });
  assert.deepEqual(f.calls, []);
});
test('creation uses schema before native write and rejects tools outside exact whitelist', async () => {
  const f = fixture(), actor = { id: owner._id, role: 'ADMIN' };
  const data = { name: 'Fixture', provider: 'openAI', model: 'fixture', author: owner._id, tools: ['run_query__clickhouse'] };
  await f.deps.createAgent(data, actor, f.state.config);
  assert.equal(f.calls[1][1].author, owner._id);
  assert.equal(f.calls[1][1].id, 'agent_abcdefghijklmnopqrstu');
  await assert.rejects(f.deps.createAgent({ ...data, tools: ['write__clickhouse'] }, actor, f.state.config));
  await assert.rejects(f.deps.createAgent(data, actor, {}));
  // P2-10: file_search alone needs no MCP config and links no MCP server; other built-in tools stay rejected.
  await f.deps.createAgent({ ...data, tools: ['file_search'] }, actor, {});
  assert.deepEqual(f.calls.at(-1)[1].mcpServerNames, []);
  await assert.rejects(f.deps.createAgent({ ...data, tools: ['execute_code'] }, actor, f.state.config));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { tools: ['execute_code'] }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { tools: ['file_search'], model: 'other' }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { provider: 'other' }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { tools: ['run_query__clickhouse', 'file_search'] }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { provider: 'google', model: 'm', tools: [] }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { provider: 'google', model: '' }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { provider: '$where', model: 'm' }, actor));
  // Presentation fields only, never identity/ownership fields mixed in.
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { name: 'n', author: 'someone' }, actor));
  await assert.rejects(f.deps.updateAgent({ id: 'x' }, { instructions: 'x'.repeat(8001) }, actor));
});
test('owner grants are idempotent and viewer grants preserve stronger roles', async () => {
  const f = fixture(), agent = { _id: 'db-id' }, actor = { id: owner._id };
  await f.deps.ensureOwners(agent, actor);
  assert.deepEqual(f.calls.filter(c => c[0] === 'grant').map(c => c[1].resourceType), ['agent', 'remote']);
  f.calls.length = 0; f.state.entries = [{ roleId: 'owner', permBits: 15 }];
  await f.deps.ensureGeneralView(agent, actor);
  await f.deps.ensureOwners(agent, actor);
  assert.deepEqual(f.calls, []);
});
test('config patch targets only base list, invalidates and rejects inactive base before write', async () => {
  const f = fixture(), actor = { id: owner._id, role: 'ADMIN' }, list = [{ name: 'fixture' }];
  await f.deps.patchSpecs(list, actor);
  assert.deepEqual(f.calls.find(c => c[0] === 'patch')[1].params, { principalType: 'role', principalId: '__base__' });
  assert.ok(f.calls.some(c => c[0] === 'invalidate'));
  f.calls.length = 0; f.state.base = { active: false };
  await assert.rejects(f.deps.patchSpecs(list, actor));
  assert.deepEqual(f.calls, []);
});

test('warehouse ACL validation rejects role, public, inherited and unregistered user shares', async () => {
  const f = fixture(), agent = { _id: 'db-id' }, actor = { id: owner._id };
  for (const entry of [{ principalType: 'role', principalId: 'USER' }, { principalType: 'public' }, { principalType: 'user', principalId: owner._id, inheritedFrom: 'group' }]) {
    f.state.entries = [entry];
    for (const registered of [false, true]) await assert.rejects(f.deps.validateWarehouseAccess(agent, actor, { registered }));
  }
  f.state.entries = [{ principalType: 'user', principalId: 'other' }];
  await assert.rejects(f.deps.validateWarehouseAccess(agent, actor, { registered: false }));
  await f.deps.validateWarehouseAccess(agent, actor, { registered: true });
  assert.deepEqual(f.calls, []);
});
