import fs from "node:fs";
const root = process.env.KAI_PANEL_SOURCE || "/app";
fs.copyFileSync('/app/kai-branding/logo.svg', `${root}/src/assets/librechat.svg`);
fs.copyFileSync('/app/kai-branding/logo.svg', `${root}/public/librechat-logo.svg`);
fs.copyFileSync('/app/kai-branding/icon.svg', `${root}/public/branding-icon.svg`);
function patch(file, before, after) {
  const path = `${root}/${file}`,
    text = fs.readFileSync(path, "utf8");
  if (text.split(before).length !== 2)
    throw new Error(`Unexpected native Users seam: ${file}`);
  fs.writeFileSync(path, text.replace(before, after));
}
const nav = `  // TODO: re-enable once user management is ready
  // {
  //   labelKey: 'com_nav_users',
  //   path: '/users',
  //   icon: 'users',
  //   capability: SystemCapabilities.READ_USERS,
  // },`;
patch('src/routes/__root.tsx', '/favicon.ico', '/branding-icon.svg');
const manifestPath = `${root}/public/manifest.json`;
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
manifest.icons = [{ src: 'branding-icon.svg', sizes: 'any', type: 'image/svg+xml' }];
manifest.name = 'K-AI platform';
manifest.short_name = 'K-AI platform';
fs.writeFileSync(manifestPath, JSON.stringify(manifest));
patch('src/routes/__root.tsx', "title: 'LibreChat Admin Panel'", "title: 'K-AI platform'");
for (const locale of fs.readdirSync(`${root}/src/locales`, { withFileTypes: true })) {
  const file = `${root}/src/locales/${locale.name}/translation.json`;
  if (!locale.isDirectory() || !fs.existsSync(file)) continue;
  const strings = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const [key, value] of Object.entries(strings)) {
    if (typeof value === 'string') strings[key] = value.replaceAll('LibreChat', 'K-AI platform');
  }
  strings.com_auth_title = 'K-AI platform';
  fs.writeFileSync(file, JSON.stringify(strings));
}
patch('src/components/Sidebar.tsx', 'className="h-6 w-6 shrink-0"', 'className="h-6 w-6 shrink-0 object-contain"');
patch(
  "src/components/Sidebar.tsx",
  nav,
  nav
    .split("\n")
    .slice(1)
    .map((l) => l.replace("  // ", "  "))
    .join("\n"),
);
patch(
  "src/components/users/UsersPage.tsx",
  "                  canManage={canManage}",
  "                  canManage={false}",
);
patch(
  "src/components/users/UserDetailDialog.tsx",
  "import { useState, useMemo } from 'react';",
  "import { useState, useMemo } from 'react';\nimport { AccountSetupSection } from './AccountSetupSection';\nimport { WarehouseAccessSection } from './WarehouseAccessSection';\nimport { AgentAccessSection } from './AgentAccessSection';\nimport { WarehouseReadinessSection } from './WarehouseReadinessSection';",
);
patch(
  "src/components/users/UserDetailDialog.tsx",
  "<UserHeader user={user} />",
  "<UserHeader user={user} />\n              <AccountSetupSection key={user.id} userId={user.id} />\n              <WarehouseAccessSection key={`warehouse-${user.id}`} userId={user.id} />\n              <AgentAccessSection key={`agents-${user.id}`} userId={user.id} />\n              <WarehouseReadinessSection key={`technical-${user.id}`} userId={user.id} />",
);
for (const [file, target] of Object.entries({
  "AgentAccessSection.tsx": "src/components/users/AgentAccessSection.tsx",
  "kaiAgents.ts": "src/server/kaiAgents.ts",
  "users.tsx": "src/routes/_app/users.tsx",
  "ProblemsPanel.tsx": "src/components/users/ProblemsPanel.tsx",
  "kaiAccounts.ts": "src/server/kaiAccounts.ts",
  "CreateUserDialog.tsx": "src/components/users/CreateUserDialog.tsx",
  "AccountFields.tsx": "src/components/users/AccountFields.tsx",
  "AccountSetupSection.tsx": "src/components/users/AccountSetupSection.tsx",
  "WarehouseReadinessSection.tsx": "src/components/users/WarehouseReadinessSection.tsx",
  "WarehouseAccessSection.tsx":
    "src/components/users/WarehouseAccessSection.tsx",
}))
  fs.copyFileSync(`/app/kai-overlays/${file}`, `${root}/${target}`);

// P6-10: the native welcome page (Dashboard) opens with the "Perlu perhatian" panel for user admins.
patch("src/components/dashboard/DashboardPage.tsx", "import { SystemCapabilities } from '@/constants';",
  "import { SystemCapabilities } from '@/constants';\nimport { ProblemsPanel } from '@/components/users/ProblemsPanel';");
patch("src/components/dashboard/DashboardPage.tsx", "      <section aria-label={localize('com_dash_quick_links')}>",
  "      {hasCapability(SystemCapabilities.READ_USERS) && <ProblemsPanel />}\n      <section aria-label={localize('com_dash_quick_links')}>");

// Brand the native login view without replacing its login, SSO or 2FA behavior.
patch('src/routes/login.tsx', "import { Container } from '@clickhouse/click-ui';", "import '@/login-branding.css';");
patch('src/routes/login.tsx', `    <Container
      orientation="vertical"
      alignItems="center"
      justifyContent="center"
      style={{ minHeight: '100vh', padding: '1rem', gap: '1rem' }}
    >`, `    <main className="kai-admin-login">
      <header className="kai-login-brand">
        <img className="kai-login-logo" src="/librechat-logo.svg" alt="K-AI" />
        <p>Administrasi K-AI platform</p>
      </header>`);
patch('src/routes/login.tsx', 'className="sm:absolute sm:bottom-0 sm:left-0 sm:m-4"', 'className="kai-login-theme"');
patch('src/routes/login.tsx', '    </Container>', '    </main>');
patch('src/components/AuthCard.tsx', `            <Button
              label={isSubmitting ? localize('com_auth_signing_in') : localize('com_auth_sign_in')}
              type="primary"
              onClick={handleLogin}
              disabled={isSubmitting}
            />`, `            <div className="kai-login-submit">
              <Button
                label={isSubmitting ? localize('com_auth_signing_in') : localize('com_auth_sign_in')}
                type="primary"
                onClick={handleLogin}
                disabled={isSubmitting}
              />
            </div>`);
fs.copyFileSync('/app/kai-overlays/login-branding.css', `${root}/src/login-branding.css`);
for (const locale of fs.readdirSync(`${root}/src/locales`, { withFileTypes: true })) {
  const file = `${root}/src/locales/${locale.name}/translation.json`;
  if (!locale.isDirectory() || !fs.existsSync(file)) continue;
  const strings = JSON.parse(fs.readFileSync(file, 'utf8'));
  Object.assign(strings, {com_auth_title:'Masuk ke panel admin',com_auth_sign_in:'Masuk',com_auth_signing_in:'Memproses…',com_auth_password_label:'Password',com_auth_email_placeholder:'nama@perusahaan.com',com_auth_password_placeholder:'Masukkan password'});
  fs.writeFileSync(file, JSON.stringify(strings));
}
