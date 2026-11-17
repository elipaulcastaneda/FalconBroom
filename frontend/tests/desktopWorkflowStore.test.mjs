import test from 'node:test'
import assert from 'node:assert/strict'
import { createDesktopWorkflowStore } from '../src/desktopWorkflowStore.mjs'

const tick = () => new Promise(resolve => setImmediate(resolve))
const revision = char => char.repeat(64)
const response = (status, data) => ({ ok: status === 200, status, json: async () => data })

test('loads account-bound data and serializes updates with revisions', async () => {
  const calls = []
  let release
  const store = createDesktopWorkflowStore('account-a', 'local', async (url, options) => {
    calls.push({ url, options })
    if (!options.method) return response(200, {
      data: url.endsWith('/drafts') ? {} : [], revision: null,
    })
    if (calls.filter(call => call.options.method === 'PUT').length === 1) {
      await new Promise(resolve => { release = resolve })
      return response(200, { revision: revision('a') })
    }
    return response(200, { revision: revision('b') })
  }, error => assert.fail(error.message))
  await store.load()
  store.update('checkpoints', items => [...items, { id: 'one' }])
  store.update('checkpoints', items => [...items, { id: 'two' }])
  await tick()
  assert.equal(calls.filter(call => call.options.method === 'PUT').length, 1)
  release()
  await tick()
  const writes = calls.filter(call => call.options.method === 'PUT')
  assert.equal(writes.length, 2)
  assert.equal(writes[1].options.headers['X-FalconBroom-Expected-Account'], 'account-a')
  assert.deepEqual(JSON.parse(writes[1].options.body), {
    data: [{ id: 'one' }, { id: 'two' }], revision: revision('a'),
  })
  store.close()
})

test('a conflict pauses queued saves rather than overwriting newer data', async () => {
  const errors = []
  let writes = 0
  const store = createDesktopWorkflowStore('account-a', 'local', async (url, options) => {
    if (!options.method) return response(200, {
      data: url.endsWith('/drafts') ? {} : [], revision: null,
    })
    writes += 1
    return response(409, {})
  }, error => errors.push(error))
  await store.load()
  store.update('history', () => [{ id: 'first' }])
  store.update('history', () => [{ id: 'second' }])
  await tick()
  assert.equal(writes, 1)
  assert.equal(errors.length, 1)
  assert.throws(() => store.update('history', () => []), /paused/)
  store.close()
})

test('closing an account stops queued saves', async () => {
  let writes = 0
  const store = createDesktopWorkflowStore('account-a', 'local', async (url, options) => {
    if (!options.method) return response(200, {
      data: url.endsWith('/drafts') ? {} : [], revision: null,
    })
    writes += 1
    return response(200, { revision: revision('a') })
  }, error => assert.fail(error.message))
  await store.load()
  store.update('drafts', () => ({ path: { instruction: 'private' } }))
  assert.equal(store.close(), true)
  await tick()
  assert.equal(writes, 0)
})

test('invalid stored data prevents workflow access instead of treating it as empty', async () => {
  const store = createDesktopWorkflowStore('account-a', 'local', async url => response(200, {
    data: url.endsWith('/checkpoints') ? {} : url.endsWith('/drafts') ? {} : [],
    revision: null,
  }), error => assert.fail(error.message))
  await assert.rejects(store.load(), /Invalid account workflow data/)
  assert.throws(() => store.get('checkpoints'), /unavailable/)
  store.close()
})
