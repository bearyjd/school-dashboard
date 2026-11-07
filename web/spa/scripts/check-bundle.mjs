// Fails when the gzipped JS or CSS in dist/assets exceeds the app-page budget.
import { readdirSync, readFileSync } from 'node:fs'
import { extname, join } from 'node:path'
import { gzipSync } from 'node:zlib'

const BUDGET_BYTES = { '.js': 300 * 1024, '.css': 50 * 1024 }
const dir = new URL('../dist/assets', import.meta.url).pathname

const totals = { '.js': 0, '.css': 0 }
for (const name of readdirSync(dir)) {
  const ext = extname(name)
  if (ext in totals) totals[ext] += gzipSync(readFileSync(join(dir, name))).length
}

let failed = false
for (const [ext, total] of Object.entries(totals)) {
  const ok = total <= BUDGET_BYTES[ext]
  failed ||= !ok
  console.log(`${ext}: ${(total / 1024).toFixed(1)} kB gzipped / ${BUDGET_BYTES[ext] / 1024} kB ${ok ? 'ok' : 'OVER BUDGET'}`)
}
if (failed) process.exit(1)
console.log('within budget')
