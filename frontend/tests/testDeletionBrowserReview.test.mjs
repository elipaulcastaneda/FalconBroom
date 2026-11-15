import test from 'node:test'
import assert from 'node:assert/strict'
import {
  hasUnownedWorkflowData, writeCoordinatedWorkflowData,
  startCoordinatedTestDeletion, checkCoordinatedTestDeletion, testWorkflowKey,
} from '../src/testDeletionBrowserReview.mjs'

const owner = '6a0d1258-0950-4bbb-a829-0d5718c47e9e'
const other = 'cc856b01-88d7-497d-b36b-04446b70c3c6'

function storage(values) {
  const entries = new Map(Object.entries(values))
  return {
    get length() { return entries.size },
    key: index => [...entries.keys()][index] ?? null,
    getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key),
  }
}

function locks() {
  let tail = Promise.resolve()
  return {
    request(_name, callback) {
      const current = tail.then(callback)
      tail = current.catch(() => {})
      return current
    },
  }
}

const clearState = async () => ({ local_pending: false, deletions: [] })

test('empty workflow data permits further review without altering preferences', () => {
  assert.equal(hasUnownedWorkflowData(storage({
    'falconbroom-upload-history': '[]',
    'falconbroom-upload-drafts': '{}',
    'falconbroom-workflow-checkpoints:local': '[]',
    'falconbroom-theme': 'dark',
  })), false)
})

test('unowned browser data or malformed data requires review', () => {
  for (const values of [
    { 'falconbroom-upload-history': '[{"path":"file.csv"}]' },
    { 'falconbroom-upload-drafts': '{"file.csv":{"instruction":"clean"}}' },
    { 'falconbroom-workflow-checkpoints:user_abc': '[{"kind":"joins"}]' },
    { 'falconbroom-workflow-checkpoints:user_abc': 'not json' },
    { 'falconbroom-upload-drafts': 'null' },
    { 'falconbroom_delete_token': 'token' },
  ]) {
    assert.equal(hasUnownedWorkflowData(storage(values)), true)
  }
})

test('storage access failures are surfaced', () => {
  assert.throws(() => hasUnownedWorkflowData({
    length: 0,
    getItem() { throw new Error('storage unavailable') },
  }), /storage unavailable/)
})

test('a concurrent workflow save is reviewed before starting deletion', async () => {
  const data = storage({})
  const manager = locks()
  const save = writeCoordinatedWorkflowData(data, manager, owner, () => {
    data.setItem('falconbroom-upload-history', '[{"path":"file.csv"}]')
  })
  let started = false
  const deletion = startCoordinatedTestDeletion(data, manager, owner, clearState, () => { started = true })
  await save
  assert.deepEqual(await deletion, { ok: false, status: 'browser_review_required' })
  assert.equal(started, false)
  assert.equal(data.getItem('falconbroom-test-deletion-pending'), null)
})

test('pending deletion blocks workflow writes until verified recovery', async () => {
  const data = storage({})
  const manager = locks()
  await startCoordinatedTestDeletion(data, manager, owner, clearState, async () => ({ status: 'ready' }))
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /pending/)
  await assert.rejects(checkCoordinatedTestDeletion(data, manager, async () => {
    throw new Error('offline')
  }), /offline/)
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /pending/)
  await checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: true, deletions: [{ user_id: owner, status: 'processing', local_status: 'completed' }],
  }))
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /pending|started/)
  await checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: false, deletions: [{ user_id: owner, status: 'completed', local_status: 'completed' }],
  }))
  await writeCoordinatedWorkflowData(data, manager, other, () => data.setItem('saved', 'yes'))
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /started/)
  assert.equal(data.getItem('saved'), 'yes')
})

test('a queued save cannot recreate data after deletion completes', async () => {
  const data = storage({})
  const manager = locks()
  let release
  const blocker = manager.request('workflow', () => new Promise(resolve => { release = resolve }))
  await new Promise(resolve => setImmediate(resolve))
  const deletion = startCoordinatedTestDeletion(data, manager, owner, clearState, async () => ({ status: 'ready' }))
  const recovery = checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: false, deletions: [{ user_id: owner, status: 'completed', local_status: 'completed' }],
  }))
  const staleSave = writeCoordinatedWorkflowData(data, manager, owner, () => data.setItem('saved', 'stale'))
  release()
  await blocker
  await deletion
  await recovery
  await assert.rejects(staleSave, /interrupted|started/)
  assert.equal(data.getItem('saved'), null)
})

test('deletion fails closed without browser locking', () => {
  const data = storage({})
  assert.throws(() => startCoordinatedTestDeletion(data, null, owner, clearState, () => {}), /coordination/)
  assert.throws(() => writeCoordinatedWorkflowData(data, null, owner, () => {}), /coordination/)
})

