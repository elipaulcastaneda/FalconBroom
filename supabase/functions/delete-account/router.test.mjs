import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { deletionRouter } from './router.mjs'

if (!globalThis.crypto) globalThis.crypto = webcrypto
const testUrl = 'https://zthtkoekvxplppxanwiu.supabase.co'
const secret = 'a'.repeat(64)

function setup(url, enableTestDeletion, workerSecret = secret) {
  const calls = []
  const route = deletionRouter({
    config: { url, anonKey: 'public', serviceKey: 'server' },
    enableTestDeletion,
    workerSecret,
    createClient: () => ({
      auth: {},
      rpc: async (name, args) => { calls.push([name, args]); return { data: null, error: null } },
    }),
    logError: () => {},
  })
  return { route, calls }
}

function request(path, headers = {}, body = '{}') {
  return new Request(`https://example.invalid/functions/v1/delete-account${path}`, {
    method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body,
  })
}

test('destructive routes stay disabled unless both project and opt-in match', async () => {
  for (const [url, flag] of [[testUrl, undefined], ['https://other.invalid', '1'], [testUrl, 'true']]) {
    const { route, calls } = setup(url, flag)
    assert.equal((await route(request('/worker', { 'x-falconbroom-worker-secret': secret }))).status, 503)
    assert.equal((await route(request(''))).status, 401)
    assert.deepEqual(calls, [])
  }
  for (const workerSecret of ['', 'short', 'a'.repeat(513)]) {
    const { route, calls } = setup(testUrl, '1', workerSecret)
    assert.equal((await route(request(''))).status, 401)
    assert.equal((await route(request('/worker', { 'x-falconbroom-worker-secret': workerSecret }))).status, 503)
    assert.deepEqual(calls, [])
  }
})

test('test worker requires its secret before claiming and jobs still validate requests', async () => {
  const { route, calls } = setup(testUrl, '1')
  assert.equal((await route(request('/worker', { 'x-falconbroom-worker-secret': 'incorrect' }))).status, 401)
  assert.equal((await route(request(''))).status, 400)
  assert.deepEqual(calls, [])
  const result = await route(request('/worker', { 'x-falconbroom-worker-secret': secret }))
  assert.deepEqual(await result.json(), { status: 'idle' })
  assert.deepEqual(calls, [['falconbroom_claim_deletion_job', {}]])
})
