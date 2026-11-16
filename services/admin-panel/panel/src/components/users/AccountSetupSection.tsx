import { useEffect, useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { ConfirmDialog } from '@/components/access';
import { accountSetupFn, disableAccountFn, reissueActivationFn, resetPasswordFn, setRoleFn, updateProfileFn } from '@/server/kaiAccounts';
import { ActivationLink, Notice, Select, errorText, inputClass } from './AccountFields';
export function AccountSetupSection({ userId }: { userId: string }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_USERS);
  const client = useQueryClient();
  const key = ['kaiAccount', userId];
  const query = useQuery({ queryKey: key, queryFn: () => accountSetupFn({ data: { id: userId } }) });
  const [name, setName] = useState(''), [email, setEmail] = useState('');
  const profile = useMutation({ gcTime: 0, mutationFn: () => updateProfileFn({ data: { id: userId, name: name.trim(), email: email.trim() } }), onSuccess: result => client.setQueryData(key, result), onSettled: () => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: ['users'] }); } });
  useEffect(() => { setName(query.data?.name ?? ''); setEmail(query.data?.email ?? ''); }, [query.data?.name, query.data?.email]);
  const [role, setRole] = useState<'USER' | 'ADMIN' | null>(null);
  const changeRole = useMutation({ gcTime: 0, mutationFn: (next: 'USER' | 'ADMIN') => setRoleFn({ data: { id: userId, role: next } }), onSuccess: result => { client.setQueryData(key, result); setRole(null); }, onSettled: () => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: ['users'] }); } });
  const [confirm, setConfirm] = useState(false), [copied, setCopied] = useState(false), [copyError, setCopyError] = useState(false);
  const reissue = useMutation({ gcTime: 0, mutationFn: () => reissueActivationFn({ data: { id: userId } }), onSuccess: () => client.invalidateQueries({ queryKey: key }) });
  const reset = useMutation({ gcTime: 0, mutationFn: () => resetPasswordFn({ data: { id: userId } }) });
  const disable = useMutation({ gcTime: 0, mutationFn: () => disableAccountFn({ data: { id: userId } }), onSuccess: result => { client.setQueryData(key, result.account); reissue.reset(); reset.reset(); setConfirm(false); }, onSettled: () => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: ['kaiWarehouse', userId] }); void client.invalidateQueries({ queryKey: ['kaiAgents', userId] }); } });
  const account = query.data;
  const pendingParts = [
    ...(disable.data?.cleanup?.sessions === false ? ['mengeluarkan sesi login'] : []),
    ...(account?.warehouse_revocation_pending || disable.data?.cleanup?.warehouse === false ? ['mencabut akses ClickHouse'] : []),
    ...(account?.chatbot_cleanup_pending || disable.data?.cleanup?.chatbot === false ? ['mencabut izin chatbot CH'] : []),
    ...(disable.data?.cleanup_complete === false && !disable.data.cleanup ? ['akses data/chatbot'] : []),
  ];
  const expired = account?.status === 'pending' && account.expiresAt && new Date(account.expiresAt) < new Date();
  const label = account?.status === 'disabled' ? 'Nonaktif' : account?.status === 'pending' ? expired ? 'Tautan aktivasi kedaluwarsa' : 'Menunggu aktivasi' : 'Aktif';
  return <section className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-4" aria-label="Akun">
    <div className="flex items-center justify-between gap-2"><h3 className="font-medium">Akun</h3><Badge text={query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : label} state={query.isError ? 'danger' : account?.status === 'disabled' ? 'disabled' : account?.status === 'pending' ? 'warning' : query.isPending ? 'neutral' : 'success'} /></div>
    <p className="text-sm text-(--cui-color-text-muted)">{account?.status === 'disabled' ? 'Pengguna tidak bisa login. Akun tetap nonaktif meskipun pembersihan akses belum selesai.' : 'Pengguna mengaktifkan akun atau mengganti password melalui tautan sekali pakai dan membuat password sendiri. Admin tidak pernah melihat password.'}</p>
    <p className="text-xs">ID pengguna: <code className="select-all">{userId}</code></p>
    <div><Button type="secondary" label={copied ? 'ID disalin' : 'Salin ID pengguna'} onClick={async () => { try { await navigator.clipboard.writeText(userId); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} /></div>
    {copyError && <Notice tone="info">Pilih dan salin ID pengguna secara manual.</Notice>}
    {account && <form className="flex flex-col gap-2" onSubmit={event => { event.preventDefault(); profile.reset(); profile.mutate(); }}>
      <label className="flex flex-col gap-1 text-sm">Nama<input className={inputClass} value={name} maxLength={200} required disabled={!canManage || profile.isPending} onChange={event => setName(event.target.value)} /></label>
      <label className="flex flex-col gap-1 text-sm">Email login<input className={inputClass} type="email" value={email} maxLength={254} required disabled={!canManage || profile.isPending} onChange={event => setEmail(event.target.value)} /></label>
      {account.status === 'disabled' && <p className="text-xs text-(--cui-color-text-muted)">Untuk memakai email ini di akun baru, ganti email akun nonaktif ini dulu (misalnya nama.lama@invalid.local).</p>}
      {canManage && <div><Button type="secondary" htmlType="submit" label="Simpan nama dan email" disabled={profile.isPending || !name.trim() || !email.trim() || (name.trim() === account.name && email.trim().toLowerCase() === account.email)} /></div>}
      {profile.isSuccess && <Notice tone="success">Nama dan email disimpan. Pengguna login dengan email baru.</Notice>}
      {profile.error && <Notice tone="error">{errorText(profile.error, 'Nama dan email gagal disimpan. Coba lagi.')}</Notice>}
    </form>}
    {account && <label className="flex flex-col gap-1 text-sm">Role platform<Select value={account.role} disabled={!canManage || changeRole.isPending || account.status === 'disabled'} onChange={event => { changeRole.reset(); setRole(event.target.value as 'USER' | 'ADMIN'); }}><option value="USER">USER — pengguna chat</option><option value="ADMIN">ADMIN — mengelola pengguna dan pengaturan</option></Select></label>}
    {changeRole.error && <Notice tone="error">{errorText(changeRole.error, 'Role gagal diubah. Coba lagi.')}</Notice>}
    {query.isError && <Notice tone="error">Pengaturan akun tidak tersedia. Coba muat ulang.</Notice>}
    {!query.isError && account && canManage && <div className="flex flex-wrap gap-2">
      {account.status === 'pending' && <Button type="primary" label="Buat tautan aktivasi baru" disabled={reissue.isPending || disable.isPending} onClick={() => reissue.mutate()} />}
      {(account.status === 'active' || account.status === 'existing') && <Button type="secondary" label="Buat tautan reset password" disabled={reset.isPending || disable.isPending} onClick={() => reset.mutate()} />}
      <Button type="danger" label={account.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} disabled={disable.isPending || reissue.isPending} onClick={() => setConfirm(true)} />
    </div>}
    {account?.status === 'pending' && reissue.data && <ActivationLink invitation={reissue.data} />}
    {account?.status !== 'pending' && account?.status !== 'disabled' && reset.data && <ActivationLink invitation={reset.data} reset />}
    {account?.status === 'disabled' && pendingParts.length > 0 && <Notice tone="warning">
      <p>Akun sudah nonaktif dan tidak bisa login, tetapi pembersihan belum selesai: <strong>{pendingParts.join(', ')}</strong>.</p>
      <p>Klik <strong>Ulangi pembersihan akses</strong>. Jika tetap gagal, periksa log server (<code>docker logs LibreChat | grep "\[kai\] disable"</code>) dan menu Perlu perhatian.</p>
    </Notice>}
    {(reissue.error || reset.error || disable.error) && <Notice tone="error">{disable.error ? errorText(disable.error, 'Akun gagal dinonaktifkan. Coba lagi.') : reset.error ? errorText(reset.error, 'Tautan reset password gagal dibuat. Coba lagi.') : errorText(reissue.error, 'Tautan aktivasi gagal dibuat. Coba lagi.')}</Notice>}
    <ConfirmDialog open={!!role} title={role === 'ADMIN' ? 'Jadikan ADMIN' : 'Jadikan USER'} description={role === 'ADMIN' ? 'Pengguna ini akan bisa membuka Admin Panel, mengelola semua pengguna, dan mengubah pengaturan.' : 'Pengguna ini kehilangan akses Admin Panel dan hanya bisa memakai chat.'} confirmLabel={role === 'ADMIN' ? 'Jadikan ADMIN' : 'Jadikan USER'} saving={changeRole.isPending} onConfirm={() => role && changeRole.mutate(role)} onCancel={() => setRole(null)} />
    <ConfirmDialog open={confirm} title={account?.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} description="Pengguna akan keluar dan tidak bisa login. Akses ClickHouse dicabut permanen dan kredensial tersimpan dihapus." confirmLabel={account?.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} saving={disable.isPending} onConfirm={() => disable.mutate()} onCancel={() => setConfirm(false)} />
  </section>;
}
