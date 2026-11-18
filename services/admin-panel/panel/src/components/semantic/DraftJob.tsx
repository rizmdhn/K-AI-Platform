import { useEffect, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { semanticDatabasesFn, semanticDraftsFn, startSemanticDraftsFn, stopSemanticDraftsFn } from '@/server/kaiAccounts';
import type { DraftModelStatus, SemanticJob } from '@/server/kaiAccounts';
import { Notice, Select, errorText } from '../users/AccountFields';
const MODEL: Record<DraftModelStatus, { label: string; state: 'success' | 'neutral' | 'danger' }> = {
  ready: { label: 'Model lokal siap', state: 'success' }, no_model: { label: 'Belum ada model aktif', state: 'neutral' },
  loading: { label: 'Server model memperbarui daftar', state: 'neutral' }, failed: { label: 'Model aktif gagal dimuat', state: 'danger' },
  unavailable: { label: 'Server model tidak berjalan', state: 'danger' }, not_configured: { label: 'Model lokal belum diatur', state: 'danger' },
};
/** Rough CPU estimate before a job starts (5 tables per model call, a few minutes each); the job reports real timing. */
const TABLES_PER_CALL = 5, SECONDS_PER_CALL = 240;
const whole = new Intl.NumberFormat('id-ID');
function duration(seconds: number) {
  if (seconds < 90) return 'kurang dari 2 menit';
  const minutes = Math.round(seconds / 60);
  return minutes < 90 ? `±${minutes} menit` : `±${(minutes / 60).toLocaleString('id-ID', { maximumFractionDigits: 1 })} jam`;
}
function Count({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return <div className="flex flex-col rounded-md border border-(--cui-color-stroke-default) px-3 py-2">
    <span className="text-xs text-(--cui-color-text-muted)">{label}</span>
    <span className="text-lg font-medium tabular-nums">{whole.format(value)}</span>
    {hint && <span className="text-xs text-(--cui-color-text-muted)">{hint}</span>}
  </div>;
}
function Outcome({ job }: { job: SemanticJob }) {
  if (job.status === 'failed') return <Notice tone="error">{job.message ?? 'Job gagal.'} Draf yang sudah tersimpan tetap ada.</Notice>;
  if (job.status === 'cancelled') return <Notice tone="info">Dihentikan setelah {whole.format(job.tables_done)} tabel. {whole.format(job.drafts_saved)} draf tersimpan dan bisa ditinjau di tab Tinjau draf.</Notice>;
  if (job.status === 'done') return <Notice tone="success">Selesai: {whole.format(job.drafts_saved)} draf tersimpan dari {whole.format(job.tables_done)} tabel di {job.database}. Tinjau di tab Tinjau draf; draf baru dipakai agent setelah disetujui.</Notice>;
  return null;
}
function Progress({ job, canManage, onStop, stopping }: { job: SemanticJob; canManage: boolean; onStop: () => void; stopping: boolean }) {
  const todo = job.tables_total - job.tables_existing;
  return <div className="flex flex-col gap-2" aria-live="polite">
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <span><b>{job.database}</b>: {job.phase ?? 'Berjalan'}{job.current.length > 0 && <span className="text-(--cui-color-text-muted)"> ({job.current.join(', ')})</span>}</span>
      {canManage && <Button type="secondary" label={stopping ? 'Menghentikan…' : 'Hentikan'} disabled={stopping} onClick={onStop} />}
    </div>
    <progress className="h-2 w-full accent-(--cui-color-accent-default)" max={Math.max(todo, 1)} value={job.tables_done} aria-label="Tabel yang sudah diproses" />
    <span className="text-xs text-(--cui-color-text-muted)">
      {whole.format(job.tables_done)} dari {whole.format(todo)} tabel diproses
      {job.eta_seconds != null ? `, sisa ${duration(job.eta_seconds)}` : ', menghitung perkiraan waktu setelah kelompok pertama'}
      {job.seconds_per_group ? ` (${whole.format(job.seconds_per_group)} detik per ${TABLES_PER_CALL} tabel)` : ''}
    </span>
  </div>;
}
function Issues({ job }: { job: SemanticJob }) {
  if (!job.issues.length) return null;
  const more = job.drafts_skipped - job.issues.length;
  return <details className="text-sm">
    <summary className="cursor-pointer text-(--cui-color-text-muted)">Draf yang dilewati ({whole.format(job.drafts_skipped)}) beserta alasannya</summary>
    <ul className="mt-2 flex flex-col gap-1">{job.issues.map((issue, i) => <li key={`${issue.item}-${i}`} className="wrap-break-word"><b className="font-medium">{issue.item}</b>: {issue.reason}</li>)}</ul>
    {more > 0 && <p className="mt-1 text-xs text-(--cui-color-text-muted)">dan {whole.format(more)} lainnya.</p>}
  </details>;
}
/** Start and follow the local-model draft job; drafts appear in the review list while it runs. */
export function DraftJob({ canManage, onReview }: { canManage: boolean; onReview: () => void }) {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['kaiSemanticDrafts'], queryFn: () => semanticDraftsFn(), retry: false,
    refetchInterval: query => (query.state.data?.job?.status === 'running' ? 5000 : false) });
  const databases = useQuery({ queryKey: ['kaiSemanticDatabases'], queryFn: () => semanticDatabasesFn(), retry: false, enabled: canManage });
  const [database, setDatabase] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const job = state.data?.job ?? null, model = state.data?.model;
  const running = job?.status === 'running';
  const saved = job?.drafts_saved ?? 0;
  useEffect(() => { void client.invalidateQueries({ queryKey: ['kaiSemantic'] }); }, [saved, job?.status, client]);
  const picked = databases.data?.databases.find(d => d.name === database);
  const remaining = picked ? picked.tables - picked.covered : 0;
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try {
      client.setQueryData(['kaiSemanticDrafts'], await action());
    } catch (cause) {
      setError(errorText(cause, 'Permintaan gagal. Coba lagi.'));
    } finally {
      setBusy(false);
    }
  }
  return <div className="flex flex-col gap-3 rounded-md border border-(--cui-color-stroke-default) p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">Buat draf dengan AI</h3>
      {model && <Badge text={`${MODEL[model.status].label}${model.status === 'ready' ? ` (${model.name})` : ''}`} state={MODEL[model.status].state} />}
    </div>
    <p className="text-sm text-(--cui-color-text-muted)">Model AI lokal di server membaca nama, tipe, dan komentar kolom pada database yang Anda pilih, memakai akses ClickHouse akun Anda sendiri. Isi data tidak dibaca dan tidak ada yang dikirim ke luar server. Setiap draf diperiksa ke ClickHouse sebelum disimpan, dan tabel yang sudah punya definisi dilewati. Proses berjalan di latar belakang; halaman ini boleh ditutup.</p>
    {state.isError && <Notice tone="error">Status pembuatan draf tidak bisa dibaca.</Notice>}
    {model && model.status !== 'ready' && !running && <Notice tone="info">{model.status === 'no_model' ? 'Unduh lalu aktifkan model di bagian Model AI lokal sebelum membuat draf.' : MODEL[model.status].label + '. Lihat bagian Model AI lokal.'}</Notice>}
    {job && running && <Progress job={job} canManage={canManage} stopping={busy} onStop={() => void act(() => stopSemanticDraftsFn())} />}
    {job && !running && <Outcome job={job} />}
    {job && job.drafts_saved > 0 && <Button type="secondary" label={`Tinjau ${whole.format(job.drafts_saved)} draf`} onClick={onReview} />}
    {job && <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <Count label="Draf tersimpan" value={job.drafts_saved} />
      <Count label="Draf dilewati" value={job.drafts_skipped} hint="Tidak lolos pemeriksaan" />
      <Count label="Tabel diproses" value={job.tables_done} hint={`dari ${whole.format(job.tables_total - job.tables_existing)}`} />
      <Count label="Sudah punya definisi" value={job.tables_existing} hint="Dilewati" />
    </div>}
    {job && job.tables_wide > 0 && <p className="text-xs text-(--cui-color-text-muted)">{whole.format(job.tables_wide)} tabel sangat lebar: hanya 60 kolom pertama yang dibaca model.</p>}
    {job && <Issues job={job} />}
    {canManage && !running && <div className="flex flex-col gap-2 border-t border-(--cui-color-stroke-default) pt-3">
      {databases.isError && <Notice tone="warning">{errorText(databases.error, 'Daftar database tidak bisa dibaca.')}</Notice>}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-56 flex-col gap-1 text-sm">Database
          <Select value={database} disabled={busy || !databases.data} onChange={e => setDatabase(e.target.value)}>
            <option value="">{databases.isPending ? 'Memuat…' : 'Pilih database'}</option>
            {databases.data?.databases.map(d => <option key={d.name} value={d.name}>{d.name} ({whole.format(d.tables)} tabel{d.covered ? `, ${whole.format(d.covered)} sudah punya definisi` : ''})</option>)}
          </Select>
        </label>
        <Button type="primary" label={busy ? 'Memulai…' : 'Buat draf'} disabled={busy || !database || model?.status !== 'ready' || remaining === 0}
          onClick={() => void act(() => startSemanticDraftsFn({ data: { database } }))} />
      </div>
      {picked && (remaining === 0
        ? <span className="text-xs text-(--cui-color-text-muted)">Semua tabel di database ini sudah punya definisi.</span>
        : <span className="text-xs text-(--cui-color-text-muted)">{whole.format(remaining)} tabel akan diproses. Perkiraan kasar {duration(Math.ceil(remaining / TABLES_PER_CALL) * SECONDS_PER_CALL)} di CPU server; perkiraan diperbarui setelah kelompok pertama.</span>)}
    </div>}
    {error && <Notice tone="error">{error}</Notice>}
  </div>;
}
