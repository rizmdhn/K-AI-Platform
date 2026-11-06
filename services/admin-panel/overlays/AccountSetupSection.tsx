import { useState } from 'react';
import { Badge, Button } from '@clickhouse/click-ui';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useCapabilities } from '@/hooks';
import { SystemCapabilities } from '@/constants';
import { ConfirmDialog } from '@/components/access';
import { accountSetupFn, disableAccountFn, reissueActivationFn } from '@/server/kaiAccounts';
import { ActivationLink } from './AccountFields';
export function AccountSetupSection({ userId }: { userId: string }) {
  const { hasCapability } = useCapabilities();
  const canManage = hasCapability(SystemCapabilities.MANAGE_USERS);
  const client = useQueryClient();
  const key = ['kaiAccount', userId];
  const query = useQuery({ queryKey: key, queryFn: () => accountSetupFn({ data: { id: userId } }) });
  const [confirm, setConfirm] = useState(false), [copied, setCopied] = useState(false), [copyError, setCopyError] = useState(false);
  const reissue = useMutation({ gcTime: 0, mutationFn: () => reissueActivationFn({ data: { id: userId } }), onSuccess: () => client.invalidateQueries({ queryKey: key }) });
  const disable = useMutation({ gcTime: 0, mutationFn: () => disableAccountFn({ data: { id: userId } }), onSuccess: result => { client.setQueryData(key, result.account); reissue.reset(); setConfirm(false); }, onSettled: () => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: ['kaiWarehouse', userId] }); void client.invalidateQueries({ queryKey: ['kaiAgents', userId] }); } });
  const account = query.data;
  const expired = account?.status === 'pending' && account.expiresAt && new Date(account.expiresAt) < new Date();
  const label = account?.status === 'disabled' ? 'Nonaktif' : account?.status === 'pending' ? expired ? 'Tautan aktivasi kedaluwarsa' : 'Menunggu aktivasi' : 'Aktif';
  return <section className="flex flex-col gap-3 border-t border-(--cui-color-stroke-default) pt-4" aria-label="Akun">
    <div className="flex items-center justify-between gap-2"><h3 className="font-medium">Akun</h3><Badge text={query.isPending ? 'Memuat' : query.isError ? 'Tidak tersedia' : label} state={query.isError ? 'danger' : account?.status === 'disabled' ? 'disabled' : account?.status === 'pending' ? 'warning' : query.isPending ? 'neutral' : 'success'} /></div>
    <p className="text-sm text-(--cui-color-text-muted)">{account?.status === 'disabled' ? 'Pengguna tidak bisa login. Akun tetap nonaktif meskipun pembersihan akses belum selesai.' : 'Pengguna mengaktifkan akun melalui tautan dan membuat password sendiri.'}</p>
    <p className="text-xs">ID pengguna: <code className="select-all">{userId}</code></p>
    <div><Button type="secondary" label={copied ? 'ID disalin' : 'Salin ID pengguna'} onClick={async () => { try { await navigator.clipboard.writeText(userId); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} /></div>
    {copyError && <p role="alert">Pilih dan salin ID pengguna secara manual.</p>}
    {query.isError && <p role="alert">Pengaturan akun tidak tersedia. Coba muat ulang.</p>}
    {!query.isError && account && canManage && <div className="flex flex-wrap gap-2">
      {account.status === 'pending' && <Button type="primary" label="Buat tautan aktivasi baru" disabled={reissue.isPending || disable.isPending} onClick={() => reissue.mutate()} />}
      <Button type="danger" label={account.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} disabled={disable.isPending || reissue.isPending} onClick={() => setConfirm(true)} />
    </div>}
    {account?.status === 'pending' && reissue.data && <ActivationLink invitation={reissue.data} />}
    {(disable.data?.cleanup_complete === false || account?.warehouse_revocation_pending) && <p role="alert">Akun nonaktif. Pembersihan sesi atau akses data/chatbot belum selesai. Ulangi pembersihan akses.</p>}
    {(reissue.error || disable.error) && <p role="alert">{disable.error?.message ?? reissue.error?.message}</p>}
    <ConfirmDialog open={confirm} title={account?.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} description="Pengguna akan keluar dan tidak bisa login. Akses ClickHouse dicabut permanen dan kredensial tersimpan dihapus." confirmLabel={account?.status === 'disabled' ? 'Ulangi pembersihan akses' : 'Nonaktifkan akun'} saving={disable.isPending} onConfirm={() => disable.mutate()} onCancel={() => setConfirm(false)} />
  </section>;
}
