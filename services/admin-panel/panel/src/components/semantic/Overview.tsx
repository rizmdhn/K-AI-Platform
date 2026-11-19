import { useEffect, useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { PageSize, SemanticDatabase, SemanticTable, SemanticTableQuery, TableStatus } from '@/server/kaiAccounts';
import type * as t from '@/types';
import { Notice, Select, errorText } from '../users/AccountFields';
import { semanticListFn, semanticTablesFn } from '@/server/kaiAccounts';
import { KindTag, StatusTag, TONE } from './Editor';
import { DataTable, SearchInput } from '@/components/shared';
const whole = new Intl.NumberFormat('id-ID');
/** Coverage of one table: no definition yet, drafts only, some approved, all approved, or only rejected ones. */
const TABLE: Record<TableStatus, { label: string; pill: string }> = {
  none: { label: 'Belum ada', pill: 'border-zinc-400/40 bg-transparent text-zinc-600 dark:text-zinc-400' },
  draft: { label: 'Draf', pill: TONE.draft_ai.pill },
  partial: { label: 'Sebagian disetujui', pill: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300' },
  approved: { label: 'Disetujui', pill: TONE.approved.pill },
  rejected: { label: 'Ditolak', pill: TONE.rejected.pill },
};
const ORDER: TableStatus[] = ['none', 'draft', 'partial', 'approved', 'rejected'];
type View = Required<Pick<SemanticTableQuery, 'page' | 'size' | 'sort' | 'dir'>>;
function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="flex flex-col gap-1 rounded-md border border-(--cui-color-stroke-default) p-3">
    <span className="text-xs text-(--cui-color-text-muted)">{label}</span>
    <span className="text-2xl font-medium tabular-nums">{value}</span>
    {hint && <span className="text-xs text-(--cui-color-text-muted)">{hint}</span>}
  </div>;
}
function TableTag({ status }: { status: TableStatus }) {
  return <span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${TABLE[status].pill}`}>{TABLE[status].label}</span>;
}
const count = (n: number) => (n ? whole.format(n) : <span className="text-(--cui-color-text-muted)">0</span>);
const COLUMNS: t.DataTableColumn<SemanticTable>[] = [
  { key: 'name', label: 'Tabel', sortKey: 'name', render: r => <div className="min-w-40 max-w-md">
    <div className="font-mono text-sm">{r.name}</div>
    {r.comment && <div className="truncate text-xs text-(--cui-color-text-muted)" title={r.comment}>{r.comment}</div>}
  </div> },
  { key: 'rows', label: 'Baris data', sortKey: 'rows', align: 'right', render: r => (r.rows == null ? '—' : whole.format(r.rows)) },
  { key: 'metrics', label: 'Metrik', sortKey: 'metrics', align: 'right', render: r => count(r.metrics) },
  { key: 'queries', label: 'Verified query', sortKey: 'queries', align: 'right', render: r => count(r.queries) },
  { key: 'drafts', label: 'Draf', sortKey: 'drafts', align: 'right', render: r => count(r.drafts) },
  { key: 'status', label: 'Status', sortKey: 'status', render: r => <TableTag status={r.status} /> },
];
/** Definitions that read one table, beside the coverage table. */
function TableDetail({ table, onClose }: { table: SemanticTable; onClose: () => void }) {
  const query = useQuery({ queryKey: ['kaiSemantic', 'table', table.ref], retry: false,
    queryFn: () => semanticListFn({ data: { table: table.ref, size: 100, sort: 'kind', dir: 'asc' } }) });
  const rows = query.data?.definitions ?? [];
  const total = query.data?.total ?? 0;
  return <aside className="flex flex-col gap-3 rounded-md border border-(--cui-color-stroke-default) p-3 xl:sticky xl:top-4 xl:max-h-[85vh] xl:self-start xl:overflow-y-auto">
    <div className="flex items-start justify-between gap-2">
      <div><h3 className="font-mono font-medium">{table.ref}</h3>{table.comment && <p className="text-xs text-(--cui-color-text-muted)">{table.comment}</p>}</div>
      <Button type="secondary" label="Tutup" onClick={onClose} />
    </div>
    {query.isPending && <p className="text-sm text-(--cui-color-text-muted)">Memuat…</p>}
    {query.isError && <Notice tone="error">{errorText(query.error, 'Definisi tabel ini tidak bisa dimuat.')}</Notice>}
    {query.data && !rows.length && <p className="text-sm text-(--cui-color-text-muted)">Belum ada definisi. Buat lewat tab Buat draf AI, atau tambah manual di Semua definisi.</p>}
    <ul className="flex flex-col gap-2">{rows.map(r => <li key={r.definition_id} className="flex flex-col gap-1 rounded-md border border-(--cui-color-stroke-default) p-2">
      <span className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{r.name}</span>
        <span className="flex gap-1"><KindTag kind={r.kind} /><StatusTag row={r} /></span></span>
      <span className={`text-xs text-(--cui-color-text-muted) ${r.kind === 'metric' ? 'font-mono' : ''}`}>{r.definition.expression || r.definition.question || r.definition.description}</span>
      {r.tables.length > 1 && <span className="text-xs text-(--cui-color-text-muted)">Juga membaca: {r.tables.filter(x => x !== table.ref).join(', ')}</span>}
    </li>)}</ul>
    {total > rows.length && <p className="text-xs text-(--cui-color-text-muted)">Menampilkan {rows.length} dari {whole.format(total)}. Lihat semuanya di tab Semua definisi.</p>}
  </aside>;
}
/** Per-database coverage: every table the admin's ClickHouse account can read, paged on the server. */
function Coverage({ databases }: { databases: SemanticDatabase[] }) {
  const [database, setDatabase] = useState(databases[0]?.name ?? '');
  const [text, setText] = useState(''), [q, setQ] = useState('');
  const [status, setStatus] = useState<TableStatus | undefined>(undefined);
  const [view, setView] = useState<View>({ page: 1, size: 25, sort: 'name', dir: 'asc' });
  const [open, setOpen] = useState<SemanticTable | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => { setQ(text.trim()); setView(v => ({ ...v, page: 1 })); }, 300);
    return () => clearTimeout(timer);
  }, [text]);
  const current = databases.some(d => d.name === database) ? database : databases[0]?.name ?? '';
  const query = useQuery({ queryKey: ['kaiSemantic', 'tables', current, status, q, view], enabled: !!current, retry: false, placeholderData: keepPreviousData,
    queryFn: () => semanticTablesFn({ data: { database: current, ...(status ? { status } : {}), ...(q ? { q } : {}), ...view } }) });
  const data = query.data?.database === current ? query.data : undefined;
  const all = data ? ORDER.reduce((n, s) => n + data.counts[s], 0) : 0;
  const pick = (next: TableStatus | undefined) => { setStatus(next); setView(v => ({ ...v, page: 1 })); };
  return <section className="flex flex-col gap-3" aria-label="Cakupan per tabel">
    <div className="flex flex-wrap items-end justify-between gap-2">
      <h2 className="text-sm font-medium">Cakupan per tabel</h2>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-sm">Database
          <Select value={current} onChange={e => { setDatabase(e.target.value); setOpen(null); setView(v => ({ ...v, page: 1 })); }}>
            {databases.map(d => <option key={d.name} value={d.name}>{d.name} ({whole.format(d.tables)} tabel)</option>)}</Select>
        </label>
        <SearchInput value={text} onChange={setText} placeholder="Cari nama atau komentar tabel" ariaLabel="Cari tabel" className="min-w-56" />
      </div>
    </div>
    <div role="group" aria-label="Saring status tabel" className="flex flex-wrap gap-1">
      <Button type={status ? 'secondary' : 'primary'} label={`Semua (${whole.format(all)})`} onClick={() => pick(undefined)} />
      {ORDER.filter(s => s !== 'rejected' || (data?.counts.rejected ?? 0) > 0).map(s =>
        <Button key={s} type={status === s ? 'primary' : 'secondary'} label={`${TABLE[s].label} (${whole.format(data?.counts[s] ?? 0)})`} onClick={() => pick(s)} />)}
    </div>
    {query.isError && <Notice tone="error">{errorText(query.error, 'Daftar tabel tidak bisa dimuat.')}</Notice>}
    <div className={open ? 'grid gap-3 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]' : ''}>
      <DataTable caption={`Tabel di ${current}`} columns={COLUMNS} rows={data?.tables ?? []} rowKey={r => r.ref} total={data?.total ?? 0}
        page={view.page} size={view.size} loading={query.isFetching} onPage={page => setView(v => ({ ...v, page }))}
        onSize={size => setView(v => ({ ...v, size: size as PageSize, page: 1 }))} sort={view.sort} dir={view.dir}
        onSort={(sort, dir) => setView(v => ({ ...v, sort: sort as View['sort'], dir, page: 1 }))}
        onRowClick={setOpen} activeKey={open?.ref ?? null} empty="Tidak ada tabel yang cocok." />
      {open && <TableDetail key={open.ref} table={open} onClose={() => setOpen(null)} />}
    </div>
  </section>;
}
export function Overview({ databases, canManage }: { databases: SemanticDatabase[] | null; canManage: boolean }) {
  const summary = useQuery({ queryKey: ['kaiSemantic', 'summary'], queryFn: () => semanticListFn({ data: { size: 10 } }), retry: false });
  const counts = summary.data?.counts ?? {};
  const tables = databases?.reduce((n, d) => n + d.tables, 0) ?? 0;
  const covered = databases?.reduce((n, d) => n + d.covered, 0) ?? 0;
  return <div className="flex flex-col gap-6">
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Tile label="Disetujui" value={whole.format(counts.approved ?? 0)} hint="Dipakai agent" />
      <Tile label="Draf menunggu" value={whole.format(counts.draft ?? 0)} hint="Perlu ditinjau" />
      <Tile label="Ditolak" value={whole.format(counts.rejected ?? 0)} hint="Tidak diusulkan ulang" />
      <Tile label="Tabel tercakup" value={tables ? `${Math.round((covered / tables) * 100)}%` : '—'}
        hint={tables ? `${whole.format(covered)} dari ${whole.format(tables)} tabel` : 'Butuh akses ClickHouse akun Anda'} />
    </div>
    {databases && databases.length > 0 && <figure className="flex flex-col gap-2">
      <figcaption className="text-sm font-medium">Cakupan per database</figcaption>
      <ul className="flex flex-col gap-2">{databases.map(d => <li key={d.name} className="grid grid-cols-[minmax(6rem,12rem)_1fr_auto] items-center gap-3 text-sm">
        <span className="truncate font-mono" title={d.name}>{d.name}</span>
        <span className="relative h-2.5 overflow-hidden rounded-full bg-(--cui-color-stroke-default)" role="img" aria-label={`${d.name}: ${d.covered} dari ${d.tables} tabel punya definisi`}>
          <span className={`absolute inset-y-0 left-0 ${TONE.approved.dot}`} style={{ width: `${(d.covered / Math.max(d.tables, 1)) * 100}%` }} /></span>
        <span className="tabular-nums text-(--cui-color-text-muted)">{whole.format(d.covered)} / {whole.format(d.tables)}</span>
      </li>)}</ul>
    </figure>}
    {canManage && databases && databases.length > 0 && <Coverage databases={databases} />}
    {!canManage && <p className="text-sm text-(--cui-color-text-muted)">Cakupan per tabel hanya tampil untuk admin yang boleh mengubah semantic layer.</p>}
    {canManage && databases?.length === 0 && <p className="text-sm text-(--cui-color-text-muted)">Akun ClickHouse Anda belum bisa membaca database apa pun.</p>}
  </div>;
}
