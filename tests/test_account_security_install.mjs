import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";

// Native login and issuance bodies from cached kai-librechat:0.2.0 (v0.8.8).
const sources = {
  "strategies/localStrategy.js": "const bcrypt = require('bcryptjs');\nconst { logger } = require('@librechat/data-schemas');\nconst { errorsToString } = require('librechat-data-provider');\nconst { Strategy: PassportLocalStrategy } = require('passport-local');\nconst { isEnabled, checkEmailConfig, comparePassword } = require('@librechat/api');\nconst { findUser, updateUser } = require('~/models');\nconst { loginSchema } = require('./validators');\n\n// Unix timestamp for 2024-06-07 15:20:18 Eastern Time\nconst verificationEnabledTimestamp = 1717788018;\n\nasync function validateLoginRequest(req) {\n  const { error } = loginSchema.safeParse(req.body);\n  return error ? errorsToString(error.errors) : null;\n}\n\nasync function passportLogin(req, email, password, done) {\n  try {\n    const validationError = await validateLoginRequest(req);\n    if (validationError) {\n      logError('Passport Local Strategy - Validation Error', { email: req.body?.email });\n      logger.error(`[Login] [Login failed] [Username: ${email}] [Request-IP: ${req.ip}]`);\n      return done(null, false, { message: validationError });\n    }\n\n    const user = await findUser({ email: email.trim() }, '+password');\n    if (!user) {\n      logError('Passport Local Strategy - User Not Found', { email });\n      logger.error(`[Login] [Login failed] [Username: ${email}] [Request-IP: ${req.ip}]`);\n      return done(null, false, { message: 'Email does not exist.' });\n    }\n\n    if (!user.password) {\n      logError('Passport Local Strategy - User has no password', { email });\n      logger.error(`[Login] [Login failed] [Username: ${email}] [Request-IP: ${req.ip}]`);\n      return done(null, false, { message: 'Email does not exist.' });\n    }\n\n    const isMatch = await comparePassword(user, password, { compare: bcrypt.compare });\n    if (!isMatch) {\n      logError('Passport Local Strategy - Password does not match', { isMatch });\n      logger.error(`[Login] [Login failed] [Username: ${email}] [Request-IP: ${req.ip}]`);\n      return done(null, false, { message: 'Incorrect password.' });\n    }\n\n    const emailEnabled = checkEmailConfig();\n    const userCreatedAtTimestamp = Math.floor(new Date(user.createdAt).getTime() / 1000);\n\n    if (\n      !emailEnabled &&\n      !user.emailVerified &&\n      userCreatedAtTimestamp < verificationEnabledTimestamp\n    ) {\n      await updateUser(user._id, { emailVerified: true });\n      user.emailVerified = true;\n    }\n\n    const unverifiedAllowed = isEnabled(process.env.ALLOW_UNVERIFIED_EMAIL_LOGIN);\n    if (user.expiresAt && unverifiedAllowed) {\n      await updateUser(user._id, {});\n    }\n\n    if (!user.emailVerified && !unverifiedAllowed) {\n      logError('Passport Local Strategy - Email not verified', { email });\n      logger.error(`[Login] [Login failed] [Username: ${email}] [Request-IP: ${req.ip}]`);\n      return done(null, user, { message: 'Email not verified.' });\n    }\n\n    logger.info(`[Login] [Login successful] [Username: ${email}] [Request-IP: ${req.ip}]`);\n    return done(null, user);\n  } catch (err) {\n    return done(err);\n  }\n}\n\nfunction logError(title, parameters) {\n  const entries = Object.entries(parameters).map(([name, value]) => ({ name, value }));\n  logger.error(title, { parameters: entries });\n}\n\nmodule.exports = () =>\n  new PassportLocalStrategy(\n    {\n      usernameField: 'email',\n      passwordField: 'password',\n      session: false,\n      passReqToCallback: true,\n    },\n    passportLogin,\n  );\n",
  "server/controllers/auth/LoginController.js": "const { logger } = require('@librechat/data-schemas');\nconst { generate2FATempToken } = require('~/server/services/twoFactorService');\nconst { setAuthTokens } = require('~/server/services/AuthService');\n\nconst loginController = async (req, res) => {\n  try {\n    if (!req.user) {\n      return res.status(400).json({ message: 'Invalid credentials' });\n    }\n\n    if (req.user.twoFactorEnabled) {\n      const tempToken = generate2FATempToken(req.user._id);\n      return res.status(200).json({ twoFAPending: true, tempToken });\n    }\n\n    const { password: _p, totpSecret: _t, __v, ...user } = req.user;\n    user.id = user._id.toString();\n\n    const token = await setAuthTokens(req.user._id, res, null, req);\n\n    return res.status(200).send({ token, user });\n  } catch (err) {\n    logger.error('[loginController]', err);\n    return res.status(500).json({ message: 'Something went wrong' });\n  }\n};\n\nmodule.exports = {\n  loginController,\n};\n",
  "server/services/AuthService.js": "const setAuthTokens = async (userId, res, _session = null, req = null) => {\n  try {\n    let session = _session;\n    let refreshToken;\n    let refreshTokenExpires;\n    const expiresIn = math(process.env.REFRESH_TOKEN_EXPIRY, DEFAULT_REFRESH_TOKEN_EXPIRY);\n\n    if (session && session._id && session.expiration != null) {\n      refreshTokenExpires = session.expiration.getTime();\n      refreshToken = await generateRefreshToken(session);\n    } else {\n      const result = await createSession(userId, { expiresIn });\n      session = result.session;\n      refreshToken = result.refreshToken;\n      refreshTokenExpires = session.expiration.getTime();\n    }\n\n    const user = await getUserById(userId);\n    const sessionExpiry = math(process.env.SESSION_EXPIRY, DEFAULT_SESSION_EXPIRY);\n    const token = await generateToken(user, sessionExpiry);\n\n    res.cookie('refreshToken', refreshToken, {\n      expires: new Date(refreshTokenExpires),\n      httpOnly: true,\n      secure: shouldUseSecureCookie(),\n      sameSite: 'strict',\n    });\n    res.cookie('token_provider', 'librechat', {\n      expires: new Date(refreshTokenExpires),\n      httpOnly: true,\n      secure: shouldUseSecureCookie(),\n      sameSite: 'strict',\n    });\n\n    setCloudFrontAuthCookies(req, res, user, { userId: user?._id ?? userId });\n\n    return token;\n  } catch (error) {\n    logger.error('[setAuthTokens] Error in setting authentication tokens:', error);\n    throw error;\n  }\n};\n\nconst resolveOpenIDAuthTokenOptions = () => {};\nconst requestPasswordReset = async (email) => {\n  const user = await findUser({ email }, 'email _id role tenantId');\n  if (user && await require('/app/kai-platform/runtime.cjs').isPending(String(user._id))) return { message: 'If an account with that email exists, a password reset link has been sent to it.' };\nreturn { link: 'native-reset-link' };\n};\nconst resetPassword = async (userId, token, password) => {\n  if (await require('/app/kai-platform/runtime.cjs').isPending(String(userId))) return new Error('Account activation required');\nreturn { message: 'native-reset' };\n};\nmodule.exports = { setAuthTokens, requestPasswordReset, resetPassword };\n"
};
import { installAccountSecurity } from '../services/platform-api/install_account_security.mjs';
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kai-auth-install-'));
  t.after(() => fs.rmSync(root, {recursive:true,force:true}));
  for (const [name, source] of Object.entries({'package.json':'{"version":"v0.8.8"}',...sources})) {
    const file = path.join(root,'api',name);
    fs.mkdirSync(path.dirname(file),{recursive:true}); fs.writeFileSync(file,source);
  }
  return root;
}
function load(source, runtime, dependencies = {}, globals = {}) {
  const context = { module:{exports:{}}, process:{env:{}}, ...globals,
    require(name) { if (name === '/app/kai-platform/runtime.cjs') return runtime;
      if (name in dependencies) return dependencies[name]; throw new Error(`Unexpected dependency: ${name}`); }
  };
  vm.runInNewContext(source,context);
  return context.module.exports;
}
function installed(root,name) { return fs.readFileSync(path.join(root,'api',name),'utf8'); }
const logger = {info(){},error(){}};
function inactive() { return Object.assign(new Error('private database details'),{code:'ACCOUNT_INACTIVE',status:403}); }
function response() { return {statusCode:0,body:null,status(code){this.statusCode=code;return this;},json(body){this.body=body;return this;},send(body){this.body=body;return this;}}; }

