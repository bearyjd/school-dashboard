/** Shapes returned by /api/v2 (web/api_v2.py + school_dashboard/assignments/readmodel.py). All inside `{ok, data, error, meta}`. */

export type ReconKind = 'assignment' | 'task' | 'practice'
export type ReconFlag = 'disputed' | 'missing' | 'done_not_received' | 'waiting_on_teacher' | 'do_tonight' | 'upcoming'
export type ReconBucket = 'needs_action' | 'do_tonight' | 'waiting_on_teacher'
export type StageAction = 'done' | 'turned_in' | 'undo'
export type Role = 'parent' | 'kid'
export type WriteAuth = 'open' | 'token' | 'unconfigured'

export interface Envelope<T> {
  ok: boolean
  data: T | null
  error: string | null
  meta: Record<string, unknown>
}

export interface ReconLink {
  id: number
  source: string
  /** Normalised portal status of this link (e.g. missing, submitted, graded, late). */
  status: string
  raw_status: string | null
  score: string | null
  live: boolean
  /** Already filtered by the server (`safe_url`); the client checks the scheme again before linking. */
  url: string | null
}

export interface ReconItem {
  id: number
  kid: string
  title: string
  class: string | null
  due: string | null
  kind: ReconKind
  stage: number
  stage_label: string
  flag: ReconFlag | null
  bucket: ReconBucket | null
  /** late | cleared_unconfirmed | unconfirmed_paper | conflict | no_due_date | data_problem */
  info: string[]
  unverified: boolean
  unverified_sources: string[]
  portal_status: string | null
  conflict: boolean
  received_evidence: string | null
  possible_duplicate: boolean
  links: ReconLink[]
}

export interface NeedsActionDetail {
  done_not_received: number
  disputed: number
  missing: number
}

export interface Counts {
  do_tonight: number
  waiting_on_teacher: number
  needs_action: number
  needs_action_detail: NeedsActionDetail
}

export interface ReconKidSummary {
  name: string
  counts: Counts
  practice: ReconItem[]
  all_clear: boolean
  all_clear_reason: 'unverified' | null
}

export interface ReconSummary {
  kids: ReconKidSummary[]
  data_as_of: string | null
  ingest_ok: boolean
  unverified_sources: string[]
  warnings: string[]
  generated_at: string
}

export interface ReconConfig {
  enabled: boolean
  migrated: boolean
  kids: { name: string; color: string }[]
  grace_default: number
  write_auth: WriteAuth
  write_token_header: string
  write_token_min_length: number
}

export interface ReconEvent {
  at: string
  setter: string
  actor: string | null
  kind: string
  from: string | null
  to: string | null
  detail: string | null
}

export interface NoteRow {
  id: number
  flag: string
  status: 'draft' | 'sent_marked' | 'discarded'
  created_at: string
  actor: string
}

export interface ReconDetail extends ReconItem {
  events: ReconEvent[]
  notes: NoteRow[]
}

export interface NoteDraft {
  id: number
  flag: string
  subject: string
  body: string
  teacher_email: string | null
  mailto: string | null
  portal_url: string | null
}

export interface Actor {
  role: Role
  name: string
}

/** Server reply to a stage tap or a teacher confirmation. */
export interface WriteResult {
  item: ReconItem
  counts: Counts
}

export interface ListData {
  items: ReconItem[]
  total: number
}
