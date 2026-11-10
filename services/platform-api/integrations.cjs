// Admin Panel settings sections (OpenMetadata, batch model, limits). Viewing needs read:configs, saving/testing
// manage:configs. Proxies the MCP's internal routes; the editor id comes from the session. Secrets go in, never out.
const { AccountError } = require("./accounts.cjs");
const MCP = "http://clickhouse-mcp:8000/v1/internal/integrations/";
const SECTIONS = {
  openmetadata: ["url", "service", "token", "ca_cert", "catalog_enabled", "databases", "scan_hours", "max_tables"],
  batch_model: ["enabled", "base_url", "model", "api_key", "jobs_per_day", "sample_rows"],
  limits: ["max_execution_seconds", "max_memory_gb", "max_rows_million", "max_read_gb", "log_retention_days"],
};

function createIntegrations({ hasCapability, fetchImpl = fetch }) {
  async function allowed(req, write) {
    const id = String(req.user?.id ?? req.user?._id ?? "");
    if (!/^[0-9a-f]{24}$/.test(id)) throw new AccountError("Unauthorized.", 401);
    const principal = { id, role: req.user.role, tenantId: req.user.tenantId };
    if (!(await hasCapability(principal, "access:admin")) || !(await hasCapability(principal, write ? "manage:configs" : "read:configs")))
      throw new AccountError("Insufficient permissions.", 403);
    return id;
  }
  async function mcp(path, method, body) {
    const token = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    if (!token) throw new AccountError("Layanan akses data (MCP) belum dikonfigurasi.", 503);
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetchImpl(MCP + path, { method, redirect: "error", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
      const text = await response.text();
      const value = text.length <= 1_000_000 ? JSON.parse(text) : {};
      if (!response.ok) throw new AccountError(typeof value.message === "string" ? value.message : "Pengaturan integrasi tidak tersedia.", response.status === 400 ? 400 : 503);
      return value;
    } catch (error) {
      throw error instanceof AccountError ? error : new AccountError("Layanan akses data (MCP) tidak bisa dihubungi.", 503);
    } finally {
      clearTimeout(timer);
    }
  }
  const section = (req) => {
    const name = req.params?.name ?? "openmetadata";
    if (!Object.hasOwn(SECTIONS, name)) throw new AccountError("Not found.", 404);
    return name;
  };
  const form = (name, body) => Object.fromEntries(SECTIONS[name].filter((k) => body && Object.hasOwn(body, k)).map((k) => [k, body[k]]));
  return {
    get: async (req) => { const name = section(req); await allowed(req, false); return mcp(name, "GET"); },
    save: async (req) => { const name = section(req); const id = await allowed(req, true); return mcp(name, "PUT", { ...form(name, req.body), updated_by: id }); },
    test: async (req) => { await allowed(req, true); return mcp("openmetadata/test", "POST", form("openmetadata", req.body)); },
    databases: async (req) => { await allowed(req, false); return mcp("openmetadata/databases", "GET"); },
  };
}
module.exports = { createIntegrations };
