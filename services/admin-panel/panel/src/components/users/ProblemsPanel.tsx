import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { problemsFn, type Problems } from '@/server/kaiAccounts';
import { Notice } from './AccountFields';
type Issue = Problems['accounts'][number]['issues'][number];
type SystemIssue = Problems['system'][number];
const time = (at?: string) => (at ? new Date(at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' }) : '');
const checks: Record<string, string> = { binding_dsn: 'koneksi database metadata', keyring: 'kunci enkripsi', warehouse_target: 'alamat ClickHouse' };
const parts = { warehouse: 'akses ClickHouse', chatbot: 'izin chatbot CH' } as const;
/** [problem, fix] for one account issue. */
function accountText(issue: Issue): [string, string] {
  switch (issue.code) {
    case 'agent_without_binding': return ['Punya akses K-AI Analis Data, tetapi kredensial ClickHouse belum aktif.', 'Detail user → Akses data ClickHouse: isi kredensial, atau matikan akses chatbot.'];
    case 'ch_login_rejected': return [`ClickHouse menolak login tersimpan sejak ${time(issue.at)}. Password mungkin sudah berubah.`, 'Detail user → Akses data ClickHouse → Ganti password.'];
    case 'many_denials': return [`${issue.count} penolakan akses data dalam 24 jam.`, 'Periksa grant ClickHouse akun ini atau pertanyaan pengguna.'];
    case 'cleanup_pending': return [`Akun nonaktif, tetapi pencabutan belum tuntas${issue.parts?.length ? `: ${issue.parts.map(part => parts[part]).join(', ')}` : ''}.`, 'Detail user → bagian Akun → klik Ulangi pembersihan akses.'];
    default: return [issue.code, ''];
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
    {query.isError && <div className="mt-2"><Notice tone="error">Daftar masalah belum tersedia.</Notice></div>}
    {data && !total && <p className="mt-2 text-sm text-(--cui-color-text-muted)">Tidak ada masalah terdeteksi dalam 24 jam terakhir.</p>}
    {data && total > 0 && <div className="mt-3 overflow-x-auto"><table className="w-full text-left text-sm">
      <thead className="text-xs text-(--cui-color-text-muted)"><tr><th className="py-2 pr-4 font-medium">Pengguna</th><th className="py-2 pr-4 font-medium">Masalah</th><th className="py-2 font-medium">Cara memperbaiki</th></tr></thead>
      <tbody>
        {data.system.map((issue, i) => <tr key={`system-${i}`} className="border-t border-(--cui-color-stroke-default) align-top"><td className="py-2 pr-4 font-medium">Sistem</td><td className="py-2 pr-4">{systemText(issue)}</td><td className="py-2">Periksa layanan di server (<code>make ps</code>, <code>make logs</code>).</td></tr>)}
        {data.accounts.flatMap(account => account.issues.map(issue => { const [problem, fix] = accountText(issue); return <tr key={`${account.id}-${issue.code}`} className="border-t border-(--cui-color-stroke-default) align-top">
          <td className="py-2 pr-4"><div className="font-medium">{account.name || account.email}</div><div className="text-xs text-(--cui-color-text-muted)">{account.email}</div></td>
          <td className="py-2 pr-4">{problem}</td>
          <td className="py-2">{fix} <Link to="/users" className="underline">Buka Users</Link></td>
        </tr>; }))}
      </tbody>
    </table></div>}
    {data && <p className="mt-2 text-xs text-(--cui-color-text-muted)">Dari log audit dan status akun, tanpa menghubungi ClickHouse. Diperbarui {time(data.generated_at)}.</p>}
  </section>;
}
