import { BACKEND } from '../config'

export default async function authFetch(url, opts = {}) {
  let access = (typeof window !== 'undefined') ? window.localStorage.getItem('falconbroom_access_token') : null
  if (!opts.headers) opts.headers = {}
  if (access) opts.headers['Authorization'] = `Bearer ${access}`
  // Ensure cookies are sent for refresh flow
  if (!opts.credentials) opts.credentials = 'include'
  let res = await fetch(url, opts)
  if (res.status !== 401) return res

  // Debug: log that we've received 401 and will attempt refresh
  try { console.debug('authFetch: initial request returned 401, attempting /refresh') } catch (e) {}

  // Attempt silent refresh via httpOnly cookie
  try {
    const rres = await fetch(`${BACKEND}/refresh`, { method: 'POST', credentials: 'include' })
    // Debug: log refresh response status and body for troubleshooting
    let refresh_body_text = null
    try {
      refresh_body_text = await rres.clone().text()
      try { console.debug('authFetch: /refresh status', rres.status, 'body:', refresh_body_text) } catch (e) {}
    } catch (e) {}
    // If server indicates missing cookie, attempt dev fallback using stored refresh token
    if (!rres.ok) {
      try {
        const parsed = refresh_body_text ? JSON.parse(refresh_body_text) : null
        const detail = parsed && parsed.detail ? parsed.detail : null
        if (detail && detail.toLowerCase().includes('missing refresh_token')) {
          try {
            const devRefresh = window.localStorage.getItem('falconbroom_refresh_token')
            if (devRefresh) {
              try { console.debug('authFetch: attempting body-based /refresh fallback with dev refresh token') } catch (e) {}
              const br = await fetch(`${BACKEND}/refresh`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refresh_token: devRefresh }) })
              if (br.ok) {
                const bj = await br.json()
                const newAccess = bj.access_token
                try { window.localStorage.setItem('falconbroom_access_token', newAccess) } catch {}
                if (!opts.headers) opts.headers = {}
                opts.headers['Authorization'] = `Bearer ${newAccess}`
                return await fetch(url, opts)
              }
            }
          } catch (e) {}
        }
      } catch (e) {}
      return res
    }
    const jr = await rres.json()
    const newAccess = jr.access_token
    try { window.localStorage.setItem('falconbroom_access_token', newAccess) } catch {}
    if (!opts.headers) opts.headers = {}
    opts.headers['Authorization'] = `Bearer ${newAccess}`
    return await fetch(url, opts)
  } catch (e) {
    return res
  }
}
