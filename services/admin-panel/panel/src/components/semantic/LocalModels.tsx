import { useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { localModelActionFn, localModelsFn } from '@/server/kaiAccounts';
import type { LocalModel, LocalModelDownload, LocalModelState } from '@/server/kaiAccounts';
import { Notice, errorText } from '../users/AccountFields';
type Action = 'download' | 'pause' | 'activate' | 'delete';
const SPARE = 1e9;
const gb = (bytes: number) => `${(bytes / 1e9).toLocaleString('id-ID', { maximumFractionDigits: 1 })} GB`;
const percent = (part: number, whole: number) => `${Math.floor((part / Math.max(whole, 1)) * 100)}%`;
function eta(seconds: number | null) {
  if (seconds == null) return '';
  if (seconds < 90) return ', sisa kurang dari 2 menit';
  return `, sisa ±${Math.round(seconds / 60)} menit`;
}
/** One-line status of a model; loading progress comes from llama.cpp's own report. */
function status(model: LocalModel, download: LocalModelDownload | null): { text: string; state: 'success' | 'neutral' | 'danger' } {
  if (download?.model === model.id && download.status === 'running') return { text: `Mengunduh ${percent(download.bytes, download.total)}`, state: 'neutral' };
  if (!model.installed) return model.partial_bytes ? { text: `Terunduh sebagian ${percent(model.partial_bytes, model.size)}`, state: 'neutral' } : { text: 'Belum diunduh', state: 'neutral' };
  if (!model.active) return { text: 'Terpasang', state: 'neutral' };
  if (model.router_status === 'loaded') return { text: 'Aktif, siap', state: 'success' };
  if (model.router_status === 'loading') return { text: `Aktif, dimuat ${model.load_progress != null ? percent(model.load_progress, 1) : ''}`, state: 'neutral' };
  if (model.router_status === 'failed') return { text: 'Aktif, gagal dimuat', state: 'danger' };
  if (model.router_status == null) return { text: 'Aktif, server memperbarui daftar', state: 'neutral' };
  return { text: 'Aktif, dimuat saat dipakai', state: 'success' };
}
function DownloadBox({ download, canManage, busy, onPause }: { download: LocalModelDownload; canManage: boolean; busy: boolean; onPause: () => void }) {
  if (download.status === 'done') return <Notice tone="success">{download.label}: {download.message}</Notice>;
  if (download.status === 'failed') return <Notice tone="error">{download.label}: {download.message}</Notice>;
  if (download.status === 'paused') return <Notice tone="info">{download.label}: {download.message}</Notice>;
  return <div className="flex flex-col gap-1" aria-live="polite">
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
      <span><b>{download.label}</b>: {download.phase ?? 'Mengunduh'}</span>
      {canManage && <Button type="secondary" label="Jeda" disabled={busy} onClick={onPause} />}
    </div>
    <progress className="h-2 w-full" max={Math.max(download.total, 1)} value={download.bytes} aria-label={`Unduhan ${download.label}`} />
    <span className="text-xs text-(--cui-color-text-muted)">
      {gb(download.bytes)} dari {gb(download.total)} ({percent(download.bytes, download.total)})
      {download.speed ? `, ${(download.speed / 1e6).toLocaleString('id-ID', { maximumFractionDigits: 1 })} MB/detik` : ''}{eta(download.eta_seconds)}
    </span>
  </div>;
}
function Row({ model, data, canManage, busy, act }: { model: LocalModel; data: LocalModelState; canManage: boolean; busy: boolean; act: (action: Action, model?: string) => void }) {
  const badge = status(model, data.download);
  const downloading = data.download?.status === 'running';
  const room = data.disk ? data.disk.free - SPARE >= model.size - model.partial_bytes : true;
  const remove = () => {
    const size = model.installed ? model.size : model.partial_bytes;
    if (window.confirm(`Hapus ${model.label}? File ${gb(size)} dihapus dari server${model.active ? ' dan model aktif dilepas' : ''}.`)) act('delete', model.id);
  };
  return <li className="flex flex-col gap-2 rounded-md border border-(--cui-color-stroke-default) p-3">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 font-medium">{model.label}<Badge text={badge.text} state={badge.state} />{model.source === 'manual' && <Badge text="Manual" state="neutral" />}</span>
        <span className="text-xs text-(--cui-color-text-muted)">{gb(model.size)}{model.ram_gb ? `, butuh RAM ±${model.ram_gb} GB` : ''}. {model.note}</span>
        {!model.installed && !room && <span className="text-xs text-(--cui-color-text-danger)">Ruang disk tidak cukup: butuh {gb(model.size - model.partial_bytes + SPARE)} kosong.</span>}
      </div>
      {canManage && <div className="flex flex-wrap gap-2">
        {!model.installed && model.source === 'catalog' && <Button type={model.partial_bytes ? 'primary' : 'secondary'} label={model.partial_bytes ? 'Lanjutkan' : 'Unduh'} disabled={busy || downloading || !room} onClick={() => act('download', model.id)} />}
        {model.installed && !model.active && <Button type="primary" label="Aktifkan" disabled={busy} onClick={() => act('activate', model.id)} />}
        {(model.installed || model.partial_bytes > 0) && <Button type="danger" label="Hapus" disabled={busy || (downloading && data.download?.model === model.id)} onClick={remove} />}
      </div>}
    </div>
  </li>;
}
/** Download, activate and delete the local models that draft the semantic layer; every step reports its progress. */
export function LocalModels({ canManage }: { canManage: boolean }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['kaiLocalModels'], queryFn: () => localModelsFn(), retry: false,
    refetchInterval: q => {
      const data = q.state.data;
      const busy = data?.download?.status === 'running' || data?.models.some(m => m.active && m.router_status !== 'loaded');
      return busy ? 2000 : false;
    } });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  async function act(action: Action, model?: string) {
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await localModelActionFn({ data: { action, model } });
      client.setQueryData(['kaiLocalModels'], result);
      if (result.message) setNotice(result.message);
      await client.invalidateQueries({ queryKey: ['kaiSemanticDrafts'] });
    } catch (cause) {
      setError(errorText(cause, 'Tindakan gagal. Coba lagi.'));
    } finally {
      setBusy(false);
    }
  }
  const data = query.data;
  return <div className="flex flex-col gap-3 rounded-md border border-(--cui-color-stroke-default) p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-medium">Model AI lokal</h3>
      {data && <Badge text={data.router === 'ready' ? 'Server model berjalan' : 'Server model tidak berjalan'} state={data.router === 'ready' ? 'success' : 'danger'} />}
    </div>
    <p className="text-sm text-(--cui-color-text-muted)">Model ini berjalan di CPU server dan hanya dipakai untuk menyusun draf semantic layer. Hanya model yang Anda pilih yang diunduh dari Hugging Face, lalu checksum-nya diperiksa sebelum dipakai. Setelah unduhan selesai atau model dihapus, server model memuat ulang daftarnya sendiri dalam beberapa detik; layanan lain tidak terganggu.</p>
    {query.isError && <Notice tone="error">Status model lokal tidak bisa dibaca.</Notice>}
    {data?.router === 'unavailable' && <Notice tone="warning">Jalankan <code>make llm-up</code> di server untuk menyalakan server model. Unduhan dan penghapusan tetap bisa dilakukan.</Notice>}
    {data?.disk && <span className="text-xs text-(--cui-color-text-muted)">Ruang disk server: {gb(data.disk.free)} kosong dari {gb(data.disk.total)}.</span>}
    {data?.download && <DownloadBox download={data.download} canManage={canManage} busy={busy} onPause={() => void act('pause')} />}
    {notice && <Notice tone="success">{notice}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {data && <ul className="flex flex-col gap-2">{data.models.map(model => <Row key={model.id} model={model} data={data} canManage={canManage} busy={busy} act={(a, m) => void act(a, m)} />)}</ul>}
  </div>;
}
