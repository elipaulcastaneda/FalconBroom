// Allow runtime override (set by Tauri) via window.__FALCONBROOM_BACKEND_URL.
// Otherwise prefer Vite env, then fall back to localhost:3009 for development.
const runtime = (typeof window !== 'undefined' && window.__FALCONBROOM_BACKEND_URL) ? window.__FALCONBROOM_BACKEND_URL : undefined
const apiUrl = runtime || import.meta.env.VITE_API_URL || 'http://127.0.0.1:3009'
export const BACKEND = apiUrl
