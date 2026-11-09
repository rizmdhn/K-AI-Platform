// P6-10: accounts and system issues for the admin "Perlu perhatian" panel. Read-only; returns codes, never secrets.
// Signals: Mongo account state + CH agent ACL (here) and the MCP's sanitized 24 h audit summary (problems.py).
const DENIALS = 5, MIN_CALLS = 20, ERROR_RATE = 0.2;
const MCP_URL = "http://clickhouse-mcp:8000/v1/internal/problems";

function createProblems({ accounts, managedAgent, aclEntries, VIEW, USER, fetchImpl = fetch, now = () => new Date() }) {
  async function summary() {
    const token = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    if (!token) return null;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetchImpl(MCP_URL, { headers: { Authorization: `Bearer ${token}` }, redirect: "error", signal: controller.signal });
      if (!response.ok) return null;
      const text = await response.text();
      const value = text.length <= 2_000_000 ? JSON.parse(text) : null;
      return value?.version === 1 ? value : null;
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  async function list(req) {
    const { actor, users } = await accounts.problemUsers(req);
    const [mcp, agent] = await Promise.all([summary(), managedAgent(actor).catch(() => undefined)]);
    const system = [];
    let viewers = new Set();
    if (agent === undefined) system.push({ code: "agent_unavailable" });
    else if (agent) {
      const view = (await aclEntries(agent._id)).filter((e) => (e.permBits & VIEW) === VIEW);
      if (view.some((e) => e.principalType !== USER)) system.push({ code: "agent_shared_broadly" });
      // ponytail: direct user grants only; group/role/public grants are flagged above instead of expanded per user
      viewers = new Set(view.filter((e) => e.principalType === USER && String(e.principalId) !== String(agent.author)).map((e) => String(e.principalId)));
    }
    if (!mcp) system.push({ code: "mcp_unreachable" });
    else {
      if (!mcp.audit_available) system.push({ code: "audit_unavailable" });
      if (!mcp.database_available) system.push({ code: "metadata_db_unavailable" });
      for (const [check, ok] of Object.entries(mcp.checks ?? {})) if (ok !== true) system.push({ code: "config_missing", check });
      if (mcp.calls_24h >= MIN_CALLS && mcp.errors_24h / mcp.calls_24h > ERROR_RATE)
        system.push({ code: "high_error_rate", calls: mcp.calls_24h, errors: mcp.errors_24h });
    }
    const active = new Set(mcp?.database_available ? mcp.active_subjects : []);
    const flagged = [];
    for (const user of users) {
      const issues = [], usage = mcp?.users?.[user.id], last = usage?.last_error;
      if (user.status === "disabled" && user.cleanupPending) issues.push({ code: "cleanup_pending" });
      if (["active", "existing"].includes(user.status) && mcp?.database_available && viewers.has(user.id) && !active.has(user.id))
        issues.push({ code: "agent_without_binding" });
      if (last?.outcome === "login_rejected" && !(usage.last_ok > last.at)) issues.push({ code: "ch_login_rejected", at: last.at });
      if (usage?.denied >= DENIALS) issues.push({ code: "many_denials", count: usage.denied });
      if (issues.length) flagged.push({ id: user.id, name: user.name, email: user.email, issues });
    }
    return { generated_at: now().toISOString(), calls_24h: mcp?.calls_24h ?? null, errors_24h: mcp?.errors_24h ?? null, system, accounts: flagged };
  }
  return { list };
}
module.exports = { createProblems };
