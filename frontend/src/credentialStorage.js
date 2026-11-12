import { createDesktopStorage, removeLegacyDesktopCredentials } from './desktopStorage.mjs'

export const isDesktop = typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__)

let storage = null
if (isDesktop) {
  let supabaseStorageKey
  try {
    const url = String(import.meta.env.VITE_SUPABASE_URL || '').trim().replace(/^["']|["']$/g, '').trim()
    supabaseStorageKey = `sb-${new URL(url).hostname.split('.')[0]}-auth-token`
  } catch (error) {
    console.warn('Cannot identify legacy Supabase credentials; remove them manually if needed.', error)
  }
  removeLegacyDesktopCredentials(window.localStorage, supabaseStorageKey)
  storage = createDesktopStorage({
    invoke: (command, args) => window.__TAURI_INTERNALS__.invoke(command, args),
    localStorage: window.localStorage,
    onFailure: () => {
      window.__FB_CREDENTIAL_STORAGE_WARNING = true
      window.dispatchEvent(new Event('fb_credential_storage_warning'))
    },
  })
}

export const credentialStorage = storage
