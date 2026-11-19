import { z } from 'zod';
import { createServerFn } from '@tanstack/react-start';
import { apiFetch, extractApiError } from './utils/api';
import { requireCapability } from './capabilities';
import { SystemCapabilities } from '@/constants';
export interface AccountSetup {
  id: string; status: 'pending' | 'active' | 'existing' | 'disabled'; role: 'USER' | 'ADMIN'; name: string; email: string; expiresAt?: string; warehouse_revocation_pending?: boolean; chatbot_cleanup_pending?: boolean;
  warehouse: { service: string; username: string; status: 'unverified' } | null;
}
export interface Invitation { activationUrl: string; expiresAt: string; email?: { to: string; sent: boolean; configured?: boolean; message?: string } }
const warehouse = z.object({ service: z.string().min(1).max(253), username: z.string().min(1).max(1024), confirmed: z.literal(true) }).strict().nullable();
const id = z.string().regex(/^[a-f0-9]{24}$/);
async function call<T>(url: string, capability: string, method = 'GET', body?: unknown): Promise<T> {
  try { await requireCapability(capability); } catch (error) {
    throw new Error(error instanceof Error && error.message.startsWith('Insufficient permissions') ? 'Anda tidak punya izin untuk tindakan ini.' : 'Sesi Anda berakhir. Silakan login ulang.');
  }
  let response: Response;
  try { response = await apiFetch(url, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); } catch {
    throw new Error('Layanan platform tidak bisa dihubungi atau sesi berakhir. Muat ulang halaman, lalu coba lagi.');
  }
  if (!response.ok) await extractApiError(response, 'Layanan akun sedang tidak tersedia');
  return response.json() as Promise<T>;
}
export const provisionUserFn = createServerFn({ method: 'POST' })
  .inputValidator(z.object({ name: z.string().trim().min(1).max(200), email: z.string().email().max(254), role: z.enum(['USER','ADMIN']), warehouse }).strict())
  .handler(async ({ data }): Promise<Invitation & { account: AccountSetup }> => call('/api/admin/users', SystemCapabilities.MANAGE_USERS, 'POST', data));
export const accountSetupFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<AccountSetup> => call(`/api/admin/users/${data.id}/kai-account`, SystemCapabilities.READ_USERS));
export const reissueActivationFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<Invitation> => call(`/api/admin/users/${data.id}/kai-activation`, SystemCapabilities.MANAGE_USERS, 'POST', {}));

export const resetPasswordFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<Invitation> => call(`/api/admin/users/${data.id}/kai-password-reset`, SystemCapabilities.MANAGE_USERS, 'POST', {}));

export const setRoleFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, role: z.enum(['USER', 'ADMIN']) }).strict())
  .handler(async ({ data }): Promise<AccountSetup> => call(`/api/admin/users/${data.id}/kai-role`, SystemCapabilities.MANAGE_USERS, 'PATCH', { role: data.role }));

export const updateProfileFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, name: z.string().trim().min(1).max(200), email: z.string().trim().email().max(254) }).strict())
  .handler(async ({ data }): Promise<AccountSetup> => call(`/api/admin/users/${data.id}/kai-profile`, SystemCapabilities.MANAGE_USERS, 'PATCH', { name: data.name, email: data.email.toLowerCase() }));

export const disableAccountFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<{ account: AccountSetup; cleanup_complete: boolean; cleanup?: { sessions: boolean; warehouse: boolean; chatbot: boolean } }> => call(`/api/admin/users/${data.id}/kai-disable`, SystemCapabilities.MANAGE_USERS, 'POST', {}));

