import fs from 'node:fs';
import path from 'node:path';
export function installAgentDuplication(root='/app') {
  if(JSON.parse(fs.readFileSync(path.join(root,'api/package.json'),'utf8')).version!=='v0.8.8') throw new Error('Unsupported native duplicate-agent version');
  const file=path.join(root,'api/server/controllers/agents/v1.js');
  const source=fs.readFileSync(file,'utf8');
  const before=`const duplicateAgentHandler = async (req, res) => {
  const { id } = req.params;
  const { id: userId, role: userRole } = req.user;
  const sensitiveFields = ['api_key', 'oauth_client_id', 'oauth_client_secret'];

  try {
    const agent = await db.getAgent({ id });`;
  if(source.split(before).length!==2 || source.includes('const chSuffix =')) throw new Error('Unexpected native duplicate-agent seam');
  const after=before+`
    // Clones must never copy per-user warehouse tools into a user-owned agent.
    const chSuffix = require('librechat-data-provider').Constants.mcp_delimiter + 'clickhouse';
    if (agent && (!Array.isArray(agent.tools ?? []) || (agent.tools ?? []).some(tool => typeof tool !== 'string' || tool.endsWith(chSuffix)))) {
      return res.status(403).json({ message: 'Agent ClickHouse tidak dapat diduplikasi. Atur akses melalui Users.' });
    }`;
  fs.writeFileSync(file,source.replace(before,after));
}
