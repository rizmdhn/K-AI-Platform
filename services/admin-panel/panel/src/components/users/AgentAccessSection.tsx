import { Badge } from '@clickhouse/click-ui';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { accountSetupFn } from '@/server/kaiAccounts';
import { agentAccessFn, assignAgentFn } from '@/server/kaiAgents';
export function AgentAccessSection({ userId }: { userId: string }) {
  const { hasCapability } = useCapabilities();
  const client = useQueryClient();
  const key = ['kaiAgents', userId];
  const account = useQuery({ queryKey: ['kaiAccount', userId], queryFn: () => accountSetupFn({ data: { id: userId } }) });
  const query = useQuery({ queryKey: key, queryFn: () => agentAccessFn({ data: { id: userId } }) });
  const mutation = useMutation({ gcTime: 0, mutationFn: ({ agent_id, assigned }: { agent_id: string; assigned: boolean }) => assignAgentFn({ data: { id: userId, agent_id, assigned } }), onSettled: () => client.invalidateQueries({ queryKey: key }) });
  const status = query.data;
  const canManage = hasCapability(SystemCapabilities.MANAGE_USERS) && hasCapability(SystemCapabilities.MANAGE_AGENTS) && status?.can_manage;
  const accountDisabled = account.data?.status === 'disabled';
  const enabled = !!status?.binding_active && !!account.data && !account.isError && !accountDisabled;
  const hasAccess = !!status?.effective_access && enabled;
  return <section className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-4" aria-label="Akses chatbot CH">
    <div className="flex items-center justify-between gap-2"><h3 className="font-medium">Akses chatbot CH</h3><Badge text={query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : accountDisabled ? 'Akun nonaktif' : hasAccess ? 'Diizinkan' : 'Belum diizinkan'} state={query.isError ? 'danger' : hasAccess ? 'success' : 'neutral'} /></div>
    <p className="text-sm text-(--cui-color-text-muted)">Chatbot umum selalu tersedia. Chatbot CH hanya muncul untuk pengguna yang diizinkan.</p>
    {query.isPending ? <p role="status">Memuat akses chatbot CH…</p> : query.isError ? <p role="alert">Akses chatbot CH tidak tersedia. Coba muat ulang.</p> : status && <>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!status.direct_assignment} disabled={!canManage || !enabled || !status.configured || !status.agent || mutation.isPending || (status.direct_assignment && !status.can_remove_direct)} onChange={event => { if (status.agent) mutation.mutate({ agent_id: status.agent.id, assigned: event.target.checked }); }} />Izinkan memakai K-AI - CH</label>
      {!enabled && <p className="text-sm text-(--cui-color-text-muted)">{accountDisabled ? 'Akun nonaktif. Akses chatbot CH tidak dapat diaktifkan.' : 'Atur akses data ClickHouse terlebih dahulu.'}</p>}
      {(!status.configured || !status.agent) && <p className="text-sm">Chatbot CH belum tersedia. Hubungi pengelola.</p>}
      {status.inherited_access && <p className="text-sm">Akses juga berasal dari grup, role, atau kepemilikan. Menghapus penetapan langsung tidak mencabut akses tersebut.</p>}
      {status.direct_assignment && !status.can_remove_direct && <p className="text-sm">Izin ini tidak dapat dihapus dari panel ini. Hubungi pengelola.</p>}
    </>}
    {mutation.isError && <p role="alert">{mutation.error.message}</p>}
  </section>;
}
