import '@/login-branding.css';
import { createFileRoute } from '@tanstack/react-router';
import ThemeSelector from '@/components/ThemeSelector';
import { AuthCard } from '@/components/AuthCard';
import { checkOpenIdFn } from '@/server';

export const Route = createFileRoute('/login')({
  validateSearch: (search: Record<string, unknown>) => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : '/',
  }),
  loader: async () => {
    const openIdStatus = await checkOpenIdFn();
    return {
      ssoAvailable: openIdStatus.available,
      ssoOnly: openIdStatus.available && openIdStatus.ssoOnly,
    };
  },
  component: LoginPage,
});

function LoginPage() {
  const { redirect } = Route.useSearch();
  const { ssoAvailable, ssoOnly } = Route.useLoaderData();

  return (
    <main className="kai-admin-login">
      <header className="kai-login-brand">
        <img className="kai-login-logo" src="/librechat-logo.svg" alt="K-AI" />
        <p>Administrasi K-AI platform</p>
      </header>
      <AuthCard redirectTo={redirect} autoRedirectSso={ssoOnly} ssoAvailable={ssoAvailable} />
      <div className="kai-login-theme">
        <ThemeSelector returnThemeOnly />
      </div>
    </main>
  );
}