export interface WarehouseAccess {
  state: 'reference_required' | 'not_prepared' | 'ready' | 'active' | 'revoked';
  service: { host: string; port: number }; ch_username: string | null;
  credentials_saved: boolean; reference_matches: boolean; cleanup_complete?: boolean;
}
export const warehouseAccessFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse`, SystemCapabilities.READ_USERS));
export const saveWarehouseCredentialsFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, username: z.string().trim().min(1).max(1024), password: z.string().min(1).max(4096).refine(value => !value.includes('\0') && new TextEncoder().encode(value).length <= 4096) }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse/save`, SystemCapabilities.MANAGE_USERS, 'POST', { username: data.username, password: data.password }));
export interface WarehouseCheck { ok: boolean; message: string }
export const checkConnectionFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseCheck> => call(`/api/admin/users/${data.id}/kai-warehouse/check/connection`, SystemCapabilities.MANAGE_USERS, 'POST', {}));
export const checkCredentialsFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, username: z.string().trim().min(1).max(1024), password: z.string().min(1).max(4096) }).strict())
  .handler(async ({ data }): Promise<WarehouseCheck> => call(`/api/admin/users/${data.id}/kai-warehouse/check/credentials`, SystemCapabilities.MANAGE_USERS, 'POST', { username: data.username, password: data.password }));
export const releaseWarehouseFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }): Promise<WarehouseAccess> => call(`/api/admin/users/${data.id}/kai-warehouse/release`, SystemCapabilities.MANAGE_USERS, 'POST', {}));
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
  accounts: { id: string; name: string; email: string; issues: { code: string; at?: string; count?: number; parts?: ('warehouse' | 'chatbot')[] }[] }[];
}
export const problemsFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<Problems> => call('/api/admin/users/kai-problems', SystemCapabilities.READ_USERS));

// Token usage from LibreChat transactions; usd is null until prices are saved under Integrasi.
export interface UsageTotals { prompt: number; cached: number; completion: number; calls: number; usd: number | null }
export interface UsageSummary {
  days: number; since: string; user_count: number; total: UsageTotals; previous: UsageTotals;
  pricing: { priced: boolean; input_usd_per_million: number | null; cached_input_usd_per_million: number | null; output_usd_per_million: number | null; usd_to_idr: number | null };
  users: (UsageTotals & { id: string; name: string | null; email: string | null })[];
  models: (UsageTotals & { model: string })[];
  bucket: 'hour' | 'day';
  daily: (UsageTotals & { day: string })[];
}
const usageDays = z.union([z.literal(1), z.literal(7), z.literal(30), z.literal(90)]);
export const usageFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ days: usageDays }).strict())
  .handler(async ({ data }): Promise<UsageSummary> => call(`/api/admin/users/kai-usage?days=${data.days}`, SystemCapabilities.READ_USERS));

// Data access log from the MCP audit table, 100 rows per page (cursor = opaque token for the next, older page): who called which data tool and the outcome. Never SQL text or result values.
export type AuditOutcome = 'ok' | 'cancelled' | 'error' | 'auth_failed' | 'login_rejected' | 'access_denied' | 'unsafe_sql' | 'query_limit' | 'no_credentials';
export type AuditDays = 1 | 7 | 30 | 90;
export interface AuditRow {
  ts: string; user: string; name: string | null; email: string | null; request_id: string | null; tool: string | null; ch_user: string | null;
  database: string | null; outcome: AuditOutcome; error_code: string | null; rows: number | null; truncated: boolean | null; duration_ms: number | null;
}
export interface AuditLog {
  days: AuditDays; available: boolean; next_cursor: string | null; total: number; counts: Partial<Record<AuditOutcome, number>>; tools: string[];
  users: { id: string; name: string | null; email: string | null }[]; rows: AuditRow[];
}
export interface AuditFilter { days: AuditDays; user?: string; outcome?: AuditOutcome; tool?: string }
const auditOutcome = z.enum(['ok', 'cancelled', 'error', 'auth_failed', 'login_rejected', 'access_denied', 'unsafe_sql', 'query_limit', 'no_credentials']);
export const auditLogFn = createServerFn({ method: 'GET' })
  .inputValidator(z.object({ days: usageDays, user: id.optional(), outcome: auditOutcome.optional(), tool: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/).optional(), cursor: z.string().regex(/^[A-Za-z0-9_-]{1,32}$/).optional() }).strict())
  .handler(async ({ data }): Promise<AuditLog> => {
    const query = new URLSearchParams(Object.entries(data).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
    return call(`/api/admin/users/kai-audit?${query}`, SystemCapabilities.READ_USERS);
  });

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
export type SettingsSectionName = 'batch_model' | 'limits' | 'smtp' | 'pricing';
export interface SectionState {
  source: 'ui' | 'env' | 'none'; settings: Record<string, string | number | boolean>; secret_set?: boolean;
  storage_available: boolean; updated_at: string | null; updated_by: string | null;
}
const sectionName = z.enum(['batch_model', 'limits', 'smtp', 'pricing']);
const sectionForm = z.object({
  name: sectionName,
  values: z.record(z.string().max(64), z.union([z.string().max(8192), z.number().int().min(0).max(1e12), z.boolean()])),
}).strict();
export const settingsSectionFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ name: sectionName }).strict())
  .handler(async ({ data }): Promise<SectionState> => call(`/api/admin/users/kai-integrations/${data.name}`, SystemCapabilities.READ_CONFIGS));
