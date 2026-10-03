// tokenStore: abstraction over secure storage (Tauri) with localStorage fallback
// Provides async get/set/remove for access and refresh tokens.
const ACCESS_KEY = 'falconbroom_access_token'
const REFRESH_KEY = 'falconbroom_refresh_token'

async function _invokeTauri(cmd, args) {
  try {
    // dynamic import to avoid bundling Tauri API into web build
    const tauri = await import('@tauri-apps/api/tauri')
    if (tauri && typeof tauri.invoke === 'function') {
      return await tauri.invoke(cmd, args || {})
    }
  } catch (e) {
    // not running under Tauri or import failed
  }
  throw new Error('tauri-invoke-unavailable')
}

async function _getSecure(key) {
  try {
    const res = await _invokeTauri('secure_store_get', { key })
    return res && typeof res === 'string' ? res : null
  } catch (e) {
    return null
  }
}

async function _setSecure(key, val) {
  try {
    await _invokeTauri('secure_store_set', { key, value: val })
    return true
  } catch (e) {
    return false
  }
}

async function _deleteSecure(key) {
  try {
    await _invokeTauri('secure_store_delete', { key })
    return true
  } catch (e) {
    return false
  }
}

export async function getAccess() {
  // try secure store first
  const s = await _getSecure(ACCESS_KEY)
  if (s) return s
  try { return window.localStorage.getItem(ACCESS_KEY) } catch(e){return null}
}

export async function setAccess(val) {
  try { window.localStorage.setItem(ACCESS_KEY, val) } catch(e){}
  await _setSecure(ACCESS_KEY, val)
}

export async function removeAccess() {
  try { window.localStorage.removeItem(ACCESS_KEY) } catch(e){}
  await _deleteSecure(ACCESS_KEY)
}

export async function getRefresh() {
  const s = await _getSecure(REFRESH_KEY)
  if (s) return s
  try { return window.localStorage.getItem(REFRESH_KEY) } catch(e){return null}
}

export async function setRefresh(val) {
  try { window.localStorage.setItem(REFRESH_KEY, val) } catch(e){}
  await _setSecure(REFRESH_KEY, val)
}

export async function removeRefresh() {
  try { window.localStorage.removeItem(REFRESH_KEY) } catch(e){}
  await _deleteSecure(REFRESH_KEY)
}

export default {
  getAccess,
  setAccess,
  removeAccess,
  getRefresh,
  setRefresh,
  removeRefresh,
}
