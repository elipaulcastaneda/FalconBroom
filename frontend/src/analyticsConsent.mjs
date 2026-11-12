const CONSENT_KEY = 'falconbroom_consent'
const CONSENT_VERSION = 2

export function shouldLoadAnalytics(isDesktop, consent, analyticsId) {
  return !isDesktop && Boolean(consent?.analytics && analyticsId)
}

export function readAnalyticsConsent(storage) {
  const raw = storage.getItem(CONSENT_KEY)
  if (!raw) return null
  const consent = JSON.parse(raw)
  if (!consent || consent.version !== CONSENT_VERSION || typeof consent.analytics !== 'boolean') return null
  return consent
}

export function saveAnalyticsConsent(storage, analytics) {
  if (typeof analytics !== 'boolean') throw new TypeError('Analytics preference must be a boolean')
  const consent = { version: CONSENT_VERSION, essential: true, analytics, marketing: false, personalized_ads: false }
  storage.setItem(CONSENT_KEY, JSON.stringify(consent))
  return consent
}