export const saveSettingsSectionFn = createServerFn({ method: 'POST' }).inputValidator(sectionForm)
  .handler(async ({ data }): Promise<SectionState> => call(`/api/admin/users/kai-integrations/${data.name}`, SystemCapabilities.MANAGE_CONFIGS, 'PUT', data.values));
export const testSmtpFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ to: z.string().trim().email().max(254) }).strict())
  .handler(async ({ data }): Promise<{ sent: boolean }> => call('/api/admin/users/kai-integrations/smtp/test', SystemCapabilities.MANAGE_CONFIGS, 'POST', data));

// Semantic layer (metrics and verified queries the CH agent reuses). Drafts come from the agent or this panel;
// only approved ones reach the agent. The MCP validates SQL safety and names; these limits only bound the request.
export type SemanticKind = 'metric' | 'verified_query';
export type SemanticStatus = 'draft' | 'approved' | 'rejected';
export interface SemanticDefinition {
  definition_id: string; kind: SemanticKind; name: string; tables: string[]; status: SemanticStatus; source: 'ai' | 'manual' | 'catalog';
  definition: { description?: string; synonyms?: string[]; expression?: string; metric_type?: string; unit?: string; time_column?: string; dimensions?: string[]; filter?: string; question?: string; sql?: string };
  external_id: string | null; proposed_by: string | null; updated_by: string | null; created_at: string | null; updated_at: string | null;
}
const words = z.array(z.string().max(128)).max(30);
const semanticForm = z.object({
  kind: z.enum(['metric', 'verified_query']), name: z.string().max(64), status: z.enum(['draft', 'approved', 'rejected']),
  description: z.string().max(500), synonyms: words, table: z.string().max(260), expression: z.string().max(1000),
  metric_type: z.string().max(32), unit: z.string().max(40), time_column: z.string().max(128), dimensions: words,
  filter: z.string().max(500), question: z.string().max(500), sql: z.string().max(4000),
}).strict();
export type SemanticForm = z.infer<typeof semanticForm>;
const definitionId = z.string().uuid();
// Tables are paged on the server: page/size/sort travel with the filters; totals cover every match.
export const PAGE_SIZES = [10, 25, 50, 100] as const;
export type PageSize = (typeof PAGE_SIZES)[number];
export interface SemanticQuery {
  status?: SemanticStatus; kind?: SemanticKind; source?: 'ai' | 'manual' | 'catalog'; database?: string; table?: string; q?: string;
  sort?: 'name' | 'updated_at' | 'kind' | 'status' | 'table'; dir?: 'asc' | 'desc'; page?: number; size?: PageSize;
}
export interface SemanticPage { definitions: SemanticDefinition[]; total: number; counts: Partial<Record<SemanticStatus, number>>; storage_available: boolean }
const pageSize = z.union([z.literal(10), z.literal(25), z.literal(50), z.literal(100)]);
const ref = z.string().regex(/^[A-Za-z0-9_-]{1,128}(\.[A-Za-z0-9_-]{1,128})?$/);
const semanticFilter = z.object({
  status: z.enum(['draft', 'approved', 'rejected']).optional(), kind: z.enum(['metric', 'verified_query']).optional(),
  source: z.enum(['ai', 'manual', 'catalog']).optional(), database: ref.optional(), table: ref.optional(), q: z.string().max(100).optional(),
}).strict();
const semanticQuery = semanticFilter.extend({
  sort: z.enum(['name', 'updated_at', 'kind', 'status', 'table']).optional(), dir: z.enum(['asc', 'desc']).optional(),
  page: z.number().int().min(1).max(100000).optional(), size: pageSize.optional(),
}).strict();
const search = (data: object) => new URLSearchParams(Object.entries(data).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString();
export const semanticListFn = createServerFn({ method: 'GET' }).inputValidator(semanticQuery)
  .handler(async ({ data }): Promise<SemanticPage> => call(`/api/admin/users/kai-semantic?${search(data)}`, SystemCapabilities.READ_CONFIGS));
/** Approve/reject in one request: the ids on screen, or every definition matching the current filter. */
export const bulkSemanticFn = createServerFn({ method: 'POST' })
  .inputValidator(z.union([
    z.object({ status: z.enum(['approved', 'rejected', 'draft']), ids: z.array(z.string().uuid()).min(1).max(1000) }).strict(),
    z.object({ status: z.enum(['approved', 'rejected', 'draft']), filter: semanticFilter }).strict(),
  ]))
  .handler(async ({ data }): Promise<{ changed: number }> => call('/api/admin/users/kai-semantic/bulk', SystemCapabilities.MANAGE_CONFIGS, 'POST', data));
export type TableStatus = 'none' | 'draft' | 'partial' | 'approved' | 'rejected';
export interface SemanticTable { name: string; ref: string; comment: string; rows: number | null; metrics: number; queries: number; drafts: number; approved: number; rejected: number; status: TableStatus }
export interface SemanticTableQuery { database: string; status?: TableStatus; q?: string; sort?: 'name' | 'metrics' | 'queries' | 'drafts' | 'status' | 'rows'; dir?: 'asc' | 'desc'; page?: number; size?: PageSize }
export interface SemanticTablePage { database: string; tables: SemanticTable[]; total: number; counts: Record<TableStatus, number> }
export const semanticTablesFn = createServerFn({ method: 'GET' })
  .inputValidator(z.object({
    database: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/), status: z.enum(['none', 'draft', 'partial', 'approved', 'rejected']).optional(),
    q: z.string().max(100).optional(), sort: z.enum(['name', 'metrics', 'queries', 'drafts', 'status', 'rows']).optional(),
    dir: z.enum(['asc', 'desc']).optional(), page: z.number().int().min(1).max(100000).optional(), size: pageSize.optional(),
  }).strict())
  .handler(async ({ data }): Promise<SemanticTablePage> => call(`/api/admin/users/kai-semantic/tables?${search(data)}`, SystemCapabilities.MANAGE_CONFIGS));
