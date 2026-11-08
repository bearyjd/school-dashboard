import type { Overview, Item, Dashboard, SyncStatus, SyncMeta, ChatMessage, Digest, InlineAgentResponse } from './types';

const BASE = '';  // same origin — Flask serves both SPA and API

/** A non-2xx API reply; `status` lets callers tell e.g. maintenance (503) from other failures. */
export class ApiError extends Error {
  readonly status: number;
  /** Seconds from a Retry-After header (429), when the server sent one. */
  readonly retryAfter?: number;
  constructor(message: string, status: number, retryAfter?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    const retry = Number.parseInt(res.headers?.get('Retry-After') ?? '', 10);
    throw new ApiError((err as { error?: string }).error || res.statusText, res.status, Number.isFinite(retry) ? retry : undefined);
  }
  return res.json() as Promise<T>;
}

// Items
export const fetchItems = (child?: string, includeCompleted = false) =>
  apiFetch<{ items: Item[] }>(
    `/api/items?${child ? `child=${encodeURIComponent(child)}&` : ''}include_completed=${includeCompleted ? 1 : 0}`
  );

export const createItem = (data: Partial<Item>) =>
  apiFetch<Item>('/api/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const updateItem = (id: number, data: Partial<Item>) =>
  apiFetch<{ ok: boolean }>(`/api/items/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const deleteItem = (id: number) =>
  apiFetch<{ ok: boolean }>(`/api/items/${id}`, { method: 'DELETE' });

// Dashboard
export const fetchDashboard = () => apiFetch<Dashboard>('/api/dashboard');

export const fetchOverview = (past = false) => apiFetch<Overview>(past ? '/api/overview?past=1' : '/api/overview');

// Sync
export const fetchSyncStatus = () => apiFetch<SyncStatus>('/api/sync/status');

export const fetchSyncMeta = () => apiFetch<SyncMeta>('/api/sync/meta');

export async function fetchDigest(id: string): Promise<Digest> {
  return apiFetch<Digest>(`/api/digest/${id}`);
}

export async function patchDigestCard(id: string, index: number, done: boolean): Promise<void> {
  await apiFetch(`/api/digest/${id}/cards/${index}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ done }),
  });
}

export const triggerSync = (sources: string, digest: string, token: string) =>
  apiFetch<{ started: boolean; sources: string; digest: string }>('/api/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sync-Token': token },
    body: JSON.stringify({ sources, digest }),
  });

// Chat
export const sendChat = (message: string, history: ChatMessage[]) =>
  apiFetch<{ reply: string }>('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history }),
  });

// Inline Agent
export const askInlineAgent = (contextType: string, contextId: string, message: string) =>
  apiFetch<InlineAgentResponse>('/api/agent/inline', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context_type: contextType, context_id: contextId, message }),
  });
