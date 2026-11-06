const { test } = require("node:test");
const assert = require("node:assert/strict");
const { createAccounts } = require("../services/platform-api/accounts.cjs");
const actorId = "a".repeat(24),
  targetId = "b".repeat(24);
function fixture({
  caps = ["access:admin", "read:users", "manage:users"],
  target = {},
  actor = {},
} = {}) {
  const users = [
    { _id: actorId, role: "ADMIN", tenantId: "one", ...actor },
    {
      _id: targetId,
      role: "USER",
      tenantId: "one",
      kaiAccount: {
        status: "pending",
        chService: "ch.example",
        chUsername: "Analyst",
      },
      ...target,
    },
  ];
  const collection = {
    findOne: async (q) =>
      users.find((u) => Object.entries(q).every(([k, v]) => u[k] === v)),
  };
  const User = { schema: { path: () => true }, collection };
  const mongoose = {
    model: () => User,
    Types: {
      ObjectId: class {
        constructor(v) {
          return new String(v);
        }
      },
    },
  };
  // Synthetic ObjectIds compare by value, as Mongo would.
  collection.findOne = async (q) =>
    users.find((u) =>
      Object.entries(q).every(
        ([k, v]) => String(u[k] ?? "") === String(v ?? ""),
      ),
    );
  return {
    accounts: createAccounts({
      mongoose,
      hasCapability: async (_p, c) => caps.includes(c),
    }),
    users,
  };
}
const req = (body = {}) => ({
  user: { id: actorId },
  params: { id: targetId },
  body,
  headers: { authorization: "Bearer native-token" },
});
test("agent context exposes fresh scoped identities without warehouse credentials", async () => {
  const f = fixture();
  assert.equal(typeof f.accounts.agentContext, "function");
  assert.deepEqual(
    await f.accounts.agentContext(
      req({ agent_id: "agent_test", assigned: true }),
      true,
    ),
    {
      actor: { id: actorId, role: "ADMIN", tenantId: "one" },
      target: {
        id: targetId,
        role: "USER",
        tenantId: "one",
        status: "pending",
      },
    },
  );
  f.users[1].tenantId = "other";
  await assert.rejects(
    f.accounts.agentContext(req(), true),
    (e) => e.status === 404,
  );
  await assert.rejects(
    fixture({
      actor: { role: "CUSTOM" },
      target: { role: "ADMIN" },
    }).accounts.agentContext(req(), true),
    (e) => e.status === 403,
  );
  await assert.rejects(
    fixture({ caps: [] }).accounts.agentContext(req()),
    (e) => e.status === 403,
  );
});
test("native context is fresh, tenant scoped and permits pending preparation without secrets", async () => {
  const { accounts, users } = fixture();
  assert.equal(typeof accounts.warehouseContext, "function");
  assert.deepEqual(await accounts.warehouseContext(req(), true), {
    actor_id: actorId,
    target_id: targetId,
    status: "pending",
    warehouse: { service: "ch.example", username: "Analyst" },
  });
  users[1].tenantId = "other";
  await assert.rejects(
    accounts.warehouseContext(req()),
    (e) => e.status === 404,
  );
});
test("native context enforces fresh capabilities and ADMIN restrictions", async () => {
  await assert.rejects(
    fixture({ caps: ["access:admin", "read:users"] }).accounts.warehouseContext(
      req(),
      true,
    ),
    (e) => e.status === 403,
  );
  await assert.rejects(
    fixture({ caps: ["read:users"] }).accounts.warehouseContext(req()),
    (e) => e.status === 403,
  );
  await assert.rejects(
    fixture({
      actor: { role: "CUSTOM" },
      target: { role: "ADMIN" },
    }).accounts.warehouseContext(req(), true),
    (e) => e.status === 403,
  );
  await assert.rejects(
    fixture({
      actor: { kaiAccount: { status: "pending" } },
    }).accounts.warehouseContext(req()),
    (e) => e.status === 401,
  );
  await assert.rejects(
    fixture().accounts.warehouseContext(req({ host: "injected" }), true),
    (e) => e.status === 400,
  );
});
test("internal lookup requires private token and denies pending or missing users", async () => {
  const { accounts, users } = fixture();
  const previous = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
  const transport = process.env.CLICKHOUSE_MCP_AUTH_TOKEN;
  process.env.KAI_PLATFORM_INTERNAL_TOKEN = "private-test-token";
  process.env.CLICKHOUSE_MCP_AUTH_TOKEN = "transport-only-token";
  try {
    await assert.rejects(
      accounts.internalWarehouseUser(req()),
      (e) => e.status === 401,
    );
    const wrongDirection = req();
    wrongDirection.headers.authorization = "Bearer transport-only-token";
    await assert.rejects(
      accounts.internalWarehouseUser(wrongDirection),
      (e) => e.status === 401,
    );
    const r = req();
    r.headers.authorization = "Bearer private-test-token";
    await assert.rejects(
      accounts.internalWarehouseUser(r),
      (e) => e.status === 403,
    );
    users[1].kaiAccount.status = "active";
    assert.deepEqual(await accounts.internalWarehouseUser(r), {
      id: targetId,
      status: "active",
      warehouse: { service: "ch.example", username: "Analyst" },
    });
    users.pop();
    await assert.rejects(
      accounts.internalWarehouseUser(r),
      (e) => e.status === 404,
    );
  } finally {
    if (previous === undefined) delete process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    else process.env.KAI_PLATFORM_INTERNAL_TOKEN = previous;
    if (transport === undefined) delete process.env.CLICKHOUSE_MCP_AUTH_TOKEN;
    else process.env.CLICKHOUSE_MCP_AUTH_TOKEN = transport;
  }
});
test("proxy forwards only fixed paths and native bearer, with no redirects or secret echo", async () => {
  const {
    createWarehouseProxy,
  } = require("../services/platform-api/warehouse_proxy.cjs");
  const calls = [];
  const proxy = createWarehouseProxy({
    accounts: fixture().accounts,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return new Response(
        JSON.stringify({
          state: "active",
          service: { host: "ch.example", port: 8443 },
          ch_username: "Analyst",
          owner_approved: true,
          data_owner_approved: true,
          can_approve_owner: false,
          can_approve_data: false,
          credentials_saved: true,
          reference_matches: true,
          password: "NEVER",
        }),
      );
    },
  });
  const result = await proxy.credentials(req({ password: " one-time " }));
  assert.equal(
    calls[0].url,
    `http://clickhouse-mcp:8000/v1/admin/warehouse-users/${targetId}/credentials`,
  );
  assert.equal(calls[0].options.redirect, "error");
  assert.equal(calls[0].options.headers.Authorization, "Bearer native-token");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    password: " one-time ",
  });
  assert.ok(calls[0].options.signal);
  assert.ok(!JSON.stringify(result).includes("NEVER"));
  for (const body of [
    { password: "a", host: "evil" },
    { password: "\0" },
    { password: "é".repeat(2049) },
    {},
  ])
    await assert.rejects(proxy.credentials(req(body)), (e) => e.status === 400);
  assert.equal(proxy.approve,undefined);
});
test("proxy sanitizes failures and bounds upstream responses", async () => {
  const {
    createWarehouseProxy,
  } = require("../services/platform-api/warehouse_proxy.cjs");
  for (const response of [
    new Response("secret password native-token", { status: 403 }),
    new Response("x".repeat(65537)),
    new Response("{}", {
      status: 302,
      headers: { Location: "https://evil.example" },
    }),
  ]) {
    const proxy = createWarehouseProxy({
      accounts: fixture().accounts,
      fetchImpl: async () => response,
    });
    await assert.rejects(
      proxy.status(req()),
      (e) => !e.message.includes("secret") && e.status >= 400,
    );
  }
});
test("native context denies deleted actor and missing target, but read does not require manage", async () => {
  const deleted = fixture({
    actor: { kaiAccount: { status: "deleted" } },
  }).accounts;
  await assert.rejects(
    deleted.warehouseContext(req()),
    (e) => e.status === 401,
  );
  const f = fixture({
    caps: ["access:admin", "read:users"],
    actor: { role: "CUSTOM" },
    target: { role: "ADMIN", kaiAccount: undefined },
  });
  assert.equal((await f.accounts.warehouseContext(req())).status, "existing");
  f.users.pop();
  await assert.rejects(
    f.accounts.warehouseContext(req()),
    (e) => e.status === 404,
  );
});
test("proxy rejects extra fields on every write and never forwards unauthorized requests", async () => {
  const {
    createWarehouseProxy,
  } = require("../services/platform-api/warehouse_proxy.cjs");
  let called = false;
  const proxy = createWarehouseProxy({
    accounts: fixture({ caps: [] }).accounts,
    fetchImpl: async () => {
      called = true;
      throw Error("should not call");
    },
  });
  await assert.rejects(proxy.prepare(req({})), (e) => e.status === 403);
  for (const action of ["prepare", "revoke", "credentials"])
    await assert.rejects(
      proxy[action](req({ unexpected: "secret" })),
      (e) => e.status === 400,
    );
  assert.equal(called, false);
});

