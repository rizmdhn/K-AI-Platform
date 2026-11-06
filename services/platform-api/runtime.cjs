const mongoose = require("mongoose");
const { hasCapability } = require("~/server/middleware/roles/capabilities");
const { createAccounts } = require("./accounts.cjs");
const origin = new URL(process.env.DOMAIN_CLIENT);
if (
  !["http:", "https:"].includes(origin.protocol) ||
  origin.username ||
  origin.password ||
  origin.search ||
  origin.hash ||
  origin.pathname !== "/"
)
  throw new Error("Invalid activation origin");
module.exports = createAccounts({
  mongoose,
  hasCapability,
  bcrypt: require("bcryptjs"),
  origin: origin.origin,
});
