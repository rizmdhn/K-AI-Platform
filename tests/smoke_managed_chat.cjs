// Run through stdin in the local API container; no provider/ClickHouse requests.
const assert = require("node:assert/strict");
const { randomUUID, randomBytes } = require("node:crypto");
require("module-alias").addAlias("~", "/app/api");
const mongoose = require("mongoose");
const schemas = require("@librechat/data-schemas");
const api = require("@librechat/api");
const data = require("librechat-data-provider");
schemas.createModels(mongoose);
const db = require("~/models");

async function main() {
  let user;
  try {
    const config = data.configSchema.parse(api.loadYaml("/app/librechat.yaml"));
    const specs = schemas.processModelSpecs(
      config.endpoints,
      config.modelSpecs,
      config.interface,
    );
    assert.equal(specs.enforce, true);
    assert.equal(specs.list.length, 1);
    const spec = specs.list[0];
    assert.equal(spec.default, true);
    assert.equal(spec.artifacts, true);
    assert.deepEqual(spec.mcpServers ?? [], []);
    assert.equal(
      spec.preset.model,
      config.endpoints.custom[0].models.default[0],
    );
    assert.equal(spec.preset.model.includes("${"), false);
    for (const flag of ["modelSelect", "parameters", "presets"]) {
      assert.equal(config.interface[flag], false);
    }
    assert.equal(
      api.resolveModelSpecForEndpoint({
        modelSpecs: specs,
        spec: "unapproved",
        endpoint: spec.preset.endpoint,
      }).error,
      "invalid-model-spec",
    );
    assert.equal(
      api.resolveModelSpecForEndpoint({
        modelSpecs: specs,
        spec: spec.name,
        endpoint: "openAI",
      }).error,
      "model-spec-mismatch",
    );
    const enforced = api.applyModelSpecPreset({
      modelSpec: spec,
      parsedBody: { model: "unapproved-expensive-model" },
      endpoint: spec.preset.endpoint,
      endpointType: "custom",
      defaultParamsEndpoint: "openAI",
      includePresetDefaults: true,
    });
    assert.equal(enforced.parsedBody.model, spec.preset.model);
    console.log("Pinned runtime schema and model enforcement: passed.");

    await mongoose.connect(process.env.MONGO_URI, {
      autoIndex: false,
      autoCreate: false,
      serverSelectionTimeoutMS: 5000,
    });
    const ordinary = await db.getRoleByName("USER");
    const admin = await db.getRoleByName("ADMIN");
    assert.equal(ordinary.permissions.AGENTS.USE, true);
    assert.equal(ordinary.permissions.AGENTS.CREATE, false);
    assert.equal(ordinary.permissions.MCP_SERVERS.USE, false);
    assert.equal(ordinary.permissions.MULTI_CONVO.USE, false);
    assert.equal(admin.permissions.AGENTS.CREATE, true);
    assert.equal(admin.permissions.MCP_SERVERS.USE, true);
    // A global permission seed must not silently replace native ADMIN decisions.
    const adminBefore = structuredClone(admin.permissions);
    await api.updateInterfacePermissions({
      appConfig: { config, interfaceConfig: config.interface },
      getRoleByName: db.getRoleByName,
      updateAccessPermissions: async (name, updates) => {
        if (name !== "ADMIN") return;
        for (const [type, actions] of Object.entries(updates)) {
          for (const [action, value] of Object.entries(actions)) {
            if (value !== undefined)
              assert.equal(
                adminBefore[type]?.[action],
                value,
                `ADMIN ${type}.${action} changed by YAML`,
              );
          }
        }
      },
    });

    // A temporary USER avoids changing, impersonating or needing a real person's login.
    const password = randomBytes(24).toString("hex");
    const email = `kai-smoke-${randomUUID()}@example.invalid`;
    user = await mongoose.models.User.create({
      email,
      username: email,
      name: "Temporary managed-chat check",
      role: "USER",
      provider: "local",
      emailVerified: true,
      password: await require("bcryptjs").hash(password, 10),
    });
    const base = `http://127.0.0.1:${process.env.PORT || 3080}`;
    const login = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      signal: AbortSignal.timeout(10000),
    });
    assert.equal(login.status, 200, "temporary USER login");
    const session = await login.json();
    assert.equal(typeof session.token, "string");
    assert.equal(
      session.user.role,
      "USER",
      "login must retain the temporary USER role",
    );
    const headers = {
      Authorization: `Bearer ${session.token}`,
      "Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36",
    };
    const startup = await fetch(`${base}/api/config`, {
      headers,
      signal: AbortSignal.timeout(10000),
    });
    assert.equal(startup.status, 200);
    const actual = await startup.json();
    assert.equal(actual.modelSpecs.enforce, true);
    assert.equal(actual.modelSpecs.list[0].name, spec.name);
    assert.equal(actual.modelSpecs.list[0].preset.model, spec.preset.model);
    assert.equal(actual.interface.modelSelect, false);
    for (const [path, method] of [
      ["/api/agents", "POST"],
      ["/api/mcp/servers", "GET"],
      ["/api/admin/users", "GET"],
    ]) {
      const response = await fetch(`${base}${path}`, {
        method,
        headers,
        ...(method === "POST" ? { body: "{}" } : {}),
        signal: AbortSignal.timeout(10000),
      });
      console.log(
        `${method} ${path}: ${response.status}, ${response.headers.get("content-type")}, path ${new URL(response.url).pathname}`,
      );
      assert.equal(response.status, 403, `${method} ${path} must deny USER`);
    }
    console.log(
      "Real USER login, startup spec, Agent/MCP/admin HTTP denial: passed.",
    );
  } finally {
    if (user) {
      await mongoose.models.Session.deleteMany({ user: user._id });
      await mongoose.models.User.deleteOne({ _id: user._id });
      assert.equal(
        await mongoose.models.User.countDocuments({ _id: user._id }),
        0,
      );
      assert.equal(
        await mongoose.models.Session.countDocuments({ user: user._id }),
        0,
      );
      console.log("Temporary USER and login sessions removed.");
    }
    await mongoose.disconnect();
  }
}

main().then(
  () => process.exit(0),
  (error) => {
    // Assertion text is safe; HTTP response bodies, tokens and database errors are not printed.
    console.error(
      error instanceof assert.AssertionError
        ? error.message
        : "Managed-chat runtime check failed.",
    );
    process.exit(1);
  },
);
