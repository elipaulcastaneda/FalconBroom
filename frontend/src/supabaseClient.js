import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || ''

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  console.warn('VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY not set')
}

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

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
