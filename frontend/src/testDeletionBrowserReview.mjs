const LOCK_NAME = 'falconbroom-test-deletion-workflow'
const PENDING_KEY = 'falconbroom-test-deletion-pending'
const GENERATION_KEY = 'falconbroom-test-deletion-generation'
const ACCOUNT_PREFIX = 'falconbroom-test-workflow:'
const DELETED_PREFIX = 'falconbroom-test-deleted:'
const ACCOUNT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const WORKFLOW_TYPES = ['history', 'drafts', 'checkpoints']

export function hasPendingBrowserDeletion(storage) {
  return storage.getItem(PENDING_KEY) !== null
}

export function testWorkflowKey(userId, type) {
  if (typeof userId !== 'string' || !ACCOUNT_ID.test(userId) || !WORKFLOW_TYPES.includes(type)) {
    throw new Error('Verified test account is required for workflow storage')
  }
  return `${ACCOUNT_PREFIX}${userId.toLowerCase()}:${type}`
}

function pendingOwner(storage) {
  const pending = storage.getItem(PENDING_KEY)
  if (pending === null) return null
  if (!ACCOUNT_ID.test(pending)) throw new Error('Browser deletion state requires review')
  return pending.toLowerCase()
}

function removeOwnedWorkflowData(storage, owner) {
  storage.setItem(`${DELETED_PREFIX}${owner}`, '1')
  for (const type of WORKFLOW_TYPES) storage.removeItem(testWorkflowKey(owner, type))
}

export function hasUnownedWorkflowData(storage) {
  const keys = ['falconbroom-upload-history', 'falconbroom-upload-drafts']
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index)
    if (key && key.startsWith('falconbroom-workflow-checkpoints:')) keys.push(key)
  }
  for (const key of keys) {
    const raw = storage.getItem(key)
    if (raw === null) continue
    let value
    try {
      value = JSON.parse(raw)
    } catch {
      return true
    }
    if (key === 'falconbroom-upload-drafts') {
      if (!value || Array.isArray(value) || typeof value !== 'object' || Object.keys(value).length) return true
    } else if (!Array.isArray(value) || value.length) {
      return true
    }
  }
  return storage.getItem('falconbroom_delete_token') !== null
}

function requireLocks(locks) {
  if (!locks || typeof locks.request !== 'function') {
    throw new Error('Desktop workflow coordination is unavailable')
  }
}

function permitsWorkflowWrites(state) {
  return state.local_pending === false && Array.isArray(state.deletions) &&
    state.deletions.every(job =>
      (job.status === 'start_unconfirmed' || job.status === 'awaiting_local') && job.local_status === 'pending' ||
      job.status === 'completed' && job.local_status === 'completed')
}

export function writeCoordinatedWorkflowData(storage, locks, owner, write) {
  requireLocks(locks)
  const normalizedOwner = testWorkflowKey(owner, 'history').split(':')[1]
  if (storage.getItem(`${DELETED_PREFIX}${normalizedOwner}`) !== null) throw new Error('Test account deletion has started')
  if (storage.getItem(PENDING_KEY) !== null) throw new Error('Test deletion is pending')
  const generation = storage.getItem(GENERATION_KEY)
  return locks.request(LOCK_NAME, () => {
    if (storage.getItem(`${DELETED_PREFIX}${normalizedOwner}`) !== null) {
      throw new Error('Test account deletion has started')
    }
    if (storage.getItem(PENDING_KEY) !== null || storage.getItem(GENERATION_KEY) !== generation) {
      throw new Error('Test deletion interrupted a workflow save')
    }
    return write()
  })
}

export function startCoordinatedTestDeletion(storage, locks, owner, verify, start) {
  requireLocks(locks)
  const normalizedOwner = testWorkflowKey(owner, 'history').split(':')[1]
  return locks.request(LOCK_NAME, async () => {
    if (hasUnownedWorkflowData(storage)) {
      return { ok: false, status: 'browser_review_required' }
    }
    const pending = pendingOwner(storage)
    if (pending !== null && pending !== normalizedOwner) {
      throw new Error('Another browser account deletion is pending')
    }
    if (pending === null) {
      const state = await verify()
      if (!permitsWorkflowWrites(state)) {
        throw new Error('Desktop deletion state requires review')
      }
      storage.setItem(GENERATION_KEY, crypto.randomUUID())
      storage.setItem(PENDING_KEY, normalizedOwner)
    }
    const result = await start()
    if (result?.ok === true && result.local_status === 'completed') {
      if (typeof result.user_id !== 'string' || result.user_id.toLowerCase() !== normalizedOwner) {
        throw new Error('Deletion response account does not match browser data')
      }
      removeOwnedWorkflowData(storage, normalizedOwner)
    }
    return result
  })
}

export function checkCoordinatedTestDeletion(storage, locks, check) {
  requireLocks(locks)
  return locks.request(LOCK_NAME, async () => {
    const owner = pendingOwner(storage)
    const result = await check()
    if (owner !== null && Array.isArray(result.deletions) &&
        result.deletions.some(job => job.user_id === owner && job.local_status === 'completed')) {
      removeOwnedWorkflowData(storage, owner)
    }
    if (permitsWorkflowWrites(result)) {
      if (owner !== null && !result.deletions.some(job => job.user_id === owner)) {
        throw new Error('Browser deletion state requires review')
      }
      storage.removeItem(PENDING_KEY)
    }
    return result
  })
}
