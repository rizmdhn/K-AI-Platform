import { useEffect, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { DraftModelStatus, QueueStatus, SemanticJob } from '@/server/kaiAccounts';
import { semanticDatabasesFn, semanticDraftsFn, startSemanticDraftsFn, stopSemanticDraftsFn } from '@/server/kaiAccounts';
import { Notice, errorText } from '../users/AccountFields';
const MODEL: Record<DraftModelStatus, { label: string; state: 'success' | 'neutral' | 'danger' }> = {
  ready: { label: 'Model lokal siap', state: 'success' }, no_model: { label: 'Belum ada model aktif', state: 'neutral' },
  loading: { label: 'Server model memperbarui daftar', state: 'neutral' }, failed: { label: 'Model aktif gagal dimuat', state: 'danger' },
  unavailable: { label: 'Server model tidak berjalan', state: 'danger' }, not_configured: { label: 'Model lokal belum diatur', state: 'danger' },
};
const QUEUE: Record<QueueStatus, { label: string; pill: string }> = {
  waiting: { label: 'Menunggu', pill: 'border-zinc-400/40 text-zinc-600 dark:text-zinc-400' },
  running: { label: 'Berjalan', pill: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300' },
  done: { label: 'Selesai', pill: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300' },
  failed: { label: 'Gagal', pill: 'border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300' },
  cancelled: { label: 'Dihentikan', pill: 'border-zinc-400/40 bg-zinc-400/10 text-zinc-600 dark:text-zinc-400' },
};
/** Before any job has run: measured on kai-dev with the 7B model on 4 CPU threads. Jobs then report real speed. */
const DEFAULT_SECONDS_PER_TABLE = 105;
const whole = new Intl.NumberFormat('id-ID');
/** 95 -> "1 mnt 35 dtk", 5580 -> "1 jam 33 mnt". */
function duration(seconds: number) {
  const s = Math.max(0, Math.round(seconds)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  if (h) return `${h} jam ${m} mnt`;
  if (m) return `${m} mnt ${s % 60} dtk`;
  return `${s} dtk`;
}
const clock = (at: number) => new Date(at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
/** Re-renders every second while `on`, so the ETA and the running group's timer count down between polls. */
function useNow(on: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}
function Count({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return <div className="flex flex-col rounded-md border border-(--cui-color-stroke-default) px-3 py-2">
    <span className="text-xs text-(--cui-color-text-muted)">{label}</span>
    <span className="text-lg font-medium tabular-nums">{whole.format(value)}</span>
    {hint && <span className="text-xs text-(--cui-color-text-muted)">{hint}</span>}
  </div>;
}
function Outcome({ job }: { job: SemanticJob }) {
  const where = job.queue.map(e => e.database).join(', ');
  if (job.status === 'failed') return <Notice tone="error">{job.message ?? 'Job gagal.'} Draf yang sudah tersimpan tetap ada.</Notice>;
  if (job.status === 'cancelled') return <Notice tone="info">Dihentikan setelah {whole.format(job.tables_done)} tabel. {whole.format(job.drafts_saved)} draf tersimpan dan bisa ditinjau. Jalankan lagi kapan saja: tabel yang sudah punya definisi dilewati.</Notice>;
  if (job.status === 'done') return <Notice tone="success">Selesai: {whole.format(job.drafts_saved)} draf dari {whole.format(job.tables_done)} tabel ({where}) dalam {duration((Date.parse(job.finished_at ?? job.started_at) - Date.parse(job.started_at)) / 1000)}. Draf baru dipakai agent setelah disetujui.</Notice>;
  return null;
}
function Progress({ job, fetchedAt, canManage, onStop, stopping }: { job: SemanticJob; fetchedAt: number; canManage: boolean; onStop: () => void; stopping: boolean }) {
  const now = useNow(true);
  const since = (now - fetchedAt) / 1000;
  const eta = job.eta_seconds == null ? null : Math.max(0, job.eta_seconds - since);
  const groupFor = job.group_started_at ? (now - Date.parse(job.group_started_at)) / 1000 : null;
  const entry = job.queue.find(e => e.status === 'running');
  const share = job.tables_total ? Math.round((job.tables_done / job.tables_total) * 100) : 0;
  return <div className="flex flex-col gap-2" aria-live="polite">
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <span className="font-medium">{entry ? `${entry.database} · ${whole.format(entry.tables_done)} / ${whole.format(entry.tables_todo)} tabel` : job.phase ?? 'Menyiapkan'}</span>
      {canManage && <Button type="secondary" label={stopping ? 'Menghentikan…' : 'Hentikan'} disabled={stopping} onClick={onStop} />}
    </div>
    <div className="flex items-center gap-3">
      <progress className="h-2 flex-1 accent-(--cui-color-accent-default)" max={Math.max(job.tables_total, 1)} value={job.tables_done} aria-label="Tabel yang sudah diproses di seluruh antrean" />
      <span className="tabular-nums text-sm">{share}%</span>
    </div>
    <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      <div><dt className="inline text-(--cui-color-text-muted)">Sedang: </dt><dd className="inline">{job.current.length ? job.current.join(', ') : job.phase}
        {groupFor != null && job.current.length > 0 && <span className="text-(--cui-color-text-muted)"> (berjalan {duration(groupFor)})</span>}</dd></div>
      <div><dt className="inline text-(--cui-color-text-muted)">Kecepatan: </dt><dd className="inline">{job.seconds_per_table ? `1 tabel / ${duration(job.seconds_per_table)}` : 'diukur setelah kelompok pertama'}</dd></div>
      <div><dt className="inline text-(--cui-color-text-muted)">Sisa: </dt><dd className="inline">{eta == null ? 'menghitung…' : `±${duration(eta)} · selesai ±${clock(now + eta * 1000)}`}</dd></div>
      <div><dt className="inline text-(--cui-color-text-muted)">Total antrean: </dt><dd className="inline">{whole.format(job.tables_done)} / {whole.format(job.tables_total)} tabel</dd></div>
    </dl>
  </div>;
}
function Queue({ job }: { job: SemanticJob }) {
  if (job.queue.length < 2 && job.status !== 'running') return null;
  return <table className="w-full text-left text-sm">
    <caption className="pb-1 text-left text-xs text-(--cui-color-text-muted)">Antrean database</caption>
    <thead className="text-xs text-(--cui-color-text-muted)"><tr><th className="py-1 pr-3 font-medium">Database</th><th className="py-1 pr-3 font-medium">Status</th>
      <th className="py-1 pr-3 text-right font-medium">Tabel</th><th className="py-1 pr-3 text-right font-medium">Draf</th><th className="py-1 text-right font-medium">Dilewati</th></tr></thead>
    <tbody>{job.queue.map(e => <tr key={e.database} className="border-t border-(--cui-color-stroke-default)">
      <td className="py-1.5 pr-3 font-mono">{e.database}</td>
      <td className="py-1.5 pr-3"><span className={`inline-flex rounded-full border px-2 py-0.5 text-xs font-medium ${QUEUE[e.status].pill}`}>{QUEUE[e.status].label}</span>
        {e.message && <span className="ml-2 text-xs text-(--cui-color-text-muted)">{e.message}</span>}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{whole.format(e.tables_done)} / {whole.format(e.tables_todo)}</td>
      <td className="py-1.5 pr-3 text-right tabular-nums">{whole.format(e.drafts_saved)}</td>
      <td className="py-1.5 text-right tabular-nums">{whole.format(e.drafts_skipped)}</td>
    </tr>)}</tbody>
  </table>;
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
/** Start and follow the local-model draft job (one or more databases, in order); drafts appear in review as it runs. */
export function DraftJob({ canManage, onReview }: { canManage: boolean; onReview: () => void }) {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['kaiSemanticDrafts'], queryFn: () => semanticDraftsFn(), retry: false,
    refetchInterval: query => (query.state.data?.job?.status === 'running' ? 5000 : false) });
  const databases = useQuery({ queryKey: ['kaiSemanticDatabases'], queryFn: () => semanticDatabasesFn(), retry: false, enabled: canManage });
  const [picked, setPicked] = useState<string[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const job = state.data?.job ?? null, model = state.data?.model;
  const running = job?.status === 'running';
  const saved = job?.drafts_saved ?? 0;
  useEffect(() => { void client.invalidateQueries({ queryKey: ['kaiSemantic'] }); }, [saved, job?.status, client]);
  const rows = databases.data?.databases ?? [];
  const todo = rows.filter(d => picked.includes(d.name)).reduce((n, d) => n + Math.max(0, d.tables - d.covered), 0);
  const speed = job?.seconds_per_table ?? DEFAULT_SECONDS_PER_TABLE;
  const startLabel = picked.length > 1 ? `Buat draf untuk ${picked.length} database` : 'Buat draf';
  const toggle = (name: string) => setPicked(p => (p.includes(name) ? p.filter(x => x !== name) : [...p, name]));
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try {
      client.setQueryData(['kaiSemanticDrafts'], await action());
      setPicked([]);
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
    <p className="text-sm text-(--cui-color-text-muted)">Model AI lokal di server membaca nama, tipe, dan komentar kolom, memakai akses ClickHouse akun Anda. Isi data tidak dibaca dan tidak ada yang dikirim ke luar server. Setiap draf diperiksa ke ClickHouse sebelum disimpan; tabel yang sudah punya definisi dilewati, jadi job boleh dihentikan dan dilanjutkan. Halaman ini boleh ditutup.</p>
    {state.isError && <Notice tone="error">Status pembuatan draf tidak bisa dibaca.</Notice>}
    {model && model.status !== 'ready' && !running && <Notice tone="info">{model.status === 'no_model' ? 'Unduh lalu aktifkan model di bagian Model AI lokal sebelum membuat draf.' : `${MODEL[model.status].label}. Lihat bagian Model AI lokal.`}</Notice>}
    {job && running && <Progress job={job} fetchedAt={state.dataUpdatedAt} canManage={canManage} stopping={busy} onStop={() => void act(() => stopSemanticDraftsFn())} />}
    {job && !running && <Outcome job={job} />}
    {job && job.drafts_saved > 0 && <Button type="secondary" label={`Tinjau ${whole.format(job.drafts_saved)} draf`} onClick={onReview} />}
    {job && <div className="grid grid-cols-3 gap-2">
      <Count label="Draf tersimpan" value={job.drafts_saved} />
      <Count label="Draf dilewati" value={job.drafts_skipped} hint="Tidak lolos pemeriksaan" />
      <Count label="Tabel diproses" value={job.tables_done} hint={`dari ${whole.format(job.tables_total)}`} />
    </div>}
    {job && <Queue job={job} />}
    {job && job.queue.some(e => e.tables_wide > 0) && <p className="text-xs text-(--cui-color-text-muted)">{whole.format(job.queue.reduce((n, e) => n + e.tables_wide, 0))} tabel sangat lebar: hanya 60 kolom pertama yang dibaca model.</p>}
    {job && <Issues job={job} />}
    {canManage && !running && <div className="flex flex-col gap-2 border-t border-(--cui-color-stroke-default) pt-3">
      <span className="text-sm font-medium">Pilih database (diproses berurutan)</span>
      {databases.isError && <Notice tone="warning">{errorText(databases.error, 'Daftar database tidak bisa dibaca.')}</Notice>}
      {databases.isPending && <p className="text-sm text-(--cui-color-text-muted)">Memuat…</p>}
      {rows.length > 0 && <div className="max-h-72 overflow-y-auto rounded-md border border-(--cui-color-stroke-default)"><table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-(--cui-color-background-muted) text-xs text-(--cui-color-text-muted)"><tr>
          <th className="w-8 px-3 py-2" /><th className="px-3 py-2 font-medium">Database</th><th className="px-3 py-2 text-right font-medium">Tabel</th>
          <th className="px-3 py-2 text-right font-medium">Sudah ada definisi</th><th className="px-3 py-2 text-right font-medium">Akan diproses</th></tr></thead>
        <tbody>{rows.map(d => { const left = Math.max(0, d.tables - d.covered); return <tr key={d.name} className="border-t border-(--cui-color-stroke-default)">
          <td className="px-3 py-1.5"><input type="checkbox" aria-label={`Pilih ${d.name}`} checked={picked.includes(d.name)} disabled={!left || busy} onChange={() => toggle(d.name)} /></td>
          <td className="px-3 py-1.5 font-mono">{d.name}</td>
          <td className="px-3 py-1.5 text-right tabular-nums">{whole.format(d.tables)}</td>
          <td className="px-3 py-1.5 text-right tabular-nums">{whole.format(d.covered)}</td>
          <td className="px-3 py-1.5 text-right tabular-nums">{left ? whole.format(left) : <span className="text-(--cui-color-text-muted)">Lengkap</span>}</td>
        </tr>; })}</tbody>
      </table></div>}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="primary" label={busy ? 'Memulai…' : startLabel}
          disabled={busy || !picked.length || model?.status !== 'ready' || !todo} onClick={() => void act(() => startSemanticDraftsFn({ data: { databases: picked } }))} />
        {todo > 0 && <span className="text-xs text-(--cui-color-text-muted)">{whole.format(todo)} tabel, perkiraan ±{duration(todo * speed)} ({job?.seconds_per_table ? 'dari kecepatan job terakhir' : 'perkiraan awal'}; diperbarui setelah kelompok pertama).</span>}
      </div>
    </div>}
    {error && <Notice tone="error">{error}</Notice>}
  </div>;
}
