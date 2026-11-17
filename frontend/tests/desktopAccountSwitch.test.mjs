import test from 'node:test'
import assert from 'node:assert/strict'
import { watchDesktopAccount } from '../src/desktopAccountSwitch.mjs'

test('resets the desktop view when the signed-in account changes', () => {
  let listener
  let unsubscribed = false
  const changes = []
  const stop = watchDesktopAccount({
    onAuthStateChange(callback) {
      listener = callback
      return { data: { subscription: { unsubscribe() { unsubscribed = true } } } }
    },
  }, id => changes.push(id))

  listener('INITIAL_SESSION', null)
  listener('SIGNED_IN', { user: { id: 'account-a' } })
  listener('TOKEN_REFRESHED', { user: { id: 'account-a' } })
  listener('SIGNED_IN', { user: { id: 'account-b' } })
  listener('SIGNED_OUT', null)

  assert.deepEqual(changes, ['account-a', 'account-b', null])
  stop()
  assert.equal(unsubscribed, true)
})
