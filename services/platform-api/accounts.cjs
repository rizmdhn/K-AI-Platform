const { randomBytes, createHash, timingSafeEqual } = require("node:crypto");

class AccountError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}
function strict(value, keys) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).some((k) => !keys.includes(k))
  )
    throw new AccountError("Invalid account fields.");
}
function text(value, max) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value !== value.trim() ||
    Buffer.byteLength(value) > max ||
    /[\x00-\x1f\x7f]/.test(value)
  )
    throw new AccountError("Invalid account details.");
  return value;
}
function parseReference(value) {
  if (value == null) return null;
  strict(value, ["service", "username", "confirmed"]);
  const service = text(value.service, 253),
    username = text(value.username, 1024);
  if (value.confirmed !== true || !/^[a-zA-Z0-9][a-zA-Z0-9.-]*$/.test(service))
    throw new AccountError("Confirm the manually created ClickHouse account.");
  return { service: service.toLowerCase(), username };
}
function parseCreate(value) {
  strict(value, ["name", "email", "role", "warehouse"]);
  const name = text(
    typeof value.name === "string" ? value.name.trim() : value.name,
    200,
  );
  const email = text(value.email, 254).toLowerCase();
  if (
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ||
    !["USER", "ADMIN"].includes(value.role)
  )
    throw new AccountError("Enter a valid email and platform role.");
  return {
    name,
    email,
    role: value.role,
    warehouse: parseReference(value.warehouse),
  };
}
function validatePassword(password, confirm, min = 8) {
  if (
    typeof password !== "string" ||
    password.length < Math.max(8, min) ||
    Buffer.byteLength(password) > 72 ||
    password.includes("\0")
  )
    throw new AccountError(
      `Use at least ${Math.max(8, min)} characters and at most 72 bytes.`,
    );
  if (password !== confirm) throw new AccountError("Passwords do not match.");
  return password;
}
const activationHash = (token) =>
  createHash("sha256").update(token).digest("hex");
