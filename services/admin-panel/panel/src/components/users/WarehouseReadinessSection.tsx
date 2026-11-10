import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery } from '@tanstack/react-query';
import { warehouseReadinessFn } from '@/server/kaiAccounts';
import { agentAccessFn } from '@/server/kaiAgents';
const labels = { binding_dsn: 'Konfigurasi identitas akun', enrollment_dsn: 'Konfigurasi pengelolaan akses', distinct_runtime_users: 'Login layanan terpisah', keyring: 'Kunci enkripsi aktif', warehouse_target: 'Konfigurasi target HTTPS' };
export function WarehouseReadinessSection({ userId }: { userId: string }) {
  const query = useQuery({ queryKey: ['kaiWarehouseReadiness', userId], queryFn: () => warehouseReadinessFn({ data: { id: userId } }), retry: false });
  const agents = useQuery({ queryKey: ['kaiAgents', userId], queryFn: () => agentAccessFn({ data: { id: userId } }) });
  return <details className="border-t border-(--cui-color-stroke-default) pt-4" aria-label="Detail teknis">
    <summary className="cursor-pointer font-medium">Detail teknis</summary>
    <div className="mt-3 flex flex-col gap-3 text-sm">
      <div><Badge text={query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : Object.values(query.data.checks).every(check => check.ok) ? 'Konfigurasi tersedia' : 'Perlu disiapkan'} state={query.isError ? 'danger' : 'neutral'} /></div>
      <p className="text-(--cui-color-text-muted)">Informasi konfigurasi layanan dan chatbot untuk pengelola.</p>
      {agents.data?.agent && <><p>ID agent: <code>{agents.data.agent.id}</code></p><p>Provider: {agents.data.agent.provider}</p><p>Model: {agents.data.agent.model}</p><p>Tools: {agents.data.agent.tools.map(tool => tool.split('_mcp_')[0]).join(', ')}</p></>}
      <h4 className="font-medium">Konfigurasi layanan</h4>
      {query.isPending ? <p role="status">Memeriksa konfigurasi lokal…</p> : query.isError ? <p role="alert">Status konfigurasi belum tersedia. Pengelola perlu memeriksa layanan.</p> : <ul>{Object.entries(labels).map(([name, label]) => <li key={name}>{label}: {query.data.checks[name as keyof typeof labels].ok ? 'tersedia' : 'perlu disiapkan'}</li>)}</ul>}
      <p className="text-xs text-(--cui-color-text-muted)">Pemeriksaan ini tidak menghubungi ClickHouse, PostgreSQL atau model. Konfigurasi tersedia belum membuktikan koneksi atau hak akses database.</p>
      <div><Button type="secondary" label="Periksa ulang konfigurasi" disabled={query.isFetching} onClick={() => void query.refetch()} /></div>
    </div>
  </details>;
}
