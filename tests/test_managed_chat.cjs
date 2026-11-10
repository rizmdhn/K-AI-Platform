const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  configureManagedChat,
} = require("../scripts/configure_managed_chat.cjs");

function fixture() {
  const roles = {
    USER: {
      name: "USER",
      description: "keep description",
      permissions: {
        AGENTS: { USE: true, CREATE: true, SHARE: true, SHARE_PUBLIC: true },
        MCP_SERVERS: {
          USE: true,
          CREATE: true,
          SHARE: true,
          SHARE_PUBLIC: true,
          CONFIGURE_OBO: true,
        },
        MULTI_CONVO: { USE: true },
        REMOTE_AGENTS: { USE: true, CREATE: true, SHARE: true, SHARE_PUBLIC: true },
        BOOKMARKS: { USE: true },
      },
    },
    ADMIN: { name: "ADMIN", permissions: { AGENTS: { CREATE: true } } },
    analyst: { name: "analyst", permissions: { AGENTS: { USE: true } } },
  };
  const writes = [];
  const db = {
    getRoleByName: async (name) => structuredClone(roles[name]),
    updateRoleByName: async (name, updates) => {
      writes.push({ name, updates });
      for (const [key, value] of Object.entries(updates)) {
        const [field, type, action] = key.split(".");
        assert.equal(field, "permissions");
        roles[name][field][type] ??= {};
        roles[name][field][type][action] = value;
      }
      return structuredClone(roles[name]);
    },
  };
  return { roles, db, writes };
}

test("ordinary users can chat but cannot create/share agents or configure MCP", async () => {
  const { roles, db, writes } = fixture();
  assert.equal(await configureManagedChat(db), true);
  assert.deepEqual(roles.USER.permissions.AGENTS, {
    USE: true,
    CREATE: false,
    SHARE: false,
    SHARE_PUBLIC: false,
  });
  assert.deepEqual(roles.USER.permissions.MCP_SERVERS, {
    USE: false,
    CREATE: false,
    SHARE: false,
    SHARE_PUBLIC: false,
    CONFIGURE_OBO: false,
  });
  assert.equal(roles.USER.permissions.MULTI_CONVO.USE, false);
  assert.deepEqual(roles.USER.permissions.REMOTE_AGENTS, { USE: false, CREATE: false, SHARE: false, SHARE_PUBLIC: false });
  assert.deepEqual(roles.USER.permissions.WEB_SEARCH, { USE: false });
  assert.deepEqual(writes.map((w) => w.name), ["USER", "ADMIN"]);
});

test("ADMIN only gets feature defaults; custom roles and unrelated USER fields are kept", async () => {
  const { roles, db } = fixture();
  const analyst = structuredClone(roles.analyst);
  await configureManagedChat(db);
  assert.deepEqual(roles.ADMIN.permissions.AGENTS, { CREATE: true });
  assert.deepEqual(roles.ADMIN.permissions.FILE_SEARCH, { USE: true });
  assert.equal(roles.ADMIN.permissions.MCP_SERVERS, undefined);
  assert.deepEqual(roles.analyst, analyst);
  assert.deepEqual(roles.USER.permissions.BOOKMARKS, { USE: true });
  assert.equal(roles.USER.description, "keep description");
});

test("rerun is idempotent and does not rewrite a matching policy", async () => {
  const { db, writes } = fixture();
  await configureManagedChat(db);
  assert.equal(await configureManagedChat(db), false);
  assert.equal(writes.length, 2);
});

test("after the first install, Admin Panel changes are never reset", async () => {
  const { roles, db, writes } = fixture();
  let set = false;
  const marker = { isSet: async () => set, set: async () => { set = true; } };
  assert.equal(await configureManagedChat(db, marker), true);
  assert.equal(set, true);
  roles.USER.permissions.WEB_SEARCH.USE = true; // admin enables web search in the panel
  assert.equal(await configureManagedChat(db, marker), false);
  assert.equal(roles.USER.permissions.WEB_SEARCH.USE, true);
  assert.equal(writes.length, 2);
});

test("a failed first install leaves the marker unset so setup retries", async () => {
  const { db } = fixture();
  let set = false;
  db.updateRoleByName = async () => { throw new Error("database unavailable"); };
  await assert.rejects(configureManagedChat(db, { isSet: async () => set, set: async () => { set = true; } }));
  assert.equal(set, false);
});

test("atomic dotted updates preserve unrelated concurrent admin changes", async () => {
  const { roles, db, writes } = fixture();
  const write = db.updateRoleByName;
  db.updateRoleByName = async (...args) => {
    roles.USER.permissions.BOOKMARKS.USE = false;
    return write(...args);
  };
  await configureManagedChat(db);
  assert.equal(roles.USER.permissions.BOOKMARKS.USE, false);
  assert.ok(
    Object.keys(writes[0].updates).every((key) =>
      key.startsWith("permissions."),
    ),
  );
  assert.equal(Object.hasOwn(writes[0].updates, "permissions"), false);
});

test("missing or incorrectly resolved USER role fails without writing", async () => {
  for (const role of [null, { name: "ADMIN", permissions: {} }]) {
    let wrote = false;
    await assert.rejects(
      configureManagedChat({
        getRoleByName: async () => role,
        updateRoleByName: async () => {
          wrote = true;
        },
      }),
      /USER role/,
    );
    assert.equal(wrote, false);
  }
});

test("write failure or a lost update cannot report successful setup", async () => {
  const { db } = fixture();
  db.updateRoleByName = async () => {
    throw new Error("database unavailable");
  };
  await assert.rejects(configureManagedChat(db), /database unavailable/);
  for (const result of [null, { name: "USER", permissions: {} }]) {
    db.updateRoleByName = async () => result;
    await assert.rejects(configureManagedChat(db), /not applied/);
  }
});
