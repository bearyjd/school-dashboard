export interface Item {
  id: number;
  child: string;
  title: string;
  type: string;
  source: string;
  due_date: string | null;
  notes: string | null;
  completed: boolean;
  created_at: string;
}

export interface IxlSubject {
  subject: string;
  remaining: number;
  assigned: number;
  done: number;
}

export interface Dashboard {
  schoology: Record<string, Array<{ title: string; course: string; due_date: string; status: string; url: string }>>;
  ixl: Record<string, IxlSubject[]>;
  email_items: Array<{ id: string; child: string; summary: string; due_iso: string | null; due_raw: string }>;
  last_updated: string;
}

export interface SyncStatus {
  running: boolean;
  last_run: string | null;
  last_result: string | null;
  last_sources: string[];
  last_error: string | null;
}

export interface SyncMeta {
  ixl?: { last_run: string; last_result: string };
  sgy?: { last_run: string; last_result: string };
  gc?: { last_run: string; last_result: string };
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
