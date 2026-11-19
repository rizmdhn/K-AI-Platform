import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { AuditDays, AuditFilter, AuditOutcome, AuditRow } from '@/server/kaiAccounts';
import { Notice, Select } from '../users/AccountFields';
import { auditLogFn } from '@/server/kaiAccounts';
const whole = new Intl.NumberFormat('id-ID');
const PERIODS: { value: AuditDays; label: string }[] = [
  { value: 1, label: '24 jam terakhir' }, { value: 7, label: '7 hari' }, { value: 30, label: '30 hari' }, { value: 90, label: '90 hari' },
];
const GREEN = 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300';
const RED = 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300';
const AMBER = 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300';
const GREY = 'border-zinc-400/40 bg-zinc-400/10 text-zinc-600 dark:text-zinc-400';
/** Label, colour and plain-language meaning for each audit outcome. */
export const OUTCOME: Record<AuditOutcome, { label: string; pill: string; hint: string }> = {
  ok: { label: 'Berhasil', pill: GREEN, hint: 'Query atau pencarian selesai.' },
  access_denied: { label: 'Ditolak ClickHouse', pill: RED, hint: 'Akun ClickHouse pengguna tidak punya grant untuk tabel atau kolom itu.' },
  unsafe_sql: { label: 'Ditolak pemeriksa SQL', pill: AMBER, hint: 'Pemeriksa SQL kAI menolak sebelum sampai ke ClickHouse, mis. perintah ubah/hapus. Agent biasanya memperbaiki sendiri.' },
  login_rejected: { label: 'Login ditolak', pill: RED, hint: 'ClickHouse menolak username/password tersimpan. Ganti password di detail user.' },
  auth_failed: { label: 'Sesi tidak valid', pill: RED, hint: 'Sesi platform pengguna tidak bisa diverifikasi.' },
  no_credentials: { label: 'Belum ada kredensial', pill: AMBER, hint: 'Pengguna belum punya akses data ClickHouse.' },
  query_limit: { label: 'Melebihi batas', pill: AMBER, hint: 'Query melewati batas waktu atau ukuran di Integrasi → Batas query dan log.' },
  error: { label: 'Error', pill: RED, hint: 'Kegagalan lain, mis. SQL keliru atau layanan tidak tersedia.' },
  cancelled: { label: 'Dibatalkan', pill: GREY, hint: 'Dihentikan sebelum selesai, mis. pengguna menekan stop.' },
};
const OUTCOMES = Object.keys(OUTCOME) as AuditOutcome[];
const TILES: { label: string; outcome?: AuditOutcome; pick: (c: Partial<Record<AuditOutcome, number>>, total: number) => number }[] = [
  { label: 'Total panggilan', pick: (_, total) => total },
  { label: 'Berhasil', outcome: 'ok', pick: c => c.ok ?? 0 },
  { label: 'Ditolak ClickHouse', outcome: 'access_denied', pick: c => c.access_denied ?? 0 },
  { label: 'Ditolak pemeriksa SQL', outcome: 'unsafe_sql', pick: c => c.unsafe_sql ?? 0 },
  { label: 'Login ditolak', outcome: 'login_rejected', pick: c => c.login_rejected ?? 0 },
];
const when = (iso: string) => new Date(iso).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'medium' });
function seconds(ms: number | null) {
  if (ms == null) return '—';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toLocaleString('id-ID', { maximumFractionDigits: 1 })} dtk`;
}
function OutcomeTag({ outcome }: { outcome: AuditOutcome }) {
  const o = OUTCOME[outcome];
  return <span title={o.hint} className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${o.pill}`}>{o.label}</span>;
}
function Row({ row }: { row: AuditRow }) {
  return <tr className="border-t border-(--cui-color-stroke-default) align-top">
    <td className="whitespace-nowrap py-2 pr-4 tabular-nums">{when(row.ts)}</td>
    <td className="py-2 pr-4"><div className="font-medium">{row.name || row.email || row.user}</div>{row.email && <div className="text-xs text-(--cui-color-text-muted)">{row.email}</div>}</td>
    <td className="py-2 pr-4 font-mono text-xs">{row.ch_user ?? '—'}</td>
    <td className="py-2 pr-4 font-mono text-xs">{row.tool ?? '—'}</td>
    <td className="py-2 pr-4"><OutcomeTag outcome={row.outcome} />{row.error_code && row.outcome !== 'ok' && <div className="mt-1 font-mono text-xs text-(--cui-color-text-muted)">{row.error_code}</div>}</td>
    <td className="py-2 pr-4 text-right tabular-nums">{row.rows == null ? '—' : whole.format(row.rows)}{row.truncated ? ' (dipotong)' : ''}</td>
    <td className="py-2 pr-4 text-right tabular-nums">{seconds(row.duration_ms)}</td>
    <td className="py-2 font-mono text-xs text-(--cui-color-text-muted)" title={row.request_id ?? undefined}>{row.request_id?.slice(0, 8) ?? '—'}</td>
  </tr>;
}
/** Data access log: every data tool call by K-AI Analis Data, filterable by user, outcome, tool and period. */
export function AuditLogPage({ filter, onFilter }: { filter: AuditFilter; onFilter: (next: AuditFilter) => void }) {
  /** Cursors of the pages above the current one; empty = newest page. Reset whenever the filter changes. */
  const [pages, setPages] = useState<{ key: string; stack: string[] }>({ key: '', stack: [] });
  const key = JSON.stringify(filter);
  const stack = pages.key === key ? pages.stack : [];
  const cursor = stack[stack.length - 1];
  const query = useQuery({ queryKey: ['kaiAudit', filter, cursor ?? ''], queryFn: () => auditLogFn({ data: { ...filter, ...(cursor ? { cursor } : {}) } }), retry: false, placeholderData: keepPreviousData });
  const data = query.data;
  const set = (patch: Partial<AuditFilter>) => onFilter({ ...filter, ...patch });
  const page = stack.length + 1;
  const filtered = !!(filter.user || filter.outcome || filter.tool);
  return <div className="flex flex-col gap-4 p-4 md:p-6">
    <header className="flex flex-col gap-1">
      <h1 className="text-xl font-medium">Log akses data</h1>
      <p className="max-w-3xl text-sm text-(--cui-color-text-muted)">Setiap kali K-AI Analis Data mencari tabel atau menjalankan query ClickHouse atas nama pengguna, kejadiannya dicatat di sini: siapa, kapan, akun ClickHouse yang dipakai, dan hasilnya. Teks SQL dan isi hasil query tidak disimpan.</p>
    </header>
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex flex-col gap-1 text-sm">Periode
        <Select value={filter.days} onChange={e => set({ days: Number(e.target.value) as AuditDays })}>{PERIODS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}</Select>
      </label>
      <label className="flex min-w-48 flex-col gap-1 text-sm">Pengguna
        <Select value={filter.user ?? ''} onChange={e => set({ user: e.target.value || undefined })}><option value="">Semua</option>
          {data?.users.map(u => <option key={u.id} value={u.id}>{u.name || u.email || u.id}</option>)}</Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Hasil
        <Select value={filter.outcome ?? ''} onChange={e => set({ outcome: (e.target.value || undefined) as AuditOutcome | undefined })}><option value="">Semua</option>
          {OUTCOMES.map(o => <option key={o} value={o}>{OUTCOME[o].label}</option>)}</Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Tool
        <Select value={filter.tool ?? ''} onChange={e => set({ tool: e.target.value || undefined })}><option value="">Semua</option>
          {data?.tools.map(t => <option key={t} value={t}>{t}</option>)}</Select>
      </label>
      {filtered && <Button type="secondary" label="Hapus filter" onClick={() => onFilter({ days: filter.days })} />}
      <Button type="secondary" label="Muat ulang" disabled={query.isFetching} onClick={() => void query.refetch()} />
    </div>
    {query.isError && <Notice tone="error">{query.error instanceof Error ? query.error.message : 'Log akses data gagal dimuat.'}</Notice>}
    {data && !data.available && <Notice tone="warning">Log audit tidak terbaca dari layanan akses data (MCP). Periksa layanan clickhouse-mcp di server.</Notice>}
    {data?.available && <div className={`flex flex-col gap-4 transition-opacity ${query.isPlaceholderData ? 'opacity-60' : ''}`}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">{TILES.map(t => {
        const active = t.outcome ? filter.outcome === t.outcome : !filter.outcome;
        return <button key={t.label} type="button" aria-pressed={active} onClick={() => set({ outcome: t.outcome })}
          className={`flex flex-col gap-1 rounded-md border p-3 text-left ${active ? 'border-(--cui-color-accent-default)' : 'border-(--cui-color-stroke-default)'}`}>
          <span className="text-xs text-(--cui-color-text-muted)">{t.label}</span>
          <span className="text-2xl font-medium tabular-nums">{whole.format(t.pick(data.counts, data.total))}</span>
        </button>;
      })}</div>
      {filter.outcome && <p className="text-sm text-(--cui-color-text-muted)"><OutcomeTag outcome={filter.outcome} /> {OUTCOME[filter.outcome].hint}</p>}
      {!data.rows.length
        ? <p className="text-sm text-(--cui-color-text-muted)">Tidak ada kejadian yang cocok dengan filter pada periode ini.</p>
        : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead className="text-xs text-(--cui-color-text-muted)"><tr>
            {['Waktu', 'Pengguna', 'Akun ClickHouse', 'Tool', 'Hasil'].map(h => <th key={h} className="py-2 pr-4 font-medium">{h}</th>)}
            <th className="py-2 pr-4 text-right font-medium">Baris</th><th className="py-2 pr-4 text-right font-medium">Durasi</th><th className="py-2 font-medium">ID</th>
          </tr></thead>
          <tbody>{data.rows.map(row => <Row key={`${row.ts}-${row.request_id ?? ''}-${row.tool ?? ''}`} row={row} />)}</tbody>
        </table></div>}
      {(page > 1 || data.next_cursor) && <nav aria-label="Halaman log" className="flex flex-wrap items-center gap-2 text-sm">
        <Button type="secondary" label="Terbaru" disabled={page === 1 || query.isFetching} onClick={() => setPages({ key, stack: [] })} />
        <Button type="secondary" label="Sebelumnya" disabled={page === 1 || query.isFetching} onClick={() => setPages({ key, stack: stack.slice(0, -1) })} />
        <span className="text-(--cui-color-text-muted)">Halaman {whole.format(page)} dari ±{whole.format(Math.max(1, Math.ceil(data.total / 100)))}</span>
        <Button type="secondary" label="Berikutnya" disabled={!data.next_cursor || query.isFetching} onClick={() => data.next_cursor && setPages({ key, stack: [...stack, data.next_cursor] })} />
      </nav>}
      <p className="text-xs text-(--cui-color-text-muted)">Sumber: log audit layanan akses data, disimpan sesuai Integrasi → Batas query dan log. ID dipakai untuk menelusuri kejadian di server.</p>
    </div>}
  </div>;
}
