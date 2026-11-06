const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const modulePath = path.resolve(
  __dirname,
  "../services/platform-api/accounts.cjs",
);
test("native account provisioning is installed", () =>
  assert.ok(fs.existsSync(modulePath)));
if (fs.existsSync(modulePath)) {
  const {
    parseCreate,
    parseReference,
    validatePassword,
    newActivation,
    activationHash,
    accountDto,
  } = require(modulePath);
  test("strict account boundary forbids credentials, tenant and privilege injection", () => {
    const input = {
      name: " Analyst ",
      email: "Person@example.com",
      role: "USER",
    };
    assert.equal(parseCreate(input).email, "person@example.com");
    for (const key of ["password", "tenantId", "provider", "kaiAccount", "id"])
      assert.throws(() => parseCreate({ ...input, [key]: "injected" }));
    assert.throws(() => parseCreate({ ...input, role: "ROOT" }));
    assert.throws(() => parseCreate({ ...input, email: "bad" }));
  });
  test("warehouse references preserve identity case and require manual confirmation", () => {
    assert.deepEqual(
      parseReference({
        service: "service.example",
        username: "Analyst",
        confirmed: true,
      }),
      { service: "service.example", username: "Analyst" },
    );
    assert.throws(() => parseReference({ service: "s", username: "a" }));
    assert.throws(() =>
      parseReference({ service: "s", username: " a ", confirmed: true }),
    );
    assert.throws(() =>
      parseReference({
        service: "s",
        username: "a",
        confirmed: true,
        password: "secret",
      }),
    );
    assert.equal(parseReference(null), null);
  });
  test("passwords honor native minimum and bcrypt byte limit", () => {
    assert.throws(() => validatePassword("short", "short", 8));
    assert.throws(() => validatePassword("x".repeat(73), "x".repeat(73), 8));
    assert.throws(() => validatePassword("é".repeat(37), "é".repeat(37), 8));
    assert.throws(() => validatePassword("valid-password", "mismatch", 8));
    assert.equal(
      validatePassword("valid-password", "valid-password", 8),
      "valid-password",
    );
  });
  test("activation tokens have entropy and DTOs never expose their hash", () => {
    const a = newActivation(),
      b = newActivation();
    assert.match(a.token, /^kai_[a-f0-9]{64}$/);
    assert.notEqual(a.token, b.token);
    assert.equal(a.hash, activationHash(a.token));
    const dto = accountDto({
      _id: "123",
      password: "hidden",
      kaiAccount: {
        status: "pending",
        activationHash: a.hash,
        activationExpiresAt: a.expiresAt,
        chService: "s",
        chUsername: "A",
      },
    });
    assert.equal(dto.warehouse.status, "unverified");
    assert.ok(!JSON.stringify(dto).includes(a.hash));
    assert.ok(!JSON.stringify(dto).includes("password"));
  });
}
