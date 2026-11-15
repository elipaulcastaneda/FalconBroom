const baseUrl = new URL(base)
const localUrl = new URL(local)
window.__FALCONBROOM_BACKEND_URL = baseUrl.href.replace(/\/$/, '')
window.__FALCONBROOM_CONTROL_URL = localUrl.href.replace(/\/$/, '')
window.__FALCONBROOM_REMOTE_ACTIVE = remote
window.__FALCONBROOM_REMOTE_WARNING = warning
const original = window.fetch.bind(window)
const localFetch = (input, init) => {
  const request = new Request(input, init)
  const headers = new Headers(request.headers)
  headers.set('X-FalconBroom-Desktop-Token', secret)
  return original(new Request(request, { headers, redirect: 'error' }))
}
window.fetch = async function(input, init) {
  const request = new Request(input, init)
  const origin = new URL(request.url).origin
  if (origin === localUrl.origin) return localFetch(request)
  if (!remote || origin !== baseUrl.origin) return original(request)
  const leaseAbort = new AbortController()
  const remoteAbort = new AbortController()
  const cancel = () => {
    remoteAbort.abort()
    leaseAbort.abort()
  }
  if (request.signal.aborted) cancel()
  else request.signal.addEventListener('abort', cancel, {once: true})
  const release = () => {
    request.signal.removeEventListener('abort', cancel)
    leaseAbort.abort()
  }
  try {
    const lease = await localFetch(localUrl.href.replace(/\/$/, '') + '/desktop/remote-lease', {signal: leaseAbort.signal})
    if (!lease.ok) throw new Error('Remote backend is blocked by your remote-service preferences.')
    const leaseReader = lease.body.getReader()
    ;(async () => {
      try { while (!(await leaseReader.read()).done) {} } catch (_) {}
      if (!leaseAbort.signal.aborted) remoteAbort.abort()
    })()
    const response = await original(new Request(request, {signal: remoteAbort.signal, redirect: 'error'}))
    const copy = response.clone()
    ;(async () => {
      try {
        const reader = copy.body?.getReader()
        if (reader) while (!(await reader.read()).done) {}
      } finally {
        release()
      }
    })().catch(error => console.error('Remote response could not be drained', error))
    return response
  } catch (error) {
    release()
    throw error
  }
}