test('installer is exported', () => assert.equal(typeof installAccountSecurity,'function'));
test('unsupported native version refuses before writing sources', t => {
  const root=fixture(t); fs.writeFileSync(path.join(root,'api/package.json'),'{"version":"future"}');
  assert.throws(()=>installAccountSecurity(root),/Unsupported native account-security version/);
  for(const [name,source] of Object.entries(sources)) assert.equal(installed(root,name),source);
});
test('a late reset seam mismatch refuses before changing earlier files',t=>{
 const root=fixture(t),name='server/services/AuthService.js';
 fs.writeFileSync(path.join(root,'api',name),sources[name].replace("Account activation required","changed"));
 const before=Object.fromEntries(Object.keys(sources).map(name=>[name,installed(root,name)]));
 assert.throws(()=>installAccountSecurity(root),/Unexpected native account-security seam/);
 for(const [name,source] of Object.entries(before)) assert.equal(installed(root,name),source);
});
test('local login checks status only after password verification',async t=>{
 const root=fixture(t); installAccountSecurity(root);
 for (const error of [inactive(),new Error('private database details')]) {
  let compared=false;
  const strategy=load(installed(root,'strategies/localStrategy.js'),{assertCanAuthenticate:async()=>{throw error;}},{
   bcryptjs:{},'@librechat/data-schemas':{logger},'librechat-data-provider':{},
   'passport-local':{Strategy:class {constructor(options,callback){this.verify=callback;}}},
   '@librechat/api':{comparePassword:async()=>{compared=true;return true;}},
   '~/models':{findUser:async()=>({_id:'user',password:'hash'})},
   './validators':{loginSchema:{safeParse:()=>({})}}
  })();
  let result;
  await strategy.verify({body:{},ip:'local'},'email','password',(...args)=>{result=args;});
  assert.equal(compared,true);assert.equal(result[0],null);assert.equal(result[1],false);
  assert.equal(result[2].message,'Akun tidak aktif.');
 }
});
test('login controller denies before temporary 2FA tokens and redacts verification errors',async t=>{
 const root=fixture(t);installAccountSecurity(root);
 for(const error of [inactive(),new Error('private database details')]) {
  let issued=0;
  const {loginController}=load(installed(root,'server/controllers/auth/LoginController.js'),{assertCanAuthenticate:async()=>{throw error;}},{
   '@librechat/data-schemas':{logger},'~/server/services/twoFactorService':{generate2FATempToken:()=>{issued++;}},
   '~/server/services/AuthService':{setAuthTokens:()=>{issued++;}}
  });
  const res=response();await loginController({user:{_id:'user',twoFactorEnabled:true}},res);
  assert.equal(issued,0);assert.equal(res.statusCode,error.code==='ACCOUNT_INACTIVE'?403:503);
  assert.equal(JSON.stringify(res.body).includes('private'),false);
 }
});
test('central native token wrapper delegates local, 2FA and refresh issuance through runtime',async t=>{
 const root=fixture(t);installAccountSecurity(root);
 let checked=0,created=0;
 const {setAuthTokens}=load(installed(root,'server/services/AuthService.js'),{
  issueTokens:async(userId,res,issue)=>{checked++;assert.equal(userId,'user');throw inactive();}
 },{}, {createSession:async()=>{created++;},math:()=>123,DEFAULT_REFRESH_TOKEN_EXPIRY:123,logger});
 for(const session of [null,{_id:'session',expiration:new Date()}]) await assert.rejects(setAuthTokens('user',response(),session),{code:'ACCOUNT_INACTIVE'});
 assert.equal(checked,2);assert.equal(created,0);
});
test('active issuance preserves the native session, request and returned token',async t=>{
 const root=fixture(t);installAccountSecurity(root);
 const session={_id:'existing',expiration:new Date()},req={request:true},res={cookie(){}};
 let scoped=false;
 const {setAuthTokens}=load(installed(root,'server/services/AuthService.js'),{issueTokens:async(id,r,issue)=>{assert.equal(id,'user');assert.equal(r,res);return issue();}}, {}, {
  generateRefreshToken:async(s)=>{assert.equal(s,session);return 'refresh';},getUserById:async()=>({_id:'user'}),
  generateToken:async()=> 'access',math:()=>123,DEFAULT_REFRESH_TOKEN_EXPIRY:123,DEFAULT_SESSION_EXPIRY:123,
  shouldUseSecureCookie:()=>true,setCloudFrontAuthCookies:(r)=>{assert.equal(r,req);scoped=true;},logger
 });
 assert.equal(await setAuthTokens('user',res,session,req),'access');assert.equal(scoped,true);
});
test('password reset uses the current blocked state and keeps request responses generic',async t=>{
 const root=fixture(t);installAccountSecurity(root);
 const {requestPasswordReset,resetPassword}=load(installed(root,'server/services/AuthService.js'),{isAuthenticationBlocked:async()=>true},{},{findUser:async()=>({_id:'user'})});
 const request=await requestPasswordReset('email');assert.equal(request.link,undefined);
 assert.equal((await resetPassword('user','token','password')).message,'Account unavailable');
});

test('repeat installation refuses and leaves the installed files unchanged',t=>{
 const root=fixture(t);installAccountSecurity(root);
 const before=Object.fromEntries(Object.keys(sources).map(name=>[name,installed(root,name)]));
 assert.throws(()=>installAccountSecurity(root),/Unexpected native account-security seam/);
 for(const [name,source] of Object.entries(before)) assert.equal(installed(root,name),source);
});
test('duplicate native seam refuses without writing any source',t=>{
 const root=fixture(t),name='strategies/localStrategy.js';
 fs.appendFileSync(path.join(root,'api',name),'\n    const emailEnabled = checkEmailConfig();');
 const before=Object.fromEntries(Object.keys(sources).map(name=>[name,installed(root,name)]));
 assert.throws(()=>installAccountSecurity(root),/Unexpected native account-security seam/);
 for(const [name,source] of Object.entries(before)) assert.equal(installed(root,name),source);
});
