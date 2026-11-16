import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import type { Invitation } from '@/server/kaiAccounts';
/** Error text for the account sections: never empty, and browser network errors become plain Indonesian. */
export function errorText(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : '';
  return !message || /failed to fetch|networkerror|load failed/i.test(message) ? fallback : message;
}
export const inputClass = 'w-full rounded-lg border border-(--cui-color-stroke-default) bg-(--cui-color-background-default) px-3 py-2 text-sm text-(--cui-color-text-default)';
export function ActivationLink({ invitation, reset = false }: { invitation: Invitation; reset?: boolean }) {
  const name = reset ? 'Tautan reset password' : 'Tautan aktivasi';
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  return <section className="flex flex-col gap-3" aria-label={name}>
    <p className="text-sm text-(--cui-color-text-muted)">Bagikan tautan sekali pakai ini secara aman kepada pengguna. Pengguna akan membuat password {reset ? 'baru' : 'sendiri'}.{reset ? ' Password lama tetap berlaku sampai tautan dipakai; setelah itu semua sesi login pengguna dikeluarkan.' : ''}</p>
    <label className="flex flex-col gap-1 text-sm">{name}<textarea className={inputClass} readOnly value={invitation.activationUrl} rows={3} onFocus={e => e.target.select()} /></label>
    <p className="text-xs text-(--cui-color-text-muted)">Berlaku sampai {new Date(invitation.expiresAt).toLocaleString('id-ID')}. Menutup dialog menyembunyikan tautan ini. {reset ? 'Membuat tautan baru membatalkan tautan sebelumnya.' : 'Tautan baru dapat dibuat untuk akun yang menunggu aktivasi.'}</p>
    <div><Button type="secondary" label={copied ? 'Tautan disalin' : `Salin ${name.toLowerCase()}`} onClick={async () => { try { await navigator.clipboard.writeText(invitation.activationUrl); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} /></div>
    {copyError && <p role="alert">Pilih dan salin tautan secara manual.</p>}
  </section>;
}
