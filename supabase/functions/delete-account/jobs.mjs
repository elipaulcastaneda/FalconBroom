import { readBody } from './core.mjs'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const RECOVERY_TOKEN = /^[0-9a-f]{64}$/

async function hashRecoveryToken(token) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

function respond(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

export async function handleDeletionJobRequest(request, deps) {
  if (request.method !== 'POST') return respond(405, { error: 'Method not allowed' })
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') || '')) {
    return respond(415, { error: 'JSON required' })
  }
  let body
  try {
    body = await readBody(request)
  } catch {
    return respond(400, { error: 'Invalid request body' })
  }
  const validKeys = body?.action === 'start'
    ? ['action', 'password', 'confirm_text', 'recovery_token']
    : body?.action === 'status'
      ? ['action', 'recovery_token', 'job_id']
      : ['action', 'password', 'confirm_text', 'job_id', 'recovery_token']
  if (!body || typeof body !== 'object' || Array.isArray(body) ||
      !['start', 'local_complete', 'status'].includes(body.action) ||
      Object.keys(body).some(key => !validKeys.includes(key)) ||
      typeof body.recovery_token !== 'string' || !RECOVERY_TOKEN.test(body.recovery_token) ||
      (body.action !== 'status' &&
       (typeof body.password !== 'string' || !body.password || body.confirm_text !== 'DELETE MY ACCOUNT')) ||
      (body.action !== 'start' &&
       (typeof body.job_id !== 'string' || !UUID.test(body.job_id)))) {
    return respond(400, { error: 'Invalid request' })
  }
  try {
    const recoveryHash = await hashRecoveryToken(body.recovery_token)
    if (body.action === 'status') {
      const result = await deps.getStatus(body.job_id, recoveryHash)
      if (result === null) return respond(404, { error: 'Deletion job not found' })
      if (!result || result.job_id !== body.job_id ||
          !['awaiting_local', 'ready', 'processing', 'needs_review', 'completed'].includes(result.status)) {
        throw new Error('Invalid status result')
      }
      return respond(200, { job_id: result.job_id, status: result.status })
    }
    const bearer = /^Bearer ([^\s]+)$/i.exec(request.headers.get('authorization') || '')
    if (!bearer) return respond(401, { error: 'Authentication required' })
    const user = await deps.getUser(bearer[1])
    if (!user || typeof user.id !== 'string' || !UUID.test(user.id) ||
        typeof user.email !== 'string' || !user.email) {
      return respond(401, { error: 'Authentication required' })
    }
    if (!await deps.verifyPassword(user, body.password)) {
      return respond(401, { error: 'Unable to verify credentials' })
    }
    const hasMfa = await deps.hasMfa(user.id)
    if (typeof hasMfa !== 'boolean') throw new Error('Invalid MFA result')
    if (hasMfa) return respond(409, { status: 'mfa_review_required' })

    if (body.action === 'start') {
      const result = await deps.createJob(user.id, recoveryHash)
      if (!result || typeof result.status !== 'string' ||
          !['awaiting_local', 'ready', 'processing', 'needs_review', 'completed',
            'storage_review_required', 'shared_data_review_required', 'recovery_review_required'].includes(result.status)) {
        throw new Error('Invalid job result')
      }
      if (!['storage_review_required', 'shared_data_review_required', 'recovery_review_required'].includes(result.status) &&
          (typeof result.job_id !== 'string' || !UUID.test(result.job_id))) {
        throw new Error('Invalid job ID')
      }
      return respond(200, result.job_id
        ? { status: result.status, job_id: result.job_id, user_id: user.id, email: user.email }
        : { status: result.status })
    }

    const accepted = await deps.localComplete(user.id, body.job_id, recoveryHash)
    if (typeof accepted !== 'boolean') throw new Error('Invalid acknowledgement result')
    if (!accepted) return respond(404, { error: 'Deletion job not found' })
    return respond(200, { status: 'accepted', job_id: body.job_id })
  } catch {
    deps.logError('Deletion job request failed')
    return respond(503, { error: 'Deletion service unavailable' })
  }
}

export function deletionJobDependencies(authDeps, admin) {
  async function rpc(name, args) {
    const { data, error } = await admin.rpc(name, args)
    if (error) throw new Error('Deletion job RPC failed')
    return data
  }
  return {
    ...authDeps,
    async hasMfa(userId) {
      const { data, error } = await admin.auth.admin.mfa.listFactors({ userId })
      if (error || !Array.isArray(data?.factors) ||
          data.factors.some(factor => !factor || !['verified', 'unverified'].includes(factor.status))) {
        throw new Error('MFA lookup failed')
      }
      return data.factors.some(factor => factor.status === 'verified')
    },
    createJob: (userId, recoveryHash) => rpc('falconbroom_create_deletion_job', {
      p_user_id: userId, p_recovery_hash: recoveryHash,
    }),
    localComplete: (userId, jobId, recoveryHash) => rpc('falconbroom_deletion_local_complete', {
      p_user_id: userId, p_job_id: jobId, p_recovery_hash: recoveryHash,
    }),
    getStatus: (jobId, recoveryHash) => rpc('falconbroom_deletion_job_status', {
      p_job_id: jobId, p_recovery_hash: recoveryHash,
    }),
  }
}
