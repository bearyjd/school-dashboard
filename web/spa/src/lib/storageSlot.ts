export type SaveResult = 'saved' | 'tab' | 'empty'

export interface Slot {
  get(): string
  /** 'saved' = in localStorage; 'tab' = storage unavailable, kept in memory until the tab closes. */
  set(value: string): SaveResult
  clear(): void
}

/**
 * One string kept in this browser's localStorage under `key`. When storage is unavailable (private mode) it is kept
 * in memory until the tab closes. Storage errors never throw. Shared by the sync token and the Reconcile settings.
 */
export function createSlot(key: string): Slot {
  let memory = ''
  return {
    get() {
      try {
        const stored = (localStorage.getItem(key) ?? '').trim()
        if (stored) return stored
      } catch {
        // storage unavailable: fall through to the in-memory copy
      }
      return memory
    },
    set(value) {
      const text = value.trim()
      if (!text) return 'empty'
      memory = text
      try {
        localStorage.setItem(key, text)
        return 'saved'
      } catch {
        return 'tab'
      }
    },
    clear() {
      memory = ''
      try {
        localStorage.removeItem(key)
      } catch {
        // storage unavailable: nothing was stored
      }
    },
  }
}
