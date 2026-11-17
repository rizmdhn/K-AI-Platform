// Token usage and cost estimate for the admin dashboard, read from LibreChat's own `transactions`
// (one prompt row and one completion row per model call, rawAmount negative). Read-only, no chat content.
const { AccountError } = require("./accounts.cjs");
const PERIODS = new Set([1, 7, 30, 90]);
const TOP_USERS = 20;
const HOUR = 3_600_000, DAY = 86_400_000;

/** Wall-clock parts of `date` in `timeZone`, and that zone's UTC offset at that moment (ms). */
function zoned(date, timeZone) {
  const part = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone, hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit" }).formatToParts(date).map((p) => [p.type, p.value]));
  const local = Date.UTC(+part.year, +part.month - 1, +part.day, +part.hour);
  return { key: `${part.year}-${part.month}-${part.day}`, hour: part.hour, local, offset: local - Math.floor(date.getTime() / HOUR) * HOUR };
}

/** Every bucket of the period, oldest first: 24 hours for one day, otherwise one per calendar day (today included). */
function buckets(days, at, timeZone) {
  const hourly = days === 1, count = hourly ? 24 : days, step = hourly ? HOUR : DAY;
  const keys = [];
  for (let i = count - 1; i >= 0; i--) {
    const z = zoned(new Date(at.getTime() - i * step), timeZone);
    keys.push(hourly ? `${z.key} ${z.hour}` : z.key);
  }
  const first = zoned(new Date(at.getTime() - (count - 1) * step), timeZone);
  const start = (hourly ? first.local : Date.UTC(...first.key.split("-").map((v, i) => (i === 1 ? v - 1 : +v)))) - first.offset;
  return { keys, since: new Date(start), format: hourly ? "%Y-%m-%d %H" : "%Y-%m-%d" };
}

function createUsage({ hasCapability, aggregate, findUsers, pricing, now = () => new Date() }) {
  async function allowed(req) {
    const id = String(req.user?.id ?? req.user?._id ?? "");
    if (!/^[0-9a-f]{24}$/.test(id)) throw new AccountError("Sesi Anda berakhir. Silakan login ulang.", 401);
    const principal = { id, role: req.user.role, tenantId: req.user.tenantId };
    if (!(await hasCapability(principal, "access:admin")) || !(await hasCapability(principal, "read:users")))
      throw new AccountError("Anda tidak punya izin untuk tindakan ini.", 403);
  }

  async function summary(req) {
    await allowed(req);
    const days = PERIODS.has(Number(req.query?.days)) ? Number(req.query.days) : 30;
    const timeZone = process.env.TZ || "Asia/Jakarta";
    const { keys, since, format } = buckets(days, now(), timeZone);
    // Same-length window just before this one, for the "vs previous period" deltas on the KPI tiles.
    const previousSince = new Date(since.getTime() - (now().getTime() - since.getTime()));
    const [rows, prices, previousRows] = await Promise.all([
      aggregate([
        { $match: { createdAt: { $gte: since }, tokenType: { $in: ["prompt", "completion"] } } },
        { $group: {
          _id: { user: "$user", model: "$model", type: "$tokenType",
                 day: { $dateToString: { format, date: "$createdAt", timezone: timeZone } } },
          tokens: { $sum: { $abs: "$rawAmount" } }, calls: { $sum: 1 } } },
      ]),
      pricing(),
      aggregate([
        { $match: { createdAt: { $gte: previousSince, $lt: since }, tokenType: { $in: ["prompt", "completion"] } } },
        { $group: { _id: "$tokenType", tokens: { $sum: { $abs: "$rawAmount" } }, calls: { $sum: 1 } } },
      ]),
    ]);
    const previous = { prompt: 0, completion: 0, calls: 0 };
    for (const { _id, tokens, calls } of previousRows) {
      if (_id === "prompt") previous.prompt += tokens;
      else { previous.completion += tokens; previous.calls += calls; }
    }
    const price = (key) => (prices?.[key] === "" || prices?.[key] == null ? null : Number(prices[key]));
    const input = price("input_usd_per_million"), output = price("output_usd_per_million"), rate = price("usd_to_idr");
    const priced = input != null && output != null;
    const cost = (t) => (priced ? (t.prompt * input + t.completion * output) / 1e6 : null);
    const bucket = (map, key) => map.get(key) ?? map.set(key, { prompt: 0, completion: 0, calls: 0 }).get(key);
    const total = { prompt: 0, completion: 0, calls: 0 }, users = new Map(), models = new Map(), daily = new Map();
    for (const { _id, tokens, calls } of rows) {
      const type = _id.type === "prompt" ? "prompt" : "completion";
      for (const t of [total, bucket(users, String(_id.user)), bucket(models, _id.model || "-"), bucket(daily, _id.day)]) {
        t[type] += tokens;
        if (type === "completion") t.calls += calls;  // one completion row per model call
      }
    }
    const top = [...users].sort((a, b) => b[1].prompt + b[1].completion - (a[1].prompt + a[1].completion)).slice(0, TOP_USERS);
    const ids = top.map(([id]) => id).filter((id) => /^[0-9a-f]{24}$/.test(id));
    const people = new Map((ids.length ? await findUsers(ids) : []).map((u) => [String(u._id), u]));
    const withCost = (t) => ({ ...t, usd: cost(t) });
    return {
      days, since: since.toISOString(),
      pricing: { priced, input_usd_per_million: input, output_usd_per_million: output, usd_to_idr: rate },
      total: withCost(total), previous: withCost(previous),
      users: top.map(([id, t]) => ({ id, name: people.get(id)?.name || null, email: people.get(id)?.email || null, ...withCost(t) })),
      user_count: users.size,
      models: [...models].map(([model, t]) => ({ model, ...withCost(t) })).sort((a, b) => b.prompt + b.completion - (a.prompt + a.completion)),
      bucket: days === 1 ? "hour" : "day",
      daily: keys.map((day) => ({ day, ...(daily.get(day) ?? { prompt: 0, completion: 0, calls: 0 }) })),
    };
  }
  return { summary };
}
module.exports = { createUsage };
