const { test } = require("node:test");
const assert = require("node:assert/strict");
const {
  createAgentAccess,
} = require("../services/platform-api/agent_access.cjs");
const agentId = "agent_abcdefghijklmnopqrstu";
function fixture() {
  const state = {
    entries: [],
    principals: [{ principalType: "user", principalId: "target" }],
    writes: [],
    allowed: true,
    tenant: "tenant",
    actorRole: "ADMIN",
    configured: true,
    valid: true,
    manageUsers: true,
    targetRole: "USER",
    owner: "owner",
    status: "active",
    binding: {version: 1, eligible: true, binding_id: "11111111-1111-4111-8111-111111111111", revision: "22222222-2222-4222-8222-222222222222"},
    pending: false,
  };
  const deps = {
    ResourceType: { AGENT: "agent", REMOTE_AGENT: "remote_agent" },
    PrincipalType: { USER: "user" },
    PermissionBits: { VIEW: 1 },
    AccessRoleIds: { AGENT_VIEWER: "agent_viewer" },
    agentContext: async () => ({
      actor: { id: "admin", role: state.actorRole, tenantId: "tenant" },
      target: {
        id: "target",
        role: state.targetRole,
        tenantId: state.tenant,
        status: state.status,
      },
    }),
    hasCapability: async (_actor, capability) =>
      state.allowed && (capability !== "manage:users" || state.manageUsers),
    getConfig: async () => ({
      modelSpecs: {
        list: state.configured
          ? [
              {
                name: "kai-ch",
                preset: { endpoint: "agents", agent_id: agentId },
              },
            ]
          : [],
      },
    }),
    resolveManagedAgent: async () =>
      state.valid
        ? {
            id: agentId,
            _id: "native-id",
            author: state.owner,
            name: "Shared CH",
          }
        : null,
    getBinding: async () => state.binding,
    getAgent: async () => ({id: agentId, _id: "native-id", tenantId: "tenant", author: state.owner}),
    findUser: async () => ({_id: "admin", role: "ADMIN", tenantId: "tenant", kaiAccount: {status: "active"}}),
    markCleanupPending: async (_id, pending) => {state.pending = pending;},
    getUserPrincipals: async () => state.principals,
    findEntriesByPrincipalsAndResource: async () => state.entries,
    checkPermission: async () => state.entries.some((e) => e.permBits & 1),
    grantPermission: async (args) => {
      state.writes.push(args);
      state.entries.push({
        principalType: "user",
        principalId: "target",
        permBits: 1,
      });
      return state.entries.at(-1);
    },
    bulkUpdateResourcePermissions: async (args) => {
      state.writes.push(args);
      state.entries = state.entries.filter((e) => e.principalId !== "target");
      return { errors: [] };
    },
  };
  return {
    state,
    deps,
    api: createAgentAccess(deps),
    req: { body: { agent_id: agentId, assigned: true } },
  };
}
test("missing curated agent shows setup state without writing", async () => {
  const { state, api } = fixture();
  state.configured = false;
  assert.equal((await api.get({})).configured, false);
  assert.equal(state.writes.length, 0);
});
test("unknown agent and extra authority flags rejected", async () => {
  for (const body of [
    { agent_id: "other", assigned: true },
    { agent_id: agentId, assigned: true, role: "ADMIN" },
  ]) {
    const { api, req } = fixture();
    req.body = body;
    await assert.rejects(api.set(req));
  }
});
test("fresh capability and tenant restrict writes", async () => {
  for (const field of ["allowed", "tenant"]) {
    const { state, api, req } = fixture();
    state[field] = field === "allowed" ? false : "other";
    await assert.rejects(api.set(req));
    assert.equal(state.writes.length, 0);
  }
});
test("grant only viewer and repeat assignment is idempotent", async () => {
  const { state, api, req } = fixture();
  await api.set(req);
  const result = await api.set(req);
  assert.equal(result.direct_assignment, true);
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].accessRoleId, "agent_viewer");
});
test("higher direct permission cannot be downgraded or removed", async () => {
  const { state, api, req } = fixture();
  state.entries = [
    { principalType: "user", principalId: "target", permBits: 3 },
  ];
  assert.equal((await api.get(req)).can_remove_direct, false);
  await api.set(req);
  req.body.assigned = false;
  await assert.rejects(api.set(req));
  assert.equal(state.writes.length, 0);
});
test("removing direct assignment reports inherited group access", async () => {
  const { state, api, req } = fixture();
  state.entries = [
    { principalType: "user", principalId: "target", permBits: 1 },
    { principalType: "group", principalId: "group", permBits: 1 },
  ];
  req.body.assigned = false;
  const result = await api.set(req);
  assert.equal(result.direct_assignment, false);
  assert.equal(result.effective_access, true);
  assert.equal(result.inherited_access, true);
  assert.equal(state.writes[0].revokedPrincipals[0].id, "target");
});
test("untrusted configured agent fails closed and hides upstream errors", async () => {
  const { state, api, req } = fixture();
  state.valid = false;
  await assert.rejects(api.set(req), {
    message: "Akses agent tidak tersedia.",
  });
  assert.equal(state.writes.length, 0);
});
test("read-only actor cannot advertise write controls", async () => {
  const { state, api } = fixture();
  state.allowed = false;
  assert.equal((await api.get({})).can_manage, false);
});
test("owner and inherited user ACL never removable through direct toggle", async () => {
  const { state, api, req } = fixture();
  state.entries = [
    {
      principalType: "user",
      principalId: "target",
      permBits: 1,
      inheritedFrom: "parent-resource",
    },
  ];
  const result = await api.get(req);
  assert.equal(result.direct_assignment, false);
  assert.equal(result.inherited_access, true);
  assert.equal(result.can_remove_direct, false);
  req.body.assigned = false;
  await api.set(req);
  assert.equal(state.writes.length, 0);
});
test("upstream native errors are generic and fail closed", async () => {
  const { deps, req } = fixture();
  deps.getConfig = async () => {
    throw Error("private-provider-key");
  };
  const api = createAgentAccess(deps);
  await assert.rejects(
    api.set(req),
    (error) =>
      error.message === "Akses agent tidak tersedia." &&
      !error.message.includes("private-provider-key"),
  );
});
test("manage agents alone cannot manage user assignments", async () => {
  const { state, api, req } = fixture();
  state.manageUsers = false;
  assert.equal((await api.get(req)).can_manage, false);
  await assert.rejects(api.set(req));
  assert.equal(state.writes.length, 0);
});
test("custom admin capability cannot manage native ADMIN targets", async () => {
  const { state, api, req } = fixture();
  state.actorRole = "CUSTOM";
  state.targetRole = "ADMIN";
  assert.equal((await api.get(req)).can_manage, false);
  await assert.rejects(api.set(req));
  assert.equal(state.writes.length, 0);
});
test("native owner assignment cannot be removed", async () => {
  const { state, api, req } = fixture();
  state.owner = "target";
  state.entries = [
    { principalType: "user", principalId: "target", permBits: 1 },
  ];
  const result = await api.get(req);
  assert.equal(result.inherited_access, true);
  assert.equal(result.can_remove_direct, false);
  req.body.assigned = false;
  await assert.rejects(api.set(req));
  assert.equal(state.writes.length, 0);
});
test("inherited user entry cannot be replaced by a direct viewer grant", async () => {
  const { state, api, req } = fixture();
  state.entries = [
    {
      principalType: "user",
      principalId: "target",
      permBits: 3,
      inheritedFrom: "parent-resource",
    },
  ];
  await assert.rejects(api.set(req));
  assert.equal(state.writes.length, 0);
});
test("direct viewer plus inherited user permission cannot trigger bulk removal", async () => {
  const { state, api, req } = fixture();
  state.entries = [
    { principalType: "user", principalId: "target", permBits: 1 },
    {
      principalType: "user",
      principalId: "target",
      permBits: 3,
      inheritedFrom: "parent-resource",
    },
  ];
  assert.equal((await api.get(req)).can_remove_direct, false);
  req.body.assigned = false;
  await assert.rejects(api.set(req), (e) => e.status === 409);
  assert.equal(state.writes.length, 0);
  assert.equal(state.entries.length, 2);
});

