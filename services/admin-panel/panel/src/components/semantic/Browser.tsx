import { useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQueryClient } from '@tanstack/react-query';
import type { SemanticDefinition, SemanticStatus } from '@/server/kaiAccounts';
import { Notice, Select, inputClass } from '../users/AccountFields';
import { Editor, KIND, SOURCE, STATUS, databaseOf, setStatus } from './Editor';
const whole = new Intl.NumberFormat('id-ID');
const ALL = '';
const summary = (row: SemanticDefinition) => row.definition.question || row.definition.expression || row.definition.description || '';
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' }) : '—');
/**
 * List and detail of definitions. mode "review": drafts only, with multi-select for bulk approve/reject.
 * mode "all": every definition with a status filter. Selecting a row opens its editor beside the list.
 */
export function Browser({ rows, mode, canManage }: { rows: SemanticDefinition[]; mode: 'review' | 'all'; canManage: boolean }) {
  const client = useQueryClient();
  const [search, setSearch] = useState(''), [database, setDatabase] = useState(ALL), [kind, setKind] = useState(ALL);
  const [source, setSource] = useState(ALL), [status, setStatusFilter] = useState<SemanticStatus | ''>(ALL);
  const [open, setOpen] = useState<string | null>(null), [picked, setPicked] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null), [notice, setNotice] = useState(''), [failed, setFailed] = useState<string[]>([]);
  const scope = mode === 'review' ? rows.filter(r => r.status === 'draft') : rows;
  const databases = [...new Set(scope.flatMap(r => r.tables.map(databaseOf)))].sort();
  const words = search.trim().toLowerCase();
  const shown = scope.filter(r => (!database || r.tables.some(t => databaseOf(t) === database)) && (!kind || r.kind === kind)
    && (!source || r.source === source) && (!status || r.status === status)
    && (!words || `${r.name} ${summary(r)} ${r.tables.join(' ')} ${(r.definition.synonyms ?? []).join(' ')}`.toLowerCase().includes(words)));
  const selected = shown.filter(r => picked.has(r.definition_id));
  const current = open === 'new' ? null : scope.find(r => r.definition_id === open);
  const toggle = (id: string) => setPicked(prev => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  async function bulk(next: SemanticStatus) {
    setNotice(''); setFailed([]); setProgress({ done: 0, total: selected.length });
    const errors = await setStatus(selected, next, done => setProgress({ done, total: selected.length }));
    await client.invalidateQueries({ queryKey: ['kaiSemantic'] });
    setProgress(null); setPicked(new Set()); setFailed(errors);
    setNotice(`${whole.format(selected.length - errors.length)} definisi ${next === 'approved' ? 'disetujui' : 'ditolak'}${errors.length ? `, ${errors.length} gagal` : ''}.`);
  }
  const done = (message: string) => { setOpen(null); setNotice(message); };
  /** Approve/reject the open definition as it is saved (edits in the form need "Simpan dan setujui"). */
  async function quick(row: SemanticDefinition, next: SemanticStatus) {
    setNotice(''); setFailed([]); setProgress({ done: 0, total: 1 });
    const errors = await setStatus([row], next, () => undefined);
    await client.invalidateQueries({ queryKey: ['kaiSemantic'] });
    setProgress(null); setFailed(errors);
    if (!errors.length) done(`${row.name} ${next === 'approved' ? 'disetujui' : 'ditolak'}.`);
  }
  return <div className="flex flex-col gap-3">
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm">Cari
        <input className={inputClass} value={search} placeholder="nama, rumus, tabel, sinonim" onChange={e => setSearch(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm">Database
        <Select value={database} onChange={e => setDatabase(e.target.value)}><option value={ALL}>Semua</option>{databases.map(d => <option key={d} value={d}>{d}</option>)}</Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Jenis
        <Select value={kind} onChange={e => setKind(e.target.value)}><option value={ALL}>Semua</option><option value="metric">Metrik</option><option value="verified_query">Verified query</option></Select>
      </label>
      <label className="flex flex-col gap-1 text-sm">Sumber
        <Select value={source} onChange={e => setSource(e.target.value)}><option value={ALL}>Semua</option><option value="ai">Draf AI</option><option value="manual">Manual</option></Select>
      </label>
      {mode === 'all' && <label className="flex flex-col gap-1 text-sm">Status
        <Select value={status} onChange={e => setStatusFilter(e.target.value as SemanticStatus | '')}><option value={ALL}>Semua</option>
          {(Object.keys(STATUS) as SemanticStatus[]).map(s => <option key={s} value={s}>{STATUS[s].label}</option>)}</Select>
      </label>}
      {mode === 'all' && canManage && <Button type="secondary" label="Tambah definisi" onClick={() => { setNotice(''); setOpen('new'); }} />}
    </div>
    {mode === 'review' && canManage && shown.length > 0 && <div className="flex flex-wrap items-center gap-2 text-sm">
      <label className="flex items-center gap-2"><input type="checkbox" checked={selected.length === shown.length}
        onChange={e => setPicked(e.target.checked ? new Set(shown.map(r => r.definition_id)) : new Set())} />Pilih semua yang tampil ({whole.format(shown.length)})</label>
      <Button type="primary" label={`Setujui ${selected.length || ''}`.trim()} disabled={!selected.length || !!progress} onClick={() => void bulk('approved')} />
      <Button type="secondary" label={`Tolak ${selected.length || ''}`.trim()} disabled={!selected.length || !!progress} onClick={() => void bulk('rejected')} />
      {progress && <span aria-live="polite" className="text-(--cui-color-text-muted)">Memproses {progress.done}/{progress.total}…</span>}
    </div>}
    {notice && <Notice tone={failed.length ? 'warning' : 'success'}>{notice}</Notice>}
    {failed.length > 0 && <ul className="text-xs text-(--cui-color-text-muted)">{failed.map(f => <li key={f}>{f}</li>)}</ul>}
    <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-(--cui-color-text-muted)">{whole.format(shown.length)} dari {whole.format(scope.length)} definisi</span>
        {!shown.length && <p className="text-sm text-(--cui-color-text-muted)">{mode === 'review' ? 'Tidak ada draf yang menunggu tinjauan.' : 'Tidak ada definisi yang cocok dengan filter.'}</p>}
        <ul className="flex max-h-[70vh] flex-col gap-1 overflow-y-auto pr-1">{shown.map(row => <li key={row.definition_id}
          className={`flex items-start gap-2 rounded-md border p-2 ${open === row.definition_id ? 'border-(--cui-color-accent-default)' : 'border-(--cui-color-stroke-default)'}`}>
          {mode === 'review' && canManage && <input type="checkbox" className="mt-1" aria-label={`Pilih ${row.name}`} checked={picked.has(row.definition_id)} onChange={() => toggle(row.definition_id)} />}
          <button type="button" className="flex min-w-0 flex-1 flex-col gap-0.5 text-left" onClick={() => { setNotice(''); setOpen(row.definition_id); }}>
            <span className="flex flex-wrap items-center gap-1.5 text-sm font-medium">{row.name}<Badge text={KIND[row.kind]} state="neutral" />
              {mode === 'all' && <Badge text={STATUS[row.status].label} state={STATUS[row.status].state} />}</span>
            <span className={`truncate text-xs text-(--cui-color-text-muted) ${row.kind === 'metric' ? 'font-mono' : ''}`}>{summary(row)}</span>
            <span className="truncate text-xs text-(--cui-color-text-muted)">{row.tables.join(', ')}, {SOURCE[row.source]}, {when(row.updated_at)}</span>
          </button>
        </li>)}</ul>
      </div>
      <div className="rounded-md border border-(--cui-color-stroke-default) p-3 lg:sticky lg:top-4 lg:max-h-[85vh] lg:self-start lg:overflow-y-auto">
        {open === 'new' && <><h3 className="mb-3 font-medium">Definisi baru</h3><Editor key="new" row={null} canManage={canManage} onDone={done} /></>}
        {current && <><div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <span className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{current.name}</h3>
            <Badge text={STATUS[current.status].label} state={STATUS[current.status].state} /><Badge text={SOURCE[current.source]} state="neutral" /></span>
          {canManage && <span className="flex gap-2">
            {current.status !== 'approved' && <Button type="primary" label="Setujui" disabled={!!progress} onClick={() => void quick(current, 'approved')} />}
            {current.status !== 'rejected' && <Button type="secondary" label="Tolak" disabled={!!progress} onClick={() => void quick(current, 'rejected')} />}
          </span>}
        </div>
          <Editor key={current.definition_id} row={current} canManage={canManage} onDone={done} /></>}
        {!open && <p className="text-sm text-(--cui-color-text-muted)">Pilih definisi di daftar untuk melihat rumus atau SQL-nya, mengubah, lalu menyetujui atau menolaknya.</p>}
      </div>
    </div>
  </div>;
}
