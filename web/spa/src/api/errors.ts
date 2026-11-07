/**
 * `error` codes in non-2xx API replies that the UI tells apart.
 * ERROR_MAINTENANCE: the Flask maintenance gate (web/app.py). ERROR_OFFLINE: the service worker's synthetic 503
 * (public/sw.js hard-codes the same string; src/sw.test.ts checks it against this constant).
 */
export const ERROR_MAINTENANCE = 'maintenance'
export const ERROR_OFFLINE = 'offline'
