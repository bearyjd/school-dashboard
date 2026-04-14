import type { Item, Dashboard, SyncStatus, SyncMeta, ChatMessage } from './types';

const BASE = '';  // same origin — Flask serves both SPA and API

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error((err as { error?: string }).error || res.statusText);
  }
  return res.json() as Promise<T>;
}

// Items
export const getItems = (child?: string, includeCompleted = false) =>
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
export const getDashboard = () => apiFetch<Dashboard>('/api/dashboard');

// Sync
export const getSyncStatus = () => apiFetch<SyncStatus>('/api/sync/status');

export const getSyncMeta = () => apiFetch<SyncMeta>('/api/sync/meta');

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
