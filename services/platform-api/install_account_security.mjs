import fs from "node:fs";
import path from "node:path";

function replace(source, before, after, seam) {
  if (source.split(before).length !== 2)
    throw new Error(`Unexpected native account-security seam: ${seam}`);
  return source.replace(before, after);
}

export function installAccountSecurity(root = "/app") {
  const version = JSON.parse(fs.readFileSync(path.join(root, "api/package.json"), "utf8")).version;
  if (version !== "v0.8.8") throw new Error("Unsupported native account-security version");
  const files = {
    local: "api/strategies/localStrategy.js",
    login: "api/server/controllers/auth/LoginController.js",
    auth: "api/server/services/AuthService.js",
  };
  const sources = Object.fromEntries(Object.entries(files).map(([name, file]) =>
    [name, fs.readFileSync(path.join(root, file), "utf8")]));

  sources.local = replace(sources.local,
    "    const emailEnabled = checkEmailConfig();",
    `    try {
      await require('/app/kai-platform/runtime.cjs').assertCanAuthenticate(String(user._id));
    } catch {
      return done(null, false, { message: 'Akun tidak aktif.' });
    }

    const emailEnabled = checkEmailConfig();`, "local login after password verification");
  sources.login = replace(sources.login,
    "    if (req.user.twoFactorEnabled) {",
    `    await require('/app/kai-platform/runtime.cjs').assertCanAuthenticate(String(req.user._id));

    if (req.user.twoFactorEnabled) {`, "login before temporary 2FA issuance");
  sources.login = replace(sources.login,
    "  } catch (err) {\n    logger.error('[loginController]', err);",
    `  } catch (err) {
    if (err?.code === 'ACCOUNT_INACTIVE') {
      return res.status(403).json({ message: 'Akun tidak aktif.' });
    }
    return res.status(503).json({ message: 'Verifikasi akun tidak tersedia.' });`,
    "login safe authentication failure");
  // Remove the now unreachable native error response, validating its exact source too.
  sources.login = replace(sources.login,
    "    return res.status(500).json({ message: 'Something went wrong' });\n",
    "", "login native error response");
  sources.auth = replace(sources.auth,
    "const setAuthTokens = async (userId, res, _session = null, req = null) => {",
    "const nativeSetAuthTokens = async (userId, res, _session = null, req = null) => {",
    "central native token issuer");
  sources.auth = replace(sources.auth,
    "const resolveOpenIDAuthTokenOptions =",
    `const setAuthTokens = (userId, res, _session = null, req = null) =>
  require('/app/kai-platform/runtime.cjs').issueTokens(String(userId), res,
    () => nativeSetAuthTokens(userId, res, _session, req));

const resolveOpenIDAuthTokenOptions =`, "central token issuance wrapper");
  sources.auth = replace(sources.auth,
    "  if (user && await require('/app/kai-platform/runtime.cjs').isPending(String(user._id))) return { message: 'If an account with that email exists, a password reset link has been sent to it.' };",
    "  if (user && await require('/app/kai-platform/runtime.cjs').isAuthenticationBlocked(String(user._id))) return { message: 'If an account with that email exists, a password reset link has been sent to it.' };",
    "blocked password reset request");
  sources.auth = replace(sources.auth,
    "  if (await require('/app/kai-platform/runtime.cjs').isPending(String(userId))) return new Error('Account activation required');",
    "  if (await require('/app/kai-platform/runtime.cjs').isAuthenticationBlocked(String(userId))) return new Error('Account unavailable');",
    "blocked password reset completion");

  // Validate every native seam before any native file is changed.
  for (const [name, file] of Object.entries(files)) fs.writeFileSync(path.join(root, file), sources[name]);
}
