import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { AccessDenied, PermissionsUnavailable } from '@/components/shared';
import { SEMANTIC_TABS, SemanticPage } from '@/components/semantic/Page';
import type { SemanticTab } from '@/components/semantic/Page';
import { SystemCapabilities } from '@/constants';
import { useCapabilities } from '@/hooks';
export const Route = createFileRoute('/_app/semantic')({
  validateSearch: (search: Record<string, unknown>): { tab: SemanticTab } => ({
    tab: SEMANTIC_TABS.includes(search.tab as SemanticTab) ? (search.tab as SemanticTab) : 'overview',
  }),
  component: SemanticRoute,
});
function SemanticRoute() {
  const { hasCapability, isLoading, isError } = useCapabilities();
  const { tab } = Route.useSearch();
  const navigate = useNavigate({ from: '/semantic' });
  if (isLoading) return null;
  if (isError) return <PermissionsUnavailable />;
  if (!hasCapability(SystemCapabilities.READ_CONFIGS)) return <AccessDenied />;
  return <SemanticPage tab={tab} onTab={next => void navigate({ search: { tab: next } })} />;
}
