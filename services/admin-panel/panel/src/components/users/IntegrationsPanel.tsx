import { useEffect, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { openMetadataDatabasesFn, openMetadataFn, saveOpenMetadataFn, testOpenMetadataFn } from '@/server/kaiAccounts';
import type { DatabaseSummary, OpenMetadataSettings } from '@/server/kaiAccounts';
import { inputClass } from './AccountFields';
const empty: OpenMetadataSettings = { url: '', service: '', ca_cert: '', catalog_enabled: false, databases: [], scan_hours: 24, max_tables: 50 };
const sources = { ui: 'Diatur di Admin Panel', env: 'Dari file .env server', none: 'Belum diatur' };
// OpenMetadata connection and the metadata draft worker, editable here instead of .env.
export function IntegrationsPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_CONFIGS);
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['kaiOpenMetadata'], queryFn: () => openMetadataFn(), retry: false });
  const [form, setForm] = useState(empty), [databases, setDatabases] = useState<string[]>([]), [token, setToken] = useState('');
  const listed = useQuery({ queryKey: ['kaiOpenMetadataDatabases'], queryFn: () => openMetadataDatabasesFn(), retry: false, enabled: query.data?.settings.url !== undefined && query.data.settings.url !== '' });
  const [tested, setTested] = useState<DatabaseSummary[] | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [lines, setLines] = useState<string[]>([]), [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (query.data) { setForm(query.data.settings); setDatabases(query.data.settings.databases); } }, [query.data]);
  const set = <K extends keyof OpenMetadataSettings>(key: K, value: OpenMetadataSettings[K]) => setForm((f) => ({ ...f, [key]: value }));
  const payload = () => ({ ...form, token, databases });
  async function run(kind: 'save' | 'test') {
    if (busy) return;
    setBusy(true); setError(''); setNotice(''); setLines([]);
    try {
      if (kind === 'test') {
        const result = await testOpenMetadataFn({ data: payload() });
        setLines(result.lines); setTested(result.databases); setNotice(result.ok ? 'Koneksi berhasil.' : 'Koneksi gagal. Lihat detail di bawah.');
      } else {
        client.setQueryData(['kaiOpenMetadata'], await saveOpenMetadataFn({ data: payload() }));
        setTested(null); void client.invalidateQueries({ queryKey: ['kaiOpenMetadataDatabases'] });
        setToken(''); setNotice('Tersimpan. Pencarian tabel langsung memakai pengaturan baru; worker draf membacanya dalam 1 menit.');
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Pengaturan integrasi gagal. Coba lagi.');
    } finally {
      setBusy(false);
    }
  }
  const data = query.data;
  const label = query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : sources[data!.source];
  const disabled = busy || !canManage || !data?.storage_available;
  return <section aria-label="Integrasi OpenMetadata" className="rounded-lg border border-(--cui-color-stroke-default) p-4">
    <div className="flex items-center justify-between gap-2">
      <h2 className="font-medium">Integrasi OpenMetadata <Badge text={label} state={data?.source === 'none' ? 'neutral' : query.isError ? 'danger' : 'success'} /></h2>
      <Button type="secondary" label={open ? 'Tutup' : 'Atur'} onClick={() => setOpen(!open)} />
    </div>
    <p className="mt-2 text-sm text-(--cui-color-text-muted)">Katalog metadata ClickHouse milik klien. Pencarian tabel memakai deskripsi dari OpenMetadata, hanya untuk tabel dan kolom yang boleh dilihat pengguna. DBA menyetujui metadata di OpenMetadata.</p>
    {query.isError && <p role="alert" className="mt-2 text-sm">Pengaturan integrasi tidak bisa dibaca.</p>}
    {open && data && <form className="mt-3 flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void run('save'); }}>
      {!data.storage_available && <p role="alert" className="text-sm">Penyimpanan pengaturan belum disiapkan di server (jalankan make warehouse-local-setup). Sampai itu, nilai dibaca dari .env.</p>}
      {!canManage && <p className="text-sm">Anda hanya bisa melihat. Perubahan butuh izin kelola konfigurasi.</p>}
      <label className="flex flex-col gap-1 text-sm">URL OpenMetadata<input className={inputClass} value={form.url} disabled={disabled} placeholder="https://openmetadata.perusahaan.co.id" maxLength={2048} onChange={(e) => set('url', e.target.value)} /></label>
      <label className="flex flex-col gap-1 text-sm">Nama service ClickHouse di OpenMetadata<input className={inputClass} value={form.service} disabled={disabled} maxLength={256} onChange={(e) => set('service', e.target.value)} /></label>
      <label className="flex flex-col gap-1 text-sm">Token bot OpenMetadata<input className={inputClass} type="password" autoComplete="new-password" value={token} disabled={disabled} maxLength={8192} placeholder={data.token_set ? 'Tersimpan. Kosongkan untuk mempertahankan' : 'Izin: baca tabel dan buat suggestion'} onChange={(e) => setToken(e.target.value)} /></label>
      <label className="flex flex-col gap-1 text-sm">Sertifikat CA (PEM, opsional, untuk sertifikat internal)<textarea className={inputClass} rows={3} value={form.ca_cert} disabled={disabled} maxLength={65536} placeholder="-----BEGIN CERTIFICATE-----" onChange={(e) => set('ca_cert', e.target.value)} /></label>
      <fieldset className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-3">
        <legend className="text-sm font-medium">Draf metadata otomatis</legend>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.catalog_enabled} disabled={disabled} onChange={(e) => set('catalog_enabled', e.target.checked)} />Buat draf deskripsi untuk tabel/kolom yang masih kosong, kirim sebagai suggestion untuk DBA</label>
        <p className="text-xs text-(--cui-color-text-muted)">Yang dikirim ke model: nama tabel/kolom, tipe, dan deskripsi yang ada. Isi baris tidak pernah dikirim. Deskripsi yang sudah ada tidak pernah ditimpa. Aktifkan dengan persetujuan pemilik data.</p>
        <DatabasePicker rows={tested ?? listed.data?.databases ?? []} selected={databases} disabled={disabled}
          loading={listed.isFetching} failed={listed.isError} onChange={setDatabases} />
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">Interval scan (jam)<input className={inputClass} type="number" min={1} max={168} value={form.scan_hours} disabled={disabled} onChange={(e) => set('scan_hours', Number(e.target.value))} /></label>
          <label className="flex flex-col gap-1 text-sm">Batas tabel per scan<input className={inputClass} type="number" min={1} max={1000} value={form.max_tables} disabled={disabled} onChange={(e) => set('max_tables', Number(e.target.value))} /></label>
        </div>
      </fieldset>
      {canManage && <div className="flex flex-wrap gap-2">
        <Button type="secondary" htmlType="button" label="Uji koneksi" disabled={disabled || !form.url} onClick={() => void run('test')} />
        <Button type="primary" htmlType="submit" label="Simpan" disabled={disabled} />
      </div>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {error && <p role="alert" className="text-sm">{error}</p>}
      {lines.length > 0 && <ul className="list-disc pl-5 text-xs">{lines.map((line, i) => <li key={i}>{line}</li>)}</ul>}
      {data.updated_at && <p className="text-xs text-(--cui-color-text-muted)">Terakhir diubah {new Date(data.updated_at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' })}.</p>}
    </form>}
  </section>;
}

// Databases from OpenMetadata as a checklist; nothing ticked = all. Saved names no longer in OpenMetadata stay
// visible (marked) so they can be unticked.
function DatabasePicker({ rows, selected, disabled, loading, failed, onChange }: {
  rows: DatabaseSummary[]; selected: string[]; disabled: boolean; loading: boolean; failed: boolean; onChange: (value: string[]) => void;
}) {
  const known = new Set(rows.map((r) => r.name));
  const all = [...rows, ...selected.filter((name) => !known.has(name)).map((name) => ({ name, tables: -1, empty_tables: 0, empty_columns: 0 }))];
  const toggle = (name: string, on: boolean) => onChange(on ? [...selected, name].sort() : selected.filter((d) => d !== name));
  const cell = 'border-b border-(--cui-color-stroke-default) px-3 py-2';
  return <div className="flex flex-col gap-1 text-sm">
    <span>Database yang dibuatkan draf <span className="text-(--cui-color-text-muted)">({selected.length ? `${selected.length} dipilih` : 'tidak ada yang dipilih = semua database'})</span></span>
    {all.length === 0
      ? <p className="text-xs text-(--cui-color-text-muted)">{loading ? 'Memuat daftar database dari OpenMetadata…' : failed ? 'Daftar database tidak bisa dibaca dari OpenMetadata.' : 'Simpan koneksi atau klik Uji koneksi untuk menampilkan daftar database.'}</p>
      : <div className="overflow-x-auto rounded-lg border border-(--cui-color-stroke-default)"><table className="w-full text-left text-sm">
          <thead><tr className="text-xs text-(--cui-color-text-muted)">
            <th className={cell}><input type="checkbox" aria-label="Pilih semua database" disabled={disabled} checked={rows.length > 0 && rows.every((r) => selected.includes(r.name))} onChange={(e) => onChange(e.target.checked ? rows.map((r) => r.name) : [])} /></th>
            <th className={cell}>Database</th><th className={`${cell} text-right`}>Tabel</th><th className={`${cell} text-right`}>Tabel tanpa deskripsi</th><th className={`${cell} text-right`}>Kolom tanpa deskripsi</th>
          </tr></thead>
          <tbody>{all.map((r) => <tr key={r.name}>
            <td className={cell}><input type="checkbox" aria-label={`Pilih ${r.name}`} disabled={disabled} checked={selected.includes(r.name)} onChange={(e) => toggle(r.name, e.target.checked)} /></td>
            <td className={cell}>{r.name}{r.tables < 0 && <span className="ml-2 text-xs text-(--cui-color-text-muted)">(tidak ada di OpenMetadata)</span>}</td>
            <td className={`${cell} text-right`}>{r.tables < 0 ? '–' : r.tables}</td><td className={`${cell} text-right`}>{r.tables < 0 ? '–' : r.empty_tables}</td><td className={`${cell} text-right`}>{r.tables < 0 ? '–' : r.empty_columns}</td>
          </tr>)}</tbody>
        </table></div>}
  </div>;
}
