import { z } from 'zod';
import { createServerFn } from '@tanstack/react-start';
import { apiFetch, extractApiError } from './utils/api';
import { requireCapability } from './capabilities';
import { SystemCapabilities } from '@/constants';
export interface AgentAccess {
  configured: boolean; agent: { id: string; name: string; provider: string; model: string; tools: string[] } | null;
  direct_assignment: boolean; effective_access: boolean; inherited_access: boolean;
  binding_active: boolean; can_manage: boolean; can_remove_direct: boolean;
}
const id = z.string().regex(/^[a-f0-9]{24}$/);
async function call(userId: string, body?: { agent_id: string; assigned: boolean }): Promise<AgentAccess> {
  await requireCapability(body ? SystemCapabilities.MANAGE_USERS : SystemCapabilities.READ_USERS);
  if (body) await requireCapability(SystemCapabilities.MANAGE_AGENTS);
  const response = await apiFetch(`/api/admin/users/${userId}/kai-agents`, { method: body ? 'POST' : 'GET', ...(body ? { body: JSON.stringify(body) } : {}) });
  if (!response.ok) await extractApiError(response, 'Akses chatbot CH tidak tersedia.');
  return response.json() as Promise<AgentAccess>;
}
export const agentAccessFn = createServerFn({ method: 'GET' }).inputValidator(z.object({ id }).strict())
  .handler(async ({ data }) => call(data.id));
export const assignAgentFn = createServerFn({ method: 'POST' }).inputValidator(z.object({ id, agent_id: z.string().min(1).max(100), assigned: z.boolean() }).strict())
  .handler(async ({ data }) => call(data.id, { agent_id: data.agent_id, assigned: data.assigned }));
