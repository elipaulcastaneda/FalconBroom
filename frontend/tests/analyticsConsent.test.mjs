import test from 'node:test'
import assert from 'node:assert/strict'
import { readAnalyticsConsent, saveAnalyticsConsent, shouldLoadAnalytics } from '../src/analyticsConsent.mjs'

test('desktop never loads analytics even with old consent and a configured ID', () => {
  assert.equal(shouldLoadAnalytics(true, { analytics: true }, 'G-EXAMPLE'), false)
  assert.equal(shouldLoadAnalytics(false, { analytics: true }, 'G-EXAMPLE'), true)
  assert.equal(shouldLoadAnalytics(false, { analytics: false }, 'G-EXAMPLE'), false)
  assert.equal(shouldLoadAnalytics(false, { analytics: true }, ''), false)
})

function storage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
}

test('requires a new explicit choice instead of trusting an old banner decision', () => {
  assert.equal(readAnalyticsConsent(storage()), null)
  assert.equal(readAnalyticsConsent(storage({
    falconbroom_consent: JSON.stringify({ essential: true, analytics: true, marketing: true }),
  })), null)
  assert.equal(readAnalyticsConsent(storage({
    falconbroom_consent: JSON.stringify({ version: 2, analytics: 'true' }),
  })), null)
})

test('saves and restores allow and reject decisions without opting into marketing', () => {
  const data = storage()
  for (const allowed of [true, false]) {
    const saved = saveAnalyticsConsent(data, allowed)
    assert.deepEqual(readAnalyticsConsent(data), saved)
    assert.equal(saved.analytics, allowed)
    assert.equal(saved.marketing, false)
    assert.equal(saved.personalized_ads, false)
  }
})

test('rejects invalid decisions and reports storage failures', () => {
  assert.throws(() => saveAnalyticsConsent(storage(), 'false'), TypeError)
  assert.throws(() => saveAnalyticsConsent({ setItem() { throw new Error('storage unavailable') } }, false), /storage unavailable/)
})
