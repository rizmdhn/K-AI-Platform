import { createFileRoute, useNavigate } from '@tanstack/react-router';
import type { AuditDays, AuditFilter, AuditOutcome } from '@/server/kaiAccounts';
import { AccessDenied, PermissionsUnavailable } from '@/components/shared';
import { AuditLogPage, OUTCOME } from '@/components/audit/Log';
import { SystemCapabilities } from '@/constants';
import { useCapabilities } from '@/hooks';
const DAYS: AuditDays[] = [1, 7, 30, 90];
const text = (value: unknown, pattern: RegExp) => (typeof value === 'string' && pattern.test(value) ? value : undefined);
export const Route = createFileRoute('/_app/audit')({
  validateSearch: (search: Record<string, unknown>): AuditFilter => ({
    days: DAYS.includes(Number(search.days) as AuditDays) ? (Number(search.days) as AuditDays) : 1,
    user: text(search.user, /^[a-f0-9]{24}$/),
    outcome: typeof search.outcome === 'string' && search.outcome in OUTCOME ? (search.outcome as AuditOutcome) : undefined,
    tool: text(search.tool, /^[a-z][a-z0-9_]{0,63}$/),
  }),
  component: AuditRoute,
});
function AuditRoute() {
  const { hasCapability, isLoading, isError } = useCapabilities();
  const filter = Route.useSearch();
  const navigate = useNavigate({ from: '/audit' });
  if (isLoading) return null;
  if (isError) return <PermissionsUnavailable />;
  if (!hasCapability(SystemCapabilities.READ_USERS)) return <AccessDenied />;
  return <AuditLogPage filter={filter} onFilter={next => void navigate({ search: next })} />;
}
