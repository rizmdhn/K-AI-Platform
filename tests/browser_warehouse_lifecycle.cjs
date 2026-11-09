// P2-11: CH credential lifecycle in the native Users panel, against a throwaway local TLS ClickHouse
// (tests/warehouse_local_tls.sh), never ClickHouse Cloud. Run: make browser-warehouse-test.
// Covers: wrong password refused, correct password saved, CH agent grant only after an active binding and visible in
// chat, ungrant hides it, "Cabut akses" revokes binding + grant, disabling an account revokes binding + grant.
// Temporary accounts; no model calls. Needs NODE_PATH/PLAYWRIGHT_BROWSERS_PATH like browser_admin_accounts.cjs.
const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const prefix = `kai-wh-${crypto.randomUUID().slice(0, 8)}`;
const container = process.env.KAI_TEST_API_CONTAINER || "kai-api-1";
// One CH username maps to one platform user for good (approved_ch_accounts), so each run creates fresh CH users.
const chPassword = crypto.randomBytes(18).toString("hex");
const chAdmin = require("node:fs").readFileSync(process.env.KAI_TEST_CH_PASSWORD_FILE || "/tmp/kai-test-tls/password", "utf8");
const chUser = (who) => {
  const name = `kaiwh_${prefix.slice(7)}_${who}`;
  execFileSync("docker", ["exec", "-i", "kai-test-ch", "clickhouse-client", "--user", "kai_e2e", "--password", chAdmin, "--multiquery"], { input: `CREATE USER ${name} IDENTIFIED BY '${chPassword}'; GRANT SELECT ON system.one TO ${name};`, stdio: ["pipe", "ignore", "pipe"] });
  return name;
};
const native = (body) => execFileSync("docker", ["exec", "-i", container, "node"], {
  input: `require('module-alias').addAlias('~','/app/api');const mongoose=require('mongoose');require('@librechat/data-schemas').createModels(mongoose);(async()=>{await mongoose.connect(process.env.MONGO_URI,{autoIndex:false,autoCreate:false,serverSelectionTimeoutMS:5000});${body};await mongoose.disconnect()})().then(()=>process.exit(0),(e)=>{console.error(e.message);process.exit(1)});`,
  encoding: "utf8", timeout: 20000, stdio: ["pipe", "pipe", "pipe"],
});
const makeUser = (who, role) => JSON.parse(native(`const password=require('crypto').randomBytes(18).toString('hex');const email=${JSON.stringify(prefix)}+'-${who}@example.invalid';const u=await mongoose.model('User').create({email,name:'WH ${who}',role:'${role}',provider:'local',emailVerified:true,password:await require('bcryptjs').hash(password,10)});await mongoose.model('User').collection.updateOne({_id:u._id},{$set:{kaiAccount:{status:'active'}}});process.stdout.write(JSON.stringify({email,password,id:String(u._id)}))`));
const cleanup = () => native(`const ids=(await mongoose.model('User').collection.find({email:{$regex:${JSON.stringify("^" + prefix)}}},{projection:{_id:1}}).toArray()).map(u=>u._id);await mongoose.model('Session').deleteMany({user:{$in:ids}});await mongoose.model('AclEntry').deleteMany({principalType:'user',principalId:{$in:[...ids,...ids.map(String)]}});await mongoose.model('User').collection.deleteMany({_id:{$in:ids}})`);

