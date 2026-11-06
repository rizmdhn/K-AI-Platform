import { useEffect, useRef, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { ConfirmDialog } from '@/components/access';
import { accountSetupFn, warehouseAccessFn, saveWarehouseCredentialsFn, revokeWarehouseFn } from '@/server/kaiAccounts';
import type { WarehouseAccess } from '@/server/kaiAccounts';
import { inputClass } from './AccountFields';
export function WarehouseAccessSection({ userId }: { userId: string }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_USERS);
  const client = useQueryClient();
  const key = ['kaiWarehouse', userId];
  const account = useQuery({ queryKey: ['kaiAccount', userId], queryFn: () => accountSetupFn({ data: { id: userId } }) });
  const query = useQuery({ queryKey: key, queryFn: () => warehouseAccessFn({ data: { id: userId } }) });
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [success, setSuccess] = useState(false), [confirm, setConfirm] = useState(false), [cleanupIncomplete, setCleanupIncomplete] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setUsername(query.data?.ch_username ?? ''); }, [query.data?.ch_username, userId]);
  useEffect(() => { setPassword(''); setSuccess(false); setError(''); }, [userId, account.data?.status]);
  async function run(action: () => Promise<WarehouseAccess>, saving: boolean) {
    if (busy) return;
    setBusy(true); setError(''); setSuccess(false);
    try {
      const result = await action();
      client.setQueryData(key, result);
      if (mounted.current) { setSuccess(saving && result.state === 'active' && result.credentials_saved && result.reference_matches); setCleanupIncomplete(result.cleanup_complete === false); setConfirm(false); }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : 'Perubahan akses ClickHouse gagal. Coba lagi.');
    } finally {
      if (mounted.current) { setPassword(''); setBusy(false); }
      await Promise.all([client.invalidateQueries({ queryKey: key }), client.invalidateQueries({ queryKey: ['kaiAccount', userId] }), client.invalidateQueries({ queryKey: ['kaiAgents', userId] })]);
    }
  }
  function save() {
    const secret = password;
    setPassword('');
    // Direct call keeps the password out of mutation variables and cache.
    void run(() => saveWarehouseCredentialsFn({ data: { id: userId, username: username.trim(), password: secret } }), true);
  }
  const status = query.data;
  const disabledAccount = account.data?.status === 'disabled';
  const active = status?.state === 'active' && status.credentials_saved && status.reference_matches && !disabledAccount;
  const lockedIdentity = !!status?.credentials_saved || status?.state === 'active';
  const canSave = canManage && !!account.data && !account.isError && account.data.status !== 'disabled' && !!status && status.state !== 'revoked';
  const label = disabledAccount ? 'Akun nonaktif' : active ? `Aktif · ${status.ch_username}` : status?.state === 'revoked' ? 'Dicabut' : query.isError ? 'Tidak tersedia' : 'Belum diatur';
  return <section className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-4" aria-label="Akses data ClickHouse">
    <div className="flex items-center justify-between gap-2"><h3 className="font-medium">Akses data ClickHouse</h3><Badge text={query.isPending ? 'Memuat' : label} state={active ? 'success' : query.isError ? 'danger' : disabledAccount || status?.state === 'revoked' ? 'disabled' : 'neutral'} /></div>
    <p className="text-sm text-(--cui-color-text-muted)">Isi akun ClickHouse milik pengguna ini. Pengguna hanya bisa melihat data sesuai hak akses akun tersebut. Password tidak pernah ditampilkan ke pengguna.</p>
    {query.isPending && <p role="status">Memuat akses data ClickHouse…</p>}
    {(query.isError || account.isError) && <p role="alert">Akses data ClickHouse tidak tersedia. Coba muat ulang.</p>}
    {disabledAccount && <p className="text-sm">Akun nonaktif. Akses data tidak dapat diaktifkan.</p>}
    {account.data?.status === 'pending' && <p className="text-sm">Akses data dapat disiapkan sekarang. Pengguna tetap perlu mengaktifkan akun untuk login.</p>}
    {status && <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); if (canSave && password && username.trim() && !busy) save(); }}>
      <label className="flex flex-col gap-1 text-sm">Server ClickHouse<input className={inputClass} readOnly value={`${status.service.host}:${status.service.port}`} /></label>
      <label className="flex flex-col gap-1 text-sm">Username ClickHouse<input className={inputClass} value={username} readOnly={lockedIdentity || !canManage} disabled={busy || !canSave} autoComplete="off" maxLength={1024} required onChange={event => setUsername(event.target.value)} /></label>
      {canManage && <label className="flex flex-col gap-1 text-sm">Password ClickHouse<input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy || !canSave} required maxLength={4096} /></label>}
      {canManage && <div className="flex flex-wrap gap-2">
        <Button type="primary" htmlType="submit" label={lockedIdentity ? 'Ganti password' : 'Simpan dan uji koneksi'} disabled={busy || !canSave || !password || !username.trim()} />
        {!['reference_required', 'not_prepared'].includes(status.state) && <Button type="danger" htmlType="button" label={status.state === 'revoked' ? 'Ulangi pencabutan akses' : 'Cabut akses'} disabled={busy} onClick={() => { setPassword(''); setConfirm(true); }} />}
      </div>}
    </form>}
    {success && !disabledAccount && <p role="status">Berhasil terhubung</p>}
    {cleanupIncomplete && <p role="alert">Akses dicabut. Pembersihan akses chatbot belum selesai. Ulangi pencabutan akses.</p>}
    {error && <p role="alert">{error}</p>}
    <ConfirmDialog open={confirm} title="Cabut akses ClickHouse" description="Pengguna tidak bisa memakai akses data ClickHouse ini. Izin chatbot CH yang dikelola juga akan dicabut." confirmLabel="Cabut akses" saving={busy} onConfirm={() => void run(() => revokeWarehouseFn({ data: { id: userId } }), false)} onCancel={() => setConfirm(false)} />
  </section>;
}
