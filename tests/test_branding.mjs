import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { brandShell, ICONS, patchLogRetention, patchDirectDownload, patchTitleFromMessage } from "../deploy/branding/apply.mjs";

function fixture() {
  const icons = Object.fromEntries(
    Object.keys(ICONS).map((name) => [name, Buffer.from(name)]),
  );
  return {
    index:
      '<html><head><title>LibreChat</title><meta name="theme-color" content="#171717"><meta name="description" content="LibreChat - An open source chat application with support for multiple AI models"></head><body><script src="assets/unchanged.js"></script></body></html>',
    manifest: JSON.stringify({
      name: "LibreChat",
      short_name: "LibreChat",
      start_url: "./",
      scope: "./",
      display: "standalone",
      icons: [{ src: "assets/icon-192x192.png", sizes: "192x192" }],
    }),
    serviceWorker:
      [...Object.keys(icons), "manifest.webmanifest"]
        .map(
          (name) =>
            `{url:"${name === "manifest.webmanifest" ? name : "assets/" + name}",revision:"${"0".repeat(32)}"}`,
        )
        .join(",") + ',{url:"assets/unchanged.js",revision:null}',
    icons,
  };
}
test("brands the shell and manifest while preserving upstream application references", () => {
  const input = fixture(),
    result = brandShell(input);
  assert.match(result.index, /<title>K-AI platform<\/title>/);
  assert.match(result.index, /<meta name="theme-color" content="#175cd3">/);
  assert.match(result.index, /content="K-AI platform - Your internal AI assistant"/);
  assert.match(result.index, /<script src="assets\/unchanged.js"><\/script>/);
  assert.match(result.index, /<style id="kai-hide-revoke">[^<]*#revoke-info-label[^<]*<\/style>/);
  const lock = result.index.match(/<script id="kai-lock-picker-js">([^<]*)<\/script><\/head>/);
  assert.ok(lock, "picker lock script before </head>");
  const pattern = new RegExp(lock[1].match(/\/(\^.*?)\/\.test/)[1]);
  for (const path of ["/c/new", "/", "/agents"]) assert.equal(pattern.test(path), false, path);
  assert.equal(pattern.test("/c/0b6f2c1e-9f1a-4c7e-bb2d-1a2b3c4d5e6f"), true);
  assert.equal(brandShell({ ...input, index: result.index }).index.split("kai-lock-picker-js").length, 2, "idempotent");
  const manifest = JSON.parse(result.manifest);
  assert.equal(manifest.name, "K-AI platform");
  assert.equal(manifest.short_name, "K-AI platform");
  assert.equal(manifest.start_url, "./");
  assert.equal(manifest.scope, "./");
  assert.equal(manifest.display, "standalone");
  assert.deepEqual(manifest.icons, JSON.parse(input.manifest).icons);
});
test("revisions change for branded install assets and repeated application stays stable", () => {
  const input = fixture(),
    result = brandShell(input);
  const digest = createHash("md5")
    .update(input.icons["icon-192x192.png"])
    .digest("hex");
  assert.ok(
    result.serviceWorker.includes(
      `url:"assets/icon-192x192.png",revision:"${digest}"`,
    ),
  );
  assert.ok(
    result.serviceWorker.includes('url:"assets/unchanged.js",revision:null'),
  );
  const again = brandShell({ ...result, icons: input.icons });
  assert.deepEqual(again, result);
});
test("refuses unexpected branding seams before producing a shell", () => {
  for (const changes of [
    { index: "<title>Something else</title>" },
    { manifest: "{}" },
    { serviceWorker: "changed upstream cache format" },
  ]) {
    assert.throws(
      () => brandShell({ ...fixture(), ...changes }),
      /branding seam/,
    );
  }
});

test("log retention follows KAI_LOG_RETENTION_DAYS with a safe default", () => {
  const upstream = 'a({maxFiles: "14d"}); b({maxFiles: "14d"}); c({maxFiles: "14d"});';
  const patched = patchLogRetention(upstream);
  assert.throws(() => patchLogRetention('a({maxFiles: "14d"})'), /seam/);
  const value = patched.match(/maxFiles: (`[^`]*`)/)[1];
  const evaluate = (env) => new Function("process", `return ${value}`)({ env });
  assert.equal(evaluate({}), "90d");
  assert.equal(evaluate({ KAI_LOG_RETENTION_DAYS: "30" }), "30d");
  for (const bad of ["0", "abc", "-1", "99999", "30d"]) assert.equal(evaluate({ KAI_LOG_RETENTION_DAYS: bad }), "90d", bad);
});

test("S3 documents never get a presigned browser URL", () => {
  const upstream = "const f = async ({ file }) => {\n  if (!getDownloadURL) {\n    return null;\n  }\n};";
  assert.match(patchDirectDownload(upstream), /if \(!getDownloadURL \|\| file\.source === FileSources\.s3\) \{/);
  assert.throws(() => patchDirectDownload("no seam"), /seam/);
});

test("chat title comes from the first message without a model call", async () => {
  const upstream = "titlePromise = Promise.race([\n        client\n          .titleConvo({\n            text,\n            abortController,\n            immediate,\n          })\n          .catch(() => {}),\n      ]);";
  const patched = patchTitleFromMessage(upstream);
  assert.doesNotMatch(patched, /titleConvo/);
  const expr = patched.match(/Promise\.resolve\([\s\S]*\)\)\)/)[0];
  const run = (text) => new Function("text", `return ${expr}`)(text);
  assert.equal(await run("  Berapa total   penjualan\nbulan ini? "), "Berapa total penjualan bulan ini?");
  const long = await run("a".repeat(100));
  assert.equal(long.length, 60);
  assert.ok(long.endsWith("…"));
  assert.throws(() => patchTitleFromMessage("no seam"), /seam/);
});
