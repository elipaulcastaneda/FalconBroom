export function createDesktopStorage({ invoke, localStorage, onFailure }) {
  const memory = new Map()
  const volatileKeys = new Set()
  let warned = false
  const warn = (error) => {
    console.error('Desktop credential storage unavailable:', error)
    if (!warned) {
      warned = true
      onFailure()
    }
  }
  return {
    async getItem(key) {
      if (volatileKeys.has(key)) return memory.get(key) ?? null
      try {
        return await invoke('secure_store_get', { key })
      } catch (error) {
        warn(error)
        return memory.get(key) ?? null
      }
    },
    async setItem(key, value) {
      memory.set(key, value)
      try {
        await invoke('secure_store_set', { key, value })
        volatileKeys.delete(key)
      } catch (error) {
        volatileKeys.add(key)
        try {
          await invoke('secure_store_delete', { key })
        } catch (deleteError) {
          console.error('Unable to remove stale credentials:', deleteError)
        }
        warn(error)
      }
      localStorage.removeItem(key)
    },
    async removeItem(key) {
      memory.delete(key)
      volatileKeys.add(key)
      localStorage.removeItem(key)
      try {
        await invoke('secure_store_delete', { key })
      } catch (error) {
        warn(error)
        throw error
      }
    },
  }
}

export function removeLegacyDesktopCredentials(storage, supabaseStorageKey) {
  const keys = []
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i)
    if (key === 'falconbroom_access_token' || key === 'falconbroom_refresh_token' ||
        (supabaseStorageKey && (key === supabaseStorageKey ||
          (key?.startsWith(`${supabaseStorageKey}-`) && key.endsWith('-code-verifier'))))) keys.push(key)
  }
  keys.forEach(key => storage.removeItem(key))
}
