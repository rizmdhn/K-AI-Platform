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

// Integration settings (OpenMetadata + metadata draft worker). The token is write-only: never returned.
export interface OpenMetadataSettings {
  url: string; service: string; ca_cert: string; catalog_enabled: boolean; databases: string[]; scan_hours: number; max_tables: number;
}
export interface IntegrationState {
  source: 'ui' | 'env' | 'none'; settings: OpenMetadataSettings; token_set: boolean; storage_available: boolean;
  updated_at: string | null; updated_by: string | null;
}
const openMetadataForm = z.object({
  url: z.string().max(2048), service: z.string().max(256), token: z.string().max(8192), ca_cert: z.string().max(65536),
  catalog_enabled: z.boolean(), databases: z.array(z.string().max(128)).max(100),
  scan_hours: z.number().int().min(1).max(168), max_tables: z.number().int().min(1).max(1000),
}).strict();
export const openMetadataFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<IntegrationState> => call('/api/admin/users/kai-integrations/openmetadata', SystemCapabilities.READ_CONFIGS));
export const saveOpenMetadataFn = createServerFn({ method: 'POST' }).inputValidator(openMetadataForm)
  .handler(async ({ data }): Promise<IntegrationState> => call('/api/admin/users/kai-integrations/openmetadata', SystemCapabilities.MANAGE_CONFIGS, 'PUT', data));
export interface DatabaseSummary { name: string; tables: number; empty_tables: number; empty_columns: number }
export const openMetadataDatabasesFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<{ databases: DatabaseSummary[] }> => call('/api/admin/users/kai-integrations/openmetadata/databases', SystemCapabilities.READ_CONFIGS));
export const testOpenMetadataFn = createServerFn({ method: 'POST' }).inputValidator(openMetadataForm)
  .handler(async ({ data }): Promise<{ ok: boolean; lines: string[]; databases: DatabaseSummary[] }> => call('/api/admin/users/kai-integrations/openmetadata/test', SystemCapabilities.MANAGE_CONFIGS, 'POST', data));

// Other Admin Panel settings sections (stored in PostgreSQL, .env only as fallback). Secrets are write-only.
export type SettingsSectionName = 'batch_model' | 'limits';
export interface SectionState {
  source: 'ui' | 'env' | 'none'; settings: Record<string, string | number | boolean>; secret_set?: boolean;
  storage_available: boolean; updated_at: string | null; updated_by: string | null;
}
const sectionName = z.enum(['batch_model', 'limits']);
const sectionForm = z.object({
  name: sectionName,
  values: z.record(z.string().max(64), z.union([z.string().max(8192), z.number().int().min(0).max(1e12), z.boolean()])),
}).strict();
export const settingsSectionFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ name: sectionName }).strict())
  .handler(async ({ data }): Promise<SectionState> => call(`/api/admin/users/kai-integrations/${data.name}`, SystemCapabilities.READ_CONFIGS));
export const saveSettingsSectionFn = createServerFn({ method: 'POST' }).inputValidator(sectionForm)
  .handler(async ({ data }): Promise<SectionState> => call(`/api/admin/users/kai-integrations/${data.name}`, SystemCapabilities.MANAGE_CONFIGS, 'PUT', data.values));
