import { useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { createSemanticFn, deleteSemanticFn, semanticListFn, updateSemanticFn } from '@/server/kaiAccounts';
import type { SemanticDefinition, SemanticForm, SemanticStatus } from '@/server/kaiAccounts';
import { Notice, Select, errorText, inputClass } from './AccountFields';
const STATUS: Record<SemanticStatus, { label: string; state: 'neutral' | 'success' | 'danger' }> = {
  draft: { label: 'Draf', state: 'neutral' }, approved: { label: 'Disetujui', state: 'success' }, rejected: { label: 'Ditolak', state: 'danger' },
};
const KIND = { metric: 'Metrik', verified_query: 'Verified query' };
const SAVED: Record<SemanticStatus, string> = { approved: 'Disetujui. Agent memakainya dalam 1 menit.', rejected: 'Ditolak.', draft: 'Tersimpan sebagai draf.' };
const SOURCE = { ai: 'Draf AI', manual: 'Manual', catalog: 'Katalog' };
const METRIC_TYPES = ['sum', 'count', 'count_distinct', 'average', 'min', 'max', 'ratio', 'other'];
const EMPTY: SemanticForm = {
  kind: 'metric', name: '', status: 'draft', description: '', synonyms: [], table: '', expression: '', metric_type: 'sum',
  unit: '', time_column: '', dimensions: [], filter: '', question: '', sql: '',
};
const list = (text: string) => text.split(',').map(item => item.trim()).filter(Boolean);
function toForm(row: SemanticDefinition): SemanticForm {
  const d = row.definition;
  return {
    ...EMPTY, kind: row.kind, name: row.name, status: row.status, table: row.tables[0] ?? '', description: d.description ?? '',
    synonyms: d.synonyms ?? [], expression: d.expression ?? '', metric_type: d.metric_type ?? 'other', unit: d.unit ?? '',
    time_column: d.time_column ?? '', dimensions: d.dimensions ?? [], filter: d.filter ?? '', question: d.question ?? '', sql: d.sql ?? '',
  };
}
function Text({ label, value, onChange, disabled, area = false, help, placeholder }: { label: string; value: string; onChange: (value: string) => void; disabled: boolean; area?: boolean; help?: string; placeholder?: string }) {
  return <label className="flex w-full flex-col gap-1 text-sm">{label}
    {area
      ? <textarea className={`${inputClass} min-h-28 font-mono`} value={value} disabled={disabled} placeholder={placeholder} onChange={e => onChange(e.target.value)} />
      : <input className={inputClass} value={value} disabled={disabled} placeholder={placeholder} onChange={e => onChange(e.target.value)} />}
    {help && <span className="text-xs text-(--cui-color-text-muted)">{help}</span>}
  </label>;
}
/** Review form for one definition (or a new one when row is null). Saving re-validates the SQL in the MCP. */
function Editor({ row, canManage, onDone }: { row: SemanticDefinition | null; canManage: boolean; onDone: (message: string) => void }) {
  const client = useQueryClient();
  const [form, setForm] = useState<SemanticForm>(row ? toForm(row) : EMPTY);
  const [synonyms, setSynonyms] = useState(form.synonyms.join(', ')), [dimensions, setDimensions] = useState(form.dimensions.join(', '));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const disabled = busy || !canManage;
  const set = (key: keyof SemanticForm) => (value: string) => setForm({ ...form, [key]: value });
  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true); setError('');
    try {
      await action();
      await client.invalidateQueries({ queryKey: ['kaiSemantic'] });
      onDone(message);
    } catch (cause) {
      setError(errorText(cause, 'Definisi gagal disimpan. Coba lagi.'));
    } finally {
      setBusy(false);
    }
  }
  const save = (status: SemanticStatus) => {
    const data = { ...form, status, synonyms: list(synonyms), dimensions: list(dimensions) };
    return run(() => (row ? updateSemanticFn({ data: { id: row.definition_id, form: data } }) : createSemanticFn({ data })), SAVED[status]);
  };
  const metric = form.kind === 'metric';
  return <form className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-3" onSubmit={e => e.preventDefault()}>
    <div className="flex flex-wrap gap-3">
      <label className="flex flex-col gap-1 text-sm">Jenis
        <Select value={form.kind} disabled={disabled || !!row} onChange={e => setForm({ ...form, kind: e.target.value as SemanticForm['kind'] })}>
          <option value="metric">Metrik</option><option value="verified_query">Verified query</option>
        </Select>
      </label>
      <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm">Nama
        <input className={inputClass} value={form.name} disabled={disabled} placeholder="pendapatan_bersih" onChange={e => set('name')(e.target.value)} />
      </label>
    </div>
    {metric
      ? <>
        <Text label="Tabel" value={form.table} onChange={set('table')} disabled={disabled} placeholder="database.tabel" />
        <Text label="Rumus (ekspresi agregat)" value={form.expression} onChange={set('expression')} disabled={disabled} placeholder="sum(pendapatan_rp)" help="Hanya kolom dari tabel ini. Agent menambahkan WHERE dan GROUP BY sesuai pertanyaan tanpa mengubah rumus." />
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">Tipe
            <Select value={form.metric_type} disabled={disabled} onChange={e => set('metric_type')(e.target.value)}>{METRIC_TYPES.map(t => <option key={t} value={t}>{t}</option>)}</Select>
          </label>
          <label className="flex flex-1 flex-col gap-1 text-sm">Satuan<input className={inputClass} value={form.unit} disabled={disabled} placeholder="Rupiah" onChange={e => set('unit')(e.target.value)} /></label>
          <label className="flex flex-1 flex-col gap-1 text-sm">Kolom waktu<input className={inputClass} value={form.time_column} disabled={disabled} placeholder="tanggal" onChange={e => set('time_column')(e.target.value)} /></label>
        </div>
        <Text label="Dimensi (pisahkan dengan koma)" value={dimensions} onChange={setDimensions} disabled={disabled} placeholder="cabang, kategori" />
        <Text label="Filter tetap (opsional)" value={form.filter} onChange={set('filter')} disabled={disabled} placeholder="status != 'batal'" help="Aturan bisnis yang selalu berlaku untuk metrik ini." />
      </>
      : <>
        <Text label="Pertanyaan" value={form.question} onChange={set('question')} disabled={disabled} placeholder="Pertumbuhan pendapatan per cabang per bulan" />
        <Text label="SQL" value={form.sql} onChange={set('sql')} disabled={disabled} area placeholder="SELECT ... FROM database.tabel ..." help="Satu SELECT read-only dengan nama database.tabel lengkap." />
      </>}
    <Text label="Deskripsi" value={form.description} onChange={set('description')} disabled={disabled} />
    <Text label="Sinonim (pisahkan dengan koma)" value={synonyms} onChange={setSynonyms} disabled={disabled} placeholder="omzet, revenue" help="Istilah sehari-hari yang dipakai pengguna saat bertanya." />
    {error && <Notice tone="error">{error}</Notice>}
    {canManage && <div className="flex flex-wrap gap-2">
      <Button type="primary" label="Simpan dan setujui" disabled={busy} onClick={() => void save('approved')} />
      <Button type="secondary" label="Simpan sebagai draf" disabled={busy} onClick={() => void save('draft')} />
      {row && row.status !== 'rejected' && <Button type="secondary" label="Tolak" disabled={busy} onClick={() => void save('rejected')} />}
      {row && <Button type="danger" label="Hapus" disabled={busy} onClick={() => { if (window.confirm(`Hapus definisi ${row.name}?`)) void run(() => deleteSemanticFn({ data: { id: row.definition_id } }), 'Dihapus.'); }} />}
    </div>}
  </form>;
}
export function SemanticPanel() {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_CONFIGS);
  const query = useQuery({ queryKey: ['kaiSemantic'], queryFn: () => semanticListFn(), retry: false });
  const [filter, setFilter] = useState<SemanticStatus>('draft');
  const [open, setOpen] = useState<string | null>(null), [notice, setNotice] = useState('');
  const rows = query.data?.definitions ?? [];
  const counts = rows.reduce<Record<SemanticStatus, number>>((acc, r) => ({ ...acc, [r.status]: acc[r.status] + 1 }), { draft: 0, approved: 0, rejected: 0 });
  const shown = rows.filter(r => r.status === filter);
  const done = (message: string) => { setOpen(null); setNotice(message); };
  const review = canManage ? 'Tinjau' : 'Lihat';
  return <section aria-label="Semantic layer" className="flex flex-col gap-3 rounded-lg border border-(--cui-color-stroke-default) p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-medium">Semantic layer {counts.draft > 0 && <Badge text={`${counts.draft} draf menunggu`} state="neutral" />}</h2>
      {canManage && <Button type="secondary" label={open === 'new' ? 'Tutup' : 'Tambah definisi'} onClick={() => { setNotice(''); setOpen(open === 'new' ? null : 'new'); }} />}
    </div>
    <div className="flex flex-col gap-2 text-sm text-(--cui-color-text-muted)">
      <p>Definisi resmi metrik (mis. &ldquo;pendapatan bersih = sum(pendapatan_rp)&rdquo;) dan verified query (pertanyaan umum beserta SQL yang sudah diperiksa). Bila pertanyaan pengguna cocok, K-AI Analis Data memakai definisi ini, jadi angkanya konsisten dan jawabannya lebih cepat.</p>
      <p>Draf bisa dibuat AI: minta K-AI Analis Data &ldquo;buatkan draf semantic layer untuk database X&rdquo;. AI hanya membaca skema, bukan isi data. Draf baru dipakai setelah disetujui di sini. Query tetap dijalankan dengan hak akses ClickHouse masing-masing pengguna.</p>
    </div>
    {query.isError && <Notice tone="error">Semantic layer tidak bisa dibaca.</Notice>}
    {query.data && !query.data.storage_available && <Notice tone="warning">Penyimpanan belum disiapkan di server (jalankan make migrate).</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {open === 'new' && <Editor row={null} canManage={canManage} onDone={done} />}
    <label className="flex items-center gap-2 text-sm"><span className="text-(--cui-color-text-muted)">Tampilkan</span>
      <Select value={filter} onChange={e => { setFilter(e.target.value as SemanticStatus); setOpen(null); }}>
        {(Object.keys(STATUS) as SemanticStatus[]).map(s => <option key={s} value={s}>{STATUS[s].label} ({counts[s]})</option>)}
      </Select>
    </label>
    {query.isSuccess && !shown.length && <p className="text-sm text-(--cui-color-text-muted)">Belum ada definisi dengan status ini.</p>}
    <ul className="flex flex-col gap-2">{shown.map(row => <li key={row.definition_id} className="rounded-md border border-(--cui-color-stroke-default) p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-2 font-medium">{row.name}<Badge text={KIND[row.kind]} state="neutral" /><Badge text={STATUS[row.status].label} state={STATUS[row.status].state} /></span>
          <span className="text-sm wrap-break-word">{row.definition.question || row.definition.description || row.definition.expression}</span>
          <span className="text-xs break-all text-(--cui-color-text-muted)">{row.tables.join(', ')} · {SOURCE[row.source]}</span>
        </div>
        <Button type="secondary" label={open === row.definition_id ? 'Tutup' : review} onClick={() => { setNotice(''); setOpen(open === row.definition_id ? null : row.definition_id); }} />
      </div>
      {open === row.definition_id && <Editor row={row} canManage={canManage} onDone={done} />}
    </li>)}</ul>
  </section>;
}
