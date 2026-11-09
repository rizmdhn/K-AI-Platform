import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { problemsFn, type Problems } from '@/server/kaiAccounts';
type Issue = Problems['accounts'][number]['issues'][number];
type SystemIssue = Problems['system'][number];
const time = (at?: string) => (at ? new Date(at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' }) : '');
const checks: Record<string, string> = { binding_dsn: 'koneksi database metadata', keyring: 'kunci enkripsi', warehouse_target: 'alamat ClickHouse' };
function accountText(issue: Issue) {
  switch (issue.code) {
    case 'agent_without_binding': return 'Punya akses K-AI Analis Data, tetapi kredensial CH belum aktif. Isi kredensial atau cabut akses agent.';
    case 'ch_login_rejected': return `ClickHouse menolak login tersimpan sejak ${time(issue.at)}; password mungkin berubah. Simpan ulang kredensial.`;
    case 'many_denials': return `${issue.count} penolakan akses data dalam 24 jam. Periksa grant CH atau pertanyaan pengguna.`;
    case 'cleanup_pending': return 'Akun nonaktif, tetapi pencabutan akses belum tuntas. Klik Nonaktifkan lagi untuk mengulang.';
    default: return issue.code;
  }
}
function systemText(issue: SystemIssue) {
  switch (issue.code) {
    case 'mcp_unreachable': return 'Layanan akses data (MCP) tidak bisa dihubungi.';
    case 'audit_unavailable': return 'Log audit tidak terbaca, jadi status login dan penolakan tidak diketahui.';
    case 'metadata_db_unavailable': return 'Database metadata (PostgreSQL) tidak bisa dibaca, jadi status kredensial tidak diketahui.';
    case 'config_missing': return `Konfigurasi belum siap: ${checks[issue.check ?? ''] ?? issue.check}.`;
    case 'high_error_rate': return `Banyak kegagalan: ${issue.errors} dari ${issue.calls} panggilan data dalam 24 jam.`;
    case 'agent_shared_broadly': return 'K-AI Analis Data dibagikan ke grup, role, atau publik. Bagikan hanya per pengguna.';
    case 'agent_unavailable': return 'Konfigurasi K-AI Analis Data tidak valid atau tidak bisa dibaca.';
    default: return issue.code;
  }
}
export function ProblemsPanel() {
  const query = useQuery({ queryKey: ['kaiProblems'], queryFn: () => problemsFn(), retry: false, refetchInterval: 60000 });
  const data = query.data;
  const total = data ? data.system.length + data.accounts.length : 0;
  return <section aria-label="Perlu perhatian" className="rounded-lg border border-(--cui-color-stroke-default) p-4">
    <div className="flex items-center justify-between gap-2">
      <h2 className="font-medium">Perlu perhatian <Badge text={query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : String(total)} state={total ? 'warning' : 'neutral'} /></h2>
      <Button type="secondary" label="Periksa ulang" disabled={query.isFetching} onClick={() => void query.refetch()} />
    </div>
    {query.isError && <p role="alert" className="mt-2 text-sm">Daftar masalah belum tersedia.</p>}
    {data && !total && <p className="mt-2 text-sm text-(--cui-color-text-muted)">Tidak ada masalah terdeteksi dalam 24 jam terakhir.</p>}
    {data && data.system.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm">{data.system.map((issue, i) => <li key={i}>{systemText(issue)}</li>)}</ul>}
    {data && data.accounts.length > 0 && <ul className="mt-2 flex flex-col gap-2 text-sm">{data.accounts.map((account) => <li key={account.id}>
      <strong>{account.name || account.email}</strong> <span className="text-(--cui-color-text-muted)">{account.email}</span>
      <ul className="list-disc pl-5">{account.issues.map((issue) => <li key={issue.code}>{accountText(issue)}</li>)}</ul>
    </li>)}</ul>}
    {data && data.accounts.length > 0 && <p className="mt-2 text-sm"><Link to="/users">Buka Users untuk memperbaiki akun</Link></p>}
    {data && <p className="mt-2 text-xs text-(--cui-color-text-muted)">Dari log audit dan status akun, tanpa menghubungi ClickHouse. Diperbarui {time(data.generated_at)}.</p>}
  </section>;
}
