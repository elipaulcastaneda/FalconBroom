import { processDeletionJob } from './worker.mjs'

function respond(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

async function sameSecret(expected, supplied) {
  const encoder = new TextEncoder()
  const expectedHash = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(expected)))
  const suppliedHash = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(supplied)))
  let different = 0
  for (let i = 0; i < expectedHash.length; i++) different |= expectedHash[i] ^ suppliedHash[i]
  return different === 0
}

// Opt-in is not a substitute for actually isolating privileged Storage writes
// before and throughout every job.
export async function handleDeletionWorkerRequest(request, { enabled, secret, worker, logError }) {
  if (enabled !== true || typeof secret !== 'string' || secret.length < 32) {
    return respond(503, { error: 'Deletion worker unavailable' })
  }
  if (request.method !== 'POST') return respond(405, { error: 'Method not allowed' })
  const supplied = request.headers.get('x-falconbroom-worker-secret') || ''
  if (supplied.length > 512 || !await sameSecret(secret, supplied)) {
    return respond(401, { error: 'Authentication required' })
  }
  try {
    const result = await processDeletionJob(worker)
    return respond(200, result)
  } catch {
    logError('Deletion worker request failed')
    return respond(503, { error: 'Deletion worker unavailable' })
  }
}
