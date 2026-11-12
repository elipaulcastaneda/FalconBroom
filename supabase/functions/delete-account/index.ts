import { createClient } from 'npm:@supabase/supabase-js@2.112.4'
import { handleDeletionPreflight, supabaseConfig, supabaseDependencies } from './core.mjs'

Deno.serve(async (request: Request) => {
  let config
  try {
    config = supabaseConfig({
      SUPABASE_URL: Deno.env.get('SUPABASE_URL'),
      SUPABASE_PUBLISHABLE_KEYS: Deno.env.get('SUPABASE_PUBLISHABLE_KEYS'),
      SUPABASE_SECRET_KEYS: Deno.env.get('SUPABASE_SECRET_KEYS'),
      SUPABASE_ANON_KEY: Deno.env.get('SUPABASE_ANON_KEY'),
      SUPABASE_SERVICE_ROLE_KEY: Deno.env.get('SUPABASE_SERVICE_ROLE_KEY'),
    })
  } catch {
    console.error('Account deletion preflight is not configured')
    return Response.json({ error: 'Preflight unavailable. No deletion was performed.' }, {
      status: 503,
      headers: { 'Cache-Control': 'no-store' },
    })
  }

  const deps = supabaseDependencies(createClient, config.url, config.anonKey, config.serviceKey, message => console.error(message))
  return handleDeletionPreflight(request, deps)
})