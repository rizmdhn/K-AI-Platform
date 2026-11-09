const {test}=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');
const modulePath=path.resolve(__dirname,'../services/platform-api/managed_agent_setup.cjs');
function fixture(){
 const state={actor:{_id:'a'.repeat(24),role:'ADMIN'},caps:true,agents:[],writes:[],config:{modelSpecs:{list:[{name:'kai-assistant',label:'K-AI platform',default:true,preset:{endpoint:'Ollama Cloud',model:'approved'}},{name:'other',preset:{endpoint:'other',model:'other'}}]}}};
 const deps={findUser:async()=>state.actor,hasCapability:async()=>state.caps,getConfig:async()=>structuredClone(state.config),getAgent:async id=>state.agents.find(a=>a.id===id),findCandidates:async name=>state.agents.filter(a=>a.name===name),createAgent:async data=>{state.writes.push('create');const agent={...data,id:'agent_'+String(state.agents.length+1).padStart(21,'x'),_id:'db'+state.agents.length};state.agents.push(agent);return agent;},ensureOwners:async()=>{},validateWarehouseAccess:async()=>{},ensureGeneralView:async()=>{},patchSpecs:async list=>{state.writes.push('config');state.config.modelSpecs.list=list;},updateAgent:async(agent,changes)=>{state.writes.push(changes.tools?'tools':changes.provider?'move':'shown');Object.assign(agent,changes);return agent;},delimiter:'_mcp_',brand:'K-AI platform'};
 return {state,deps};
}
async function setup(f){assert.ok(fs.existsSync(modulePath),'native managed setup helper exists');return require(modulePath).provisionManagedAgents('a'.repeat(24),f.deps);}
test('creates two scoped agents and preserves unrelated model specs, repeat is idempotent',async()=>{const f=fixture();const result=await setup(f);assert.deepEqual(result.general.tools,['file_search']);assert.ok(!result.warehouse.tools.includes('file_search'));assert.equal(result.warehouse.tools.length,10);assert.ok(result.warehouse.tools.includes('start_row_labeling_mcp_clickhouse'));assert.ok(result.warehouse.tools.includes('suggest_comment_sql_mcp_clickhouse'));assert.ok(result.warehouse.tools.includes('search_rows_by_meaning_mcp_clickhouse'));assert.ok(result.warehouse.tools.includes('search_database_metadata_mcp_clickhouse'));assert.ok(result.warehouse.tools.every(k=>k.endsWith('_mcp_clickhouse')));assert.equal(f.state.config.modelSpecs.list[0].preset.endpoint,'agents');assert.equal(f.state.config.modelSpecs.list.find(s=>s.name==='other').preset.model,'other');const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);});
test('fresh ADMIN capability and active status are mandatory before writing',async()=>{for(const change of [{role:'USER'},{role:'ADMIN',kaiAccount:{status:'pending'}}]){const f=fixture();f.state.actor={...f.state.actor,...change};await assert.rejects(setup(f),e=>e.status===403);assert.equal(f.state.writes.length,0);}const f=fixture();f.state.caps=false;await assert.rejects(setup(f),e=>e.status===403);assert.equal(f.state.writes.length,0);});
test('foreign owner or unsafe tools in an existing candidate are rejected',async()=>{const f=fixture();f.state.agents.push({id:'agent_'+ 'z'.repeat(21),_id:'db',name:'K-AI platform - Umum',author:'foreign',provider:'Ollama Cloud',model:'approved',tools:['run_query_mcp_clickhouse']});await assert.rejects(setup(f),e=>e.status===409);assert.equal(f.state.writes.length,0);});
test('ambiguous candidates never silently acquire access',async()=>{const f=fixture();f.state.agents=[{name:'K-AI platform - Umum'},{name:'K-AI platform - Umum'}];await assert.rejects(setup(f),e=>e.status===409);assert.equal(f.state.writes.length,0);});
test('an unsupported baseline fails closed before creating records',async()=>{const f=fixture();f.state.config.modelSpecs.list=[];await assert.rejects(setup(f),e=>e.status===503);assert.equal(f.state.writes.length,0);});
test('missing registered CH agent is never silently replaced',async()=>{const f=fixture();f.state.config.modelSpecs.list.push({name:'kai-ch',preset:{endpoint:'agents',agent_id:'agent_'+'z'.repeat(21)}});await assert.rejects(setup(f),e=>e.status===409);assert.equal(f.state.writes.length,0);});
test('partial creation can be retried without duplicating native agents',async()=>{const f=fixture();const patch=f.deps.patchSpecs;f.deps.patchSpecs=async()=>{throw Error('fixture config unavailable');};await assert.rejects(setup(f));assert.equal(f.state.agents.length,2);f.deps.patchSpecs=patch;await setup(f);assert.equal(f.state.agents.length,2);assert.equal(f.state.config.modelSpecs.list[0].preset.endpoint,'agents');});
test('a pre-shared CH candidate is rejected before general creation or registration',async()=>{
 const f=fixture();f.state.agents.push({id:'agent_'+'z'.repeat(21),_id:'shared',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:['list_databases','list_tables','run_query','get_table_schema','execute_readonly_sql'].map(n=>n+'_mcp_clickhouse')});
 f.deps.validateWarehouseAccess=async()=>{throw Object.assign(Error('shared candidate'),{status:409});};
 await assert.rejects(setup(f),e=>e.status===409);assert.equal(f.state.writes.length,0);
});
test('a general agent from an earlier release (no tools) is upgraded to file_search once',async()=>{
 const f=fixture();f.state.agents.push({id:'agent_'+'u'.repeat(21),_id:'old',name:'K-AI platform - Umum',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:[]});
 const result=await setup(f);assert.equal(result.general.id,'agent_'+'u'.repeat(21));assert.deepEqual(result.general.tools,['file_search']);
 assert.equal(f.state.writes.filter(w=>w==='tools').length,1);const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
test('an upgrade never applies to a foreign general agent',async()=>{
 const f=fixture();f.state.agents.push({id:'agent_'+'u'.repeat(21),_id:'old',name:'K-AI platform - Umum',author:'foreign',provider:'Ollama Cloud',model:'approved',tools:[]});
 await assert.rejects(setup(f),e=>e.status===409);assert.ok(!f.state.writes.includes('tools'));
});
test('guardrail instructions: General points to CH, CH points back to General, stale text is synced once',async()=>{
 const f=fixture();const {general,warehouse}=await setup(f);
 assert.match(general.instructions,/TIDAK memiliki akses/);assert.match(general.instructions,/"K-AI Analis Data"/);
 assert.match(warehouse.instructions,/khusus analisis data warehouse/);assert.match(warehouse.instructions,/"K-AI Asisten"/);
 general.instructions='old text';warehouse.instructions='old text';
 await setup(f);assert.match(general.instructions,/"K-AI Analis Data"/);assert.match(warehouse.instructions,/"K-AI Asisten"/);
 const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
test('a CH agent from before P4-08 (five tools) gains search_database_metadata in place, once',async()=>{
 const f=fixture();const old=['list_databases','list_tables','run_query','get_table_schema','execute_readonly_sql'].map(n=>n+'_mcp_clickhouse');
 f.state.agents.push({id:'agent_'+'c'.repeat(21),_id:'ch',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:[...old]});
 const {warehouse}=await setup(f);assert.equal(warehouse.id,'agent_'+'c'.repeat(21));assert.equal(warehouse.tools.length,10);
 assert.equal(f.state.writes.filter(w=>w==='tools').length,1);const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
test('a CH agent with any other tool set is still rejected',async()=>{
 const f=fixture();f.state.agents.push({id:'agent_'+'c'.repeat(21),_id:'ch',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:['run_query_mcp_clickhouse','file_search']});
 await assert.rejects(setup(f),e=>e.status===409);assert.ok(!f.state.writes.includes('tools'));
});
test('a CH agent from the P4-08 release (six tools) gains search_rows_by_meaning in place',async()=>{
 const f=fixture();const six=['list_databases','list_tables','run_query','get_table_schema','execute_readonly_sql','search_database_metadata'].map(n=>n+'_mcp_clickhouse');
 f.state.agents.push({id:'agent_'+'d'.repeat(21),_id:'ch6',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:[...six]});
 const {warehouse}=await setup(f);assert.equal(warehouse.id,'agent_'+'d'.repeat(21));assert.equal(warehouse.tools.length,10);
});
test('inference switch (e.g. Gemini on Vertex) moves both agents in place: Flash for General, Pro for CH',async()=>{
 const f=fixture();await setup(f);const ids=f.state.agents.map(a=>a.id);
 f.deps.target={provider:'google',generalModel:'flash-model',chModel:'pro-model'};
 const {general,warehouse}=await setup(f);
 assert.deepEqual([general.id,warehouse.id],ids);
 assert.deepEqual([general.provider,general.model],['google','flash-model']);
 assert.deepEqual([warehouse.provider,warehouse.model],['google','pro-model']);
 const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
test('a provider switch without a model fails before any write',async()=>{
 const f=fixture();await setup(f);const count=f.state.writes.length;
 f.deps.target={provider:'google'};
 await assert.rejects(setup(f),e=>e.status===503);assert.equal(f.state.writes.length,count);
});
test('the CH model defaults to the General model when no CH model is named',async()=>{
 const f=fixture();f.deps.target={provider:'google',generalModel:'flash-model'};
 const {warehouse}=await setup(f);assert.equal(warehouse.model,'flash-model');
});
test('a CH agent from the AF-03 release (seven tools) gains suggest_comment_sql in place',async()=>{
 const f=fixture();const seven=['list_databases','list_tables','run_query','get_table_schema','execute_readonly_sql','search_database_metadata','search_rows_by_meaning'].map(n=>n+'_mcp_clickhouse');
 f.state.agents.push({id:'agent_'+'e'.repeat(21),_id:'ch7',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:[...seven]});
 const {warehouse}=await setup(f);assert.equal(warehouse.id,'agent_'+'e'.repeat(21));assert.equal(warehouse.tools.length,10);
});
test('agents carry the new names, Indonesian landing text, four starters and a team contact (no admin name)',async()=>{
 const f=fixture();const {general,warehouse}=await setup(f);
 assert.deepEqual([general.name,warehouse.name],['K-AI Asisten','K-AI Analis Data']);
 for(const a of [general,warehouse]){assert.equal(a.conversation_starters.length,4);assert.ok(a.description.length>40);assert.deepEqual(a.support_contact,{name:'Tim K-AI platform',email:''});}
 const specs=f.state.config.modelSpecs.list;
 assert.equal(specs.find(s=>s.name==='kai-assistant').label,'K-AI Asisten');assert.equal(specs.find(s=>s.name==='kai-ch').label,'K-AI Analis Data');
 assert.ok(!JSON.stringify(specs).includes('Your AI assistant'));
});
test('agents named by an earlier release are found by the old name and renamed in place, once',async()=>{
 const f=fixture();f.state.agents.push({id:'agent_'+'g'.repeat(21),_id:'old-general',name:'K-AI platform - Umum',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:['file_search']});
 const {general}=await setup(f);assert.equal(general.id,'agent_'+'g'.repeat(21));assert.equal(general.name,'K-AI Asisten');
 assert.equal(f.state.agents.filter(a=>a.name==='K-AI Asisten').length,1);
 const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
test('a CH agent from the P4-04 release (eight tools) gains the two labelling tools in place',async()=>{
 const f=fixture();const eight=['list_databases','list_tables','run_query','get_table_schema','execute_readonly_sql','search_database_metadata','search_rows_by_meaning','suggest_comment_sql'].map(n=>n+'_mcp_clickhouse');
 f.state.agents.push({id:'agent_'+'h'.repeat(21),_id:'ch8',name:'K-AI platform - CH',author:'a'.repeat(24),provider:'Ollama Cloud',model:'approved',tools:[...eight]});
 const {warehouse}=await setup(f);assert.equal(warehouse.id,'agent_'+'h'.repeat(21));assert.equal(warehouse.tools.length,10);
});
test('an Admin Panel model list overrides a .env model it does not list; a listed .env model is kept',async()=>{
 const f=fixture();f.state.config.endpoints={custom:[{name:'Ollama Cloud',models:{default:['admin-model']}}]};
 f.deps.target={generalModel:'env-model',chModel:'env-pro'};
 let {general,warehouse}=await setup(f);
 assert.deepEqual([general.model,warehouse.model],['admin-model','admin-model']);
 f.state.config.endpoints.custom[0].models.default=['env-model','env-pro'];
 ({general,warehouse}=await setup(f));
 assert.deepEqual([general.model,warehouse.model],['env-model','env-pro']);
});
test('without .env models, changing the endpoint list in the Admin Panel moves both agents on the next setup',async()=>{
 const f=fixture();await setup(f);
 f.state.config.endpoints={custom:[{name:'Ollama Cloud',models:{default:['new-model','other']}}]};
 const {general,warehouse}=await setup(f);
 assert.deepEqual([general.model,warehouse.model],['new-model','new-model']);
 const count=f.state.writes.length;await setup(f);assert.equal(f.state.writes.length,count);
});
