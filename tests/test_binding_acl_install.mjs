import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const nativePut = `router.put(
  '/:resourceType/:resourceId',
  checkResourcePermissionAccess(PermissionBits.SHARE),
  checkShareAccessUnlessAgentAdmin,
  checkSharePublicAccess,
  rejectSharedLinkOwnerPermissionChanges,
  updateResourcePermissions,
);`;

async function fixture(fn, {version='v0.8.8', route=nativePut}={}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'kai-binding-acl-'));
  const file=path.join(root,'api/server/routes/accessPermissions.js');
  fs.mkdirSync(path.dirname(file),{recursive:true});
  fs.writeFileSync(path.join(root,'api/package.json'),JSON.stringify({version}));
  fs.writeFileSync(file,route);
  try { await fn(root,file); } finally {fs.rmSync(root,{recursive:true,force:true});}
}

test('binding ACL installer patches only the exact authorized native PUT seam',async()=>{
 const {installBindingACL}=await import('../services/platform-api/install_binding_acl.mjs');
 await fixture(async(root,file)=>{
  installBindingACL(root);
  const source=fs.readFileSync(file,'utf8');
  assert.ok(source.includes("  checkSharePublicAccess,\n  require('/app/kai-platform/agent_runtime.cjs').guardNativeShare,\n  rejectSharedLinkOwnerPermissionChanges,"));
  assert.equal(source.split('.guardNativeShare').length-1,1);
 });
});
test('binding ACL installer validates version and all seams before writes',async()=>{
 const {installBindingACL}=await import('../services/platform-api/install_binding_acl.mjs');
 for(const options of [{version:'future'},{route:nativePut.replace('  updateResourcePermissions,','  upstreamChanged,')},{route:nativePut+'\n'+nativePut}]) {
  await fixture(async(root,file)=>{const before=fs.readFileSync(file,'utf8');assert.throws(()=>installBindingACL(root));assert.equal(fs.readFileSync(file,'utf8'),before);},options);
 }
});
