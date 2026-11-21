import { getActor, getReconToken } from './identity'
import type { Actor, ReconConfig } from './types'

export type Need = 'name' | 'token'
export type Access =
  | { kind: 'ready'; actor: Actor }
  | { kind: 'off' }
  | { kind: 'setup'; needs: Need[] }

/**
 * May this device write right now? `unconfigured`: the server has no write token, so actions are off.
 * `token` (also assumed until the config is known): needs a name and a stored token. `open`: needs only a name.
 */
export function checkAccess(config: ReconConfig | undefined): Access {
  const auth = config?.write_auth ?? 'token'
  if (auth === 'unconfigured') return { kind: 'off' }
  const actor = getActor()
  const needs: Need[] = [...(actor ? [] : ['name' as const]), ...(auth === 'token' && !getReconToken() ? ['token' as const] : [])]
  return actor && needs.length === 0 ? { kind: 'ready', actor } : { kind: 'setup', needs }
}
