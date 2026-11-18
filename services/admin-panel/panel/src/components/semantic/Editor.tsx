import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import { useQueryClient } from '@tanstack/react-query';
import { createSemanticFn, deleteSemanticFn, updateSemanticFn } from '@/server/kaiAccounts';
import type { SemanticDefinition, SemanticForm, SemanticStatus } from '@/server/kaiAccounts';
import { Notice, Select, errorText, inputClass } from '../users/AccountFields';
export const STATUS: Record<SemanticStatus, { label: string; state: 'neutral' | 'success' | 'danger' }> = {
  draft: { label: 'Draf', state: 'neutral' }, approved: { label: 'Disetujui', state: 'success' }, rejected: { label: 'Ditolak', state: 'danger' },
};
export const KIND = { metric: 'Metrik', verified_query: 'Verified query' };
const SAVED: Record<SemanticStatus, string> = { approved: 'Disetujui. Agent memakainya dalam 1 menit.', rejected: 'Ditolak.', draft: 'Tersimpan sebagai draf.' };
export const SOURCE = { ai: 'Draf AI', manual: 'Manual', catalog: 'Katalog' };
/** Status as one label: AI drafts and hand-written drafts get their own colour. Dot + label, never colour alone. */
export type Tone = 'approved' | 'draft_ai' | 'draft_manual' | 'rejected';
export const TONE: Record<Tone, { label: string; dot: string; pill: string }> = {
  approved: { label: 'Disetujui', dot: 'bg-emerald-500', pill: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  draft_ai: { label: 'Draf AI', dot: 'bg-violet-500', pill: 'border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300' },
  draft_manual: { label: 'Draf manual', dot: 'bg-amber-500', pill: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300' },
  rejected: { label: 'Ditolak', dot: 'bg-zinc-400', pill: 'border-zinc-400/40 bg-zinc-400/10 text-zinc-600 dark:text-zinc-400' },
};
export function toneOf(r: Pick<SemanticDefinition, 'status' | 'source'>): Tone {
  if (r.status !== 'draft') return r.status;
  return r.source === 'ai' ? 'draft_ai' : 'draft_manual';
}
/** Kind is a square-cornered outline tag so it never reads as a status pill. */
const KIND_STYLE = { metric: 'border-sky-500/50 text-sky-700 dark:text-sky-300', verified_query: 'border-rose-500/50 text-rose-700 dark:text-rose-300' };
export function StatusTag({ row }: { row: Pick<SemanticDefinition, 'status' | 'source'> }) {
  const t = TONE[toneOf(row)];
  return <span className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${t.pill}`}>
    <span aria-hidden="true" className={`size-1.5 rounded-full ${t.dot}`} />{t.label}</span>;
}
export function KindTag({ kind }: { kind: SemanticDefinition['kind'] }) {
  return <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs ${KIND_STYLE[kind]}`}>
    <span aria-hidden="true" className="font-mono">{kind === 'metric' ? 'Σ' : '{ }'}</span>{KIND[kind]}</span>;
}
const METRIC_TYPES = ['sum', 'count', 'count_distinct', 'average', 'min', 'max', 'ratio', 'other'];
const EMPTY: SemanticForm = {
  kind: 'metric', name: '', status: 'draft', description: '', synonyms: [], table: '', expression: '', metric_type: 'sum',
  unit: '', time_column: '', dimensions: [], filter: '', question: '', sql: '',
};
const list = (text: string) => text.split(',').map(item => item.trim()).filter(Boolean);
export function toForm(row: SemanticDefinition): SemanticForm {
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
export function Editor({ row, canManage, onDone }: { row: SemanticDefinition | null; canManage: boolean; onDone: (message: string) => void }) {
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
  return <form className="flex flex-col gap-3" onSubmit={e => e.preventDefault()}>
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
/** "kai_demo.penjualan" -> "kai_demo". */
export const databaseOf = (table: string) => table.split('.', 1)[0];
/** Approve or reject several definitions, one request each (the MCP re-validates every one). */
export async function setStatus(rows: SemanticDefinition[], status: SemanticStatus, onStep: (done: number) => void) {
  const failed: string[] = [];
  for (let i = 0; i < rows.length; i++) {
    try {
      await updateSemanticFn({ data: { id: rows[i].definition_id, form: { ...toForm(rows[i]), status } } });
    } catch (cause) {
      failed.push(`${rows[i].name}: ${errorText(cause, 'gagal')}`);
    }
    onStep(i + 1);
  }
  return failed;
}
