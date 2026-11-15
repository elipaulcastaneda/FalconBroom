import { createClient } from 'npm:@supabase/supabase-js@2.112.4'
import { supabaseConfig } from './core.mjs'
import { deletionRouter } from './router.mjs'

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

  const logError = (message: string) => console.error(message)
  const route = deletionRouter({
    config,
    enableTestDeletion: Deno.env.get('FALCONBROOM_TEST_ACCOUNT_DELETION'),
    workerSecret: Deno.env.get('FALCONBROOM_DELETION_WORKER_SECRET'),
    createClient,
    logError,
  })
  return route(request)
})