test("proxy requires reference matching metadata instead of silently approving stale identity", async () => {
  const {
    createWarehouseProxy,
  } = require("../services/platform-api/warehouse_proxy.cjs");
  const missing = {
    state: "active",
    service: { host: "ch.example", port: 8443 },
    ch_username: "Old",
    owner_approved: true,
    data_owner_approved: true,
    can_approve_owner: false,
    can_approve_data: false,
    credentials_saved: true,
  };
  const proxy = createWarehouseProxy({
    accounts: fixture().accounts,
    fetchImpl: async () => new Response(JSON.stringify(missing)),
  });
  await assert.rejects(proxy.status(req()), (e) => e.status === 503);
});

test('readiness is read-only, bounded and never forwards private diagnostics',async()=>{
 const {createWarehouseProxy}=require('../services/platform-api/warehouse_proxy.cjs');
 const names=['binding_dsn','enrollment_dsn','distinct_runtime_users','keyring','warehouse_target'];
 const codes=['configured','configured','distinct','active_key_available','https_configured'];
 const value={version:2,scope:'local_configuration_only',database_verified:false,cloud_verified:false,checks:Object.fromEntries(names.map((name,i)=>[name,{ok:true,code:codes[i]}])),password:'never'};
 let options,url;const proxy=createWarehouseProxy({accounts:fixture({caps:['access:admin','read:users']}).accounts,fetchImpl:async(u,o)=>{url=u;options=o;return new Response(JSON.stringify(value));}});
 assert.equal(typeof proxy.readiness,'function');const result=await proxy.readiness(req());
 assert.ok(url.endsWith('/readiness'));assert.equal(options.method,'GET');assert.equal(options.body,undefined);assert.equal(result.database_verified,false);assert.ok(!JSON.stringify(result).includes('never'));
 value.database_verified=true;await assert.rejects(proxy.readiness(req()),e=>e.status===503);
});
test('disabled targets can only reach write-authorized revoke, never enrollment',async()=>{const {createWarehouseProxy}=require('../services/platform-api/warehouse_proxy.cjs');const f=fixture({target:{kaiAccount:{status:'disabled',chService:'ch.example',chUsername:'Analyst'}}});let calls=0;const proxy=createWarehouseProxy({accounts:f.accounts,fetchImpl:async(_url,options)=>{calls++;assert.equal(options.method,'DELETE');return new Response(JSON.stringify({state:'revoked',service:{host:'ch.example',port:8443},ch_username:'Analyst',credentials_saved:false,reference_matches:true}))}});assert.equal((await proxy.revoke(req())).state,'revoked');assert.equal(calls,1);await assert.rejects(proxy.prepare(req()),e=>e.status===403);const readOnly=fixture({caps:['access:admin','read:users']});const denied=createWarehouseProxy({accounts:readOnly.accounts,fetchImpl:async()=>{throw Error('must not forward')}});await assert.rejects(denied.revoke(req()),e=>e.status===403);});
