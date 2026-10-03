import React, { useState } from 'react'
import supabase from '../supabaseClient'

export default function SignIn({ onSignedIn }){
  const [identity, setIdentity] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e){
    e && e.preventDefault && e.preventDefault()
    setLoading(true); setError(null)
    try{
      // use Supabase signInWithPassword for email/username
      const { data, error } = await supabase.auth.signInWithPassword({ email: identity, password })
      if (error) {
        setError(error.message || 'Login failed')
        setLoading(false)
        return
      }
      const access = data?.session?.access_token
      if (access) {
        try { window.localStorage.setItem('falconbroom_access_token', access) } catch(e){}
      }
      try { window.dispatchEvent(new CustomEvent('fb_signed_in')) } catch(e){}
      if (onSignedIn) onSignedIn()
    }catch(err){
      setError(err.message || String(err))
    }finally{ setLoading(false) }
  }

  return (
    <form onSubmit={submit} style={{display:'flex',flexDirection:'column',gap:8}}>
      <input placeholder="Email" value={identity} onChange={e=>setIdentity(e.target.value)} />
      <input placeholder="Password" type="password" value={password} onChange={e=>setPassword(e.target.value)} />
      {error && <div style={{color:'var(--error)'}}>{error}</div>}
      <div style={{display:'flex',gap:8}}>
        <button type="submit" disabled={loading}>{loading? 'Signing...' : 'Sign in'}</button>
      </div>
    </form>
  )
}
