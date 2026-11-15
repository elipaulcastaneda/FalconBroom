import test from 'node:test'
import assert from 'node:assert/strict'
import { deletionWorkerDependencies, processDeletionJob } from './worker.mjs'

function fake(overrides = {}) {
  const calls = []
  return {
    calls,
    claim: async () => ({ job_id: 'job', user_id: 'user', lease_id: 'lease' }),
    deleteAtomically: async (...args) => { calls.push(['atomic', ...args]); return 'completed' },
    finishRetry: async (...args) => { calls.push(['retry', ...args]); return true },
    logError: (...args) => calls.push(['error', ...args]),
    ...overrides,
  }
}

test('one atomic operation completes the job without a separate Auth call', async () => {
  const deps = fake()
  assert.deepEqual(await processDeletionJob(deps), { status: 'completed', job_id: 'job' })
  assert.deepEqual(deps.calls, [['atomic', 'job', 'lease']])
})

test('storage or shared data pauses the job for review', async () => {
  for (const result of ['storage_review_required', 'shared_data_review_required']) {
    const deps = fake({ deleteAtomically: async () => result })
    assert.deepEqual(await processDeletionJob(deps), { status: 'needs_review', job_id: 'job' })
    assert.deepEqual(deps.calls, [])
  }
})

test('failed or invalid atomic results request a retry without exposing details', async () => {
  for (const deleteAtomically of [
    async () => { throw new Error('private detail') },
    async () => 'unexpected',
  ]) {
    const deps = fake({ deleteAtomically })
    assert.deepEqual(await processDeletionJob(deps), { status: 'retry', job_id: 'job' })
    assert.deepEqual(deps.calls, [
      ['error', 'Deletion worker failed'], ['retry', 'job', 'lease'],
    ])
  }
})

test('a lost response after commit does not falsely reset the completed job', async () => {
  const deps = fake({
    deleteAtomically: async () => { throw new Error('connection lost') },
    finishRetry: async () => false,
  })
  assert.equal((await processDeletionJob(deps)).status, 'retry')
  assert.ok(deps.calls.some(call => call[1].includes('already completed')))
})

test('an empty queue makes no changes', async () => {
  const deps = fake({ claim: async () => null })
  assert.deepEqual(await processDeletionJob(deps), { status: 'idle' })
  assert.deepEqual(deps.calls, [])
})

test('real worker dependencies cannot claim a job before Supabase verification', async () => {
  let rpcCalled = false
  const deps = deletionWorkerDependencies({
    rpc: async () => { rpcCalled = true; throw new Error('unexpected RPC') },
  }, () => {})
  await assert.rejects(processDeletionJob(deps), /not verified on Supabase/)
  assert.equal(rpcCalled, false)
})

test('explicitly enabled worker uses the job claim RPC', async () => {
  const calls = []
  const deps = deletionWorkerDependencies({
    rpc: async (name, args) => { calls.push([name, args]); return { data: null, error: null } },
  }, () => {}, { enableClaim: true })
  assert.deepEqual(await processDeletionJob(deps), { status: 'idle' })
  assert.deepEqual(calls, [['falconbroom_claim_deletion_job', {}]])
})
