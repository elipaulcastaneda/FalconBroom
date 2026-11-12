import test from 'node:test'
import assert from 'node:assert/strict'
import { createClient } from '@supabase/supabase-js'
import { handleDeletionPreflight, supabaseDependencies } from '../../supabase/functions/delete-account/core.mjs'

test('preflight uses the real Supabase client without revoking the original session', async () => {
  const user = { id: '6a0d1258-0950-4bbb-a829-0d5718c47e9e', email: 'test@example.invalid' }
  const calls = []
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url')
  const temporaryToken = `${encode({ alg: 'none' })}.${encode({ sub: user.id, exp: Math.floor(Date.now() / 1000) + 3600 })}.signature`
  const fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url)
    const headers = new Headers(init?.headers || input.headers)
    calls.push({ path: url.pathname, search: url.search, authorization: headers.get('authorization') })
    if (url.pathname === '/auth/v1/user') {
      return Response.json(user)
    }
    if (url.pathname === '/auth/v1/token') {
      return Response.json({
        access_token: temporaryToken,
        refresh_token: 'temporary-refresh',
        token_type: 'bearer',
        expires_in: 3600,
        user,
      })
    }
    if (url.pathname === '/auth/v1/logout') return new Response(null, { status: 204 })
    if (url.pathname === '/rest/v1/rpc/falconbroom_account_has_shared_data') return Response.json(false)
    throw new Error(`Unexpected request: ${url.pathname}`)
  }
  const clientFactory = (url, key, options) => createClient(url, key, { ...options, global: { fetch } })
  const deps = supabaseDependencies(clientFactory, 'https://example.invalid', 'public-key', 'service-key', () => {})
  const request = new Request('https://example.invalid/preflight', {
    method: 'POST',
    headers: { Authorization: 'Bearer original-session', 'Content-Type': 'application/json' },
    body: JSON.stringify({ password: 'synthetic-password', confirm_text: 'DELETE MY ACCOUNT' }),
  })

  const response = await handleDeletionPreflight(request, deps)
  assert.equal(response.status, 200)
  assert.equal((await response.json()).status, 'preflight_only')
  assert.deepEqual(calls.map(call => call.path), [
    '/auth/v1/user',
    '/auth/v1/token',
    '/auth/v1/logout',
    '/rest/v1/rpc/falconbroom_account_has_shared_data',
  ])
  assert.equal(calls[0].authorization, 'Bearer original-session')
  assert.equal(calls[2].authorization, `Bearer ${temporaryToken}`)
  assert.equal(calls[2].search, '?scope=local')
  assert.equal(calls[3].authorization, 'Bearer service-key')
})
