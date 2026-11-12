import test from 'node:test'
import assert from 'node:assert/strict'
import { handleDeletionPreflight, supabaseConfig, supabaseDependencies } from './core.mjs'

const userId = '6a0d1258-0950-4bbb-a829-0d5718c47e9e'

function request(body, token = 'session-token') {
  return new Request('https://example.invalid/preflight', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function dependencies(overrides = {}) {
  const calls = []
  return {
    calls,
    getUser: async token => {
      calls.push(['getUser', token])
      return { id: userId, email: 'test@example.invalid' }
    },
    verifyPassword: async (user, password) => {
      calls.push(['verifyPassword', user.id, password])
      return password === 'correct'
    },
    hasSharedData: async id => {
      calls.push(['hasSharedData', id])
      return false
    },
    logError: message => calls.push(['logError', message]),
    ...overrides,
  }
}

const validBody = { password: 'correct', confirm_text: 'DELETE MY ACCOUNT' }

test('configuration supports current and legacy injected keys and fails closed', () => {
  const current = {
    SUPABASE_URL: 'https://example.invalid',
    SUPABASE_PUBLISHABLE_KEYS: JSON.stringify({ default: 'public-key' }),
    SUPABASE_SECRET_KEYS: JSON.stringify({ default: 'secret-key' }),
  }
  assert.deepEqual(supabaseConfig(current), {
    url: 'https://example.invalid', anonKey: 'public-key', serviceKey: 'secret-key',
  })
  assert.equal(supabaseConfig({
    SUPABASE_URL: current.SUPABASE_URL,
    SUPABASE_ANON_KEY: 'legacy-public',
    SUPABASE_SERVICE_ROLE_KEY: 'legacy-secret',
  }).serviceKey, 'legacy-secret')
  assert.throws(() => supabaseConfig({ ...current, SUPABASE_SECRET_KEYS: '{}' }))
  assert.throws(() => supabaseConfig({ ...current, SUPABASE_URL: 'http://example.invalid' }))
})

test('preflight checks only the authenticated user and never reports deletion', async () => {
  const deps = dependencies()
  const result = await handleDeletionPreflight(request(validBody), deps)
  assert.equal(result.status, 200)
  const body = await result.json()
  assert.equal(body.status, 'preflight_only')
  assert.match(body.message, /No data or account was deleted/)
  assert.deepEqual(deps.calls, [
    ['getUser', 'session-token'],
    ['verifyPassword', userId, 'correct'],
    ['hasSharedData', userId],
  ])
})

test('shared records require review and no deletion', async () => {
  const deps = dependencies({ hasSharedData: async () => true })
  const result = await handleDeletionPreflight(request(validBody), deps)
  assert.equal(result.status, 200)
  assert.equal((await result.json()).status, 'shared_data_review_required')
})

test('an arbitrary target user cannot be supplied', async () => {
  const deps = dependencies()
  const result = await handleDeletionPreflight(request({ ...validBody, user_id: userId }), deps)
  assert.equal(result.status, 400)
  assert.deepEqual(deps.calls, [])
})

test('invalid session or password never reaches the shared-data check', async () => {
  for (const overrides of [
    { getUser: async () => null },
    { verifyPassword: async () => false },
  ]) {
    let checked = false
    const deps = dependencies({ ...overrides, hasSharedData: async () => { checked = true; return false } })
    const result = await handleDeletionPreflight(request(validBody), deps)
    assert.equal(result.status, 401)
    assert.equal(checked, false)
  }
})

test('invalid shared-data results and provider failures fail closed without leaking details', async () => {
  for (const hasSharedData of [
    async () => null,
    async () => { throw new Error('sensitive provider detail') },
  ]) {
    const deps = dependencies({ hasSharedData })
    const result = await handleDeletionPreflight(request(validBody), deps)
    assert.equal(result.status, 503)
    assert.doesNotMatch(await result.text(), /sensitive provider detail/)
    assert.deepEqual(deps.calls.at(-1), ['logError', 'Account deletion preflight failed'])
  }
})

test('missing authentication, wrong confirmation, and oversized bodies are rejected', async () => {
  const deps = dependencies()
  const noToken = new Request('https://example.invalid/preflight', { method: 'POST' })
  assert.equal((await handleDeletionPreflight(noToken, deps)).status, 401)
  assert.equal((await handleDeletionPreflight(request({ ...validBody, confirm_text: 'DELETE' }), deps)).status, 400)
  assert.equal((await handleDeletionPreflight(request({ ...validBody, password: 'x'.repeat(5000) }), deps)).status, 400)
  assert.deepEqual(deps.calls, [])
})

test('Supabase adapter verifies identity and revokes only the temporary session', async () => {
  const calls = []
  function createClient(url, key, options) {
    calls.push(['client', url, key, options.auth.persistSession])
    return {
      auth: {
        getUser: async token => {
          calls.push(['getUser', token])
          return { data: { user: { id: userId, email: 'test@example.invalid' } }, error: null }
        },
        signInWithPassword: async credentials => {
          calls.push(['signIn', credentials])
          return { data: { user: { id: userId } }, error: null }
        },
        signOut: async options => {
          calls.push(['signOut', options])
          return { error: null }
        },
      },
      rpc: async (name, args) => {
        calls.push(['rpc', key, name, args])
        return { data: false, error: null }
      },
    }
  }
  const deps = supabaseDependencies(createClient, 'https://example.invalid', 'public-key', 'server-key', () => {})
  const result = await handleDeletionPreflight(request(validBody), deps)
  assert.equal(result.status, 200)
  assert.deepEqual(calls, [
    ['client', 'https://example.invalid', 'public-key', false],
    ['client', 'https://example.invalid', 'server-key', false],
    ['getUser', 'session-token'],
    ['client', 'https://example.invalid', 'public-key', false],
    ['signIn', { email: 'test@example.invalid', password: 'correct' }],
    ['signOut', { scope: 'local' }],
    ['rpc', 'server-key', 'falconbroom_account_has_shared_data', { p_user_id: userId }],
  ])
})

test('temporary-session cleanup failure is reported rather than ignored', async () => {
  let rpcCalled = false
  const createClient = () => ({
    auth: {
      getUser: async () => ({ data: { user: { id: userId, email: 'test@example.invalid' } }, error: null }),
      signInWithPassword: async () => ({ data: { user: { id: userId } }, error: null }),
      signOut: async () => ({ error: { message: 'failed' } }),
    },
    rpc: async () => { rpcCalled = true; return { data: false, error: null } },
  })
  const deps = supabaseDependencies(createClient, 'https://example.invalid', 'public-key', 'server-key', () => {})
  const result = await handleDeletionPreflight(request(validBody), deps)
  assert.equal(result.status, 503)
  assert.equal(rpcCalled, false)
})
