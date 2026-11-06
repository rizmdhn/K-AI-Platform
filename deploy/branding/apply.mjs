import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { gzipSync, brotliCompressSync } from "node:zlib";

export const ICONS = {
  "favicon-16x16.png": 16,
  "favicon-32x32.png": 32,
  "apple-touch-icon-180x180.png": 180,
  "icon-192x192.png": 192,
  "maskable-icon.png": 512,
};
const description = "K-AI platform - Your internal AI assistant";
const original =
  "LibreChat - An open source chat application with support for multiple AI models";
// kAI has no user-provided provider keys, so "Revoke all provider API keys" is a no-op; hide its row
// (and the API keys box when that row is all it holds). Upstream: Nav/Settings/registry.tsx `revokeKeys`.
const HIDE_REVOKE = `<style id="kai-hide-revoke">div:has(> div > #revoke-info-label){display:none}section:has(> div > div:only-child > div > #revoke-info-label){display:none}</style>`;
// Lock the model/agent picker once a conversation exists (/c/<id>), so a chat that touched CH data
// cannot move to another spec. ponytail: client-side only (polls the SPA path); both managed agents
// share one provider/model, so a crafted request still stays on that model.
const LOCK_PICKER = `<style id="kai-lock-picker">[data-testid="model-selector-button"][inert]{opacity:.6;cursor:not-allowed}</style><script id="kai-lock-picker-js">setInterval(function(){var b=document.querySelector('[data-testid="model-selector-button"]');if(b)b.inert=/^\\/c\\/(?!new\\b)[^/]+/.test(location.pathname)},300)</script>`;
// P6-11: LibreChat keeps its rotated file logs 14 days (hard-coded in data-schemas); follow
// KAI_LOG_RETENTION_DAYS instead (whole days, default 90), read when the api process starts.
const RETENTION_14D = 'maxFiles: "14d"';
const RETENTION_ENV =
  'maxFiles: `${/^[1-9][0-9]{0,3}$/.test(process.env.KAI_LOG_RETENTION_DAYS || "") ? process.env.KAI_LOG_RETENTION_DAYS : 90}d`';
export function patchLogRetention(source) {
  if (source.split(RETENTION_14D).length !== 4) throw new Error("Unexpected log retention seam");
  return source.replaceAll(RETENTION_14D, RETENTION_ENV);
}
// P6-12: documents live in internal RustFS (http://rustfs:9000), which browsers cannot reach. Never hand
// out presigned S3 URLs; the UI then falls back to the proxied, ownership-checked /api/files/download.
const DIRECT_URL = "  if (!getDownloadURL) {\n    return null;";
export function patchDirectDownload(source) {
  if (source.split(DIRECT_URL).length !== 2) throw new Error("Unexpected direct download seam");
  return source.replace(DIRECT_URL, "  if (!getDownloadURL || file.source === FileSources.s3) {\n    return null;");
}
// P1-05: title a new chat from the user's first message, like other chat platforms, instead of a
// second model call (no extra provider egress or tokens). addTitle still caches/persists it.
const TITLE_CALL = `client
          .titleConvo({
            text,
            abortController,
            immediate,
          })`;
const TITLE_LOCAL = `Promise.resolve(((t) => (t.length > 60 ? t.slice(0, 59).trimEnd() + "…" : t))(
          String(text ?? "").replace(/\\s+/g, " ").trim()))`;
export function patchTitleFromMessage(source) {
  if (source.split(TITLE_CALL).length !== 2) throw new Error("Unexpected title seam");
  return source.replace(TITLE_CALL, TITLE_LOCAL);
}
const digest = (data) => createHash("md5").update(data).digest("hex"); // Workbox content revision, not a security hash.

