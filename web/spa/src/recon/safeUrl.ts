/** The URL when it is plain http(s) without credentials, else null. The server filters too; this is the client re-check. */
export function safeHttpUrl(raw: string | null | undefined): string | null {
  if (!raw) return null
  try {
    const u = new URL(raw)
    return (u.protocol === 'http:' || u.protocol === 'https:') && !u.username && !u.password ? u.href : null
  } catch {
    return null
  }
}

/** A `mailto:` link without whitespace or control characters, else null. */
export function safeMailto(raw: string | null | undefined): string | null {
  if (!raw || raw.length <= 7 || !/^mailto:/i.test(raw)) return null
  for (const ch of raw) {
    const code = ch.charCodeAt(0)
    if (code <= 32 || code === 127) return null
  }
  return raw
}
