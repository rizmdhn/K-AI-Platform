const express = require("express");
const { randomBytes } = require("node:crypto");
const accounts = require("./runtime.cjs");
const { AccountError } = require("./accounts.cjs");
const warehouse = require("./warehouse_proxy.cjs").createWarehouseProxy({
  accounts,
});
const agents = require("./agent_runtime.cjs");
const warehouseActions = require('./warehouse_actions.cjs').createWarehouseActions({accounts,warehouse,agents});
const middleware = require("~/server/middleware");
const problems = require("./problems.cjs").createProblems({
  accounts,
  managedAgent: async (actor) => {
    const config = await require("~/server/services/Config").getAppConfig({ role: actor.role, tenantId: actor.tenantId, userId: actor.id });
    if (!config?.modelSpecs?.list?.some((spec) => spec?.name === "kai-ch")) return null;
    const db = require("~/models");
    const { Constants } = require("librechat-data-provider");
    return require("./managed_agents.cjs").resolveManagedAgent(config, actor.tenantId, { getAgent: db.getAgent, findUser: db.findUser, delimiter: Constants.mcp_delimiter });
  },
  aclEntries: (resourceId) =>
    require("mongoose").model("AclEntry").find({ resourceType: "agent", resourceId }, { principalType: 1, principalId: 1, permBits: 1 }).lean().maxTimeMS(3000),
  VIEW: require("librechat-data-provider").PermissionBits.VIEW,
  USER: require("librechat-data-provider").PrincipalType.USER,
});
const integrations = require("./integrations.cjs").createIntegrations({
  hasCapability: require("~/server/middleware/roles/capabilities").hasCapability,
});
function errorResponse(res, error) {
  if (error?.code === 11000)
    return res
      .status(409)
      .json({ message: "Email or ClickHouse reference is already assigned." });
  return res.status(error instanceof AccountError ? error.status : 503).json({
    message:
      error instanceof AccountError
        ? error.message
        : "Account setup is unavailable.",
  });
}
function installAdmin(router) {
  const handle = (action) => async (req, res) => {
    res.set("Cache-Control", "no-store");
    try {
      res.json(await action(req));
    } catch (error) {
      errorResponse(res, error);
    }
  };
  router.get("/kai-problems", handle(problems.list));
  router.post("/kai-integrations/openmetadata/test", handle(integrations.test));
  router.get("/kai-integrations/openmetadata/databases", handle(integrations.databases));
  router.get("/kai-integrations/:name", handle(integrations.get));
  router.put("/kai-integrations/:name", handle(integrations.save));
  router.get(
    "/:id/kai-warehouse-context",
    handle((req) => accounts.warehouseContext(req, false)),
  );
  const agentAction = (action) => async (req) => {
    try {
      return await action(req);
    } catch (error) {
      throw new AccountError(
        error?.code === 'BINDING_REQUIRED' ? 'Isi kredensial CH dulu' : 'Akses agent tidak tersedia.',
        [400, 403, 409].includes(error?.status) ? error.status : 503,
      );
    }
  };
  router.get("/:id/kai-agents", handle(agentAction(agents.get)));
  router.post("/:id/kai-agents", handle(agentAction(agents.set)));
  router.post(
    "/:id/kai-warehouse-context",
    handle((req) => accounts.warehouseContext(req, true)),
  );
  router.post("/:id/kai-warehouse-revoke-context", handle(accounts.warehouseRevocationContext));
  router.get("/:id/kai-warehouse", handle(warehouse.status));
  router.get("/:id/kai-warehouse/readiness", handle(warehouse.readiness));
  router.post("/:id/kai-warehouse/prepare", handle(warehouse.prepare));
  router.post("/:id/kai-warehouse/save", handle(warehouseActions.save));
  router.post("/:id/kai-warehouse/credentials", handle(warehouse.credentials));
  router.delete("/:id/kai-warehouse", handle(warehouseActions.revoke));
  router.post("/:id/kai-disable", handle(warehouseActions.disable));
  router.post("/", handle(accounts.create));
  router.get("/:id/kai-account", handle(accounts.read));
  router.patch("/:id/kai-account", handle(warehouseActions.updateReference));
  router.post("/:id/kai-activation", handle(accounts.reissue));
}
function page(res, message = "", success = false, status = 200) {
  const nonce = randomBytes(18).toString("base64");
  const escape = (value) =>
    String(value).replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  res.status(status).set({
    "Cache-Control": "no-store",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`,
  });
  res
    .type("html")
    .send(
      `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Activate your K-AI platform account</title><style nonce="${nonce}">body{margin:0;background:#f5f7fb;color:#172b4d;font:16px system-ui;display:grid;min-height:100vh;place-items:center}main{background:white;border:1px solid #dce3ef;border-radius:16px;padding:32px;width:min(360px,80vw)}h1{font-size:24px}label,input,button{display:block;box-sizing:border-box;width:100%;margin-top:16px}input,button{padding:12px;border:1px solid #bac7da;border-radius:8px}button{background:#175cd3;color:white;cursor:pointer}p{line-height:1.5}a{color:#175cd3}[role=alert]{color:#a32323}</style></head><body><main><strong>K-AI platform</strong><h1>${success ? "Account activated" : "Set your password"}</h1><p>${success ? "Your account is ready. Sign in with your email and new password." : "Choose your own password to activate your account. This link works once and expires after 24 hours."}</p>${message ? `<p role="alert">${escape(message)}</p>` : ""}${success ? '<a href="/login">Sign in to K-AI platform</a>' : `<form method="post" action="/activate-account"><input type="hidden" name="userId" id="userId"><input type="hidden" name="token" id="token"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" required minlength="${Math.max(8, parseInt(process.env.MIN_PASSWORD_LENGTH, 10) || 8)}" maxlength="72"><label for="confirm">Confirm password</label><input id="confirm" name="confirm_password" type="password" autocomplete="new-password" required maxlength="72"><button type="submit">Activate account</button></form><p id="link-error" role="alert" hidden>Open the activation link from your administrator.</p><script nonce="${nonce}">const p=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'','/activate-account');document.getElementById('userId').value=p.get('userId')||'';document.getElementById('token').value=p.get('token')||'';if(!p.get('token')){document.querySelector('button').disabled=true;document.getElementById('link-error').hidden=false}</script>`}</main></body></html>`,
    );
}
const activationRouter = express.Router();
activationRouter.get("/", (_req, res) => page(res));
activationRouter.post(
  "/",
  middleware.resetPasswordSubmissionLimiter,
  middleware.checkBan,
  (req, res, next) => {
    // Form submissions always carry Origin; require the application origin, never a caller-supplied Host.
    if (req.get("origin") !== new URL(process.env.DOMAIN_CLIENT).origin)
      return page(
        res,
        "Open the activation link on the K-AI platform application.",
        false,
        403,
      );
    if (Number(req.get("content-length") || 0) > 8192)
      return page(res, "Activation request is too large.", false, 413);
    return next();
  },
  async (req, res) => {
    try {
      await accounts.activate(req.body);
      page(res, "", true);
    } catch (error) {
      page(
        res,
        error instanceof AccountError
          ? `${error.message} Reopen your activation link to try again.`
          : "Activation is unavailable. Try again later.",
        false,
        error instanceof AccountError ? error.status : 503,
      );
    }
  },
);
const internalWarehouseRouter = express.Router();
internalWarehouseRouter.get("/:id", async (req, res) => {
  res.set("Cache-Control", "no-store");
  try {
    res.json(await accounts.internalWarehouseUser(req));
  } catch (error) {
    errorResponse(res, error);
  }
});
// AF-02 CSV download of a bulk labelling job (signed link, see job_download.cjs).
const jobsRouter = express.Router();
jobsRouter.get("/:file", require("./job_download.cjs").createJobDownload());
module.exports = { installAdmin, activationRouter, internalWarehouseRouter, jobsRouter };