function newActivation() {
  const token = `kai_${randomBytes(32).toString("hex")}`;
  return {
    token,
    hash: activationHash(token),
    expiresAt: new Date(Date.now() + 24 * 60 * 60 * 1000),
  };
}
function accountDto(user) {
  const a = user.kaiAccount ?? {};
  return {
    id: String(user._id),
    status: a.status ?? "existing",
    expiresAt: a.status === "pending" ? a.activationExpiresAt : undefined,
    warehouse_revocation_pending: a.warehouseRevocationPending === true,
    warehouse:
      a.chService && a.chUsername
        ? { service: a.chService, username: a.chUsername, status: "unverified" }
        : null,
  };
}
function createAccounts({ mongoose, hasCapability, bcrypt, origin }) {
  const User = mongoose.model("User");
  if (!User.schema.path("kaiAccount"))
    User.schema.add({
      kaiAccount: {
        type: new mongoose.Schema(
          {
            status: String,
            activationHash: String,
            activationExpiresAt: Date,
            createdBy: String,
            activatedAt: Date,
            disabledAt: Date,
            disabledBy: String,
            chAclCleanupPending: Boolean,
            warehouseRevocationPending: Boolean,
            chService: String,
            chUsername: String,
          },
          { _id: false },
        ),
        select: false,
      },
    });
  const collection = User.collection;
  let indexReady;
  const ready = () =>
    (indexReady ??= collection
      .createIndex(
        { tenantId: 1, "kaiAccount.chService": 1, "kaiAccount.chUsername": 1 },
        {
          name: "kai_unique_warehouse_reference",
          unique: true,
          partialFilterExpression: {
            "kaiAccount.chService": { $type: "string" },
            "kaiAccount.chUsername": { $type: "string" },
          },
        },
      )
      .catch(() => {
        indexReady = null;
        throw new AccountError("Account setup is unavailable.", 503);
      }));
  const id = (value) => {
    if (typeof value !== "string" || !/^[a-f0-9]{24}$/.test(value))
      throw new AccountError("Invalid user.", 400);
    return new mongoose.Types.ObjectId(value);
  };
  const find = (query, projection) =>
    collection.findOne(query, { projection, maxTimeMS: 2000 });
  const scope = (actor) => ({ tenantId: actor.tenantId ?? null });
  async function actorFor(req, write) {
    if (!req.user?.id && !req.user?._id) throw new AccountError("Unauthorized.", 401);
    const actor = await find(
      { _id: id(String(req.user.id ?? req.user._id)) },
      { role: 1, tenantId: 1, "kaiAccount.status": 1 },
    );
    if (
      !actor ||
      !["active", "existing"].includes(actor.kaiAccount?.status ?? "existing")
    )
      throw new AccountError("Unauthorized.", 401);
    const principal = {
      id: String(actor._id),
      role: actor.role,
      tenantId: actor.tenantId,
    };
    if (
      !(await hasCapability(principal, "access:admin")) ||
      !(await hasCapability(principal, write ? "manage:users" : "read:users"))
    )
      throw new AccountError("Insufficient permissions.", 403);
    return actor;
  }
  async function targetFor(actor, userId, write) {
    const target = await find({ _id: id(userId), ...scope(actor) });
    if (!target) throw new AccountError("User not found.", 404);
    if (write && target.role === "ADMIN" && actor.role !== "ADMIN")
      throw new AccountError(
        "Only a platform ADMIN can manage an ADMIN account.",
        403,
      );
    return target;
  }
  function invitation(userId, activation) {
    return {
      activationUrl: `${origin}/activate-account#userId=${userId}&token=${activation.token}`,
      expiresAt: activation.expiresAt,
    };
  }
  async function create(req) {
    const actor = await actorFor(req, true),
      input = parseCreate(req.body);
    if (input.role === "ADMIN" && actor.role !== "ADMIN")
      throw new AccountError(
        "Only a platform ADMIN can create an ADMIN account.",
        403,
      );
    await ready();
    const a = newActivation(),
      meta = {
        status: "pending",
        activationHash: a.hash,
        activationExpiresAt: a.expiresAt,
        createdBy: String(actor._id),
      };
    if (input.warehouse)
      Object.assign(meta, {
        chService: input.warehouse.service,
        chUsername: input.warehouse.username,
      });
    // No password exists until the invitation capability is consumed.
    const user = await User.create({
      name: input.name,
      email: input.email,
      role: input.role,
      provider: "local",
      emailVerified: false,
      ...(actor.tenantId ? { tenantId: actor.tenantId } : {}),
      kaiAccount: meta,
    });
    return {
      account: accountDto(user.toObject()),
      ...invitation(String(user._id), a),
    };
  }
  // P6-10: tenant-scoped account states for the admin problem view; no hashes or credentials.
  async function problemUsers(req) {
    const actor = await actorFor(req, false);
    const users = await collection
      .find(scope(actor), { projection: { name: 1, email: 1, "kaiAccount.status": 1, "kaiAccount.warehouseRevocationPending": 1, "kaiAccount.chAclCleanupPending": 1 }, limit: 5000, maxTimeMS: 5000 })
      .toArray();
    return {
      actor: { id: String(actor._id), role: actor.role, tenantId: actor.tenantId ?? null },
      users: users.map((u) => ({
        id: String(u._id), name: u.name ?? "", email: u.email ?? "",
        status: u.kaiAccount?.status ?? "existing",
        cleanupPending: u.kaiAccount?.warehouseRevocationPending === true || u.kaiAccount?.chAclCleanupPending === true,
      })),
    };
  }
  async function read(req) {
    const actor = await actorFor(req, false);
    return accountDto(await targetFor(actor, req.params.id, false));
  }
  async function update(req) {
    const actor = await actorFor(req, true),
      target = await targetFor(actor, req.params.id, true);
    strict(req.body, ["warehouse"]);
    if (!Object.hasOwn(req.body, "warehouse"))
      throw new AccountError("Warehouse reference is required.");
    const ref = parseReference(req.body.warehouse);
    await ready();
    const change = ref
      ? {
          $set: {
            "kaiAccount.chService": ref.service,
            "kaiAccount.chUsername": ref.username,
          },
        }
      : { $unset: { "kaiAccount.chService": "", "kaiAccount.chUsername": "" } };
    await collection.updateOne({ _id: target._id, ...scope(actor) }, change);
    return accountDto(await targetFor(actor, req.params.id, false));
  }
  async function reissue(req) {
    const actor = await actorFor(req, true),
      target = await targetFor(actor, req.params.id, true);
    strict(req.body, []);
    const a = newActivation();
    const result = await collection.updateOne(
      {
        _id: target._id,
        ...scope(actor),
        "kaiAccount.status": "pending",
        password: { $exists: false },
      },
      {
        $set: {
          "kaiAccount.activationHash": a.hash,
          "kaiAccount.activationExpiresAt": a.expiresAt,
        },
      },
    );
    if (!result.modifiedCount)
      throw new AccountError(
        "Only pending accounts can receive a new activation link.",
        409,
      );
    return invitation(req.params.id, a);
  }
  async function activate(body) {
    strict(body, ["userId", "token", "password", "confirm_password"]);
    if (
      typeof body.token !== "string" ||
      !/^kai_[a-f0-9]{64}$/.test(body.token)
    )
      throw new AccountError("Activation link is invalid or expired.");
    const password = validatePassword(
      body.password,
      body.confirm_password,
      parseInt(process.env.MIN_PASSWORD_LENGTH, 10) || 8,
    );
    const filter = {
      _id: id(body.userId),
      "kaiAccount.status": "pending",
      "kaiAccount.activationHash": activationHash(body.token),
      "kaiAccount.activationExpiresAt": { $gt: new Date() },
      password: { $exists: false },
    };
    if (!(await find(filter, { _id: 1 })))
      throw new AccountError("Activation link is invalid or expired.");
    const hashed = await bcrypt.hash(password, 12);
    // Token consumption and password installation are one atomic Mongo operation.
    const result = await collection.updateOne(filter, {
      $set: {
        password: hashed,
        emailVerified: true,
        "kaiAccount.status": "active",
        "kaiAccount.activatedAt": new Date(),
      },
      $unset: {
        "kaiAccount.activationHash": "",
        "kaiAccount.activationExpiresAt": "",
      },
    });
    if (!result.modifiedCount)
      throw new AccountError("Activation link is invalid or expired.");
  }
  async function gate(req, res, next) {
    let user;
    try {
      user = await find(
        { _id: id(String(req.user.id ?? req.user._id)) },
        { "kaiAccount.status": 1 },
      );
    } catch {
      return res
        .status(503)
        .json({ message: "Account verification unavailable." });
    }
    if (!user) return res.status(401).json({ message: "Unauthorized." });
    if (user.kaiAccount?.status === "disabled")
      return res.status(403).json({code:"ACCOUNT_DISABLED",message:"Akun dinonaktifkan. Hubungi administrator."});
    if (user.kaiAccount?.status === "pending")
      return res.status(403).json({
        code: "ACCOUNT_ACTIVATION_REQUIRED",
        message: "Activate your account first.",
      });
    return next();
  }
  async function isPending(userId) {
    if (typeof userId !== "string" || !/^[a-f0-9]{24}$/.test(userId))
      return false;
    const user = await find({ _id: id(userId) }, { "kaiAccount.status": 1 });
    return user?.kaiAccount?.status === "pending";
  }
  async function assertCanAuthenticate(userId) {
    const user = await find({_id:id(String(userId))},{"kaiAccount.status":1,deleted:1,deletedAt:1,disabled:1,active:1});
    if (!user || user.deleted || user.deletedAt || user.disabled || user.active === false || !["active","existing"].includes(user.kaiAccount?.status??"existing"))
      throw Object.assign(new AccountError("Akun tidak aktif.",403),{code:"ACCOUNT_INACTIVE"});
  }
  async function clearSessions(userId) {
    await mongoose.model("Session").collection.deleteMany({user:{$in:[id(String(userId)),String(userId)]}},{maxTimeMS:2000});
  }
  async function isAuthenticationBlocked(userId) {
    try {await assertCanAuthenticate(userId);return false;}
    catch(error) {if(error instanceof AccountError && error.status===403)return true;throw error;}
  }
  async function issueTokens(userId,res,issue) {
    await assertCanAuthenticate(userId);
    try {
      const token=await issue();
      await assertCanAuthenticate(userId);
      return token;
    } catch(error) {
      res.removeHeader?.('Set-Cookie');
      if (error instanceof AccountError && error.status===403) await clearSessions(userId).catch(()=>{});
      throw error;
    }
  }
  async function disable(req) {
    strict(req.body,[]);
    const actor=await actorFor(req,true),target=await targetFor(actor,req.params.id,true);
    if(String(actor._id)===String(target._id)) throw new AccountError("Anda tidak dapat menonaktifkan akun sendiri.",409);
    const update = await collection.updateOne({_id:target._id,...scope(actor)},{$set:{"kaiAccount.status":"disabled","kaiAccount.warehouseRevocationPending":true,"kaiAccount.disabledAt":target.kaiAccount?.disabledAt??new Date(),"kaiAccount.disabledBy":target.kaiAccount?.disabledBy??String(actor._id)},$unset:{"kaiAccount.activationHash":"","kaiAccount.activationExpiresAt":""}});
    if (update.matchedCount !== 1) throw new AccountError("Akun berubah. Muat ulang, lalu coba lagi.",409);
    let cleanup_complete=true;
    try {await clearSessions(req.params.id);} catch {cleanup_complete=false;}
    return {account:accountDto(await targetFor(actor,req.params.id,false)),cleanup_complete};
  }
  async function agentContext(req, write = false) {
    const actor = await actorFor(req, write);
    const target = await targetFor(actor, req.params.id, write);
    const dto = accountDto(target);
    if (!["pending", "active", "existing", "disabled"].includes(dto.status))
      throw new AccountError("User unavailable.", 403);
    return {
      actor: {
        id: String(actor._id),
        role: actor.role,
        tenantId: actor.tenantId,
      },
      target: {
        id: dto.id,
        role: target.role,
        tenantId: target.tenantId,
        status: dto.status,
      },
    };
  }
  async function warehouseContext(req, write = false, revocation = false) {
    const actor = await actorFor(req, write);
    if (write) strict(req.body, []);
    const target = await targetFor(actor, req.params.id, write);
    const dto = accountDto(target);
    if (!["pending", "active", "existing", ...(revocation ? ["disabled"] : [])].includes(dto.status))
      throw new AccountError("User unavailable.", 403);
    return {
      actor_id: String(actor._id),
      target_id: dto.id,
      status: dto.status,
      warehouse: dto.warehouse
        ? { service: dto.warehouse.service, username: dto.warehouse.username }
        : null,
    };
  }
  async function warehouseRevocationContext(req) {
    return warehouseContext(req, true, true);
  }
  async function completeWarehouseRevocation(req) {
    strict(req.body, []);
    const actor = await actorFor(req, true), target = await targetFor(actor, req.params.id, true);
    if (target.kaiAccount?.status !== 'disabled') throw new AccountError('Akun berubah. Muat ulang, lalu coba lagi.', 409);
    const update = await collection.updateOne({_id: target._id, ...scope(actor), 'kaiAccount.status': 'disabled'}, {$set: {'kaiAccount.warehouseRevocationPending': false}});
    if (update.matchedCount !== 1) throw new AccountError('Akun berubah. Muat ulang, lalu coba lagi.', 409);
    return accountDto(await targetFor(actor, req.params.id, false));
  }
  async function internalWarehouseUser(req) {
    // MCP -> platform direction only; the API -> MCP transport token is never accepted here.
    const token = process.env.KAI_PLATFORM_INTERNAL_TOKEN;
    const expected = Buffer.from(token ? `Bearer ${token}` : "");
    const actual = Buffer.from(req.headers?.authorization ?? "");
    if (
      !token ||
      actual.length !== expected.length ||
      !timingSafeEqual(actual, expected)
    )
      throw new AccountError("Unauthorized.", 401);
    const target = await find({ _id: id(req.params.id) });
    if (!target) throw new AccountError("User not found.", 404);
    const dto = accountDto(target);
    if (!["active", "existing"].includes(dto.status))
      throw new AccountError("User unavailable.", 403);
    return {
      id: dto.id,
      status: dto.status,
      warehouse: dto.warehouse
        ? { service: dto.warehouse.service, username: dto.warehouse.username }
        : null,
    };
  }
  return {
    create,
    read,
    update,
    reissue,
    activate,
    gate,
    disable,
    assertCanAuthenticate,
    issueTokens,
    isAuthenticationBlocked,
    isPending,
    agentContext,
    warehouseContext,
    warehouseRevocationContext,
    completeWarehouseRevocation,
    internalWarehouseUser,
    problemUsers,
  };
}
module.exports = {
  AccountError,
  parseCreate,
  parseReference,
  validatePassword,
  newActivation,
  activationHash,
  accountDto,
  createAccounts,
};
