import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import { useMutation } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { testSmtpFn } from '@/server/kaiAccounts';
import { Notice, errorText, inputClass } from './AccountFields';
/** Sends one test mail with the saved SMTP settings (save first). */
export function SmtpTest() {
  const { hasCapability } = useCapabilities();
  const [to, setTo] = useState('');
  const test = useMutation({ gcTime: 0, mutationFn: () => testSmtpFn({ data: { to: to.trim() } }) });
  if (!hasCapability(SystemCapabilities.MANAGE_CONFIGS)) return null;
  return <form className="mt-4 flex flex-col gap-2 border-t border-(--cui-color-stroke-default) pt-4" onSubmit={(e) => { e.preventDefault(); test.reset(); test.mutate(); }}>
    <label className="flex flex-col gap-1 text-sm">Kirim email uji ke<input className={inputClass} type="email" required maxLength={254} value={to} placeholder="nama@perusahaan.co.id" onChange={(e) => setTo(e.target.value)} /></label>
    <span className="text-xs text-(--cui-color-text-muted)">Memakai pengaturan yang sudah disimpan. Simpan dulu bila baru mengubah isian di atas.</span>
    <div><Button type="secondary" htmlType="submit" label={test.isPending ? 'Mengirim…' : 'Kirim email uji'} disabled={test.isPending || !to.trim()} /></div>
    {test.isSuccess && <Notice tone="success">Email uji terkirim ke {to.trim()}. Periksa kotak masuk (dan folder spam).</Notice>}
    {test.error && <Notice tone="error">{errorText(test.error, 'Email uji gagal dikirim. Coba lagi.')}</Notice>}
  </form>;
}