export function brandShell({ index, manifest, serviceWorker, icons }) {
  const parsed = JSON.parse(manifest);
  if (
    !["LibreChat", "kAI", "K-AI platform"].includes(parsed.name) ||
    !["LibreChat", "kAI", "K-AI platform"].includes(parsed.short_name)
  )
    throw new Error("Unexpected manifest branding seam");
  if (!/<title>(LibreChat|kAI|K-AI platform)<\/title>/.test(index))
    throw new Error("Unexpected title branding seam");
  if (!index.includes(original) && !index.includes(description))
    throw new Error("Unexpected description branding seam");
  index = index
    .replace(/<title>(LibreChat|kAI|K-AI platform)<\/title>/, "<title>K-AI platform</title>")
    .replace(original, description);
  index = index.replace(
    /(<meta name="theme-color" content=")#[a-fA-F0-9]{6}("\s*\/?>)/,
    "$1#175cd3$2",
  );
  if (!index.includes("</head>")) throw new Error("Unexpected head branding seam");
  if (!index.includes(HIDE_REVOKE)) index = index.replace("</head>", HIDE_REVOKE + "</head>");
  if (!index.includes(LOCK_PICKER)) index = index.replace("</head>", LOCK_PICKER + "</head>");
  parsed.name = "K-AI platform";
  parsed.short_name = "K-AI platform";
  parsed.description = description;
  parsed.theme_color = "#175cd3";
  parsed.background_color = "#ffffff";
  manifest = JSON.stringify(parsed);
  const revisions = {
    ...Object.fromEntries(
      Object.keys(ICONS).map((name) => ["assets/" + name, digest(icons[name])]),
    ),
    "manifest.webmanifest": digest(manifest),
  };
  for (const [name, hash] of Object.entries(revisions)) {
    const entry = new RegExp(
      'url:"' +
        name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") +
        '",revision:"[a-f0-9]{32}"',
      "g",
    );
    if ([...serviceWorker.matchAll(entry)].length !== 1)
      throw new Error("Unexpected precache branding seam");
    serviceWorker = serviceWorker.replace(
      entry,
      `url:"${name}",revision:"${hash}"`,
    );
  }
  return { index, manifest, serviceWorker };
}

async function writeVariants(path, data) {
  await writeFile(path, data);
  await writeFile(path + ".gz", gzipSync(data));
  await writeFile(path + ".br", brotliCompressSync(data));
}

async function main() {
  const sharp = createRequire("/app/package.json")("sharp"); // Already installed in the pinned upstream image.
  const dist = "/app/client/dist",
    publicAssets = "/app/client/public/assets";
  const icon = await readFile(new URL("./icon.svg", import.meta.url));
  const logo = await readFile(new URL("./logo.svg", import.meta.url));
  const icons = {};
  // The logo is wide (~2.4:1): letterbox it into the square instead of the default crop ("cover").
  // Home-screen icons get a white square; maskable keeps the logo inside the 80% safe zone.
  const clear = { r: 0, g: 0, b: 0, alpha: 0 };
  const opaqueScale = { "apple-touch-icon-180x180.png": 0.9, "maskable-icon.png": 0.7 };
  for (const [name, size] of Object.entries(ICONS)) {
    const inner = Math.round(size * (opaqueScale[name] ?? 1)), pad = size - inner;
    let image = sharp(icon, { density: 600 })
      .resize(inner, inner, { fit: "contain", background: clear })
      .extend({ top: pad >> 1, bottom: pad - (pad >> 1), left: pad >> 1, right: pad - (pad >> 1), background: clear });
    if (opaqueScale[name]) image = sharp(await image.png().toBuffer()).flatten({ background: "#ffffff" });
    icons[name] = await image.png().toBuffer();
  }
  const result = brandShell({
    index: await readFile(dist + "/index.html", "utf8"),
    manifest: await readFile(dist + "/manifest.webmanifest", "utf8"),
    serviceWorker: await readFile(dist + "/sw.js", "utf8"),
    icons,
  });
  await writeVariants(dist + "/index.html", result.index);
  await writeVariants(dist + "/manifest.webmanifest", result.manifest);
  await writeVariants(dist + "/sw.js", result.serviceWorker);
  for (const file of ["index.cjs", "index.mjs"]) {
    const path = "/app/packages/data-schemas/dist/" + file;
    await writeFile(path, patchLogRetention(await readFile(path, "utf8")));
  }
  const title = "/app/api/server/services/Endpoints/agents/title.js";
  await writeFile(title, patchTitleFromMessage(await readFile(title, "utf8")));
  const files = "/app/api/server/routes/files/files.js";
  await writeFile(files, patchDirectDownload(await readFile(files, "utf8")));
  for (const folder of [dist + "/assets", publicAssets]) {
    await writeVariants(folder + "/logo.svg", logo);
    for (const [name, bytes] of Object.entries(icons))
      await writeVariants(folder + "/" + name, bytes);
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  await main();
