import test from 'node:test'
import assert from 'node:assert/strict'
import { bindAccountFetch } from '../src/accountBoundFetch.mjs'

test('old requests stay bound to the account that created them', async () => {
  const sent = []
  const fetcher = async (_url, options) => { sent.push(options.headers) }
  const fromA = bindAccountFetch(fetcher, 'ACCOUNT-A')
  const fromB = bindAccountFetch(fetcher, 'ACCOUNT-B')
  await fromB('/history')
  await fromA('/history', { headers: { 'Content-Type': 'application/json' } })
  assert.deepEqual(sent, [
    { 'X-FalconBroom-Expected-Account': 'account-b' },
    { 'Content-Type': 'application/json', 'X-FalconBroom-Expected-Account': 'account-a' },
  ])
})

test('remote requests do not receive a desktop account binding', async () => {
  const request = bindAccountFetch(async (_url, options) => options, null)
  assert.deepEqual((await request('/history')).headers, {})
})
