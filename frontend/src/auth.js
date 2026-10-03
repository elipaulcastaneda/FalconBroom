import { useState, useEffect } from 'react'
import supabase from './supabaseClient'
import authFetch from './utils/authFetch'
import { BACKEND } from './config'

export function useAccountUser(){
  const [accountUser, setAccountUser] = useState(null)
  const [loading, setLoading] = useState(false)

  async function loadRemote(){
    setLoading(true)
    try{
      const res = await authFetch(`${BACKEND}/me`, { method: 'GET' })
      if (!res.ok) {
        setAccountUser(null)
        setLoading(false)
        return
      }
      const j = await res.json()
      setAccountUser(j)
    }catch(e){
      setAccountUser(null)
    }finally{ setLoading(false) }
  }

  useEffect(()=>{
    // initial: check supabase user and then remote /me
    let mounted = true
    async function init(){
      setLoading(true)
      try{
        const { data } = await supabase.auth.getUser()
        // try to load remote mapping; backend will accept Supabase access token via authFetch
        await loadRemote()
      }catch(e){
        // fallback: load remote anyway
        await loadRemote()
      }finally{ if (mounted) setLoading(false) }
    }
    init()

    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED'){
        // refresh remote mapping
        loadRemote()
      } else if (event === 'SIGNED_OUT'){
        setAccountUser(null)
      }
    })

    return ()=>{
      mounted = false
      try{ sub?.subscription?.unsubscribe && sub.subscription.unsubscribe() }catch(e){}
    }
  }, [])

  return { accountUser, loading, reload: loadRemote }
}
