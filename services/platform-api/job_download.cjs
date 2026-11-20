// AF-02 CSV of a bulk labelling job, and files made by create_document. The link carries an MCP-signed signature
// bound to the item and its owner, so a plain browser link works without the API bearer; MCP verifies signature,
// expiry and owner and sets the file name.
const MAX_BYTES = 20 * 1024 * 1024;
const TYPES = {
  csv: "text/csv; charset=utf-8",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pdf: "application/pdf",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};
function createDownload({ path, exts, again, fetchImpl = fetch, token = () => process.env.CLICKHOUSE_MCP_AUTH_TOKEN }) {
  const pattern = new RegExp(`^([0-9a-f]{32})\\.(${exts.join("|")})$`);
  return async function download(req, res) {
    res.set("Cache-Control", "no-store");
    const match = pattern.exec(req.params?.file ?? "");
    const { exp, sig } = req.query ?? {};
    if (!match || typeof exp !== "string" || !/^\d{10}$/.test(exp) || typeof sig !== "string" || !/^[0-9a-f]{64}$/.test(sig))
      return res.status(400).json({ message: "Link tidak valid." });
    try {
      const upstream = await fetchImpl(`http://clickhouse-mcp:8000/v1/${path}/${match[1]}.${match[2]}?exp=${exp}&sig=${sig}`, {
        headers: { Authorization: `Bearer ${token()}` },
        redirect: "error",
        signal: AbortSignal.timeout(20000),
      });
      if (upstream.status !== 200) {
        await upstream.body?.cancel();
        return res.status(upstream.status === 403 ? 403 : 503).json({
          message: upstream.status === 403 ? `Link kedaluwarsa atau tidak valid. ${again}` : "Unduhan belum tersedia.",
        });
      }
      const body = Buffer.from(await upstream.arrayBuffer());
      if (body.length > MAX_BYTES) return res.status(503).json({ message: "Unduhan belum tersedia." });
      const name = /filename="([A-Za-z0-9 ._-]+)"/.exec(upstream.headers?.get?.("content-disposition") ?? "")?.[1] ?? `kai-${match[1].slice(0, 8)}.${match[2]}`;
      res.set("Content-Type", TYPES[match[2]]);
      res.set("Content-Disposition", `attachment; filename="${name}"`);
      return res.status(200).send(body);
    } catch {
      return res.status(503).json({ message: "Unduhan belum tersedia." });
    }
  };
}
const createJobDownload = (deps = {}) => createDownload({ path: "jobs", exts: ["csv"], again: "Minta link baru ke asisten.", ...deps });
const createFileDownload = (deps = {}) =>
  createDownload({ path: "files", exts: Object.keys(TYPES), again: "Minta asisten membuat file-nya lagi.", ...deps });
module.exports = { createJobDownload, createFileDownload };
