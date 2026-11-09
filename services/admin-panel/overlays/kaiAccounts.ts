import { z } from 'zod';
import { createServerFn } from '@tanstack/react-start';
import { apiFetch, extractApiError } from './utils/api';
import { requireCapability } from './capabilities';
import { SystemCapabilities } from '@/constants';
export interface AccountSetup {
  id: string; status: 'pending' | 'active' | 'existing' | 'disabled'; expiresAt?: string; warehouse_revocation_pending?: boolean;
  warehouse: { service: string; username: string; status: 'unverified' } | null;
}
export interface Invitation { activationUrl: string; expiresAt: string }
const warehouse = z.object({ service: z.string().min(1).max(253), username: z.string().min(1).max(1024), confirmed: z.literal(true) }).strict().nullable();
const id = z.string().regex(/^[a-f0-9]{24}$/);
async function call<T>(url: string, capability: string, method = 'GET', body?: unknown): Promise<T> {
  await requireCapability(capability);
  const response = await apiFetch(url, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) await extractApiError(response, 'Pengaturan akun tidak tersedia');
  return response.json() as Promise<T>;
}
export const provisionUserFn = createServerFn({ method: 'POST' })
  .inputValidator(z.object({ name: z.string().trim().min(1).max(200), email: z.string().email().max(254), role: z.enum(['USER','ADMIN']), warehouse }).strict())
  .handler(async ({ data }): Promise<Invitation & { account: AccountSetup }> => call('/api/admin/users', SystemCapabilities.MANAGE_USERS, 'POST', data));
export const accountSetupFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<AccountSetup> => call(`/api/admin/users/${data.id}/kai-account`, SystemCapabilities.READ_USERS));
export const reissueActivationFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<Invitation> => call(`/api/admin/users/${data.id}/kai-activation`, SystemCapabilities.MANAGE_USERS, 'POST', {}));

export const disableAccountFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<{ account: AccountSetup; cleanup_complete: boolean }> => call(`/api/admin/users/${data.id}/kai-disable`, SystemCapabilities.MANAGE_USERS, 'POST', {}));

export interface WarehouseAccess {
  state: 'reference_required' | 'not_prepared' | 'ready' | 'active' | 'revoked';
  service: { host: string; port: number }; ch_username: string | null;
  credentials_saved: boolean; reference_matches: boolean; cleanup_complete?: boolean;
}
export const warehouseAccessFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse`, SystemCapabilities.READ_USERS));
export const saveWarehouseCredentialsFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, username: z.string().trim().min(1).max(1024), password: z.string().min(1).max(4096).refine(value => !value.includes('\0') && new TextEncoder().encode(value).length <= 4096) }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse/save`, SystemCapabilities.MANAGE_USERS, 'POST', { username: data.username, password: data.password }));
export const revokeWarehouseFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse`, SystemCapabilities.MANAGE_USERS, 'DELETE', {}));

export interface WarehouseReadiness {
  version: 2; scope: 'local_configuration_only'; database_verified: false; cloud_verified: false;
  checks: Record<'binding_dsn'|'enrollment_dsn'|'distinct_runtime_users'|'keyring'|'warehouse_target', {ok:boolean;code:string}>;
}
export const warehouseReadinessFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseReadiness> => call(`/api/admin/users/${data.id}/kai-warehouse/readiness`, SystemCapabilities.READ_USERS));

export interface Problems {
  generated_at: string; calls_24h: number | null; errors_24h: number | null;
  system: { code: string; check?: string; calls?: number; errors?: number }[];
  accounts: { id: string; name: string; email: string; issues: { code: string; at?: string; count?: number }[] }[];
}
export const problemsFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<Problems> => call('/api/admin/users/kai-problems', SystemCapabilities.READ_USERS));
