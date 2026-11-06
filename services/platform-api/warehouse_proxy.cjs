const { AccountError } = require("./accounts.cjs");
const unavailable = () =>
  new AccountError("Warehouse access is unavailable.", 503);
function fields(body, allowed) {
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Object.keys(body).some((k) => !allowed.includes(k))
  )
    throw new AccountError("Invalid warehouse request.");
}
function statusDto(value) {
  if (
    !value ||
    ![
      "reference_required",
      "not_prepared",
      "ready",
      "active",
      "revoked",
    ].includes(value.state) ||
    !value.service ||
    typeof value.service.host !== "string" ||
    value.service.host.length > 253 ||
    !Number.isInteger(value.service.port) ||
    value.service.port < 1 ||
    value.service.port > 65535 ||
    !(
      value.ch_username === null ||
      (typeof value.ch_username === "string" &&
        value.ch_username.length <= 1024)
    )
  )
    throw unavailable();
  const result = {
    state: value.state,
    service: { host: value.service.host, port: value.service.port },
    ch_username: value.ch_username,
  };
  for (const key of [
    "credentials_saved",
    "reference_matches",
  ]) {
    if (typeof value[key] !== "boolean") throw unavailable();
    result[key] = value[key];
  }
  return result;
}
function createWarehouseProxy({ accounts, fetchImpl = fetch }) {
  async function call(req, action) {
    const write = !["status", "readiness", "eligibility"].includes(action);
    const allowed =
      action === "credentials"
        ? ["password"]
        : [];
    if (write) fields(req.body, allowed);
    if (
      action === "credentials" &&
      (typeof req.body.password !== "string" ||
        !req.body.password.length ||
        Buffer.byteLength(req.body.password) > 4096 ||
        req.body.password.includes("\0"))
    )
      throw new AccountError("Invalid warehouse password.");
    // Reuse the native, fresh capability/tenant check without forwarding secret request fields to context.
    if (action === "revoke") await accounts.warehouseRevocationContext({ ...req, headers:req.headers, body:{} });
    else await accounts.warehouseContext({ ...req, headers:req.headers, body: {} }, write);
    const bearer = req.headers?.authorization;
    if (typeof bearer !== "string" || !/^Bearer [^\s]+$/.test(bearer))
      throw new AccountError("Unauthorized.", 401);
    const suffix = ["status", "revoke"].includes(action) ? "" : `/${action}`;
    const method =
      !write ? "GET" : action === "revoke" ? "DELETE" : "POST";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetchImpl(
        `http://clickhouse-mcp:8000/v1/admin/warehouse-users/${req.params.id}${suffix}`,
        {
          method,
          headers: {
            Authorization: bearer,
            "Content-Type": "application/json",
          },
          redirect: "error",
          signal: controller.signal,
          ...(write ? { body: JSON.stringify(req.body) } : {}),
        },
      );
      if (!response.ok) {
        await response.body?.cancel();
        throw new AccountError(
          response.status === 400 ? "Akun ClickHouse ditolak. Periksa username, password, dan hak akses." : response.status === 409 ? "Akses berubah. Muat ulang, lalu coba lagi." : "Layanan akses data belum dapat digunakan.",
          [400, 401, 403, 404, 409, 413, 429, 503].includes(response.status)
            ? response.status
            : 503,
        );
      }
      const reader = response.body?.getReader();
      if (!reader) throw unavailable();
      let size = 0;
      const chunks = [];
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 65536) {
          await reader.cancel();
          throw unavailable();
        }
        chunks.push(Buffer.from(value));
      }
      const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      return action === "readiness" ? readinessDto(value) : action === "eligibility" ? eligibilityDto(value) : statusDto(value);
    } catch (error) {
      if (error instanceof AccountError) throw error;
      throw unavailable();
    } finally {
      clearTimeout(timer);
    }
  }
  return Object.fromEntries(
    ["status", "prepare", "credentials", "revoke", "readiness", "eligibility"].map((action) => [
      action,
      (req) => call(req, action),
    ]),
  );
}
function readinessDto(value) {
  if (value?.version !== 2 || value.scope !== 'local_configuration_only' || value.database_verified !== false || value.cloud_verified !== false) throw unavailable();
  const codes = {binding_dsn:['configured','missing_or_invalid'],enrollment_dsn:['configured','missing_or_invalid'],distinct_runtime_users:['distinct','missing_or_shared'],keyring:['active_key_available','active_key_unavailable'],warehouse_target:['https_configured','missing_or_invalid']};
  const checks = {};
  for (const [name, allowed] of Object.entries(codes)) {
    const item = value.checks?.[name];
    if (typeof item?.ok !== 'boolean' || item.code !== allowed[item.ok ? 0 : 1]) throw unavailable();
    checks[name] = {ok:item.ok,code:item.code};
  }
  return {version:2,scope:'local_configuration_only',database_verified:false,cloud_verified:false,checks};
}
function eligibilityDto(value) {
  const uuid = v => typeof v === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
  if(value?.version!==1 || typeof value.eligible!=='boolean' || (value.eligible ? !uuid(value.binding_id)||!uuid(value.revision) : value.binding_id!==null||value.revision!==null))throw unavailable();
  return {version:1,eligible:value.eligible,binding_id:value.binding_id,revision:value.revision};
}
module.exports = { createWarehouseProxy };