test('assigned agent exposes configured model and exact tools for admin inspection',async()=>{
 const {deps}=fixture();const original=deps.resolveManagedAgent;
 deps.resolveManagedAgent=async(...args)=>({...await original(...args),provider:'Approved provider',model:'approved-model',tools:['execute_readonly_sql_mcp_clickhouse']});
 const result=await createAgentAccess(deps).get({});
 assert.equal(result.agent.provider,'Approved provider');assert.equal(result.agent.model,'approved-model');assert.deepEqual(result.agent.tools,['execute_readonly_sql_mcp_clickhouse']);
});

test("inactive binding refuses assignment including idempotent requests", async () => {
  for (const existing of [false, true]) {
    const {state, api, req} = fixture();
    state.binding = {version: 1, eligible: false, binding_id: null, revision: null};
    if (existing) state.entries.push({principalType: "user", principalId: "target", permBits: 1});
    await assert.rejects(api.set(req), {status:409, code:"BINDING_REQUIRED", message:"Isi kredensial CH dulu"});
    assert.equal(state.writes.length,0);
    assert.equal((await api.get(req)).binding_active,false);
  }
});
test("active binding is visible and does not automatically assign", async () => {
 const {api,req,state}=fixture();
 const result=await api.get(req);assert.equal(result.binding_active,true);assert.equal(result.direct_assignment,false);assert.equal(state.writes.length,0);
});
test("binding revision changes during grant roll back only the new viewer", async () => {
 for (const change of ["revision","eligible","unavailable","disabled"]) {
  const {deps,state,req}=fixture();const grant=deps.grantPermission;
  deps.grantPermission=async(args)=>{const result=await grant(args);if(change==="unavailable") deps.getBinding=async()=>{throw Error("password-private");};else if(change==="disabled")state.status="disabled";else state.binding={...state.binding,[change]:change==="revision"?"33333333-3333-4333-8333-333333333333":false};return result;};
  // Dependencies are captured, so unavailable is driven through a mutable receipt function.
  deps.getBinding=async()=>{if(change==="unavailable"&&state.writes.length)throw Error("password-private");return state.binding;};
  await assert.rejects(createAgentAccess(deps).set(req),e=>e.status===409&&!e.message.includes("password-private"));
  assert.equal(state.entries.length,0);assert.equal(state.writes.length,2);
 }
});
test("missing binding dependency fails closed", async()=>{
 const {deps,req,state}=fixture();delete deps.getBinding;await assert.rejects(createAgentAccess(deps).set(req),{status:503});assert.equal(state.writes.length,0);
});
test("disabled target cannot be assigned and read skips protected eligibility", async()=>{
 const {deps,state,req}=fixture();state.status="disabled";deps.getBinding=async()=>{throw Error("must not call");};const api=createAgentAccess(deps);
 assert.equal((await api.get(req)).binding_active,false);await assert.rejects(api.set(req),{status:409});assert.equal(state.writes.length,0);
});
test("binding cleanup works for disabled target without managed owner/model validation",async()=>{
 const {state,deps,req}=fixture();state.status="disabled";state.valid=false;deps.hasCapability=async(_actor,cap)=>cap==="manage:users";const api=createAgentAccess(deps);
 state.entries=[{principalType:"user",principalId:"target",permBits:1},{principalType:"group",principalId:"group",permBits:1}];
 assert.deepEqual(await api.revokeManaged(req),{cleanup_complete:true});assert.equal(state.entries.length,1);assert.equal(state.pending,false);
});
test("cleanup preserves owner higher and mixed inherited user permissions",async()=>{
 for(const kind of ["owner","higher","inherited"]){const {state,api,req}=fixture();state.entries=[{principalType:"user",principalId:"target",permBits:kind==="higher"?3:1}];if(kind==="owner")state.owner="target";if(kind==="inherited")state.entries.push({principalType:"user",principalId:"target",permBits:1,inheritedFrom:"parent"});
 assert.deepEqual(await api.revokeManaged(req),{cleanup_complete:false});assert.equal(state.writes.length,0);assert.equal(state.pending,true);}
});
test("cleanup failures persist pending without exposing upstream errors",async()=>{
 const {deps,state,req}=fixture();deps.getAgent=async()=>{throw Error("private-password");};assert.deepEqual(await createAgentAccess(deps).revokeManaged(req),{cleanup_complete:false});assert.equal(state.pending,true);
});
test("native share guard blocks managed CH writes for agent and remote agent only",async()=>{
 for(const type of ["agent","remote_agent","promptGroup"]){for(const id of ["native-id","other"]){const {api}=fixture();let next=false;let status;let body;const res={status(n){status=n;return this;},json(b){body=b;return this;}};
 await api.guardNativeShare({user:{id:"admin",tenantId:"tenant"},params:{resourceType:type,resourceId:id}},res,()=>{next=true;});
 if(type!=="promptGroup"&&id==="native-id"){assert.equal(status,409);assert.equal(body.message,"Atur akses chatbot CH melalui detail pengguna.");assert.equal(next,false);}else assert.equal(next,true);
 }}
});
test("native share guard fails closed on configuration errors with safe response",async()=>{
 const {deps}=fixture();deps.getConfig=async()=>{throw Error("private-password");};let status;let body;await createAgentAccess(deps).guardNativeShare({user:{id:"admin",tenantId:"tenant"},params:{resourceType:"agent",resourceId:"native-id"}},{status(n){status=n;return this;},json(b){body=b;}},()=>assert.fail("continued"));assert.equal(status,503);assert.equal(body.message,"Akses agent tidak tersedia.");
});