let step = "setup", panelRef;
(async () => {
  let browser;
  try {
    const admin = makeUser("admin", "ADMIN"), alice = makeUser("alice", "USER"), bob = makeUser("bob", "USER");
    alice.ch = chUser("alice"); bob.ch = chUser("bob");
    browser = await chromium.launch({ headless: true });
    const panel = await (await browser.newContext()).newPage();
    panel.setDefaultTimeout(20000);
    panelRef = panel;
    step = "admin login";
    await panel.goto("http://localhost:3081/login");
    await panel.getByLabel("Email", { exact: true }).fill(admin.email);
    await panel.locator("input[type=password]").fill(admin.password);
    await panel.getByRole("button", { name: /sign in|log in|masuk/i }).click();
    await panel.waitForURL("http://localhost:3081/");
    const open = async (user) => {
      await panel.goto("http://localhost:3081/users");
      await panel.getByRole("row").filter({ hasText: user.email }).click();
      await panel.getByRole("dialog").getByRole("heading", { name: "Akses data ClickHouse" }).waitFor();
      return panel.getByRole("dialog");
    };
    const warehouse = (d) => d.getByLabel("Akses data ClickHouse", { exact: true });
    const agentBox = (d) => d.getByLabel("Izinkan memakai K-AI - CH");
    const save = async (d, password, username) => {
      const w = warehouse(d);
      const field = w.getByLabel("Username ClickHouse");
      if (await field.isEditable()) await field.fill(username);
      await w.getByLabel("Password ClickHouse").fill(password);
      await w.getByRole("button", { name: /Simpan dan uji koneksi|Ganti password/ }).click();
    };
    const chatSees = async (user) => {
      const page = await (await browser.newContext()).newPage();
      await page.goto("http://localhost:3080/login");
      await page.locator("#email").fill(user.email);
      await page.locator("input[type=password]").fill(user.password);
      await page.getByRole("button", { name: /continue|log in|sign in/i }).click();
      await page.waitForURL(/\/c\//);
      const selector = page.getByTestId("model-selector-button");
      await selector.waitFor();
      await selector.click();
      await page.getByText("K-AI Asisten", { exact: true }).last().waitFor();
      const visible = (await page.getByText("K-AI Analis Data", { exact: true }).count()) > 0;
      await page.context().close();
      return visible;
    };

    step = "wrong password refused";
    let d = await open(alice);
    await save(d, "wrong-" + crypto.randomBytes(6).toString("hex"), alice.ch);
    await warehouse(d).getByRole("alert").filter({ hasText: "Akun ClickHouse ditolak" }).waitFor();
    assert.equal(await agentBox(d).isDisabled(), true, "grant stays locked without an active binding");

    step = "correct password saved";
    await save(d, chPassword, alice.ch);
    await warehouse(d).getByRole("status").filter({ hasText: "Berhasil terhubung" }).waitFor();
    await warehouse(d).getByText(`Aktif · ${alice.ch}`).waitFor();

    step = "grant after active binding";
    await panel.waitForFunction(() => { const b = [...document.querySelectorAll("input[type=checkbox]")].find((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH")); return b && !b.disabled; });
    await agentBox(d).click();
    await panel.waitForFunction(() => [...document.querySelectorAll("input[type=checkbox]")].some((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH") && e.checked && !e.disabled));
    assert.equal(await chatSees(alice), true, "granted user sees K-AI Analis Data");

    step = "ungrant hides agent";
    d = await open(alice);
    await agentBox(d).click();
    await panel.waitForFunction(() => [...document.querySelectorAll("input[type=checkbox]")].some((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH") && !e.checked && !e.disabled));
    assert.equal(await chatSees(alice), false, "ungranted user no longer sees K-AI Analis Data");

    step = "disable revokes binding and grant";
    await agentBox(d).click();
    await panel.waitForFunction(() => [...document.querySelectorAll("input[type=checkbox]")].some((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH") && e.checked));
    await d.getByRole("button", { name: "Nonaktifkan akun", exact: true }).click();
    await panel.getByRole("button", { name: "Nonaktifkan akun", exact: true }).last().click();
    await panel.getByText("Nonaktif", { exact: true }).first().waitFor();
    d = await open(alice);
    await warehouse(d).getByText("Akun nonaktif").first().waitFor();
    assert.equal(await agentBox(d).isChecked(), false, "disable removed the CH grant");
    native(`const n=await mongoose.model('AclEntry').countDocuments({principalType:'user',principalId:{$in:[${JSON.stringify(alice.id)},new mongoose.Types.ObjectId(${JSON.stringify(alice.id)})]},resourceType:'agent'});if(n)throw new Error('disabled user still has agent ACL')`);

    step = "Cabut akses revokes binding and grant";
    d = await open(bob);
    await save(d, chPassword, bob.ch);
    await warehouse(d).getByText(`Aktif · ${bob.ch}`).waitFor();
    await panel.waitForFunction(() => { const b = [...document.querySelectorAll("input[type=checkbox]")].find((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH")); return b && !b.disabled; });
    await agentBox(d).click();
    await panel.waitForFunction(() => [...document.querySelectorAll("input[type=checkbox]")].some((e) => e.parentElement?.textContent?.includes("Izinkan memakai K-AI - CH") && e.checked));
    await warehouse(d).getByRole("button", { name: "Cabut akses", exact: true }).click();
    await panel.getByRole("button", { name: /Cabut akses/ }).last().click();
    await warehouse(d).getByText("Dicabut", { exact: true }).waitFor();
    d = await open(bob);
    assert.equal(await agentBox(d).isChecked(), false, "revoke removed the CH grant");
    assert.equal(await chatSees(bob), false, "revoked user does not see K-AI Analis Data");

    console.log("Warehouse lifecycle OK: wrong password refused, save+grant+chat visibility, ungrant, disable and Cabut akses revoke binding and grant. Local TLS ClickHouse only; no model calls.");
  } catch (error) {
    // Diagnostic only: the ClickHouse section text (server, username, status, messages; never a password).
    const text = await panelRef?.getByLabel("Akses data ClickHouse", { exact: true }).innerText({ timeout: 2000 }).catch(() => "");
    if (text) console.error("ClickHouse section:", text.replace(/\s+/g, " ").slice(0, 400));
    throw error;
  } finally {
    await browser?.close();
    cleanup();
  }
})().catch((error) => { console.error(`FAIL at ${step}: ${error.message.split("\n")[0]}`); process.exit(1); });
