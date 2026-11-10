import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { saveSettingsSectionFn, settingsSectionFn } from '@/server/kaiAccounts';
import type { SectionState, SettingsSectionName } from '@/server/kaiAccounts';
import { inputClass } from './AccountFields';
export interface Field { key: string; label: string; type: 'text' | 'secret' | 'number' | 'checkbox'; min?: number; max?: number; placeholder?: string; help?: string }
type Values = SectionState['settings'];
const sources = { ui: 'Diatur di Admin Panel', env: 'Dari .env / bawaan', none: 'Belum diatur' };
// One settings card (batch model, limits): load, edit, save. The secret field is write-only; empty keeps the saved one.
export function SettingsSection({ name, title, description, fields }: { name: SettingsSectionName; title: string; description: ReactNode; fields: Field[] }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_CONFIGS);
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['kaiSettings', name], queryFn: () => settingsSectionFn({ data: { name } }), retry: false });
  const [form, setForm] = useState<Values>({}), [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => { if (query.data) setForm(query.data.settings); }, [query.data]);
  const data = query.data;
  const disabled = busy || !canManage || !data?.storage_available;
  async function save() {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const secretKey = fields.find((f) => f.type === 'secret')?.key;
      const values = { ...form, ...(secretKey ? { [secretKey]: secret } : {}) };
      client.setQueryData(['kaiSettings', name], await saveSettingsSectionFn({ data: { name, values } }));
      setSecret(''); setNotice('Tersimpan. Berlaku dalam 1 menit tanpa restart.');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Pengaturan gagal disimpan. Coba lagi.');
    } finally {
      setBusy(false);
    }
  }
  const label = query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : sources[data!.source];
  return <section aria-label={title} className="rounded-lg border border-(--cui-color-stroke-default) p-4">
    <h2 className="font-medium">{title} <Badge text={label} state={query.isError ? 'danger' : data?.source === 'ui' ? 'success' : 'neutral'} /></h2>
    <div className="mt-2 text-sm text-(--cui-color-text-muted)">{description}</div>
    {query.isError && <p role="alert" className="mt-2 text-sm">Pengaturan tidak bisa dibaca.</p>}
    {data && <form className="mt-3 flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {!data.storage_available && <p role="alert" className="text-sm">Penyimpanan pengaturan belum disiapkan di server (jalankan make warehouse-local-setup). Sampai itu, nilai dibaca dari .env.</p>}
      {!canManage && <p className="text-sm">Anda hanya bisa melihat. Perubahan butuh izin kelola konfigurasi.</p>}
      <div className="flex flex-wrap gap-3">
        {fields.map((f) => f.type === 'checkbox'
          ? <div key={f.key} className="flex w-full flex-col gap-1"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form[f.key] === true} disabled={disabled} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} />{f.label}</label>
              {f.help && <span className="text-xs text-(--cui-color-text-muted)">{f.help}</span>}</div>
          : <label key={f.key} className={`flex flex-col gap-1 text-sm ${f.type === 'number' ? '' : 'w-full'}`}>{f.label}
              {f.type === 'secret'
                ? <input className={inputClass} type="password" autoComplete="new-password" value={secret} disabled={disabled} maxLength={8192} placeholder={data.secret_set ? 'Tersimpan. Kosongkan untuk mempertahankan' : f.placeholder} onChange={(e) => setSecret(e.target.value)} />
                : f.type === 'number'
                  ? <input className={inputClass} type="number" min={f.min} max={f.max} value={Number(form[f.key] ?? 0)} disabled={disabled} onChange={(e) => setForm({ ...form, [f.key]: Number(e.target.value) })} />
                  : <input className={inputClass} value={String(form[f.key] ?? '')} disabled={disabled} maxLength={2048} placeholder={f.placeholder} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />}
              {f.help && <span className="text-xs text-(--cui-color-text-muted)">{f.help}</span>}
            </label>)}
      </div>
      {canManage && <div><Button type="primary" htmlType="submit" label="Simpan" disabled={disabled} /></div>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {error && <p role="alert" className="text-sm">{error}</p>}
      {data.updated_at && <p className="text-xs text-(--cui-color-text-muted)">Terakhir diubah {new Date(data.updated_at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' })}.</p>}
    </form>}
  </section>;
}
