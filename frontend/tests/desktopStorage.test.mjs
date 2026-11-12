import test from 'node:test'
import assert from 'node:assert/strict'
import { createDesktopStorage, removeLegacyDesktopCredentials } from '../src/desktopStorage.mjs'

function localStorage(initial = {}) {
  const data = new Map(Object.entries(initial))
  return {
    get length() { return data.size },
    key: index => [...data.keys()][index],
    getItem: key => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: key => data.delete(key),
  }
}

test('stores sessions in credential storage and removes local copies', async () => {
  const local = localStorage({ 'sb-project-auth-token': 'old', unrelated: 'keep' })
  const secure = new Map()
  const storage = createDesktopStorage({
    localStorage: local,
    onFailure: () => assert.fail('unexpected credential-store failure'),
    invoke: async (command, { key, value }) => {
      if (command === 'secure_store_set') secure.set(key, value)
      if (command === 'secure_store_delete') secure.delete(key)
      if (command === 'secure_store_get') return secure.get(key) ?? null
    },
  })
  await storage.setItem('sb-project-auth-token', 'new')
  assert.equal(local.getItem('sb-project-auth-token'), null)
  assert.equal(await storage.getItem('sb-project-auth-token'), 'new')
  await storage.removeItem('sb-project-auth-token')
  assert.equal(await storage.getItem('sb-project-auth-token'), null)
  assert.equal(local.getItem('unrelated'), 'keep')
})

test('store failure uses memory and warns without writing to localStorage', async () => {
  const local = localStorage()
  let warnings = 0
  const storage = createDesktopStorage({
    localStorage: local,
    onFailure: () => { warnings++ },
    invoke: async () => { throw new Error('unavailable') },
  })
  await storage.setItem('auth', 'private')
  assert.equal(await storage.getItem('auth'), 'private')
  assert.equal(local.getItem('auth'), null)
  assert.equal(warnings, 1)
  await assert.rejects(storage.removeItem('auth'))
  assert.equal(await storage.getItem('auth'), null)
})

test('migration removes only old desktop authentication keys', () => {
  const local = localStorage({
    falconbroom_access_token: 'a',
    falconbroom_refresh_token: 'b',
    'sb-project-auth-token': 'c',
    'sb-project-auth-token-code-verifier': 'd',
    'sb-project-auth-token-flow-123-code-verifier': 'e',
    'sb-project-auth-token-flows-code-verifier': 'f',
    'sb-other-auth-token': 'other',
    'falconbroom_consent': 'keep',
  })
  removeLegacyDesktopCredentials(local, 'sb-project-auth-token')
  assert.equal(local.length, 2)
  assert.equal(local.getItem('falconbroom_consent'), 'keep')
  assert.equal(local.getItem('sb-other-auth-token'), 'other')
})

test('persistent sessions survive adapter recreation and deletion', async () => {
  const secure = new Map()
  const invoke = async (command, { key, value }) => {
    if (command === 'secure_store_set') secure.set(key, value)
    if (command === 'secure_store_delete') secure.delete(key)
    if (command === 'secure_store_get') return secure.get(key) ?? null
  }
  const create = () => createDesktopStorage({ invoke, localStorage: localStorage(), onFailure: () => assert.fail() })
  const session = JSON.stringify({ access_token: 'a'.repeat(1500), refresh_token: 'b'.repeat(500), user: { id: 'user' } })
  await create().setItem('session', session)
  assert.equal(await create().getItem('session'), session)
  await create().removeItem('session')
  assert.equal(await create().getItem('session'), null)
})

test('a later successful secure write recovers from session-only mode', async () => {
  const secure = new Map()
  let unavailable = true
  const storage = createDesktopStorage({
    localStorage: localStorage(), onFailure: () => {},
    invoke: async (command, { key, value }) => {
      if (unavailable) throw new Error('unavailable')
      if (command === 'secure_store_set') secure.set(key, value)
      if (command === 'secure_store_get') return secure.get(key) ?? null
      if (command === 'secure_store_delete') secure.delete(key)
    },
  })
  await storage.setItem('session', 'temporary')
  unavailable = false
  await storage.setItem('session', 'persistent')
  assert.equal(await storage.getItem('session'), 'persistent')
  assert.equal(secure.get('session'), 'persistent')
})
