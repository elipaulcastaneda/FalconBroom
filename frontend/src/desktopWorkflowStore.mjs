const kinds = ['checkpoints', 'drafts', 'history']
const copy = value => JSON.parse(JSON.stringify(value))

export function createDesktopWorkflowStore(account, backend, fetcher, onError) {
  const entries = {}
  const controller = new AbortController()
  let closed = false
  let failure = null
  let pending = 0

  async function request(kind, options = {}) {
    const response = await fetcher(`${backend}/desktop/account-workflow/${kind}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'X-FalconBroom-Expected-Account': account,
        ...(options.headers || {}),
      },
    })
    if (!response.ok) throw new Error(`Account workflow storage failed (${response.status})`)
    return response.json()
  }

  return {
    async load() {
      const results = await Promise.all(kinds.map(kind => request(kind)))
      if (closed) throw new Error('Account workflow storage was closed')
      kinds.forEach((kind, index) => {
        const result = results[index]
        const valid = kind === 'drafts'
          ? result.data && typeof result.data === 'object' && !Array.isArray(result.data)
          : Array.isArray(result.data)
        if (!valid || (result.revision !== null && !/^[a-f0-9]{64}$/.test(result.revision))) {
          throw new Error('Invalid account workflow data')
        }
        entries[kind] = { data: result.data, revision: result.revision, queue: Promise.resolve() }
      })
    },
    get(kind) {
      if (closed || !entries[kind]) throw new Error('Account workflow storage is unavailable')
      return copy(entries[kind].data)
    },
    update(kind, change) {
      if (closed || failure || !entries[kind]) throw new Error('Account workflow saves are paused')
      const entry = entries[kind]
      const next = copy(change(copy(entry.data)))
      entry.data = next
      pending += 1
      entry.queue = entry.queue.then(async () => {
        if (closed || failure) return
        const result = await request(kind, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ data: next, revision: entry.revision }),
        })
        if (!/^[a-f0-9]{64}$/.test(result.revision)) throw new Error('Invalid account workflow revision')
        entry.revision = result.revision
      }).catch(error => {
        if (!closed && !failure) {
          failure = error
          onError(error)
        }
      }).finally(() => {
        pending -= 1
      })
      return copy(next)
    },
    close() {
      closed = true
      controller.abort()
      return pending > 0
    },
  }
}
