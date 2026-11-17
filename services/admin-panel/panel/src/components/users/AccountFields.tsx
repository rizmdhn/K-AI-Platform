import { useState } from 'react';
import { Button } from '@clickhouse/click-ui';
import type { ReactNode, SelectHTMLAttributes } from 'react';
import type { Invitation } from '@/server/kaiAccounts';
/** Error text for the account sections: never empty, and browser network errors become plain Indonesian. */
export function errorText(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : '';
  return !message || /failed to fetch|networkerror|load failed/i.test(message) ? fallback : message;
}
export const inputClass = 'w-full rounded-lg border border-(--cui-color-stroke-default) bg-(--cui-color-background-default) px-3 py-2 text-sm text-(--cui-color-text-default)';
/** Native select with its own chevron inset from the border (the browser arrow ignores padding). */
export function Select({ className = '', ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <div className="relative">
    <select {...props} className={`${inputClass} appearance-none pr-10 ${className}`} />
    <svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-(--cui-color-text-muted)"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
  </div>;
}
type Tone = 'success' | 'error' | 'warning' | 'info';
const tones: Record<Tone, { box: string; fg: string; mark: string }> = {
  success: { box: 'border-(--cui-color-accent-success) bg-(--cui-color-feedback-success-bg)', fg: 'text-(--cui-color-feedback-success-fg)', mark: '✓' },
  error: { box: 'border-(--cui-color-accent-danger) bg-(--cui-color-feedback-danger-bg)', fg: 'text-(--cui-color-feedback-danger-fg)', mark: '!' },
  warning: { box: 'border-(--cui-color-accent-warning) bg-(--cui-color-feedback-warning-bg)', fg: 'text-(--cui-color-feedback-warning-fg)', mark: '!' },
  info: { box: 'border-(--cui-color-accent-info) bg-(--cui-color-feedback-info-bg)', fg: 'text-(--cui-color-feedback-info-fg)', mark: 'i' },
};
/** Inline notice: tone-colored left rule, tint and mark; body text stays text-default for 4.5:1 contrast. Errors and warnings announce immediately. */
export function Notice({ tone, children }: { tone: Tone; children: ReactNode }) {
  const { box, fg, mark } = tones[tone];
  return <div role={tone === 'error' || tone === 'warning' ? 'alert' : 'status'} className={`flex items-start gap-2 rounded-md border border-l-4 px-3 py-2 text-sm leading-snug text-(--cui-color-text-default) ${box}`}>
    <span aria-hidden="true" className={`mt-px inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-current text-[10px] font-bold ${fg}`}>{mark}</span>
    {/* Plain block flow: a flex container would put every text run and inline link on its own line. */}
    <div className="min-w-0">{children}</div>
  </div>;
}
export function ActivationLink({ invitation, reset = false }: { invitation: Invitation; reset?: boolean }) {
  const name = reset ? 'Tautan reset password' : 'Tautan aktivasi';
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  return <section className="flex flex-col gap-3" aria-label={name}>
    <p className="text-sm text-(--cui-color-text-muted)">Bagikan tautan sekali pakai ini secara aman kepada pengguna. Pengguna akan membuat password {reset ? 'baru' : 'sendiri'}.{reset ? ' Password lama tetap berlaku sampai tautan dipakai; setelah itu semua sesi login pengguna dikeluarkan.' : ''}</p>
    {invitation.email?.sent && <Notice tone="success">Tautan juga dikirim ke {invitation.email.to}.</Notice>}
    {invitation.email && !invitation.email.sent && (invitation.email.configured === false
      ? <Notice tone="info">Email belum diatur (Integrasi → Email keluar). Salin tautan di bawah dan kirimkan ke {invitation.email.to}.</Notice>
      : <Notice tone="warning">Email ke {invitation.email.to} gagal: {invitation.email.message || 'tidak diketahui'}. Salin tautan di bawah dan kirimkan manual.</Notice>)}
    <label className="flex flex-col gap-1 text-sm">{name}<textarea className={inputClass} readOnly value={invitation.activationUrl} rows={3} onFocus={e => e.target.select()} /></label>
    <p className="text-xs text-(--cui-color-text-muted)">Berlaku sampai {new Date(invitation.expiresAt).toLocaleString('id-ID')}. Menutup dialog menyembunyikan tautan ini. {reset ? 'Membuat tautan baru membatalkan tautan sebelumnya.' : 'Tautan baru dapat dibuat untuk akun yang menunggu aktivasi.'}</p>
    <div><Button type="secondary" label={copied ? 'Tautan disalin' : `Salin ${name.toLowerCase()}`} onClick={async () => { try { await navigator.clipboard.writeText(invitation.activationUrl); setCopied(true); setCopyError(false); } catch { setCopyError(true); } }} /></div>
    {copyError && <Notice tone="info">Pilih dan salin tautan secara manual.</Notice>}
  </section>;
}
