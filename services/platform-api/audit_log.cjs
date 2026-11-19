// Admin data access log: the MCP audit (who called which data tool, outcome, rows, duration; never SQL text or
// results), with platform names attached. Read-only; same permission as the usage dashboard.
const { AccountError } = require("./accounts.cjs");
const MCP_URL = "http://clickhouse-mcp:8000/v1/internal/audit";
const DAYS = new Set([1, 7, 30, 90]);
const OUTCOMES = new Set(["ok", "cancelled", "error", "auth_failed", "login_rejected", "access_denied", "unsafe_sql", "query_limit", "no_credentials"]);
const USER = /^[0-9a-f]{24}$/, TOOL = /^[a-z][a-z0-9_]{0,63}$/;

function createAuditLog({ hasCapability, findUsers, fetchImpl = fetch }) {
  async function list(req) {
    const id = String(req.user?.id ?? req.user?._id ?? "");
    if (!USER.test(id)) throw new AccountError("Sesi Anda berakhir. Silakan login ulang.", 401);
    const principal = { id, role: req.user.role, tenantId: req.user.tenantId };
    if (!(await hasCapability(principal, "access:admin")) || !(await hasCapability(principal, "read:users")))
      throw new AccountError("Anda tidak punya izin untuk tindakan ini.", 403);
    const q = req.query ?? {};
    const days = DAYS.has(Number(q.days)) ? Number(q.days) : 1;
    const params = new URLSearchParams({ days: String(days) });
    if (q.user) { if (!USER.test(q.user)) throw new AccountError("Isian tidak valid.", 400); params.set("user", q.user); }
    if (q.outcome) { if (!OUTCOMES.has(q.outcome)) throw new AccountError("Isian tidak valid.", 400); params.set("outcome", q.outcome); }
    if (q.tool) { if (!TOOL.test(q.tool)) throw new AccountError("Isian tidak valid.", 400); params.set("tool", q.tool); }
    const token = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    const unavailable = { days, available: false, rows: [], counts: {}, total: 0, tools: [], users: [], partial: false };
    if (!token) return unavailable;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    let log;
    try {
      const response = await fetchImpl(`${MCP_URL}?${params}`, { headers: { Authorization: `Bearer ${token}` }, redirect: "error", signal: controller.signal });
      if (!response.ok) return unavailable;
      const text = await response.text();
      log = text.length <= 5_000_000 ? JSON.parse(text) : null;
    } catch {
      return unavailable;
    } finally {
      clearTimeout(timer);
    }
    if (log?.version !== 1 || !log.available) return unavailable;
    const ids = log.users.filter((u) => USER.test(u));
    const people = new Map((ids.length ? await findUsers(ids) : []).map((u) => [String(u._id), u]));
    const person = (u) => ({ id: u, name: people.get(u)?.name || null, email: people.get(u)?.email || null });
    return {
      days, available: true, partial: !!log.partial, total: log.total, counts: log.counts, tools: log.tools,
      users: ids.map(person),
      rows: log.rows.map((r) => ({ ...r, name: people.get(r.user)?.name || null, email: people.get(r.user)?.email || null })),
    };
  }
  return { list };
}
module.exports = { createAuditLog };