export const createSemanticFn = createServerFn({ method: 'POST' }).inputValidator(semanticForm)
  .handler(async ({ data }): Promise<SemanticDefinition> => call('/api/admin/users/kai-semantic', SystemCapabilities.MANAGE_CONFIGS, 'POST', data));
export const updateSemanticFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id: definitionId, form: semanticForm }).strict())
  .handler(async ({ data }): Promise<SemanticDefinition> => call(`/api/admin/users/kai-semantic/${data.id}`, SystemCapabilities.MANAGE_CONFIGS, 'PUT', data.form));
export const deleteSemanticFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id: definitionId }).strict())
  .handler(async ({ data }): Promise<SemanticDefinition> => call(`/api/admin/users/kai-semantic/${data.id}`, SystemCapabilities.MANAGE_CONFIGS, 'DELETE', {}));

// AI draft job (local model on the server). Status is readable with read:configs; start/stop need manage:configs.
export type DraftModelStatus = 'ready' | 'no_model' | 'loading' | 'failed' | 'unavailable' | 'not_configured';
export type QueueStatus = 'waiting' | 'running' | 'done' | 'failed' | 'cancelled';
export interface SemanticQueueEntry {
  database: string; status: QueueStatus; tables_total: number | null; tables_existing: number | null; tables_todo: number; tables_wide: number;
  tables_done: number; drafts_saved: number; drafts_skipped: number; message: string | null;
}
export interface SemanticJob {
  id: string; status: 'running' | 'done' | 'failed' | 'cancelled'; started_by: string; started_at: string; finished_at: string | null;
  phase: string | null; current: string[]; current_database: string | null; group_started_at: string | null; queue: SemanticQueueEntry[];
  tables_total: number; tables_done: number; drafts_saved: number; drafts_skipped: number; issues: { item: string; reason: string }[];
  seconds_per_table: number | null; eta_seconds: number | null; message: string | null; model: string;
}
export interface SemanticDraftState { job: SemanticJob | null; model: { status: DraftModelStatus; name: string } }
export interface SemanticDatabase { name: string; tables: number; covered: number }
export const semanticDraftsFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<SemanticDraftState> => call('/api/admin/users/kai-semantic/drafts', SystemCapabilities.READ_CONFIGS));
export const startSemanticDraftsFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ databases: z.array(z.string().min(1).max(128)).min(1).max(20) }).strict())
  .handler(async ({ data }): Promise<SemanticDraftState> => call('/api/admin/users/kai-semantic/drafts', SystemCapabilities.MANAGE_CONFIGS, 'POST', data));
