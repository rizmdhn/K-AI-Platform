const { AccountError } = require('./accounts.cjs');
function createWarehouseActions({accounts,warehouse,agents}) {
  const empty=req=>({...req,headers:req.headers,body:{}});
  async function cleanup(req) {try{return (await agents.revokeManaged(empty(req))).cleanup_complete===true;}catch{return false;}}
  async function save(req) {
    const data=req.body;
    if(!data||Object.keys(data).sort().join(',')!=='password,username'||typeof data.username!=='string'||!data.username||data.username!==data.username.trim()||Buffer.byteLength(data.username)>1024||/[\x00-\x1f\x7f]/.test(data.username)||typeof data.password!=='string'||!data.password||Buffer.byteLength(data.password)>4096||data.password.includes('\0'))throw new AccountError('Periksa username dan password ClickHouse.',400);
    const account=await accounts.read(req);
    if(account.status==='disabled')throw new AccountError('Akun dinonaktifkan.',403);
    const status=await warehouse.status(empty(req));
    if(status.state==='revoked')throw new AccountError('Akses telah dicabut. Hubungi pengelola untuk mengatur ulang akun.',409);
    if(['active','ready'].includes(status.state)&&status.ch_username!==data.username)throw new AccountError('Penggantian username memerlukan pengaturan akun baru. Gunakan username yang sudah tersimpan.',409);
    await accounts.update({...req,headers:req.headers,body:{warehouse:{service:status.service.host,username:data.username,confirmed:true}}});
    await warehouse.prepare(empty(req));
    return warehouse.credentials({...req,headers:req.headers,body:{password:data.password}});
  }
  async function revoke(req) {
    let result;
    try { result=await warehouse.revoke(req); }
    catch(error) { await cleanup(req);throw error; }
    return {...result,cleanup_complete:await cleanup(req)};
  }
  async function disable(req) {
    const result=await accounts.disable(req);
    let account=result.account, revoked=false;
    try {
      const receipt=await warehouse.revoke(empty(req));
      if (['revoked','not_prepared','reference_required'].includes(receipt.state) && receipt.credentials_saved===false) {
        account=await accounts.completeWarehouseRevocation(empty(req));
        revoked=true;
      }
    } catch { /* Disabled remains authoritative; pending is cleared only after durable revocation. */ }
    const acl=await cleanup(req);
    return {account,cleanup_complete:result.cleanup_complete&&revoked&&acl};
  }
  async function updateReference(req) {
    const before=await accounts.read(req),after=await accounts.update(req);
    if(JSON.stringify(before.warehouse)!==JSON.stringify(after.warehouse))await cleanup(req);
    return after;
  }
  return {save,revoke,disable,updateReference};
}
module.exports={createWarehouseActions};
