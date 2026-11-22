import { ApiError, apiFetch } from '../api'
import { getReconToken } from './identity'
import type {
  Actor, Envelope, ListData, NoteDraft, NoteRow, ReconDetail, ReconItem, ReconSummary, StageAction, WriteResult,
} from './types'

const DEFAULT_TOKEN_HEADER = 'X-Recon-Token'
const HEADER_NAME = /^[A-Za-z0-9-]{1,64}$/
let tokenHeader = DEFAULT_TOKEN_HEADER

/** The header name the server wants the write token in (`config.write_token_header`); anything odd means the default. */
export function setWriteTokenHeader(name: string | undefined): void {
  tokenHeader = name && HEADER_NAME.test(name) ? name : DEFAULT_TOKEN_HEADER
}
const MAX_RETRY_S = 5
const DEFAULT_RETRY_S = 2

const isWrite = (method: string): boolean => !['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())
const isV2 = (path: string): boolean => path === '/api/v2' || path.startsWith('/api/v2/')

/**
 * Request headers for `path`. The write token is added only to same-origin `/api/v2` requests that change data;
 * never to reads, to the v1 routes, or to another origin.
 */
export function reconHeaders(path: string, method: string, base: Record<string, string> = {}): Record<string, string> {
  const token = getReconToken()
  const write = isWrite(method)
  return token && write && isV2(path) ? { ...base, [tokenHeader]: token } : { ...base }
}

async function envelope<T>(path: string, method = 'GET', body?: unknown): Promise<Envelope<T>> {
  const json: Record<string, string> = body === undefined ? {} : { 'Content-Type': 'application/json' }
  const out = await apiFetch<Envelope<T>>(path, {
    method,
    headers: reconHeaders(path, method, json),
    // A write must never follow a redirect: the token header would travel with it.
    ...(isV2(path) && isWrite(method) ? { redirect: 'error' as const } : {}),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!out || out.ok !== true || out.data === null || out.data === undefined) {
    throw new ApiError(out?.error ?? 'bad_response', 502)
  }
  return out
}

const data = async <T>(path: string, method = 'GET', body?: unknown): Promise<T> => (await envelope<T>(path, method, body)).data as T

const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms))

/** A write; when the server says "busy" (503) it is retried once after Retry-After (at most 5 s). */
async function write<T>(path: string, method: string, body: unknown): Promise<T> {
  try {
    return await data<T>(path, method, body)
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 503 || e.message !== 'busy') throw e
    await sleep(Math.min(e.retryAfter ?? DEFAULT_RETRY_S, MAX_RETRY_S) * 1000)
    return data<T>(path, method, body)
  }
}

export const getConfig = (): Promise<unknown> => data<unknown>('/api/v2/config')
export const getSummary = (): Promise<ReconSummary> => data<ReconSummary>('/api/v2/summary')

export const LIST_LIMIT = 500

async function getAssignmentList(filters?: Record<string, string>): Promise<ListData> {
  const params = new URLSearchParams({ limit: String(LIST_LIMIT), ...filters })
  const out = await envelope<ReconItem[]>(`/api/v2/assignments?${params}`)
  const total = Number(out.meta.total)
  return { items: out.data as ReconItem[], total: Number.isFinite(total) ? total : (out.data as ReconItem[]).length }
}

export const getList = (): Promise<ListData> => getAssignmentList()
export const getDismissedList = (kid: string, offset: number): Promise<ListData> =>
  getAssignmentList({ dismissed_only: '1', offset: String(offset), ...(kid ? { kid } : {}) })

export const getDetail = (id: number): Promise<ReconDetail> => data<ReconDetail>(`/api/v2/assignments/${id}`)

const base = (id: number) => `/api/v2/assignments/${id}`

export const postStage = (id: number, action: StageAction, actor: Actor, expectedDismissedAt?: string): Promise<WriteResult> =>
  write<WriteResult>(`${base(id)}/stage`, 'POST', { action, actor, expected_dismissed_at: expectedDismissedAt })

export const postTeacherConfirmed = (id: number, note: string, actor: Actor): Promise<WriteResult> =>
  write<WriteResult>(`${base(id)}/teacher-confirmed`, 'POST', { note, actor })

export const postNote = (id: number, actor: Actor): Promise<NoteDraft> =>
  write<NoteDraft>(`${base(id)}/note`, 'POST', { actor })

export const patchNote = (id: number, noteId: number, status: 'sent_marked' | 'discarded', actor: Actor): Promise<{ note: NoteRow }> =>
  write<{ note: NoteRow }>(`${base(id)}/note/${noteId}`, 'PATCH', { status, actor })
