const MAX_BODY_BYTES = 4096
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function supabaseConfig(env) {
  function key(name, legacyName) {
    if (env[name]) {
      const parsed = JSON.parse(env[name])
      if (!parsed || typeof parsed.default !== 'string' || !parsed.default) {
        throw new Error('Invalid Supabase key configuration')
      }
      return parsed.default
    }
    return env[legacyName]
  }
  const url = env.SUPABASE_URL
  const anonKey = key('SUPABASE_PUBLISHABLE_KEYS', 'SUPABASE_ANON_KEY')
  const serviceKey = key('SUPABASE_SECRET_KEYS', 'SUPABASE_SERVICE_ROLE_KEY')
  if (!url || !anonKey || !serviceKey || new URL(url).protocol !== 'https:') {
    throw new Error('Missing or insecure Supabase configuration')
  }
  return { url, anonKey, serviceKey }
}

function respond(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

async function readBody(request) {
  if (!request.body) throw new Error('Missing request body')
  const reader = request.body.getReader()
  const parts = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > MAX_BODY_BYTES) {
        await reader.cancel()
        throw new Error('Request body too large')
      }
      parts.push(value)
    }
  } finally {
    reader.releaseLock()
  }
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const part of parts) {
    bytes.set(part, offset)
    offset += part.byteLength
  }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
}

// This endpoint is deliberately read-only; a preflight result is not a deletion authorization.
export async function handleDeletionPreflight(request, { getUser, verifyPassword, hasSharedData, logError }) {
  if (request.method !== 'POST') return respond(405, { error: 'Method not allowed' })
  const authorization = request.headers.get('authorization') || ''
  const match = /^Bearer ([^\s]+)$/i.exec(authorization)
  if (!match) return respond(401, { error: 'Authentication required' })
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get('content-type') || '')) {
    return respond(415, { error: 'JSON required' })
  }

  let body
  try {
    body = await readBody(request)
  } catch {
    return respond(400, { error: 'Invalid request body' })
  }
  if (!body || typeof body !== 'object' || Array.isArray(body) ||
      Object.keys(body).some(key => !['password', 'confirm_text'].includes(key)) ||
      typeof body.password !== 'string' || !body.password ||
      body.confirm_text !== 'DELETE MY ACCOUNT') {
    return respond(400, { error: 'Password and confirmation are required' })
  }

  try {
    const user = await getUser(match[1])
    if (!user || typeof user.id !== 'string' || !UUID.test(user.id) ||
        typeof user.email !== 'string' || !user.email) {
      return respond(401, { error: 'Authentication required' })
    }
    if (!await verifyPassword(user, body.password)) {
      return respond(401, { error: 'Unable to verify credentials' })
    }
    const shared = await hasSharedData(user.id)
    if (typeof shared !== 'boolean') throw new Error('Invalid preflight result')
    return respond(200, {
      status: shared ? 'shared_data_review_required' : 'preflight_only',
      message: shared
        ? 'Shared data requires review. No data or account was deleted.'
        : 'No covered shared data was detected. No data or account was deleted; this check does not authorize deletion.',
    })
  } catch {
    logError('Account deletion preflight failed')
    return respond(503, { error: 'Preflight unavailable. No deletion was performed.' })
  }
}

export function supabaseDependencies(createClient, url, anonKey, serviceKey, logError) {
  const options = { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } }
  const auth = createClient(url, anonKey, options)
  const admin = createClient(url, serviceKey, options)
  return {
    async getUser(token) {
      const { data, error } = await auth.auth.getUser(token)
      if (error) {
        if (error.status === 401 || error.status === 403) return null
        throw new Error('User verification failed')
      }
      return data.user
    },
    async verifyPassword(user, password) {
      const reauth = createClient(url, anonKey, options)
      const { data, error } = await reauth.auth.signInWithPassword({ email: user.email, password })
      if (error) {
        if (error.status === 400 || error.status === 401 || error.status === 403) return false
        throw new Error('Password verification failed')
      }
      const matches = data.user?.id === user.id
      const { error: signOutError } = await reauth.auth.signOut({ scope: 'local' })
      if (signOutError) throw new Error('Temporary session cleanup failed')
      return matches
    },
    async hasSharedData(userId) {
      const { data, error } = await admin.rpc('falconbroom_account_has_shared_data', { p_user_id: userId })
      if (error || typeof data !== 'boolean') throw new Error('Shared data check failed')
      return data
    },
    logError,
  }
}