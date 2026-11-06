import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
const prefix = `const duplicateAgentHandler = async (req, res) => {
  const { id } = req.params;
  const { id: userId, role: userRole } = req.user;
  const sensitiveFields = ['api_key', 'oauth_client_id', 'oauth_client_secret'];

  try {
    const agent = await db.getAgent({ id });`;
test('native duplicate refuses CH tools before making any clone, permits General files',async()=>{
 const {installAgentDuplication}=await import('../services/platform-api/install_agent_duplication.mjs');
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'kai-duplicate-'));
 try{
  const file=path.join(root,'api/server/controllers/agents/v1.js');fs.mkdirSync(path.dirname(file),{recursive:true});
  fs.writeFileSync(path.join(root,'api/package.json'),JSON.stringify({version:'v0.8.8'}));
  fs.writeFileSync(file,prefix+`\n    return createAgent(agent);\n  } catch(e) {throw e;}\n};\nmodule.exports=duplicateAgentHandler;`);
  installAgentDuplication(root);let writes=0,status;
  const context={db:{getAgent:async()=>({tools:['search_database_metadata_mcp_clickhouse']})},require:name=>{assert.equal(name,'librechat-data-provider');return{Constants:{mcp_delimiter:'_mcp_'}}},createAgent:()=>{writes++},module:{exports:{}}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),context);
  const req={params:{id:'agent'},user:{id:'viewer',role:'USER'}},res={status(n){status=n;return this},json(){}};
  await context.module.exports(req,res);assert.equal(status,403);assert.equal(writes,0);
  context.db.getAgent=async()=>({tools:['file_search']});await context.module.exports(req,res);assert.equal(writes,1);
  const installed=fs.readFileSync(file,'utf8');assert.throws(()=>installAgentDuplication(root));assert.equal(fs.readFileSync(file,'utf8'),installed);
 }finally{fs.rmSync(root,{recursive:true,force:true})}
});