test('unavailable or inconsistent desktop status does not leave a browser marker', async () => {
  for (const verify of [
    async () => { throw new Error('disabled') },
    async () => ({ local_pending: true, deletions: [] }),
    async () => ({}),
  ]) {
    const data = storage({})
    let started = false
    await assert.rejects(startCoordinatedTestDeletion(data, locks(), owner, verify, () => { started = true }))
    assert.equal(started, false)
    assert.equal(data.getItem('falconbroom-test-deletion-pending'), null)
  }
})

test('completed local cleanup removes only the verified account workflow data', async () => {
  const data = storage({
    [testWorkflowKey(owner, 'history')]: '[{"path":"mine.csv"}]',
    [testWorkflowKey(owner, 'drafts')]: '{"mine.csv":{}}',
    [testWorkflowKey(owner, 'checkpoints')]: '[{"kind":"joins"}]',
    [testWorkflowKey(other, 'checkpoints')]: '[{"kind":"source"}]',
    'falconbroom-theme': 'dark',
  })
  await startCoordinatedTestDeletion(data, locks(), owner, clearState,
    async () => ({ ok: true, user_id: owner, local_status: 'completed', status: 'ready' }))
  for (const type of ['history', 'drafts', 'checkpoints']) assert.equal(data.getItem(testWorkflowKey(owner, type)), null)
  assert.notEqual(data.getItem(testWorkflowKey(other, 'checkpoints')), null)
  assert.equal(data.getItem('falconbroom-theme'), 'dark')
})

test('an interrupted browser cleanup is retried using the recorded account', async () => {
  const data = storage({
    [testWorkflowKey(owner, 'checkpoints')]: '[{"kind":"joins"}]',
    [testWorkflowKey(other, 'history')]: '[{"path":"other.csv"}]',
  })
  const manager = locks()
  await assert.rejects(startCoordinatedTestDeletion(data, manager, owner, clearState, async () => {
    throw new Error('connection lost')
  }), /connection lost/)
  await checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: false, deletions: [{ user_id: owner, local_status: 'completed', status: 'completed' }],
  }))
  assert.equal(data.getItem(testWorkflowKey(owner, 'checkpoints')), null)
  assert.notEqual(data.getItem(testWorkflowKey(other, 'history')), null)
  assert.equal(data.getItem('falconbroom-test-deletion-pending'), null)
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /started/)
})

test('mismatched or unknown accounts never authorize browser cleanup', async () => {
  const data = storage({ [testWorkflowKey(owner, 'history')]: '["mine"]' })
  const manager = locks()
  await assert.rejects(startCoordinatedTestDeletion(data, manager, owner, clearState,
    async () => ({ ok: true, user_id: other, local_status: 'completed' })), /does not match/)
  await assert.rejects(startCoordinatedTestDeletion(data, manager, other, clearState, async () => ({})), /Another browser/)
  await assert.rejects(checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: false, deletions: [{ user_id: other, local_status: 'completed', status: 'completed' }],
  })), /requires review/)
  assert.equal(data.getItem(testWorkflowKey(owner, 'history')), '["mine"]')
  assert.throws(() => testWorkflowKey('local', 'history'), /Verified test account/)
})

test('partial browser cleanup stays blocked and is retryable', async () => {
  const data = storage({
    [testWorkflowKey(owner, 'history')]: '["mine"]',
    [testWorkflowKey(owner, 'drafts')]: '{"mine":{}}',
  })
  const remove = data.removeItem
  let fail = true
  data.removeItem = key => {
    if (fail && key === testWorkflowKey(owner, 'drafts')) throw new Error('storage unavailable')
    remove(key)
  }
  const manager = locks()
  await assert.rejects(startCoordinatedTestDeletion(data, manager, owner, clearState,
    async () => ({ ok: true, user_id: owner, local_status: 'completed' })), /storage unavailable/)
  assert.throws(() => writeCoordinatedWorkflowData(data, manager, owner, () => {}), /started/)
  assert.notEqual(data.getItem('falconbroom-test-deletion-pending'), null)
  fail = false
  await checkCoordinatedTestDeletion(data, manager, async () => ({
    local_pending: false, deletions: [{ user_id: owner, local_status: 'completed', status: 'completed' }],
  }))
  assert.equal(data.getItem(testWorkflowKey(owner, 'drafts')), null)
  assert.equal(data.getItem('falconbroom-test-deletion-pending'), null)
})

test('an old unowned pending marker requires review and never removes account data', async () => {
  const data = storage({
    'falconbroom-test-deletion-pending': '1',
    [testWorkflowKey(owner, 'history')]: '["mine"]',
  })
  await assert.rejects(checkCoordinatedTestDeletion(data, locks(), clearState), /requires review/)
  assert.equal(data.getItem(testWorkflowKey(owner, 'history')), '["mine"]')
})
