const { AccountError } = require('./accounts.cjs');
function createWarehouseActions({accounts,warehouse,agents}) {
  const empty=req=>({...req,headers:req.headers,body:{}});
  async function cleanup(req) {try{return await agents.revokeManaged(empty(req));}catch(error){return{cleanup_complete:false,reason:`error ${error?.status??''}`};}}
  const OWNER='Akun ini pemilik chatbot K-AI. Pindahkan kepemilikan ke ADMIN lain dulu (make agents-transfer AGENT_OWNER_ID=<ID ADMIN lain>), lalu ulangi.';
  // Lookup failure never blocks a disable: losing the chatbot is better than keeping a compromised account.
  const ownsAgent=req=>agents.ownsManaged?agents.ownsManaged(empty(req)).catch(()=>false):false;
  async function save(req) {
    const data=req.body;
    if(!data||Object.keys(data).sort().join(',')!=='password,username'||typeof data.username!=='string'||!data.username||data.username!==data.username.trim()||Buffer.byteLength(data.username)>1024||/[\x00-\x1f\x7f]/.test(data.username)||typeof data.password!=='string'||!data.password||Buffer.byteLength(data.password)>4096||data.password.includes('\0'))throw new AccountError('Periksa username dan password ClickHouse.',400);
    const account=await accounts.read(req);
    if(account.status==='disabled')throw new AccountError('Akun dinonaktifkan.',403);
    const status=await warehouse.status(empty(req));
    if(status.state==='revoked')throw new AccountError('Akses telah dicabut. Hubungi pengelola untuk mengatur ulang akun.',409);
    if((status.credentials_saved||status.state==='active')&&status.ch_username!==data.username)throw new AccountError('Penggantian username memerlukan pengaturan akun baru. Gunakan username yang sudah tersimpan.',409);
    await accounts.update({...req,headers:req.headers,body:{warehouse:{service:status.service.host,username:data.username,confirmed:true}}});
    await warehouse.prepare(empty(req));
    return warehouse.credentials({...req,headers:req.headers,body:{password:data.password}});
  }
  async function revoke(req) {
    // Manual revoke fences the account permanently; refuse it while no credential was ever saved (failed first binding).
    // Account disable calls warehouse.revoke directly and keeps its permanent revocation.
    const current=await warehouse.status(empty(req));
    if(!current.credentials_saved&&!['active','revoked'].includes(current.state))throw new AccountError('Belum ada akses ClickHouse yang tersimpan. Isi username dan password yang benar, lalu simpan.',409);
    let result;
    try { result=await warehouse.revoke(req); }
    catch(error) { await cleanup(req);throw error; }
    return {...result,cleanup_complete:(await cleanup(req)).cleanup_complete===true};
  }
  async function disable(req) {
    if(await ownsAgent(req))throw new AccountError(OWNER,409);
    const result=await accounts.disable(req);
    let account=result.account, revoked=false, reason='';
    try {
      const receipt=await warehouse.revoke(empty(req));
      if (['revoked','not_prepared','reference_required'].includes(receipt.state) && receipt.credentials_saved===false) {
        account=await accounts.completeWarehouseRevocation(empty(req));
        revoked=true;
      } else reason=`unexpected state ${receipt.state}`;
    } catch(error) { reason=`HTTP ${error?.status??'-'} ${error?.message??''}`; /* Disabled remains authoritative; pending is cleared only after durable revocation. */ }
    // Status and our own Indonesian message only (never upstream bodies), so operators can see why the retry is needed.
    if(reason) console.warn(`[kai] disable ${req.params?.id}: ClickHouse revocation incomplete (${reason})`);
    const aclResult=await cleanup(req), acl=aclResult.cleanup_complete===true;
    if(!acl) console.warn(`[kai] disable ${req.params?.id}: chatbot permission cleanup incomplete (${aclResult.reason??'unknown'})`);
    return {account,cleanup_complete:result.cleanup_complete&&revoked&&acl,cleanup:{sessions:result.cleanup_complete,warehouse:revoked,chatbot:acl}};
  }
  async function updateReference(req) {
    const before=await accounts.read(req),after=await accounts.update(req);
    if(JSON.stringify(before.warehouse)!==JSON.stringify(after.warehouse))await cleanup(req);
    return after;
  }
  async function release(req) {
    // SQL only releases a revoked binding; the platform reference is cleared afterwards so the Mongo unique index frees too.
    let result;
    try { result=await warehouse.release(empty(req)); }
    catch(error) {
      if(error?.status===409)throw new AccountError('Akun ClickHouse hanya bisa dilepas setelah aksesnya dicabut. Cabut akses dulu.',409);
      throw error;
    }
    await accounts.update({...req,headers:req.headers,body:{warehouse:null}});
    return {...result,released:true};
  }
  async function setRole(req) {
    if(req.body?.role==='USER'&&await ownsAgent(req))throw new AccountError(OWNER,409);
    return accounts.setRole(req);
  }
  return {save,revoke,release,disable,updateReference,setRole};
}
module.exports={createWarehouseActions};
