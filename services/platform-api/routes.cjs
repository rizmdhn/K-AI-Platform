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
      .json({ message: "Email ini sudah terdaftar, atau akun ClickHouse ini sudah dipakai pengguna lain." });
  return res.status(error instanceof AccountError ? error.status : 503).json({
    message:
      error instanceof AccountError
        ? error.message
        : "Layanan akun sedang tidak tersedia. Coba lagi beberapa saat lagi.",
  });
}
// The link is always returned for manual sharing; with SMTP configured it is also mailed to the user.
const MAIL_TEXT = {
  activation: ["Aktivasi akun K-AI", "Administrator membuatkan akun K-AI untuk Anda. Buka tautan berikut untuk membuat password"],
  reset: ["Reset password akun K-AI", "Administrator meminta reset password akun K-AI Anda. Buka tautan berikut untuk membuat password baru"],
};
function withMail(action, kind) {
  return async (req) => {
    const result = await action(req);
    const id = result.account?.id ?? req.params.id;
    const account = result.account?.email ? result.account : await accounts.read({ ...req, params: { id } });
    const [subject, intro] = MAIL_TEXT[kind];
    const until = new Date(result.expiresAt).toLocaleString("id-ID", { dateStyle: "long", timeStyle: "short", timeZone: process.env.TZ || "Asia/Jakarta" });
    const text = `Halo ${account.name || account.email},\n\n${intro} (sekali pakai, berlaku sampai ${until}):\n\n${result.activationUrl}\n\nPassword lama tetap berlaku sampai tautan dipakai. Jika Anda tidak merasa meminta ini, abaikan email ini dan hubungi administrator.\n`;
    return { ...result, email: { to: account.email, ...(await integrations.mail(account.email, subject, kind === "activation" ? text.replace("Password lama tetap berlaku sampai tautan dipakai. ", "") : text)) } };
  };
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
  router.post("/kai-integrations/smtp/test", handle(integrations.testSmtp));
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
  router.post("/:id/kai-warehouse/release", handle(warehouseActions.release));
  router.post("/:id/kai-warehouse/check/connection", handle(warehouse.checkConnection));
  router.post("/:id/kai-warehouse/check/credentials", handle(warehouse.checkCredentials));
  router.post("/:id/kai-disable", handle(warehouseActions.disable));
  router.post("/", handle(withMail(accounts.create, "activation")));
  router.get("/:id/kai-account", handle(accounts.read));
  router.patch("/:id/kai-account", handle(warehouseActions.updateReference));
  router.post("/:id/kai-activation", handle(withMail(accounts.reissue, "activation")));
  router.post("/:id/kai-password-reset", handle(withMail(accounts.resetLink, "reset")));
  router.patch("/:id/kai-role", handle(warehouseActions.setRole));
  router.patch("/:id/kai-profile", handle(accounts.updateProfile));
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
      `<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Password akun K-AI</title><style nonce="${nonce}">body{margin:0;background:#f5f7fb;color:#172b4d;font:16px system-ui;display:grid;min-height:100vh;place-items:center}main{background:white;border:1px solid #dce3ef;border-radius:16px;padding:32px;width:min(360px,80vw)}h1{font-size:24px}label,input,button{display:block;box-sizing:border-box;width:100%;margin-top:16px}input,button{padding:12px;border:1px solid #bac7da;border-radius:8px}button{background:#175cd3;color:white;cursor:pointer}p{line-height:1.5}a{color:#175cd3}[role=alert]{color:#a32323}</style></head><body><main><strong>K-AI platform</strong><h1>${success ? "Password berhasil disimpan" : "Buat password Anda"}</h1><p>${success ? "Akun Anda siap dipakai. Login dengan email dan password baru Anda." : "Buat password baru untuk akun K-AI Anda. Tautan ini hanya bisa dipakai sekali dan berlaku 24 jam."}</p>${message ? `<p role="alert">${escape(message)}</p>` : ""}${success ? '<a href="/login">Login ke K-AI</a>' : `<form method="post" action="/activate-account"><input type="hidden" name="userId" id="userId"><input type="hidden" name="token" id="token"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" required minlength="${Math.max(8, parseInt(process.env.MIN_PASSWORD_LENGTH, 10) || 8)}" maxlength="72"><label for="confirm">Ulangi password</label><input id="confirm" name="confirm_password" type="password" autocomplete="new-password" required maxlength="72"><button type="submit">Simpan password</button></form><p id="link-error" role="alert" hidden>Buka tautan yang dikirim administrator Anda.</p><script nonce="${nonce}">const p=new URLSearchParams(location.hash.slice(1));history.replaceState(null,'','/activate-account');document.getElementById('userId').value=p.get('userId')||'';document.getElementById('token').value=p.get('token')||'';if(!p.get('token')){document.querySelector('button').disabled=true;document.getElementById('link-error').hidden=false}</script>`}</main></body></html>`,
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
        "Buka tautan langsung dari alamat aplikasi K-AI.",
        false,
        403,
      );
    if (Number(req.get("content-length") || 0) > 8192)
      return page(res, "Permintaan terlalu besar.", false, 413);
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
          ? error.message
          : "Layanan sedang tidak tersedia. Coba lagi nanti.",
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
