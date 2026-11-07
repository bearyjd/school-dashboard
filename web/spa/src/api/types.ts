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

export interface RosterChild {
  id: string;
  name: string;
  grade: string;
  school: string;
}

export interface PsAssignment {
  course: string | null;
  title: string | null;
  due: string | null;
  category: string | null;
  score: string | null;
}

export interface PsChild {
  updated?: string;
  missing: PsAssignment[];
  late: PsAssignment[];
  upcoming: PsAssignment[];
}

export interface Dashboard {
  schoology: Record<string, Array<{ title: string; course: string; due_date: string; status: string; url: string }>>;
  ixl: Record<string, IxlSubject[]>;
  powerschool?: Record<string, PsChild>;
  powerschool_warnings?: string[];
  email_items: Array<{ id: string; child: string; summary: string; due_iso: string | null; due_raw: string }>;
  children: RosterChild[];
  has_data: Record<string, { ixl: boolean; sgy: boolean; ps?: boolean }>;
  last_updated: string;
}

export interface SyncStatus {
  running: boolean;
  last_run: string | null;
  last_result: string | null;
  last_sources: string[];
  last_error: string | null;
}

export type SyncResult = 'ok' | 'error' | 'paused' | (string & {});

export interface SyncMeta {
  ixl?: { last_run: string; last_result: SyncResult };
  sgy?: { last_run: string; last_result: SyncResult };
  gc?: { last_run: string; last_result: SyncResult };
  powerschool?: { last_run: string; last_result: SyncResult };
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface DigestCard {
  title: string;
  body: string;
  done: boolean;
  type: string;
}

export interface Digest {
  id: string;
  created_at: string;
  title: string;
  cards: DigestCard[];
}

export type OverviewStatus = 'overdue' | 'missing' | 'late' | 'tonight' | 'upcoming' | 'done';
export type OverviewSource = 'powerschool' | 'schoology' | 'ixl' | 'manual';

export interface OverviewKid {
  name: string;
  colorIndex: number;
}

export interface OverviewItem {
  id: string;
  kid: string;
  title: string;
  course: string;
  due: string | null;
  status: OverviewStatus;
  source: OverviewSource;
  category: string | null;
  score: string | null;
  url: string;
  readonly: boolean;
}

export interface OverviewEvent {
  kids: string[];
  unassigned: boolean;
  title: string;
  date: string;
  time: string;
  type: string;
  location: string;
  source: 'gc' | 'school';
  scheduleSource: 'api' | 'ics' | null;
  team: string | null;
}

export interface Freshness {
  last_ok: string | null;
  last_run: string | null;
  result: string | null;
  issue: string | null;
  stale: boolean;
  failing: boolean;
  age_hours: number | null;
}

export interface Overview {
  generated_at: string;
  kids: OverviewKid[];
  items: OverviewItem[];
  events: OverviewEvent[];
  freshness: Record<string, Freshness>;
  warnings: string[];
}

export interface InlineAgentAction {
  type: 'mark_item_done' | 'reschedule_item' | 'create_item' | 'trigger_sync';
  payload: Record<string, unknown>;
}

export interface InlineAgentResponse {
  reply: string;
  action: InlineAgentAction | null;
}
