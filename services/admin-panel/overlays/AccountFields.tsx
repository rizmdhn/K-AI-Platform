import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import type { Invitation } from '@/server/kaiAccounts';
export const inputClass = 'w-full rounded-lg border border-(--cui-color-stroke-default) bg-(--cui-color-background-default) px-3 py-2 text-sm text-(--cui-color-text-default)';
export function ActivationLink({ invitation }: { invitation: Invitation }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  return <section className="flex flex-col gap-3" aria-label="Tautan aktivasi">
    <p className="text-sm text-(--cui-color-text-muted)">Bagikan tautan sekali pakai ini secara aman kepada pengguna. Pengguna akan membuat password sendiri.</p>
    <label className="flex flex-col gap-1 text-sm">Tautan aktivasi<textarea className={inputClass} readOnly value={invitation.activationUrl} rows={3} onFocus={e => e.target.select()} /></label>
    <p className="text-xs text-(--cui-color-text-muted)">Berlaku sampai {new Date(invitation.expiresAt).toLocaleString('id-ID')}. Menutup dialog menyembunyikan tautan ini. Tautan baru dapat dibuat untuk akun yang menunggu aktivasi.</p>
    <div><Button type="secondary" label={copied ? 'Tautan disalin' : 'Salin tautan aktivasi'} onClick={async () => { try { await navigator.clipboard.writeText(invitation.activationUrl); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} /></div>
    {copyError && <p role="alert">Pilih dan salin tautan secara manual.</p>}
  </section>;
}