test("fresh native account state is checked after the post-grant receipt", async()=>{
 const {deps,state,req}=fixture();let calls=0;
 deps.getBinding=async()=>{calls++;if(calls===2)state.status="disabled";return state.binding;};
 await assert.rejects(createAgentAccess(deps).set(req),{status:409});assert.equal(state.entries.length,0);
});
test("a failing final native ACL read also rolls back this request's new grant",async()=>{
 const {deps,state,req}=fixture();const original=deps.checkPermission;let calls=0;
 deps.checkPermission=async(...args)=>{calls++;if(calls===2)throw Error("private-native-error");return original(...args);};
 await assert.rejects(createAgentAccess(deps).set(req),{status:409});assert.equal(state.entries.length,0);
});
test("rollback preserves a newly promoted editor and records cleanup pending",async()=>{
 const {deps,state,req}=fixture();const original=deps.grantPermission;
 deps.grantPermission=async(args)=>{const granted=await original(args);granted.permBits=3;state.binding={version:1,eligible:false,binding_id:null,revision:null};return granted;};
 await assert.rejects(createAgentAccess(deps).set(req),{status:409});assert.equal(state.entries[0].permBits,3);assert.equal(state.writes.length,1);assert.equal(state.pending,true);
});
test("malformed receipts fail closed and never grant",async()=>{
 for(const receipt of [{version:1,eligible:true,binding_id:"secret",revision:"secret"},{version:2,eligible:true},{version:1,eligible:false,binding_id:"secret",revision:null}]){
 const {state,api,req}=fixture();state.binding=receipt;await assert.rejects(api.set(req),{status:503,message:"Akses agent tidak tersedia."});assert.equal(state.writes.length,0);}
});
test("cleanup rejects actors without manage users and cross tenant targets",async()=>{
 for(const field of ["manageUsers","tenant"]){const {state,api,req}=fixture();state[field]=field==="tenant"?"other":false;await assert.rejects(api.revokeManaged(req),{status:403});assert.equal(state.writes.length,0);assert.equal(state.pending,false);}
});

test("native guard fails closed on absent configuration rather than allowing a bypass", async()=>{
 const {deps}=fixture();deps.getConfig=async()=>null;let status;let next=false;
 await createAgentAccess(deps).guardNativeShare({user:{id:"admin",tenantId:"tenant"},params:{resourceType:"agent",resourceId:"native-id"}},{status(n){status=n;return this;},json(){}},()=>{next=true;});assert.equal(status,503);assert.equal(next,false);
});
test('eligibility preserves inherited Express authorization headers', async()=>{const {deps,req}=fixture();const headers={authorization:'Bearer fixture'};Object.setPrototypeOf(req,{headers});const getBinding=deps.getBinding;deps.getBinding=async request=>{assert.equal(request.headers,headers);return getBinding(request)};await createAgentAccess(deps).get(req);});
