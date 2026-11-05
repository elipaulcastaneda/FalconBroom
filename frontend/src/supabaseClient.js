import { createClient } from '@supabase/supabase-js'

// Secrets pasted into CI often carry stray quotes, spaces or newlines.
const clean = (v) => String(v || '').trim().replace(/^["']|["']$/g, '').trim()
const SUPABASE_URL = clean(import.meta.env.VITE_SUPABASE_URL)
const SUPABASE_ANON_KEY = clean(import.meta.env.VITE_SUPABASE_ANON_KEY)

export const supabaseConfigured = /^https?:\/\//.test(SUPABASE_URL) && !!SUPABASE_ANON_KEY

if (!supabaseConfigured) {
  console.warn('VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY is missing or invalid; sign-in is unavailable')
  try { window.__FB_CONFIG_ERROR = 'Sign-in is not configured in this build (the Supabase URL or key was missing when it was built).' } catch (e) {}
}

// A placeholder keeps the app loading when the settings are missing; sign-in then fails with a clear error.
export const supabase = createClient(
  supabaseConfigured ? SUPABASE_URL : 'https://not-configured.invalid',
  supabaseConfigured ? SUPABASE_ANON_KEY : 'not-configured',
)

export default supabase

// Persist refresh token to localStorage when Supabase auth state changes (auto-refresh)
try {
  if (typeof window !== 'undefined' && supabase && supabase.auth && typeof supabase.auth.onAuthStateChange === 'function') {
    supabase.auth.onAuthStateChange((event, session) => {
      try {
        const refresh = (session && (session.refresh_token || (session.session && session.session.refresh_token))) || null
        if (refresh) {
          try { window.localStorage.setItem('falconbroom_refresh_token', refresh) } catch (e) {}
        } else if (event === 'SIGNED_OUT' || event === 'SIGNED_OUT' || event === 'USER_DELETED') {
          try { window.localStorage.removeItem('falconbroom_refresh_token') } catch (e) {}
        }
      } catch (e) {}
    })
  }
} catch (e) {}
