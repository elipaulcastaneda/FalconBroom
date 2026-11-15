import test from 'node:test'
import assert from 'node:assert/strict'
import { fetchEncryptedZip } from '../src/encryptedDownload.mjs'

test('encrypted exports post the passphrase to the local backend, not the URL', async () => {
  const original = globalThis.fetch
  let request
  globalThis.fetch = async (url, options) => {
    request = {url, options}
    return new Response('encrypted', {status: 200})
  }
  try {
    const blob = await fetchEncryptedZip('http://127.0.0.1:1234', {path: 'data/outputs/output.csv'}, 'a-long-test-passphrase')
    assert.equal(await blob.text(), 'encrypted')
    assert.equal(request.url, 'http://127.0.0.1:1234/desktop/encrypted-download')
    assert.equal(request.options.method, 'POST')
    assert.equal(request.options.cache, 'no-store')
    assert.deepEqual(JSON.parse(request.options.body), {path: 'data/outputs/output.csv', password: 'a-long-test-passphrase'})
  } finally {
    globalThis.fetch = original
  }
})

test('encrypted export failure does not fall back to a normal download', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async url => {
    calls.push(url)
    return new Response(JSON.stringify({detail: 'export failed'}), {status: 500})
  }
  try {
    await assert.rejects(fetchEncryptedZip('http://127.0.0.1:1234', {preview_csv: 'secret'}, 'a-long-test-passphrase'), /export failed/)
    assert.deepEqual(calls, ['http://127.0.0.1:1234/desktop/encrypted-download'])
  } finally {
    globalThis.fetch = original
  }
})

test('encrypted exports use the supplied authenticated request function', async () => {
  let called = false
  const authenticatedRequest = async (url, options) => {
    called = true
    assert.match(url, /\/desktop\/encrypted-download$/)
    assert.equal(options.credentials, 'omit')
    return new Response('encrypted', {status: 200})
  }
  await fetchEncryptedZip('http://127.0.0.1:1234', {path: 'output.csv'}, 'a-long-test-passphrase', authenticatedRequest)
  assert.equal(called, true)
})