export const stopSemanticDraftsFn = createServerFn({ method: 'POST' })
  .handler(async (): Promise<SemanticDraftState> => call('/api/admin/users/kai-semantic/drafts', SystemCapabilities.MANAGE_CONFIGS, 'DELETE', {}));
export const semanticDatabasesFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<{ databases: SemanticDatabase[] }> => call('/api/admin/users/kai-semantic/databases', SystemCapabilities.MANAGE_CONFIGS));

// Local model manager: curated catalog, checksum-verified downloads (pause/resume), activate, delete.
export interface LocalModel {
  id: string; label: string; size: number; ram_gb: number | null; note: string; source: 'catalog' | 'manual'; installed: boolean;
  partial_bytes: number; active: boolean; router_status: string | null; load_progress: number | null;
}
export interface LocalModelDownload {
  model: string; label: string; status: 'running' | 'paused' | 'done' | 'failed'; phase: string | null; bytes: number; total: number;
  speed: number | null; eta_seconds: number | null; message: string | null;
}
export interface LocalModelState {
  models: LocalModel[]; active: string | null; router: 'ready' | 'unavailable' | 'not_configured'; download: LocalModelDownload | null;
  disk: { free: number; total: number } | null; message: string | null;
}
export const localModelsFn = createServerFn({ method: 'GET' })
  .handler(async (): Promise<LocalModelState> => call('/api/admin/users/kai-semantic/models', SystemCapabilities.READ_CONFIGS));
export const localModelActionFn = createServerFn({ method: 'POST' })
  .inputValidator(z.object({ action: z.enum(['download', 'pause', 'activate', 'delete']), model: z.string().max(128).optional() }).strict())
  .handler(async ({ data }): Promise<LocalModelState> => call(`/api/admin/users/kai-semantic/models/${data.action}`, SystemCapabilities.MANAGE_CONFIGS, 'POST', { model: data.model }));
