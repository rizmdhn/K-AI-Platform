// Optional local browser check: install Playwright in a private temporary directory,
// then set NODE_PATH and PLAYWRIGHT_BROWSERS_PATH. Requires the local admin stack.
// Uses temporary accounts; no real administrator password, model or CH calls.
const { chromium } = require("playwright");
const { execFileSync } = require("node:child_process");
const assert = require("node:assert/strict");
const prefix = `kai-browser-${require("crypto").randomUUID()}`;
const container = process.env.KAI_TEST_API_CONTAINER || "kai-api-1";
function nativeScript(body) {
  return execFileSync("docker", ["exec", "-i", container, "node"], {
    input: `require('module-alias').addAlias('~','/app/api');const mongoose=require('mongoose');require('@librechat/data-schemas').createModels(mongoose);(async()=>{await mongoose.connect(process.env.MONGO_URI,{autoIndex:false,autoCreate:false,serverSelectionTimeoutMS:5000});${body};await mongoose.disconnect()})().then(()=>process.exit(0),()=>process.exit(1));`,
    encoding: "utf8",
    timeout: 20000,
    stdio: ["pipe", "pipe", "pipe"],
  });
}
let fixture;
function cleanup() {
  nativeScript(
    `const users=await mongoose.model('User').collection.find({email:{$regex:${JSON.stringify("^" + prefix)}}},{projection:{_id:1}}).toArray();const ids=users.map(u=>u._id);await mongoose.model('Session').deleteMany({user:{$in:ids}});await mongoose.model('AclEntry').deleteMany({principalType:'user',principalId:{$in:[...ids,...ids.map(String)]}});await mongoose.model('User').collection.deleteMany({_id:{$in:ids}});if(await mongoose.model('User').collection.countDocuments({email:{$regex:${JSON.stringify("^" + prefix)}}}))throw new Error('Fixture cleanup failed')`,
  );
}
let step = "launch";
(async () => {
  let browser;
  try {
    fixture = JSON.parse(
      nativeScript(
        `const password=require('crypto').randomBytes(24).toString('hex');const email=${JSON.stringify(prefix + "@example.invalid")};const user=await mongoose.model('User').create({email,name:'Temporary browser admin',role:'ADMIN',provider:'local',emailVerified:true,password:await require('bcryptjs').hash(password,10)});process.stdout.write(JSON.stringify({email,password,id:String(user._id)}))`,
      ),
    );
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {}) });
    const context = await browser.newContext();
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    const errors = [];
    page.on("pageerror", () => errors.push("pageerror"));
    step = "admin login";
    await page.goto("http://localhost:3081/login");
    await page.getByLabel("Email", { exact: true }).fill(fixture.email);
    await page.locator("input[type=password]").fill(fixture.password);
    await page.getByRole("button", { name: /sign in|log in|masuk/i }).click();
    await page.waitForURL("http://localhost:3081/");
    step = "native Users";
    await page.goto("http://localhost:3081/users");
    await page.getByRole("button", { name: "Add user", exact: true }).click();
    step = "create dialog";
    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Nama", { exact: true })
      .fill("Temporary browser analyst");
    const email = fixture.email.replace(
      "@",
      "-" + require("crypto").randomBytes(4).toString("hex") + "@",
    );
    await dialog.getByLabel("Email", { exact: true }).fill(email);
    assert.equal(
      await dialog.getByLabel("Hostname service ClickHouse").count(),
      0,
    );
    assert.equal(await dialog.getByLabel("Username database").count(), 0);
    assert.equal(await dialog.getByRole("radio").count(), 0);
    await dialog
      .getByRole("button", { name: "Buat akun platform", exact: true })
      .click();
    step = "activation link";
    const link = dialog.getByRole("textbox", {
      name: "Tautan aktivasi",
      exact: true,
    });
    await link.waitFor();
    const url = await link.inputValue();
    assert.match(url, /^http:\/\/localhost:3080\/activate-account#userId=/);
    await dialog.getByRole("button", { name: "Selesai", exact: true }).click();
    step = "details";
    await page.getByRole("row").filter({ hasText: email }).click();
    await page
      .getByRole("dialog")
      .getByRole("heading", { name: "Akun", exact: true })
      .waitFor();
    await page
      .getByRole("dialog")
      .getByRole("heading", { name: "Akses chatbot CH", exact: true })
      .waitFor();
    const readiness = page.getByLabel('Detail teknis', {exact:true});
    await readiness.locator('summary').click();
    await readiness.getByText('Provider: Ollama Cloud', {exact:true}).waitFor();
    await readiness.getByRole('listitem').first().waitFor();
    assert.equal(await readiness.getByRole('listitem').count(),5);
    await page.getByText("Menunggu aktivasi", { exact: true }).waitFor();
    assert.equal(
      await page.getByRole("button", { name: "Delete", exact: true }).count(),
      0,
    );
    step = "public activation";
    const activation = await context.newPage();
    await activation.goto(url);
    step = "activation URL cleared";
    await activation.waitForURL("http://localhost:3080/activate-account");
    assert.equal(new URL(activation.url()).hash, "");
    const password = require("crypto").randomBytes(18).toString("hex");
    step = "activation password entry";
    await activation.getByLabel("Password", { exact: true }).fill(password);
    await activation
      .getByLabel("Confirm password", { exact: true })
      .fill(password);
    step = "activation submit";
    const activationResponse = activation.waitForResponse(response => response.url().endsWith("/activate-account") && response.request().method() === "POST");
    await activation
      .getByRole("button", { name: "Activate account", exact: true })
      .click();
    assert.equal((await activationResponse).status(),200, "Activation must succeed; native submission limits remain enabled.");
    step = "activation confirmation";
    try {
      await activation
        .getByRole("heading", { name: "Account activated" })
        .waitFor();
    } catch (e) {
      console.log(
        "Activation alerts",
        await activation.getByRole("alert").allTextContents(),
      );
      throw e;
    }
    step = "native user login";
    const chatContext = await browser.newContext();
    const chat = await chatContext.newPage();
    await chat.goto("http://localhost:3080/login");
    await chat.locator("#email").fill(email);
    await chat.locator("input[type=password]").fill(password);
    await chat
      .getByRole("button", { name: /continue|log in|sign in/i })
      .click();
    await chat.waitForURL(/\/c\//);
    step = "active status";
    await page.reload();
    await page.getByRole("row").filter({ hasText: email }).click();
    await page.getByText("Aktif", { exact: true }).waitFor();
    step = 'General agent visible; CH absent before assignment';
    const selector = chat.getByTestId('model-selector-button');
    await selector.waitFor();
    assert.match(await selector.textContent(), /K-AI Asisten/);
    await selector.click();
    assert.equal(await chat.getByText('K-AI Analis Data', {exact:true}).count(),0);
    await chat.keyboard.press('Escape');
    step = 'CH requires active binding in UI and API';
    const auth = await context.request.post('http://localhost:3080/api/auth/login', {data:{email:fixture.email,password:fixture.password}});
    assert.equal(auth.status(),200);
    const adminToken = (await auth.json()).token;
    const targetId = new URLSearchParams(new URL(url).hash.slice(1)).get('userId');
    const headers = {Authorization:`Bearer ${adminToken}`};
    for (const path of ['kai-warehouse-context','kai-warehouse','kai-warehouse/readiness']) {
      const response = await context.request.get(`http://localhost:3080/api/admin/users/${targetId}/${path}`,{headers});
      console.log('Local admin diagnostic',path,response.status());
    }
    const agentResponse = await context.request.get(`http://localhost:3080/api/admin/users/${targetId}/kai-agents`,{headers});
    step = 'agent status response';
    if(agentResponse.status()!==200) console.log("Agent status HTTP",agentResponse.status());
    assert.equal(agentResponse.status(),200);
    assert.equal(await page.getByLabel('Izinkan memakai K-AI - CH').isDisabled(),true);
    const access = await agentResponse.json();
    assert.equal(access.binding_active,false);
    const grant = await context.request.post(`http://localhost:3080/api/admin/users/${targetId}/kai-agents`,{headers,data:{agent_id:access.agent.id,assigned:true}});
    assert.equal(grant.status(),409);
    assert.equal((await grant.json()).message,'Isi kredensial CH dulu');
    const userAuth = await chatContext.request.post('http://localhost:3080/api/auth/login',{data:{email,password}});
    assert.equal(userAuth.status(),200);
    const oldToken = (await userAuth.json()).token;
    step = 'disable account through confirmed native Users action';
    await page.getByRole('button',{name:'Nonaktifkan akun',exact:true}).click();
    await page.getByRole('button',{name:'Nonaktifkan akun',exact:true}).last().click();
    await page.getByText('Nonaktif',{exact:true}).waitFor();
    const denied = await chatContext.request.get('http://localhost:3080/api/user',{headers:{Authorization:`Bearer ${oldToken}`}});
    step = 'disabled old JWT';
    console.log('Disabled JWT HTTP',denied.status());
    assert.equal(denied.status(),403);
    const loginDenied = await chatContext.request.post('http://localhost:3080/api/auth/login',{data:{email,password}});
    step = 'disabled login';
    console.log('Disabled login HTTP',loginDenied.status());
    // Native requireLocalAuth answers 404 for every local-strategy refusal (also a wrong password); the account
    // policy refuses disabled users there, so require the policy message, not just any failure.
    assert.ok([401,403,404].includes(loginDenied.status()), 'Disabled login must be rejected by account policy; rerun after native rate limit clears if status429.');
    assert.equal((await loginDenied.json()).message, 'Akun tidak aktif.');
    const refreshDenied = await chatContext.request.post('http://localhost:3080/api/auth/refresh');
    step = 'disabled refresh';
    console.log('Disabled refresh HTTP',refreshDenied.status());
    assert.ok([401,403].includes(refreshDenied.status()));
    nativeScript(`const id=new mongoose.Types.ObjectId(${JSON.stringify(targetId)});if(await mongoose.model('Session').collection.countDocuments({user:{$in:[id,String(id)]}}))throw Error('Disabled sessions remain')`);
    assert.equal(errors.length, 0);
    if (process.env.KAI_TEST_SCREENSHOT)
      await page.screenshot({ path: process.env.KAI_TEST_SCREENSHOT });
    console.log(
      "Browser passed: create/activate USER, local readiness, General agent available, CH requires active binding, disable confirmed, existing JWT denied, refresh checked and sessions removed; login HTTP status reported separately. No message/model/CH calls.",
    );
  } finally {
    if (browser) await browser.close();
    cleanup();
    console.log("Temporary browser accounts, sessions and ACL entries removed.");
  }
})().catch((e) => {
  console.error(
    `Browser check failed at ${step}: ${e.name}. Sensitive page content omitted.`,
  );
  process.exit(1);
});
