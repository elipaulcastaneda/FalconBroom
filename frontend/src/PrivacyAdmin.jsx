import React, { useEffect, useState } from 'react'
import { BACKEND } from './config'
import authFetch from './utils/authFetch'

export default function PrivacyAdmin({ addToast }) {
  const [inventory, setInventory] = useState([])
  const [dpias, setDpias] = useState([])
  const [loading, setLoading] = useState(false)

  const [newInv, setNewInv] = useState({ title: '', description: '', data_categories: '' })
  const [newDpia, setNewDpia] = useState({ title: '', summary: '' })

  // UI helpers: search, pagination, edit state
  const [invSearch, setInvSearch] = useState('')
  const [dpiaSearch, setDpiaSearch] = useState('')
  const [invPage, setInvPage] = useState(0)
  const [dpiaPage, setDpiaPage] = useState(0)
  const PAGE_SIZE = 8
  const [editingInvId, setEditingInvId] = useState(null)
  const [editingDpiaId, setEditingDpiaId] = useState(null)
  const [editInvVals, setEditInvVals] = useState({})
  const [editDpiaVals, setEditDpiaVals] = useState({})

  async function loadAll() {
    setLoading(true)
    try {
      const r1 = await authFetch(`${BACKEND}/privacy/inventory`)
      if (r1.ok) setInventory(await r1.json())
      else addToast('Failed to load inventory: ' + (await r1.text()), 'error')
      const r2 = await authFetch(`${BACKEND}/privacy/dpias`)
      if (r2.ok) setDpias(await r2.json())
      else addToast('Failed to load DPIAs: ' + (await r2.text()), 'error')
    } catch (e) {
      addToast('Load error: ' + e.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadAll() }, [])

  async function addInventory(e) {
    e.preventDefault()
    try {
      const res = await authFetch(`${BACKEND}/privacy/inventory`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newInv)
      })
      if (!res.ok) { addToast('Add inventory failed: ' + (await res.text()), 'error'); return }
      addToast('Inventory entry added', 'success')
      setNewInv({ title: '', description: '', data_categories: '' })
      loadAll()
    } catch (e) { addToast('Add inventory error: ' + e.message, 'error') }
  }

  async function addDpia(e) {
    e.preventDefault()
    try {
      const res = await authFetch(`${BACKEND}/privacy/dpias`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(newDpia)
      })
      if (!res.ok) { addToast('Add DPIA failed: ' + (await res.text()), 'error'); return }
      addToast('DPIA added', 'success')
      setNewDpia({ title: '', summary: '' })
      loadAll()
    } catch (e) { addToast('Add DPIA error: ' + e.message, 'error') }
  }

  async function deleteInventory(id) {
    try {
      const res = await authFetch(`${BACKEND}/privacy/inventory/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!res.ok) { addToast('Delete failed: ' + (await res.text()), 'error'); return }
      addToast('Inventory deleted', 'success')
      loadAll()
    } catch (e) { addToast('Delete error: ' + e.message, 'error') }
  }

  async function updateInventory(id, payload) {
    try {
      const res = await authFetch(`${BACKEND}/privacy/inventory/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) { addToast('Update failed: ' + (await res.text()), 'error'); return }
      addToast('Inventory updated', 'success')
      setEditingInvId(null)
      loadAll()
    } catch (e) { addToast('Update error: ' + e.message, 'error') }
  }

  async function deleteDpia(id) {
    try {
      const res = await authFetch(`${BACKEND}/privacy/dpias/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!res.ok) { addToast('Delete failed: ' + (await res.text()), 'error'); return }
      addToast('DPIA deleted', 'success')
      loadAll()
    } catch (e) { addToast('Delete error: ' + e.message, 'error') }
  }

  async function updateDpia(id, payload) {
    try {
      const res = await authFetch(`${BACKEND}/privacy/dpias/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) { addToast('Update failed: ' + (await res.text()), 'error'); return }
      addToast('DPIA updated', 'success')
      setEditingDpiaId(null)
      loadAll()
    } catch (e) { addToast('Update error: ' + e.message, 'error') }
  }

  return (
    <div style={{marginTop:12,border:'1px solid rgba(255,255,255,0.03)',padding:12,borderRadius:6}}>
      <h4 style={{marginTop:0}}>Privacy Inventory & DPIAs</h4>
      <div style={{display:'flex',gap:12,alignItems:'flex-start'}}>
        <div style={{flex:1}}>
          <h5 style={{margin:'6px 0'}}>Inventory</h5>
          <form onSubmit={addInventory}>
            <label style={{display:'block',marginBottom:6}}>Title
              <input value={newInv.title} onChange={(e)=>setNewInv({...newInv,title:e.target.value})} style={{width:'100%'}} />
            </label>
            <label style={{display:'block',marginBottom:6}}>Data categories (comma separated)
              <input value={newInv.data_categories} onChange={(e)=>setNewInv({...newInv,data_categories:e.target.value})} style={{width:'100%'}} />
            </label>
            <label style={{display:'block',marginBottom:6}}>Description
              <input value={newInv.description} onChange={(e)=>setNewInv({...newInv,description:e.target.value})} style={{width:'100%'}} />
            </label>
            <div><button className="primary" type="submit">Add inventory</button></div>
          </form>
          <div style={{marginTop:8}}>
            <div style={{display:'flex',gap:8,marginBottom:8}}>
              <input placeholder="Search inventory" value={invSearch} onChange={(e)=>{ setInvSearch(e.target.value); setInvPage(0) }} />
              <div style={{marginLeft:'auto'}}>
                <button onClick={()=>{ setInvPage(Math.max(0, invPage-1)) }} disabled={invPage===0}>Prev</button>
                <button style={{marginLeft:8}} onClick={()=>{ setInvPage(invPage+1) }} disabled={(invPage+1)*PAGE_SIZE >= (inventory.filter(i => !invSearch || (i.title||'').toLowerCase().includes(invSearch.toLowerCase())).length)}>Next</button>
              </div>
            </div>
            {loading ? <div>Loading...</div> : (
              <div>
                {inventory.length === 0 ? <div className="empty-state">No inventory entries</div> : (
                  <div>
                    {inventory.filter(i => !invSearch || (i.title||'').toLowerCase().includes(invSearch.toLowerCase())).slice(invPage*PAGE_SIZE, (invPage+1)*PAGE_SIZE).map((it) => (
                      <div key={it.id || it.title} style={{padding:'8px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                        {editingInvId === it.id ? (
                          <div>
                            <input value={editInvVals.title||''} onChange={(e)=>setEditInvVals({...editInvVals,title:e.target.value})} placeholder="Title" />
                            <input value={editInvVals.data_categories||''} onChange={(e)=>setEditInvVals({...editInvVals,data_categories:e.target.value})} placeholder="Data categories" />
                            <input value={editInvVals.description||''} onChange={(e)=>setEditInvVals({...editInvVals,description:e.target.value})} placeholder="Description" />
                            <div style={{marginTop:6}}>
                              <button onClick={()=>updateInventory(it.id, editInvVals)}>Save</button>
                              <button style={{marginLeft:8}} onClick={()=>{ setEditingInvId(null); setEditInvVals({}) }}>Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <div>
                            <strong>{it.title}</strong> <small style={{color:'var(--muted)'}}> {it.data_categories||''}</small>
                            <div style={{color:'var(--muted)'}}>{it.description}</div>
                            <div style={{marginTop:6}}>
                              <button onClick={()=>{ setEditingInvId(it.id); setEditInvVals({ title: it.title, description: it.description, data_categories: it.data_categories }) }}>Edit</button>
                              <button style={{marginLeft:8}} onClick={()=>{ if(window.confirm('Delete this inventory item?')) deleteInventory(it.id) }}>Delete</button>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div style={{width:2,background:'rgba(255,255,255,0.02)'}} />
        <div style={{flex:1}}>
          <h5 style={{margin:'6px 0'}}>DPIAs</h5>
          <form onSubmit={addDpia}>
            <label style={{display:'block',marginBottom:6}}>Title
              <input value={newDpia.title} onChange={(e)=>setNewDpia({...newDpia,title:e.target.value})} style={{width:'100%'}} />
            </label>
            <label style={{display:'block',marginBottom:6}}>Summary
              <input value={newDpia.summary} onChange={(e)=>setNewDpia({...newDpia,summary:e.target.value})} style={{width:'100%'}} />
            </label>
            <div><button className="primary" type="submit">Add DPIA</button></div>
          </form>
          <div style={{marginTop:8}}>
            <div style={{display:'flex',gap:8,marginBottom:8}}>
              <input placeholder="Search DPIAs" value={dpiaSearch} onChange={(e)=>{ setDpiaSearch(e.target.value); setDpiaPage(0) }} />
              <div style={{marginLeft:'auto'}}>
                <button onClick={()=>setDpiaPage(Math.max(0, dpiaPage-1))} disabled={dpiaPage===0}>Prev</button>
                <button style={{marginLeft:8}} onClick={()=>setDpiaPage(dpiaPage+1)} disabled={(dpiaPage+1)*PAGE_SIZE >= (dpias.filter(d => !dpiaSearch || (d.title||'').toLowerCase().includes(dpiaSearch.toLowerCase())).length)}>Next</button>
              </div>
            </div>
            {loading ? <div>Loading...</div> : (
              <div>
                {dpias.length === 0 ? <div className="empty-state">No DPIAs</div> : (
                  <div>
                    {dpias.filter(d => !dpiaSearch || (d.title||'').toLowerCase().includes(dpiaSearch.toLowerCase())).slice(dpiaPage*PAGE_SIZE, (dpiaPage+1)*PAGE_SIZE).map((d) => (
                      <div key={d.id || d.title} style={{padding:'8px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                        {editingDpiaId === d.id ? (
                          <div>
                            <input value={editDpiaVals.title||''} onChange={(e)=>setEditDpiaVals({...editDpiaVals,title:e.target.value})} placeholder="Title" />
                            <input value={editDpiaVals.summary||''} onChange={(e)=>setEditDpiaVals({...editDpiaVals,summary:e.target.value})} placeholder="Summary" />
                            <div style={{marginTop:6}}>
                              <button onClick={()=>updateDpia(d.id, editDpiaVals)}>Save</button>
                              <button style={{marginLeft:8}} onClick={()=>{ setEditingDpiaId(null); setEditDpiaVals({}) }}>Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <div>
                            <strong>{d.title}</strong>
                            <div style={{color:'var(--muted)'}}>{d.summary}</div>
                            <div style={{marginTop:6}}>
                              <button onClick={()=>{ setEditingDpiaId(d.id); setEditDpiaVals({ title: d.title, summary: d.summary }) }}>Edit</button>
                              <button style={{marginLeft:8}} onClick={()=>{ if(window.confirm('Delete this DPIA entry?')) deleteDpia(d.id) }}>Delete</button>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      <div style={{marginTop:8}}>
        <button onClick={loadAll}>Refresh</button>
      </div>
    </div>
  )
}
