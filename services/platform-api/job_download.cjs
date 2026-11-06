// AF-02: CSV of a bulk labelling job. The link carries an MCP-signed 15-minute signature bound to the job and
// its owner, so a plain browser link works without the API bearer; MCP verifies signature, expiry and owner.
const MAX_BYTES = 20 * 1024 * 1024;
function createJobDownload({ fetchImpl = fetch, token = () => process.env.CLICKHOUSE_MCP_AUTH_TOKEN } = {}) {
  return async function download(req, res) {
    res.set("Cache-Control", "no-store");
    const match = /^([0-9a-f]{32})\.csv$/.exec(req.params?.file ?? "");
    const { exp, sig } = req.query ?? {};
    if (!match || typeof exp !== "string" || !/^\d{10}$/.test(exp) || typeof sig !== "string" || !/^[0-9a-f]{64}$/.test(sig))
      return res.status(400).json({ message: "Link tidak valid." });
    try {
      const upstream = await fetchImpl(`http://clickhouse-mcp:8000/v1/jobs/${match[1]}.csv?exp=${exp}&sig=${sig}`, {
        headers: { Authorization: `Bearer ${token()}` },
        redirect: "error",
        signal: AbortSignal.timeout(20000),
      });
      if (upstream.status !== 200) {
        await upstream.body?.cancel();
        return res.status(upstream.status === 403 ? 403 : 503).json({
          message: upstream.status === 403 ? "Link kedaluwarsa atau tidak valid. Minta link baru ke asisten." : "Unduhan belum tersedia.",
        });
      }
      const body = Buffer.from(await upstream.arrayBuffer());
      if (body.length > MAX_BYTES) return res.status(503).json({ message: "Unduhan belum tersedia." });
      res.set("Content-Type", "text/csv; charset=utf-8");
      res.set("Content-Disposition", `attachment; filename="kai-label-${match[1].slice(0, 8)}.csv"`);
      return res.status(200).send(body);
    } catch {
      return res.status(503).json({ message: "Unduhan belum tersedia." });
    }
  };
}
module.exports = { createJobDownload };
