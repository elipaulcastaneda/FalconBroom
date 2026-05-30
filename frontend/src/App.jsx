import React, {useState} from "react"

const BACKEND = "http://127.0.0.1:3008"

export default function App(){
  const [path, setPath] = useState("")
  const [tauriAvailable, setTauriAvailable] = useState(false)
  const [profile, setProfile] = useState(null)
  const [suggest, setSuggest] = useState(null)
  const [recipeText, setRecipeText] = useState("")
  const [preview, setPreview] = useState(null)
  const [applyRes, setApplyRes] = useState(null)

  async function doProfile(){
    const res = await fetch(BACKEND+"/profile",{
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({path})
    })
    const j = await res.json()
    setProfile(j.profile)
  }

  async function pickFile(){
    // If running inside Tauri, call the Rust command that opens the native dialog
    try{
      const tauri = await import('@tauri-apps/api')
      const { invoke } = tauri
      const res = await invoke('pick_file_and_profile')
      // res is the backend response (e.g., { profile: { ... } })
      if(res && res.profile){
        // set profile and try to extract path from returned detail if present
        setProfile(res.profile)
      }
      // If the response included the path, set it too (some platforms return the path)
      if(res && res.path){
        setPath(res.path)
      }
    }catch(e){
      // Not in Tauri or command failed: fall back to the dialog module
      try{
        const dialog = await import('@tauri-apps/api/dialog')
        const selected = await dialog.open({multiple:false, directory:false})
        if(selected){ setPath(selected) }
      }catch(_e){
        alert('Tauri dialog not available. Please type the path manually.')
      }
    }
  }

  async function doSuggest(){
    const res = await fetch(BACKEND+"/suggest",{
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({path})
    })
    const j = await res.json()
    setSuggest(j.suggestions)
    // populate recipe editor with a suggested skeleton
    const rec = {sources:[{path}], cleaning_steps: j.suggestions.map(s=>({action:s.action,column:s.column,params:{strategy:s.strategy}})), outputs:[{path:"output_preview.csv"}]}
    setRecipeText(JSON.stringify(rec, null, 2))
  }

  async function doPreview(){
    try{
      const recipe = JSON.parse(recipeText)
      const res = await fetch(BACKEND+"/preview",{
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(recipe)
      })
      const j = await res.json()
      setPreview(j.preview)
    }catch(e){
      alert("Invalid recipe JSON: "+e.message)
    }
  }

  async function doApply(){
    try{
      const recipe = JSON.parse(recipeText)
      const res = await fetch(BACKEND+"/apply",{
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(recipe)
      })
      const j = await res.json()
      setApplyRes(j.result)
    }catch(e){
      alert("Invalid recipe JSON: "+e.message)
    }
  }

  return (
    <div className="container">
      <h1>FalconBroom — Prototype UI</h1>
      <div className="controls">
        <label>Local CSV Path (backend-accessible):</label>
        <input value={path} onChange={e=>setPath(e.target.value)} placeholder="C:/data/customers.csv" />
        <div className="buttons">
          <button onClick={pickFile}>Pick file (Tauri)</button>
          <button onClick={doProfile}>Profile</button>
          <button onClick={doSuggest}>Suggest</button>
        </div>
      </div>

      <section>
        <h2>Profile</h2>
        <pre>{profile?JSON.stringify(profile,null,2):"(no profile yet)"}</pre>
      </section>

      <section>
        <h2>Suggested Recipe</h2>
        <pre>{suggest?JSON.stringify(suggest,null,2):"(no suggestions)"}</pre>
      </section>

      <section>
        <h2>Recipe Editor</h2>
        <textarea value={recipeText} onChange={e=>setRecipeText(e.target.value)} rows={12}></textarea>
        <div className="buttons">
          <button onClick={doPreview}>Preview</button>
          <button onClick={doApply}>Apply</button>
        </div>
      </section>

      <section>
        <h2>Preview Diff (first rows)</h2>
        {preview ? (
          <div className="preview">
            <div>
              <h3>Before</h3>
              <pre>{JSON.stringify(preview.before,null,2)}</pre>
            </div>
            <div>
              <h3>After</h3>
              <pre>{JSON.stringify(preview.after,null,2)}</pre>
            </div>
          </div>
        ) : <div>(no preview yet)</div>}
      </section>

      <section>
        <h2>Apply Result</h2>
        <pre>{applyRes?JSON.stringify(applyRes,null,2):"(not applied)"}</pre>
      </section>
    </div>
  )
}
