import { useEffect, useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import type { PageSize, SemanticDefinition, SemanticQuery, SemanticStatus } from '@/server/kaiAccounts';
import type * as t from '@/types';
import { Notice, Select, errorText } from '../users/AccountFields';
import { bulkSemanticFn, semanticListFn } from '@/server/kaiAccounts';
import { Editor, KindTag, STATUS, StatusTag } from './Editor';
import { DataTable, SearchInput } from '@/components/shared';
const whole = new Intl.NumberFormat('id-ID');
const summary = (row: SemanticDefinition) => row.definition.question || row.definition.expression || row.definition.description || '';
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' }) : '—');
const VERB: Record<SemanticStatus, string> = { approved: 'disetujui', rejected: 'ditolak', draft: 'dikembalikan ke draf' };
type Filter = Pick<SemanticQuery, 'status' | 'kind' | 'source' | 'database' | 'q'>;
type View = { page: number; size: PageSize; sort: SemanticQuery['sort']; dir: 'asc' | 'desc' };
const COLUMNS: t.DataTableColumn<SemanticDefinition>[] = [
  { key: 'name', label: 'Nama', sortKey: 'name', render: r => <div className="min-w-48 max-w-md">
    <div className="font-medium">{r.name}</div>
    <div className={`truncate text-xs text-(--cui-color-text-muted) ${r.kind === 'metric' ? 'font-mono' : ''}`} title={summary(r)}>{summary(r)}</div>
  </div> },
  { key: 'kind', label: 'Jenis', sortKey: 'kind', render: r => <KindTag kind={r.kind} /> },
  { key: 'status', label: 'Status', sortKey: 'status', render: r => <StatusTag row={r} /> },
  { key: 'tables', label: 'Tabel', sortKey: 'table', render: r => <span className="font-mono text-xs" title={r.tables.join(', ')}>
    {r.tables[0]}{r.tables.length > 1 ? ` +${r.tables.length - 1}` : ''}</span> },
  { key: 'updated', label: 'Diperbarui', sortKey: 'updated_at', className: 'whitespace-nowrap',
    render: r => <span className="text-xs text-(--cui-color-text-muted)">{when(r.updated_at)}</span> },
];
/** Search waits for a pause in typing, so each keystroke is not a server round trip. */
function useDebounced(value: string, ms = 300) {
  const [settled, setSettled] = useState(value);
  useEffect(() => { const timer = setTimeout(() => setSettled(value), ms); return () => clearTimeout(timer); }, [value, ms]);
  return settled;
}
/**
 * Definitions as a server-paged table. mode "review": drafts only, with bulk approve/reject (selected rows, or every
 * match of the filter, in one request). mode "all": every status. Clicking a row opens its editor beside the table.
 */
export function Browser({ mode, canManage, databases }: { mode: 'review' | 'all'; canManage: boolean; databases: string[] }) {
  const client = useQueryClient();
  const [text, setText] = useState('');
  const q = useDebounced(text.trim());
  const [filter, setFilter] = useState<Filter>({});
  const [view, setView] = useState<View>({ page: 1, size: 25, sort: 'updated_at', dir: 'desc' });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<SemanticDefinition | 'new' | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const active: Filter = { ...filter, q: q || undefined, ...(mode === 'review' ? { status: 'draft' as const } : {}) };
  const query = useQuery({ queryKey: ['kaiSemantic', 'list', active, view], queryFn: () => semanticListFn({ data: { ...active, ...view } }),
    retry: false, placeholderData: keepPreviousData });
  const data = query.data;
  const narrowed = Object.values(active).some(Boolean);
  const reset = () => { setView(v => ({ ...v, page: 1 })); setSelected(new Set()); };
  const change = (patch: Partial<Filter>) => { setFilter(f => ({ ...f, ...patch })); reset(); };
  useEffect(reset, [q]);
  /** One request for any number of rows; the server answers with how many actually changed. */
  async function apply(status: SemanticStatus, target: { ids: string[] } | { filter: Filter }, label?: string) {
    setBusy(true); setNotice(null);
    try {
      const { changed } = await bulkSemanticFn({ data: { status, ...target } });
      const what = label ?? `${whole.format(changed)} definisi`;
      setNotice({ tone: 'success', text: `${what} ${VERB[status]}.${status === 'approved' ? ' Agent memakainya dalam 1 menit.' : ''}` });
      setSelected(new Set());
      if (label) setOpen(null);
      await client.invalidateQueries({ queryKey: ['kaiSemantic'] });
    } catch (cause) {
      setNotice({ tone: 'error', text: errorText(cause, 'Perubahan gagal disimpan.') });
    } finally {
      setBusy(false);
    }
  }
  const approveAll = () => {
    if (data && window.confirm(`${whole.format(data.total)} draf yang cocok dengan filter akan disetujui. Lanjutkan?`)) void apply('approved', { filter: active });
  };
  const done = (message: string) => { setOpen(null); setNotice({ tone: 'success', text: message }); };
  const current = open && open !== 'new' ? open : null;
  return <div className="flex flex-col gap-3">
    <div className="flex flex-wrap items-end gap-2">
      <SearchInput value={text} onChange={setText} placeholder="Cari nama, rumus, tabel, sinonim" ariaLabel="Cari definisi" className="min-w-64 flex-1" />
      <label className="flex flex-col gap-1 text-sm">Database
        <Select value={filter.database ?? ''} onChange={e => change({ database: e.target.value || undefined })}><option value="">Semua</option>
          {databases.map(d => <option key={d} value={d}>{d}</option>)}</Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Jenis
        <Select value={filter.kind ?? ''} onChange={e => change({ kind: (e.target.value || undefined) as Filter['kind'] })}><option value="">Semua</option>
          <option value="metric">Metrik</option><option value="verified_query">Verified query</option></Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Sumber
        <Select value={filter.source ?? ''} onChange={e => change({ source: (e.target.value || undefined) as Filter['source'] })}><option value="">Semua</option>
          <option value="ai">Draf AI</option><option value="manual">Manual</option></Select>
      </label>
      {mode === 'all' && <label className="flex flex-col gap-1 text-sm">Status
        <Select value={filter.status ?? ''} onChange={e => change({ status: (e.target.value || undefined) as SemanticStatus | undefined })}><option value="">Semua</option>
          {(Object.keys(STATUS) as SemanticStatus[]).map(s => <option key={s} value={s}>{STATUS[s].label}</option>)}</Select>
      </label>}
      {mode === 'all' && canManage && <Button type="secondary" label="Tambah definisi" onClick={() => { setNotice(null); setOpen('new'); }} />}
    </div>
    {canManage && mode === 'review' && (data?.total ?? 0) > 0 && <div className="flex flex-wrap items-center gap-2 rounded-md border border-(--cui-color-stroke-default) p-2 text-sm">
      {selected.size > 0 ? <>
        <span className="font-medium">{whole.format(selected.size)} dipilih</span>
        <Button type="primary" label="Setujui yang dipilih" disabled={busy} onClick={() => void apply('approved', { ids: [...selected] })} />
        <Button type="secondary" label="Tolak yang dipilih" disabled={busy} onClick={() => void apply('rejected', { ids: [...selected] })} />
        <Button type="secondary" label="Batal pilih" disabled={busy} onClick={() => setSelected(new Set())} />
      </> : <>
        <span className="text-(--cui-color-text-muted)">Centang baris untuk menyetujui sebagian, atau setujui semua {whole.format(data?.total ?? 0)} draf yang cocok dengan filter.</span>
        {narrowed && <Button type="secondary" label={`Setujui semua (${whole.format(data?.total ?? 0)})`} disabled={busy} onClick={approveAll} />}
      </>}
      {busy && <span aria-live="polite" className="text-(--cui-color-text-muted)">Menyimpan…</span>}
    </div>}
    {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
    {query.isError && <Notice tone="error">{errorText(query.error, 'Definisi tidak bisa dimuat.')}</Notice>}
    <div className={open ? 'grid gap-3 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]' : ''}>
      <DataTable caption={mode === 'review' ? 'Draf menunggu tinjauan' : 'Semua definisi'} columns={COLUMNS} rows={data?.definitions ?? []}
        rowKey={r => r.definition_id} total={data?.total ?? 0} page={view.page} size={view.size} loading={query.isFetching}
        onPage={page => setView(v => ({ ...v, page }))} onSize={size => setView(v => ({ ...v, size: size as PageSize, page: 1 }))}
        sort={view.sort} dir={view.dir} onSort={(sort, dir) => setView(v => ({ ...v, sort: sort as SemanticQuery['sort'], dir, page: 1 }))}
        selected={canManage && mode === 'review' ? selected : undefined} onSelect={setSelected}
        onRowClick={row => { setNotice(null); setOpen(row); }} activeKey={current?.definition_id ?? null}
        empty={mode === 'review' ? 'Tidak ada draf yang menunggu tinjauan.' : 'Tidak ada definisi yang cocok dengan filter.'} />
      {open && <aside className="rounded-md border border-(--cui-color-stroke-default) p-3 xl:sticky xl:top-4 xl:max-h-[85vh] xl:self-start xl:overflow-y-auto">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {current ? <span className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{current.name}</h3><KindTag kind={current.kind} /><StatusTag row={current} /></span>
            : <h3 className="font-medium">Definisi baru</h3>}
          <span className="flex gap-2">
            {current && canManage && current.status !== 'approved' && <Button type="primary" label="Setujui" disabled={busy}
              onClick={() => void apply('approved', { ids: [current.definition_id] }, current.name)} />}
            {current && canManage && current.status !== 'rejected' && <Button type="secondary" label="Tolak" disabled={busy}
              onClick={() => void apply('rejected', { ids: [current.definition_id] }, current.name)} />}
            <Button type="secondary" label="Tutup" onClick={() => setOpen(null)} />
          </span>
        </div>
        <Editor key={current?.definition_id ?? 'new'} row={current} canManage={canManage} onDone={done} />
      </aside>}
    </div>
  </div>;
}
