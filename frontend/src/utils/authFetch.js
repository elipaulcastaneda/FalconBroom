import { BACKEND } from '../config'

export default async function authFetch(url, opts = {}) {
  let access = (typeof window !== 'undefined') ? window.localStorage.getItem('falconbroom_access_token') : null
  if (!opts.headers) opts.headers = {}
  if (access) opts.headers['Authorization'] = `Bearer ${access}`
  // Ensure cookies are sent for refresh flow
  if (!opts.credentials) opts.credentials = 'include'
  let res = await fetch(url, opts)
  if (res.status !== 401) return res

  // Attempt silent refresh via httpOnly cookie
  try {
    const rres = await fetch(`${BACKEND}/refresh`, { method: 'POST', credentials: 'include' })
    if (!rres.ok) return res
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
