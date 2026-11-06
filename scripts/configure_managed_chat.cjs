// Operator command: run inside the pinned LibreChat API container.
const POLICY = {
  AGENTS: { USE: true, CREATE: false, SHARE: false, SHARE_PUBLIC: false },
  MCP_SERVERS: { USE: false, CREATE: false, SHARE: false, SHARE_PUBLIC: false, CONFIGURE_OBO: false },
  MULTI_CONVO: { USE: false },
  // Agents-over-API (API keys, sharing). Unused by kAI; ADMIN keeps it (owner, 6 Nov 2026).
  REMOTE_AGENTS: { USE: false, CREATE: false, SHARE: false, SHARE_PUBLIC: false },
};

async function configureManagedChat({ getRoleByName, updateRoleByName }) {
  const role = await getRoleByName('USER');
  if (role?.name !== 'USER') throw new Error('USER role is unavailable. Start LibreChat first.');
  const updates = {};
  for (const [type, actions] of Object.entries(POLICY)) {
    for (const [action, value] of Object.entries(actions)) {
      if (role.permissions?.[type]?.[action] !== value) {
        updates[`permissions.${type}.${action}`] = value;
      }
    }
  }
  if (!Object.keys(updates).length) return false;
  const saved = await updateRoleByName('USER', updates);
  for (const [type, actions] of Object.entries(POLICY)) {
    for (const [action, value] of Object.entries(actions)) {
      if (saved?.name !== 'USER' || saved.permissions?.[type]?.[action] !== value) {
        throw new Error('Managed USER policy was not applied. Retry setup before inviting users.');
      }
    }
  }
  return true;
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
      const changed = await configureManagedChat(db);
      console.log(changed ? 'Managed USER chat policy applied.' : 'Managed USER chat policy already applied.');
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
