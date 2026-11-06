import fs from 'node:fs';
import path from 'node:path';

export function installBindingACL(root = '/app') {
  const version = JSON.parse(fs.readFileSync(path.join(root, 'api/package.json'), 'utf8')).version;
  if (version !== 'v0.8.8') throw new Error('Unsupported native binding-ACL version');
  const file = path.join(root, 'api/server/routes/accessPermissions.js');
  const source = fs.readFileSync(file, 'utf8');
  const before = `router.put(
  '/:resourceType/:resourceId',
  checkResourcePermissionAccess(PermissionBits.SHARE),
  checkShareAccessUnlessAgentAdmin,
  checkSharePublicAccess,
  rejectSharedLinkOwnerPermissionChanges,
  updateResourcePermissions,
);`;
  if (source.split(before).length !== 2) throw new Error('Unexpected native binding-ACL seam: authorized permission PUT');
  const after = before.replace('  rejectSharedLinkOwnerPermissionChanges,',
    "  require('/app/kai-platform/agent_runtime.cjs').guardNativeShare,\n  rejectSharedLinkOwnerPermissionChanges,");
  fs.writeFileSync(file, source.replace(before, after));
}
