import { useEffect, useRef, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { ConfirmDialog } from '@/components/access';
import { accountSetupFn, warehouseAccessFn, saveWarehouseCredentialsFn, revokeWarehouseFn, releaseWarehouseFn, checkConnectionFn, checkCredentialsFn } from '@/server/kaiAccounts';
import type { WarehouseAccess, WarehouseCheck } from '@/server/kaiAccounts';
import { Notice, errorText, inputClass } from './AccountFields';
export function WarehouseAccessSection({ userId }: { userId: string }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_USERS);
  const client = useQueryClient();
  const key = ['kaiWarehouse', userId];
  const account = useQuery({ queryKey: ['kaiAccount', userId], queryFn: () => accountSetupFn({ data: { id: userId } }) });
  const query = useQuery({ queryKey: key, queryFn: () => warehouseAccessFn({ data: { id: userId } }) });
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [success, setSuccess] = useState(false), [confirm, setConfirm] = useState(false), [confirmRelease, setConfirmRelease] = useState(false), [cleanupIncomplete, setCleanupIncomplete] = useState(false), [editing, setEditing] = useState(false);
  const [check, setCheck] = useState<(WarehouseCheck & { kind: 'connection' | 'credentials' }) | null>(null), [checking, setChecking] = useState<'connection' | 'credentials' | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setUsername(query.data?.ch_username ?? ''); }, [query.data?.ch_username, userId]);
  useEffect(() => { setPassword(''); setSuccess(false); setError(''); setEditing(false); }, [userId, account.data?.status]);
  async function run(action: () => Promise<WarehouseAccess>, saving: boolean) {
    if (busy) return;
    setBusy(true); setError(''); setSuccess(false);
    try {
      const result = await action();
      client.setQueryData(key, result);
      const connected = result.state === 'active' && result.credentials_saved && result.reference_matches;
      if (mounted.current) { setSuccess(saving && connected); setError(saving && !connected ? 'Akses ClickHouse belum aktif. Periksa username dan password, lalu coba lagi.' : ''); setCleanupIncomplete(result.cleanup_complete === false); setConfirm(false); setConfirmRelease(false); if (saving && connected) setEditing(false); }
    } catch (cause) {
      if (mounted.current) setError(errorText(cause, 'Perubahan akses ClickHouse gagal. Coba lagi.'));
    } finally {
      if (mounted.current) { setPassword(''); setBusy(false); }
      await Promise.all([client.invalidateQueries({ queryKey: key }), client.invalidateQueries({ queryKey: ['kaiAccount', userId] }), client.invalidateQueries({ queryKey: ['kaiAgents', userId] })]);
    }
  }
  // Tests store nothing; the password stays in the field so the admin can save right after a successful test.
  async function test(kind: 'connection' | 'credentials') {
    if (checking || busy) return;
    setChecking(kind); setCheck(null); setError(''); setSuccess(false);
    try {
      const result = kind === 'connection'
        ? await checkConnectionFn({ data: { id: userId } })
        : await checkCredentialsFn({ data: { id: userId, username: username.trim(), password } });
      if (mounted.current) setCheck({ ...result, kind });
    } catch (cause) {
      if (mounted.current) setCheck({ ok: false, kind, message: errorText(cause, 'Tes tidak bisa dijalankan. Coba lagi.') });
    } finally {
      if (mounted.current) setChecking(null);
    }
  }
  function save() {
    setCheck(null);
    const secret = password;
    setPassword('');
    // Direct call keeps the password out of mutation variables and cache.
    void run(() => saveWarehouseCredentialsFn({ data: { id: userId, username: username.trim(), password: secret } }), true);
  }
  const status = query.data;
  const disabledAccount = account.data?.status === 'disabled';
  const active = status?.state === 'active' && status.credentials_saved && status.reference_matches && !disabledAccount;
  const bound = !!status && (status.credentials_saved || status.state === 'active' || status.state === 'revoked');
  const canSave = canManage && !!account.data && !account.isError && account.data.status !== 'disabled' && !!status && status.state !== 'revoked';
  const showForm = !!status && canSave && (!bound || editing);
  // Released: the binding stays revoked for audit, but the platform no longer references this CH username.
  const released = status?.state === 'revoked' && !!account.data && account.data.warehouse === null;
  const label = disabledAccount ? 'Akun nonaktif' : active ? `Aktif · ${status.ch_username}` : status?.state === 'revoked' ? 'Dicabut' : query.isError ? 'Tidak tersedia' : 'Belum diatur';
  return <section className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-4" aria-label="Akses data ClickHouse">
    <div className="flex items-center justify-between gap-2"><h3 className="font-medium">Akses data ClickHouse</h3><Badge text={query.isPending ? 'Memuat' : label} state={active ? 'success' : query.isError ? 'danger' : disabledAccount || status?.state === 'revoked' ? 'disabled' : 'neutral'} /></div>
    {/* Bound credentials show as a summary with actions; the password form opens only to set or change it. */}
    <p className="text-sm text-(--cui-color-text-muted)">{bound ? 'Pengguna ini memakai akun ClickHouse di bawah. Hak akses datanya mengikuti akun tersebut. Password tersimpan terenkripsi dan tidak pernah ditampilkan.' : 'Isi akun ClickHouse milik pengguna ini. Pengguna hanya bisa melihat data sesuai hak akses akun tersebut. Password tidak pernah ditampilkan ke pengguna.'}</p>
    {query.isPending && <p role="status" className="text-sm text-(--cui-color-text-muted)">Memuat akses data ClickHouse…</p>}
    {(query.isError || account.isError) && <Notice tone="error">Akses data ClickHouse tidak tersedia. Coba muat ulang.</Notice>}
    {disabledAccount && <p className="text-sm">Akun nonaktif. Akses data tidak dapat diaktifkan.</p>}
    {account.data?.status === 'pending' && !bound && <p className="text-sm">Akses data dapat disiapkan sekarang. Pengguna tetap perlu mengaktifkan akun untuk login.</p>}
    {status && <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      <dt className="text-(--cui-color-text-muted)">Server</dt><dd className="break-all">{status.service.host}:{status.service.port}</dd>
      {(bound || !canSave) && <><dt className="text-(--cui-color-text-muted)">Username</dt><dd className="break-all">{status.ch_username ?? 'Belum diatur'}</dd></>}
    </dl>}
    {showForm && <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); if (canSave && password && username.trim() && !busy) save(); }}>
      {!bound && <label className="flex flex-col gap-1 text-sm">Username ClickHouse<input className={inputClass} value={username} disabled={busy} autoComplete="off" maxLength={1024} required onChange={event => setUsername(event.target.value)} /></label>}
      <label className="flex flex-col gap-1 text-sm">{bound ? 'Password ClickHouse baru' : 'Password ClickHouse'}<input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} disabled={busy} required maxLength={4096} autoFocus={bound} /></label>
      <div className="flex flex-wrap gap-2">
        {!bound && <Button type="secondary" htmlType="button" label={checking === 'connection' ? 'Menguji…' : 'Tes koneksi'} disabled={busy || !!checking} onClick={() => void test('connection')} />}
        <Button type="secondary" htmlType="button" label={checking === 'credentials' ? 'Menguji…' : 'Tes kredensial'} disabled={busy || !!checking || !password || !username.trim()} onClick={() => void test('credentials')} />
        <Button type="primary" htmlType="submit" label={bound ? 'Simpan password' : 'Simpan kredensial'} disabled={busy || !!checking || !password || !username.trim()} />
        {bound && <Button type="secondary" htmlType="button" label="Batal" disabled={busy} onClick={() => { setEditing(false); setPassword(''); setCheck(null); setError(''); }} />}
      </div>
      <p className="text-xs text-(--cui-color-text-muted)">{bound ? 'Tes kredensial memeriksa password baru tanpa menyimpan. Simpan password menguji ulang lalu menggantinya.' : 'Tes koneksi memeriksa server ClickHouse bisa dijangkau (tanpa login). Tes kredensial memeriksa login dan akses read-only tanpa menyimpan. Simpan kredensial menguji ulang lalu menyimpan password terenkripsi.'}</p>
    </form>}
    {status && bound && !editing && canManage && <div className="flex flex-wrap gap-2">
      {canSave && <Button type="secondary" htmlType="button" label="Ganti password" disabled={busy} onClick={() => { setCheck(null); setSuccess(false); setEditing(true); }} />}
      {canSave && <Button type="secondary" htmlType="button" label={checking === 'connection' ? 'Menguji…' : 'Tes koneksi'} disabled={busy || !!checking} onClick={() => void test('connection')} />}
      {(status.state === 'revoked' ? cleanupIncomplete || !!account.data?.chatbot_cleanup_pending : true) && <Button type="danger" htmlType="button" label={status.state === 'revoked' ? 'Ulangi pencabutan akses' : 'Cabut akses'} disabled={busy} onClick={() => { setPassword(''); setConfirm(true); }} />}
      {status.state === 'revoked' && !released && <Button type="secondary" htmlType="button" label="Lepaskan akun ClickHouse" disabled={busy} onClick={() => { setPassword(''); setConfirmRelease(true); }} />}
    </div>}
    {status && bound && !editing && canSave && <p className="text-xs text-(--cui-color-text-muted)">Akun ClickHouse lain tidak bisa dipasang ke pengguna ini: pencabutan bersifat permanen. Untuk akun lain, buat akun platform baru.</p>}
    {check && <Notice tone={check.ok ? 'success' : 'error'}>{check.kind === 'connection' ? 'Tes koneksi: ' : 'Tes kredensial: '}{check.message}</Notice>}
    {success && !disabledAccount && <Notice tone="success">Berhasil terhubung ke ClickHouse.</Notice>}
    {cleanupIncomplete && <Notice tone="warning">Akses dicabut. Pembersihan akses chatbot belum selesai. Ulangi pencabutan akses.</Notice>}
    {released && <Notice tone="info">Akun ClickHouse {status?.ch_username} sudah dilepas dari pengguna ini dan bisa dipasang ke pengguna lain.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    <ConfirmDialog open={confirmRelease} title="Lepaskan akun ClickHouse" description={`Username ${status?.ch_username ?? ''} akan bisa dipasang ke pengguna lain, dan pengguna itu mendapat hak akses data akun ClickHouse tersebut. Pastikan akun ini memang dialihkan ke orang lain (bukan akun pribadi orang yang sudah keluar). Akses pengguna ini tetap dicabut permanen.`} confirmLabel="Lepaskan akun ClickHouse" saving={busy} onConfirm={() => void run(() => releaseWarehouseFn({ data: { id: userId } }), false)} onCancel={() => setConfirmRelease(false)} />
    <ConfirmDialog open={confirm} title="Cabut akses ClickHouse" description="Pengguna tidak bisa memakai akses data ClickHouse ini. Izin chatbot CH yang dikelola juga akan dicabut." confirmLabel="Cabut akses" saving={busy} onConfirm={() => void run(() => revokeWarehouseFn({ data: { id: userId } }), false)} onCancel={() => setConfirm(false)} />
  </section>;
}
