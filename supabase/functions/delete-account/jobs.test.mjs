import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, webcrypto } from 'node:crypto'
import { deletionJobDependencies, handleDeletionJobRequest } from './jobs.mjs'

if (!globalThis.crypto) globalThis.crypto = webcrypto
const userId = '6a0d1258-0950-4bbb-a829-0d5718c47e9e'
const jobId = '3b87c304-bef2-4c79-8ebd-bf26426467aa'
const token = 'b'.repeat(64)
const tokenHash = createHash('sha256').update(token).digest('hex')
const base = { action: 'start', password: 'synthetic', confirm_text: 'DELETE MY ACCOUNT', recovery_token: token }
const request = body => new Request('https://example.invalid', {
  method: 'POST',
  headers: { authorization: 'Bearer access', 'content-type': 'application/json' },
  body: JSON.stringify(body),
})
function fake(overrides = {}) {
  const calls = []
  return {
    calls,
    getUser: async token => { calls.push(['user', token]); return { id: userId, email: 'test@example.invalid' } },
    verifyPassword: async (user, password) => {
      calls.push(['password', user.id, password])
      return true
    },
    hasMfa: async id => { calls.push(['mfa', id]); return false },
    createJob: async (...args) => { calls.push(['create', ...args]); return { status: 'awaiting_local', job_id: jobId } },
    localComplete: async (...args) => { calls.push(['complete', ...args]); return true },
    getStatus: async (...args) => { calls.push(['status', ...args]); return { status: 'completed', job_id: jobId } },
    logError: (...args) => calls.push(['log', ...args]),
    ...overrides,
  }
}

test('job creation uses only the verified account', async () => {
  const deps = fake()
  const result = await handleDeletionJobRequest(request(base), deps)
  assert.deepEqual(await result.json(), { status: 'awaiting_local', job_id: jobId, user_id: userId, email: 'test@example.invalid' })
  assert.deepEqual(deps.calls, [
    ['user', 'access'], ['password', userId, 'synthetic'], ['mfa', userId], ['create', userId, tokenHash],
  ])
})

test('client cannot nominate a target account and oversized bodies are rejected', async () => {
  const deps = fake()
  for (const body of [{ ...base, user_id: userId }, { ...base, password: 'x'.repeat(5000) }]) {
    assert.equal((await handleDeletionJobRequest(request(body), deps)).status, 400)
  }
  assert.deepEqual(deps.calls, [])
})

test('wrong password, MFA, and provider errors do not create or acknowledge jobs', async () => {
  for (const [overrides, status] of [
    [{ verifyPassword: async () => false }, 401],
    [{ hasMfa: async () => true }, 409],
    [{ hasMfa: async () => { throw new Error('private data') } }, 503],
    [{ hasMfa: async () => null }, 503],
  ]) {
    const deps = fake(overrides)
    const response = await handleDeletionJobRequest(request(base), deps)
    assert.equal(response.status, status)
    assert.doesNotMatch(await response.text(), /private data/)
    assert.ok(!deps.calls.some(call => call[0] === 'create' || call[0] === 'complete'))
  }
})

test('shared or Storage data is reported without exposing provider fields', async () => {
  for (const status of ['storage_review_required', 'shared_data_review_required', 'recovery_review_required']) {
    const deps = fake({ createJob: async () => ({ status, private_detail: 'do not expose' }) })
    const response = await handleDeletionJobRequest(request(base), deps)
    assert.deepEqual(await response.json(), { status })
  }
})

test('local acknowledgement is scoped to the verified user and a valid job', async () => {
  const deps = fake()
  const result = await handleDeletionJobRequest(request({ ...base, action: 'local_complete', job_id: jobId }), deps)
  assert.deepEqual(await result.json(), { status: 'accepted', job_id: jobId })
  assert.deepEqual(deps.calls.at(-1), ['complete', userId, jobId, tokenHash])
  const rejected = fake({ localComplete: async () => false })
  assert.equal((await handleDeletionJobRequest(request({ ...base, action: 'local_complete', job_id: jobId }), rejected)).status, 404)
})

test('status uses the recovery capability after the Auth session no longer exists', async () => {
  const deps = fake()
  const response = await handleDeletionJobRequest(request({ action: 'status', job_id: jobId, recovery_token: token }), deps)
  assert.deepEqual(await response.json(), { status: 'completed', job_id: jobId })
  assert.deepEqual(deps.calls, [['status', jobId, tokenHash]])
  const missing = fake({ getStatus: async () => null })
  assert.equal((await handleDeletionJobRequest(request({ action: 'status', job_id: jobId, recovery_token: token }), missing)).status, 404)
})

test('invalid MFA results fail closed and unverified factors do not require MFA', async () => {
  let factors = [{ status: 'unverified' }]
  const deps = deletionJobDependencies({}, {
    auth: { admin: { mfa: { listFactors: async () => ({ data: { factors }, error: null }) } } },
  })
  assert.equal(await deps.hasMfa(userId), false)
  factors = [{ status: 'verified' }]
  assert.equal(await deps.hasMfa(userId), true)
  factors = [{}]
  await assert.rejects(deps.hasMfa(userId), /MFA lookup failed/)
})
