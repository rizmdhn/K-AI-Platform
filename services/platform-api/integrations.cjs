// Admin Panel settings sections (OpenMetadata, batch model, limits, SMTP, pricing) and the semantic layer. Viewing needs read:configs, saving/testing
// manage:configs. Proxies the MCP's internal routes; the editor id comes from the session. Secrets go in, never out.
const { AccountError } = require("./accounts.cjs");
const MCP = "http://clickhouse-mcp:8000/v1/internal/integrations/";
const MAIL = "http://clickhouse-mcp:8000/v1/internal/mail";
const SEMANTIC = "http://clickhouse-mcp:8000/v1/internal/semantic";
// Semantic layer form fields; the MCP validates them (SQL safety, names) and keeps only what the kind uses.
const SEMANTIC_FIELDS = ["kind", "name", "status", "description", "synonyms", "table", "expression", "metric_type", "unit",
  "time_column", "dimensions", "filter", "question", "sql"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SECTIONS = {
  openmetadata: ["url", "service", "token", "ca_cert", "catalog_enabled", "databases", "scan_hours", "max_tables"],
  batch_model: ["enabled", "base_url", "model", "api_key", "jobs_per_day", "sample_rows"],
  limits: ["max_execution_seconds", "max_memory_gb", "max_rows_million", "max_read_gb", "log_retention_days"],
  smtp: ["host", "port", "security", "username", "password", "from", "from_name"],
  pricing: ["input_usd_per_million", "cached_input_usd_per_million", "output_usd_per_million", "usd_to_idr"],
};

function createIntegrations({ hasCapability, fetchImpl = fetch }) {
  async function allowed(req, write) {
    const id = String(req.user?.id ?? req.user?._id ?? "");
    if (!/^[0-9a-f]{24}$/.test(id)) throw new AccountError("Sesi Anda berakhir. Silakan login ulang.", 401);
    const principal = { id, role: req.user.role, tenantId: req.user.tenantId };
    if (!(await hasCapability(principal, "access:admin")) || !(await hasCapability(principal, write ? "manage:configs" : "read:configs")))
      throw new AccountError("Anda tidak punya izin untuk tindakan ini.", 403);
    return id;
  }
  async function mcp(path, method, body, base = MCP) {
    const token = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    if (!token) throw new AccountError("Layanan akses data (MCP) belum dikonfigurasi.", 503);
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 60000);
    try {
      const response = await fetchImpl(base + path, { method, redirect: "error", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
      const text = await response.text();
      const value = text.length <= 1_000_000 ? JSON.parse(text) : {};
      if (!response.ok) throw new AccountError(typeof value.message === "string" ? value.message : "Pengaturan integrasi tidak tersedia.", [400, 409].includes(response.status) ? response.status : 503);
      return value;
    } catch (error) {
      throw error instanceof AccountError ? error : new AccountError("Layanan akses data (MCP) tidak bisa dihubungi.", 503);
    } finally {
      clearTimeout(timer);
    }
  }
  const section = (req) => {
    const name = req.params?.name ?? "openmetadata";
    if (!Object.hasOwn(SECTIONS, name)) throw new AccountError("Bagian pengaturan tidak dikenal.", 404);
    return name;
  };
  const definition = (req) => {
    if (!UUID.test(String(req.params?.id ?? ""))) throw new AccountError("Definisi tidak ditemukan.", 404);
    return `/${req.params.id}`;
  };
  const semanticForm = (body) => Object.fromEntries(SEMANTIC_FIELDS.filter((k) => body && Object.hasOwn(body, k)).map((k) => [k, body[k]]));
  const form = (name, body) => Object.fromEntries(SECTIONS[name].filter((k) => body && Object.hasOwn(body, k)).map((k) => [k, body[k]]));
  return {
    get: async (req) => { const name = section(req); await allowed(req, false); return mcp(name, "GET"); },
    save: async (req) => { const name = section(req); const id = await allowed(req, true); return mcp(name, "PUT", { ...form(name, req.body), updated_by: id }); },
    test: async (req) => { await allowed(req, true); return mcp("openmetadata/test", "POST", form("openmetadata", req.body)); },
    databases: async (req) => { await allowed(req, false); return mcp("openmetadata/databases", "GET"); },
    semanticList: async (req) => { await allowed(req, false); return mcp("", "GET", undefined, SEMANTIC); },
    semanticCreate: async (req) => { const id = await allowed(req, true); return mcp("", "POST", { ...semanticForm(req.body), updated_by: id }, SEMANTIC); },
    semanticUpdate: async (req) => { const path = definition(req); const id = await allowed(req, true); return mcp(path, "PUT", { ...semanticForm(req.body), updated_by: id }, SEMANTIC); },
    semanticDelete: async (req) => { const path = definition(req); const id = await allowed(req, true); return mcp(path, "DELETE", { updated_by: id }, SEMANTIC); },
    /** AI draft job (local model): status for viewers; start/stop and the database list use the admin's own CH access. */
    semanticDrafts: async (req) => { await allowed(req, false); return mcp("/drafts", "GET", undefined, SEMANTIC); },
    semanticDraftStart: async (req) => {
      const id = await allowed(req, true);
      if (typeof req.body?.database !== "string") throw new AccountError("Pilih database.");
      return mcp("/drafts", "POST", { database: req.body.database, updated_by: id }, SEMANTIC);
    },
    semanticDraftStop: async (req) => { await allowed(req, true); return mcp("/drafts", "DELETE", {}, SEMANTIC); },
    /** Local model manager (catalog download with checksum, pause/resume, activate, delete); viewing needs read:configs. */
    localModels: async (req) => { await allowed(req, false); return mcp("/models", "GET", undefined, SEMANTIC); },
    localModelAction: async (req) => {
      await allowed(req, true);
      const action = req.params?.action;
      if (!["download", "pause", "activate", "delete"].includes(action)) throw new AccountError("Tindakan tidak dikenal.", 404);
      if (action === "pause") return mcp("/models/download", "DELETE", {}, SEMANTIC);
      if (typeof req.body?.model !== "string") throw new AccountError("Pilih model.");
      return mcp(`/models/${action}`, "POST", { model: req.body.model }, SEMANTIC);
    },
    semanticDatabases: async (req) => { const id = await allowed(req, true); return mcp(`/databases?user=${id}`, "GET", undefined, SEMANTIC); },
    testSmtp: async (req) => {
      await allowed(req, true);
      if (typeof req.body?.to !== "string") throw new AccountError("Isi alamat email tujuan.");
      return mcp("smtp/test", "POST", { to: req.body.to });
    },
    /** Internal (no session): saved price settings for the usage estimate; {} when the MCP is unreachable. */
    pricing: async () => (await mcp("pricing", "GET").catch(() => ({}))).settings ?? {},
    /** Never throws: {sent:true} or {sent:false, configured, message} so account actions still return their link. */
    mail: async (to, subject, text) => {
      try { await mcp("", "POST", { to, subject, text }, MAIL); return { sent: true }; }
      catch (error) { return { sent: false, configured: error?.status !== 409, message: error?.message ?? "Email tidak bisa dikirim." }; }
    },
  };
}
module.exports = { createIntegrations };
