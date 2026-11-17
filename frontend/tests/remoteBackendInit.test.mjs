import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const script = await readFile(new URL('../../src-tauri/src/remote_init.js', import.meta.url), 'utf8')
const loadingPage = await readFile(new URL('../public/loading.html', import.meta.url), 'utf8')
const local = 'http://127.0.0.1:1234'
const remote = 'https://backend.example.test'

function install(fetch) {
  const window = { fetch }
  const source = `((base, local, secret, remote, warning) => {\n${script}\n})(${JSON.stringify(remote)}, ${JSON.stringify(local)}, "test-secret", true, null)`
  vm.runInNewContext(source, { window, URL, Request, Headers, AbortController, console })
  return window
}

test('a blocked remote lease prevents the backend request', async () => {
  let remoteCalls = 0
  const window = install(async request => {
    if (request.url.startsWith(local)) {
      assert.equal(request.headers.get('X-FalconBroom-Desktop-Token'), 'test-secret')
      return new Response('blocked', {status: 403})
    }
    remoteCalls++
    return new Response('unexpected')
  })
  await assert.rejects(window.fetch(remote + '/profile'), /blocked/)
  assert.equal(remoteCalls, 0)
})

test('remote lease remains active until the response finishes', async () => {
  let body
  let leaseClosed = false
  const window = install(async request => {
    if (request.url.startsWith(local)) {
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array([10]))
          request.signal.addEventListener('abort', () => {
            leaseClosed = true
            controller.error(new Error('lease closed'))
          }, {once: true})
        },
      })
      return new Response(stream)
    }
    assert.equal(request.headers.get('X-FalconBroom-Desktop-Token'), null)
    return new Response(new ReadableStream({ start(controller) { body = controller } }))
  })
  const response = await window.fetch(remote + '/download')
  assert.equal(leaseClosed, false)
  body.enqueue(new TextEncoder().encode('data'))
  body.close()
  assert.equal(await response.text(), 'data')
  for (let i = 0; i < 50 && !leaseClosed; i++) await new Promise(resolve => setTimeout(resolve, 10))
  assert.equal(leaseClosed, true)
})

test('cancellation while waiting for a lease never starts the remote request', async () => {
  let remoteCalls = 0
  const window = install(async request => {
    if (request.url.startsWith(local)) {
      await new Promise((resolve, reject) => {
        request.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true})
      })
    }
    remoteCalls++
    return new Response('unexpected')
  })
  const controller = new AbortController()
  const pending = window.fetch(remote + '/profile', {signal: controller.signal})
  controller.abort()
  await assert.rejects(pending, /aborted/)
  assert.equal(remoteCalls, 0)
})

test('loading page waits for the local coordinator in remote mode', async () => {
  const source = loadingPage.match(/<script>([\s\S]*?)<\/script>/)[1]
  let healthUrl
  let destination
  const context = {
    window: {__FALCONBROOM_CONTROL_URL: local, __FALCONBROOM_BACKEND_URL: remote},
    fetch: async url => { healthUrl = url; return {ok: true} },
    location: {replace: value => { destination = value }},
    Date,
  }
  vm.runInNewContext(source, context)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(healthUrl, local + '/health')
  assert.equal(destination, 'index.html')
})

test('loading page explains a missing vault key without opening the app', async () => {
  const source = loadingPage.match(/<script>([\s\S]*?)<\/script>/)[1]
  const elements = new Map()
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', hidden: true, value: '', files: [],
      addEventListener(name, callback) { this[name] = callback },
      appendChild() {},
    })
    return elements.get(id)
  }
  const document = { body: { className: '' }, getElementById: element, createElement: () => ({}) }
  let destination
  const context = {
    window: {__FALCONBROOM_CONTROL_URL: local},
    fetch: async url => url.endsWith('/health')
      ? new Response(JSON.stringify({code: 'vault_key_missing'}), {status: 503})
      : new Response(JSON.stringify({current: 'legacy', profiles: ['legacy']}), {status: 200}),
    location: {replace: value => { destination = value }},
    document,
    Date,
    setTimeout: () => { throw new Error('Missing key must not retry') },
  }
  vm.runInNewContext(source, context)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(document.body.className, 'error')
  assert.match(element('msg').textContent, /key is missing/)
  assert.equal(element('recovery').hidden, false)
  assert.equal(destination, undefined)
})

test('loading page retries unrelated service-unavailable responses', async () => {
  const source = loadingPage.match(/<script>([\s\S]*?)<\/script>/)[1]
  let attempts = 0
  let destination
  const context = {
    window: {__FALCONBROOM_CONTROL_URL: local},
    fetch: async () => ++attempts === 1
      ? new Response(JSON.stringify({detail: 'busy'}), {status: 503})
      : new Response('', {status: 200}),
    location: {replace: value => { destination = value }},
    document: {body: {className: ''}, getElementById: () => ({textContent: ''})},
    Date,
    setTimeout: callback => callback(),
  }
  vm.runInNewContext(source, context)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(attempts, 2)
  assert.equal(destination, 'index.html')
})

test('fresh start requires explicit confirmation before selecting a new profile', async () => {
  const source = loadingPage.match(/<script>([\s\S]*?)<\/script>/)[1]
  const elements = new Map()
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', hidden: true, value: '', files: [],
      addEventListener(name, callback) { this[name] = callback },
      appendChild() {},
    })
    return elements.get(id)
  }
  const requests = []
  const context = {
    window: {__FALCONBROOM_CONTROL_URL: local},
    fetch: async (url, init) => {
      requests.push({url, init})
      if (url.endsWith('/health')) return new Response(JSON.stringify({code: 'vault_key_missing'}), {status: 503})
      if (url.endsWith('/profiles')) return new Response(JSON.stringify({current: 'legacy', profiles: ['legacy']}))
      return new Response(JSON.stringify({status: 'restart_required'}))
    },
    document: {body: {className: ''}, getElementById: element, createElement: () => ({})},
    location: {replace() { throw new Error('Must not open the app') }},
    Date,
  }
  vm.runInNewContext(source, context)
  await new Promise(resolve => setImmediate(resolve))
  element('fresh').click()
  assert.match(element('recovery-status').textContent, /START FRESH/)
  assert.equal(requests.filter(request => request.url.endsWith('/fresh')).length, 0)
  element('fresh-confirm').value = 'START FRESH'
  element('fresh').click()
  await new Promise(resolve => setImmediate(resolve))
  const fresh = requests.find(request => request.url.endsWith('/fresh'))
  assert.deepEqual(JSON.parse(fresh.init.body), {confirmation: 'START FRESH'})
  assert.equal(element('recovery').hidden, true)
  assert.match(element('msg').textContent, /restart/)
})
