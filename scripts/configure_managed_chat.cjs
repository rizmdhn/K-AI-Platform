// Operator command: run inside the pinned LibreChat API container.
// First-install seed only: once applied (marker in Mongo, next to the roles), role permissions belong to the
// Admin Panel and updates/restarts never reset them. Role permissions are not set in librechat.yaml for the
// same reason: LibreChat re-applies YAML role settings on every start.
const MARKER = 'role-policy-v1';
// Former librechat.yaml interface settings, now seeded once for USER and ADMIN.
const FEATURES = {
  FILE_SEARCH: { USE: true },
  WEB_SEARCH: { USE: false },
  RUN_CODE: { USE: false },
  SCHEDULES: { USE: false, CREATE: false },
  SHARED_LINKS: { CREATE: false, SHARE: false, SHARE_PUBLIC: false },
};
const POLICY = {
  USER: {
    ...FEATURES,
    AGENTS: { USE: true, CREATE: false, SHARE: false, SHARE_PUBLIC: false },
    MCP_SERVERS: { USE: false, CREATE: false, SHARE: false, SHARE_PUBLIC: false, CONFIGURE_OBO: false },
    MULTI_CONVO: { USE: false },
    // Agents-over-API (API keys, sharing). Unused by kAI; ADMIN keeps it (owner, 6 Nov 2026).
    REMOTE_AGENTS: { USE: false, CREATE: false, SHARE: false, SHARE_PUBLIC: false },
  },
  ADMIN: FEATURES,
};

function matches(role, policy) {
  return Object.entries(policy).every(([type, actions]) =>
    Object.entries(actions).every(([action, value]) => role?.permissions?.[type]?.[action] === value));
}

// marker: { isSet(), set() }; without one (tests, manual runs) the policy is enforced like before.
async function configureManagedChat({ getRoleByName, updateRoleByName }, marker) {
  if (marker && await marker.isSet()) return false;
  const roles = {};
  for (const name of Object.keys(POLICY)) {
    roles[name] = await getRoleByName(name);
    if (roles[name]?.name !== name) throw new Error(`${name} role is unavailable. Start LibreChat first.`);
  }
  let changed = false;
  for (const [name, policy] of Object.entries(POLICY)) {
    const updates = {};
    for (const [type, actions] of Object.entries(policy)) {
      for (const [action, value] of Object.entries(actions)) {
        if (roles[name].permissions?.[type]?.[action] !== value) updates[`permissions.${type}.${action}`] = value;
      }
    }
    if (!Object.keys(updates).length) continue;
    const saved = await updateRoleByName(name, updates);
    if (saved?.name !== name || !matches(saved, policy)) {
      throw new Error('Managed role policy was not applied. Retry setup before inviting users.');
    }
    changed = true;
  }
  if (marker) await marker.set();
  return changed;
}

module.exports = { configureManagedChat };

if (require.main === module) {
  async function main() {
    require('module-alias').addAlias('~', require('node:path').resolve(__dirname, '../api'));
    const mongoose = require('mongoose');
    require('@librechat/data-schemas').createModels(mongoose);
    const db = require('~/models');
    if (!process.env.MONGO_URI) throw new Error('Missing database configuration.');
    try {
      await mongoose.connect(process.env.MONGO_URI, {
        autoIndex: false, autoCreate: false, serverSelectionTimeoutMS: 5000,
      });
      const setup = mongoose.connection.db.collection('kai_setup');
      const marker = {
        isSet: async () => Boolean(await setup.findOne({ _id: MARKER })),
        set: () => setup.updateOne({ _id: MARKER }, { $setOnInsert: { at: new Date() } }, { upsert: true }),
      };
      const already = await marker.isSet();
      const changed = await configureManagedChat(db, marker);
      console.log(already ? 'Role permissions are managed in the Admin Panel; first-install policy not reapplied.'
        : changed ? 'First-install role policy applied.' : 'First-install role policy already in place.');
    } finally {
      await mongoose.disconnect();
    }
  }
  // LibreChat's cache services own timers; disconnect Mongo before exiting.
  main().then(() => process.exit(0), () => {
    console.error('Managed chat setup failed. Check the local database and role configuration.');
    process.exit(1);
  });
}
