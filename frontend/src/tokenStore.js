import { credentialStorage } from './credentialStorage'

const ACCESS_KEY = 'falconbroom_access_token'
const REFRESH_KEY = 'falconbroom_refresh_token'
const storage = credentialStorage || window.localStorage

export const getAccess = () => storage.getItem(ACCESS_KEY)
export const setAccess = value => storage.setItem(ACCESS_KEY, value)
export const removeAccess = () => storage.removeItem(ACCESS_KEY)
export const getRefresh = () => storage.getItem(REFRESH_KEY)
export const setRefresh = value => storage.setItem(REFRESH_KEY, value)
export const removeRefresh = () => storage.removeItem(REFRESH_KEY)

export default { getAccess, setAccess, removeAccess, getRefresh, setRefresh, removeRefresh }
