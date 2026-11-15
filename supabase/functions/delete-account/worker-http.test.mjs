import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { handleDeletionWorkerRequest } from './worker-http.mjs'
import { deletionWorkerDependencies } from './worker.mjs'

if (!globalThis.crypto) globalThis.crypto = webcrypto
const secret = 'a'.repeat(64)
const request = value => new Request('https://example.invalid', {
  method: 'POST', headers: { 'x-falconbroom-worker-secret': value },
})

test('worker requires explicit enablement and the server secret', async () => {
  let claims = 0
  const deps = { enabled: false, secret, worker: { claim: async () => { claims++; return null } }, logError() {} }
  assert.equal((await handleDeletionWorkerRequest(request(secret), deps)).status, 503)
  deps.enabled = 'false'
  assert.equal((await handleDeletionWorkerRequest(request(secret), deps)).status, 503)
  deps.enabled = true
  assert.equal((await handleDeletionWorkerRequest(request('incorrect'), deps)).status, 401)
  assert.equal(claims, 0)
  const result = await handleDeletionWorkerRequest(request(secret), deps)
  assert.equal(result.status, 200)
  assert.deepEqual(await result.json(), { status: 'idle' })
  assert.equal(claims, 1)
})

test('worker failures do not expose provider details', async () => {
  const logs = []
  const response = await handleDeletionWorkerRequest(request(secret), {
    enabled: true, secret,
    worker: { claim: async () => { throw new Error('private data') } },
    logError: message => logs.push(message),
  })
  assert.equal(response.status, 503)
  assert.doesNotMatch(await response.text(), /private data/)
  assert.deepEqual(logs, ['Deletion worker request failed'])
})

test('real dependencies return unavailable without claiming a job', async () => {
  let rpcCalled = false
  const worker = deletionWorkerDependencies({
    rpc: async () => { rpcCalled = true; throw new Error('unexpected RPC') },
  }, () => {})
  const response = await handleDeletionWorkerRequest(request(secret), {
    enabled: true, secret, worker, logError() {},
  })
  assert.equal(response.status, 503)
  assert.equal(rpcCalled, false)
})
