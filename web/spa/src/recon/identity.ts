import { createSlot, type SaveResult } from '../lib/storageSlot'
import type { Actor } from './types'

/**
 * Who is using this device, and the write token for /api/v2. The token is a server secret the server never sends
 * to the browser: it lives only in this browser's storage (same helper as the sync token), is sent only as the
 * X-Recon-Token header on /api/v2 writes, and is never logged, put in a URL or rendered back.
 */
export const ACTOR_KEY = 'school-actor'
export const RECON_TOKEN_KEY = 'school-recon-token'
export const ACTOR_NAME_MAX = 40
export const DEFAULT_TOKEN_MIN = 32

const tokenSlot = createSlot(RECON_TOKEN_KEY)
const actorSlot = createSlot(ACTOR_KEY)

export const getReconToken = (): string => tokenSlot.get()
export const setReconToken = (value: string): SaveResult => tokenSlot.set(value)
export const clearReconToken = (): void => tokenSlot.clear()

/** A fixed mask: it says a token exists without revealing any of it. */
export const maskedToken = (): string => (getReconToken() ? '••••••••' : '')

export function validateActorName(raw: string): string | null {
  const name = raw.trim()
  return name.length >= 1 && name.length <= ACTOR_NAME_MAX ? null : `Name must be 1-${ACTOR_NAME_MAX} characters`
}

export function getActor(): Actor | null {
  try {
    const parsed: unknown = JSON.parse(actorSlot.get() || 'null')
    if (typeof parsed !== 'object' || parsed === null) return null
    const { name, role } = parsed as Record<string, unknown>
    if (typeof name !== 'string' || validateActorName(name) !== null) return null
    if (role !== 'parent' && role !== 'kid') return null
    return { name: name.trim(), role }
  } catch {
    return null
  }
}

export function setActor(actor: Actor): SaveResult {
  return actorSlot.set(JSON.stringify({ name: actor.name.trim(), role: actor.role }))
}

export const clearActor = (): void => actorSlot.clear()
