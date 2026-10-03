import React, { useState } from 'react'
import supabase from '../supabaseClient'

export default function SignOut({ onSignedOut }){
  const [loading, setLoading] = useState(false)
  async function doSignOut(){
    setLoading(true)
    try{
      await supabase.auth.signOut()
      try { window.localStorage.removeItem('falconbroom_access_token') } catch(e){}
      try { window.localStorage.setItem('fb_manual_signed_out','1') } catch(e){}
      try { window.dispatchEvent(new CustomEvent('fb_signed_out')) } catch(e){}
      if (onSignedOut) onSignedOut()
    }catch(e){
      console.error('Sign out error', e)
    }finally{ setLoading(false) }
  }

  return (<button onClick={doSignOut} disabled={loading}>{loading? 'Signing out...' : 'Sign out'}</button>)
}
