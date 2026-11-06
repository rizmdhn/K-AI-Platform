import { useState } from 'react';
import { useRouteContext } from '@tanstack/react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type * as t from '@/types';
import { FormDialog } from '@/components/shared';
import { provisionUserFn } from '@/server/kaiAccounts';
import { ActivationLink, inputClass } from './AccountFields';
export function CreateUserDialog({ open, onClose }: t.CreateUserDialogProps) {
  const { user } = useRouteContext({ from: '/_app' });
  const queryClient = useQueryClient();
  const [name, setName] = useState(''), [email, setEmail] = useState('');
  const [role, setRole] = useState<'USER' | 'ADMIN'>('USER');
  const mutation = useMutation({ gcTime: 0, mutationFn: () => provisionUserFn({ data: { name, email, role, warehouse: null } }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }) });
  const close = () => { if (mutation.isPending) return; mutation.reset(); setName(''); setEmail(''); setRole('USER'); onClose(); };
  return <FormDialog open={open} title={mutation.isSuccess ? 'Akun berhasil dibuat' : 'Tambah pengguna'} submitLabel={mutation.isSuccess ? 'Selesai' : 'Buat akun platform'} saving={mutation.isPending} submitDisabled={!mutation.isSuccess && (!name.trim() || !email.trim())} error={mutation.error?.message} onClose={close} onSubmit={() => { if (mutation.isSuccess) close(); else if (!mutation.isPending) mutation.mutate(); }}>
    {mutation.data ? <>
      <ActivationLink invitation={mutation.data} />
      <p className="text-sm">Pengguna perlu mengaktifkan akun melalui tautan di atas. Akses agent dikelola terpisah oleh administrator.</p>
    </> : <>
      <label className="text-sm">Nama<input autoFocus className={inputClass} value={name} onChange={e => setName(e.target.value)} required maxLength={200} autoComplete="off" /></label>
      <label className="text-sm">Email<input type="email" className={inputClass} value={email} onChange={e => setEmail(e.target.value)} required maxLength={254} autoComplete="off" /></label>
      <label className="text-sm">Role platform<select className={inputClass} value={role} onChange={e => setRole(e.target.value as 'USER' | 'ADMIN')}><option value="USER">USER</option>{user?.role === 'ADMIN' && <option value="ADMIN">ADMIN</option>}</select></label>
    </>}
  </FormDialog>;
}
