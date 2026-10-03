import { BACKEND } from '../config'
import supabase from '../supabaseClient'
import tokenStore from '../tokenStore'

export default async function authFetch(url, opts = {}) {
  if (!opts.headers) opts.headers = {}
  // Ensure cookies are sent for server refresh flow
  if (!opts.credentials) opts.credentials = 'include'

  try {
    const { data: session } = await supabase.auth.getSession()
    const accessFromStore = await tokenStore.getAccess()
    const access = session?.session?.access_token || accessFromStore
    if (access) {
      opts.headers['Authorization'] = `Bearer ${access}`
    }
  } catch (e) {
    // ignore, proceed without header
  }

  let res = await fetch(url, opts)
  if (res.status !== 401) return res
  // Attempt to refresh local supabase session and retry once.
  // Use a global single-flight promise to avoid concurrent refresh calls
  try {
    // Short-circuit if we recently failed a refresh to avoid tight retry loops.
    // Use exponential backoff based on recent failure count.
    const failures = window.__fb_refresh_failures || 0
    const baseMs = 2000
    const COOLDOWN_MS = Math.min(60000, baseMs * Math.pow(2, Math.max(0, failures - 1)))
    if (window.__fb_last_failed_refresh && (Date.now() - window.__fb_last_failed_refresh) < COOLDOWN_MS) {
      return res
    }

    // emit refresh-started event for UI and perform both supabase and backend refresh attempts
    try {
      try { window.dispatchEvent(new Event('fb_refresh_started')) } catch(e){}

      if (!window.__fb_refresh_promise) {
        window.__fb_refresh_promise = (async () => {
          try {
            const { data: refreshed, error } = await supabase.auth.refreshSession()
            return { refreshed, error }
          } catch (e) {
            return { refreshed: null, error: e }
          }
        })()
      }

      const { refreshed, error } = await window.__fb_refresh_promise
      // clear the global promise so future 401s can trigger a new refresh attempt
      window.__fb_refresh_promise = null

      if (!error && refreshed?.session?.access_token) {
        const newAccess = refreshed.session.access_token
        try { await tokenStore.setAccess(newAccess) } catch(e){}
        // if refresh token rotation provided, persist refresh too
        try { if (refreshed.session.refresh_token) await tokenStore.setRefresh(refreshed.session.refresh_token) } catch(e){}
        // reset failure counters on success
        window.__fb_refresh_failures = 0
        window.__fb_last_failed_refresh = null
        opts.headers['Authorization'] = `Bearer ${newAccess}`
        return await fetch(url, opts)
      }

      // If Supabase refresh didn't produce a new access token, attempt backend /refresh
      // using a dev-friendly refresh token stored in localStorage (fallback).
      try {
        const devRefresh = await tokenStore.getRefresh()
        if (devRefresh) {
          const backendBase = (BACKEND === '/api' ? 'http://127.0.0.1:3009' : BACKEND)
          const r = await fetch(`${backendBase}/refresh`, { method: 'POST', headers: { 'Authorization': `Bearer ${devRefresh}`, 'Content-Type': 'application/json' }, credentials: 'include' })
          if (r && r.ok) {
            try {
              const j = await r.json()
              if (j && j.access_token) {
                const newAccess = j.access_token
                try { await tokenStore.setAccess(newAccess) } catch(e){}
                try { if (j.refresh_token) await tokenStore.setRefresh(j.refresh_token) } catch(e){}
                window.__fb_refresh_failures = 0
                window.__fb_last_failed_refresh = null
                opts.headers['Authorization'] = `Bearer ${newAccess}`
                try { window.dispatchEvent(new CustomEvent('fb_backend_refresh_success', { detail: { source: 'backend' } })) } catch(e){}
                return await fetch(url, opts)
              }
            } catch (e) {}
            // if backend returned non-json or no access token, emit failure
            try { window.dispatchEvent(new CustomEvent('fb_backend_refresh_failure', { detail: { status: r.status } })) } catch(e){}
            // clear stale dev refresh tokens on 400/401 responses to avoid repeated failing attempts
            try {
              if (r && (r.status === 400 || r.status === 401)) {
                await tokenStore.removeRefresh()
              }
            } catch (e) {}
          }
        }
      } catch (e) {
        // ignore backend refresh errors
      }
    } finally {
      // ensure UI is notified that refresh attempts finished
      try { window.dispatchEvent(new Event('fb_refresh_finished')) } catch(e){}
    }

    // record failed refresh time and increment failure counter for backoff
    window.__fb_last_failed_refresh = Date.now()
    window.__fb_refresh_failures = (window.__fb_refresh_failures || 0) + 1
  } catch (e) {
    // On unexpected error, record a failed refresh time and bump failures
    try {
      window.__fb_last_failed_refresh = Date.now()
      window.__fb_refresh_failures = (window.__fb_refresh_failures || 0) + 1
    } catch(_) {}
  }

  return res
}
