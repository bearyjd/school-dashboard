import { expect, test } from 'vitest'
import { safeHttpUrl } from './safeUrl'

test.each([
  ['https://portal.example.org/a?x=1', 'https://portal.example.org/a?x=1'],
  ['http://portal.example.org/', 'http://portal.example.org/'],
])('%s is allowed', (input, out) => {
  expect(safeHttpUrl(input)).toBe(out)
})

test.each([
  'javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' javascript:alert(1)', 'data:text/html,<script>1</script>',
  'vbscript:x', 'file:///etc/passwd', 'ftp://x.example/', '//evil.example/x', '/relative/path', 'not a url',
  'https://user:pw@portal.example.org/', '', null, undefined,
])('%s is rejected', input => {
  expect(safeHttpUrl(input)).toBeNull()
})
