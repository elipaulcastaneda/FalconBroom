import React, {useMemo, useRef, useState, useEffect} from "react"
import { BACKEND } from './config'
import authFetch from './utils/authFetch'
const NAV_ITEMS = [
  { section: "Start", id: "source", label: "Source", detail: "Profile and prompt to recipe", icon: "⟡" },
  { section: "Start", id: "uploads", label: "Uploads", detail: "Saved uploads", icon: "⇪" },
  { section: "Build", id: "joins", label: "Joins", detail: "Match and merge hints", icon: "⧉" },
  { section: "Review", id: "preview", label: "Preview", detail: "Compare before and after", icon: "↔" },
  { section: "Account", id: "settings", label: "Settings", detail: "Account & privacy", icon: "⚙" },
  { section: "Account", id: "team", label: "Team", detail: "Team members & sharing", icon: "👥" },
]

const NAV_SECTIONS = ["Start", "Build", "Review", "Account"]

function Card({ eyebrow, title, subtitle, children, className = "" }) {
  return (
    <section className={`card ${className}`.trim()}>
      {(eyebrow || title || subtitle) && (
        <div className="card-header">
          {eyebrow && <span className="eyebrow">{eyebrow}</span>}
          {title && <h2>{title}</h2>}
          {subtitle && <p>{subtitle}</p>}
        </div>
      )}
      {children}
    </section>
  )
}

function Stat({ label, value, tone = "neutral" }) {
  return (
    <div className={`stat stat-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function JsonBlock({ value, empty }) {
  if (value === null || value === undefined) {
    return <div className="empty-state">{empty}</div>
  }
  return <pre className="json-block json-block-soft">{JSON.stringify(value, null, 2)}</pre>
}

function formatCount(value) {
  if (value === null || value === undefined) return "—"
  return String(value)
}

function loadUploadHistory() {
  try {
    const raw = window.localStorage.getItem("falconbroom-upload-history")
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export default function App() {
  const uploadInputRef = useRef(null)
  const recipeNameRef = useRef(null)
  const instructionRef = useRef(null)
  const runFormatRef = useRef(null)
  const INSPECT_PAGE_SIZE = 100
  const [path, setPath] = useState("")
  const [instruction, setInstruction] = useState("")
  const [leftPath, setLeftPath] = useState("")
  const [rightPath, setRightPath] = useState("")
  const [uploadTarget, setUploadTarget] = useState(null) // 'left'|'right'|null
  const [profile, setProfile] = useState(null)
  const [suggest, setSuggest] = useState(null)
  const [cleaningSuggestions, setCleaningSuggestions] = useState(null)
  const [joinSuggestions, setJoinSuggestions] = useState(null)
  const [joinPreviewResult, setJoinPreviewResult] = useState(null)
  const [joinPreviewLoading, setJoinPreviewLoading] = useState(false)
  const [joinExporting, setJoinExporting] = useState(false)
  const [exportFormat, setExportFormat] = useState('csv')
  const [exportFilename, setExportFilename] = useState('joined')
  const [joinLeftOn, setJoinLeftOn] = useState('')
  const [joinRightOn, setJoinRightOn] = useState('')
  const [joinType, setJoinType] = useState('inner')
  const [joinSampleSize, setJoinSampleSize] = useState(5)
  const [joinLeftPreview, setJoinLeftPreview] = useState(null)
  const [joinRightPreview, setJoinRightPreview] = useState(null)
  const [suffixLeft, setSuffixLeft] = useState('_left')
  const [suffixRight, setSuffixRight] = useState('_right')
  const [preferResolve, setPreferResolve] = useState('left')
  const [mappingText, setMappingText] = useState('')
  const [joinLeftColsOptions, setJoinLeftColsOptions] = useState([])
  const [joinRightColsOptions, setJoinRightColsOptions] = useState([])
  const [joinLeftOnArr, setJoinLeftOnArr] = useState([])
  const [joinRightOnArr, setJoinRightOnArr] = useState([])
  const [leftFilter, setLeftFilter] = useState('')
  const [rightFilter, setRightFilter] = useState('')
  const [compositePairs, setCompositePairs] = useState([])
  const [recipeFromText, setRecipeFromText] = useState(null)
  const [recipeText, setRecipeText] = useState("")
  const [sourceInspection, setSourceInspection] = useState(null)
  const [showAsTable, setShowAsTable] = useState(true)
  const [diagnostics, setDiagnostics] = useState(null)
  const [inspectOffset, setInspectOffset] = useState(0)
  const [preview, setPreview] = useState(null)
  const [applyRes, setApplyRes] = useState(null)
  const [recipeId, setRecipeId] = useState("")
  const [recipeStatus, setRecipeStatus] = useState("")
  const [showApproveConfirm, setShowApproveConfirm] = useState(false)
  const [historyList, setHistoryList] = useState([])
  const [toasts, setToasts] = useState([])
  const [toastArchive, setToastArchive] = useState([])
  const [showToastPanel, setShowToastPanel] = useState(false)

  const toastTimeouts = useRef({})

  function removeToast(id) {
    setToasts((t) => t.filter((x) => x.id !== id))
    const to = toastTimeouts.current[id]
    if (to) {
      clearTimeout(to)
      delete toastTimeouts.current[id]
    }
  }

  function addToast(message, tone = "info", ttl = 4200) {
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`
    setToasts((t) => [...t, { id, message, tone, ttl }])
    // also record into archive for history panel (keep most recent 50)
    const archived = { id, message, tone, ts: new Date().toISOString() }
    setToastArchive((a) => [archived, ...a].slice(0, 50))
    const to = setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id))
      delete toastTimeouts.current[id]
    }, ttl)
    toastTimeouts.current[id] = to
  }
  const [uploadHistory, setUploadHistory] = useState(() => loadUploadHistory())
  const [uploadsList, setUploadsList] = useState([])
  const [uploadExplanationsOpen, setUploadExplanationsOpen] = useState({})
  const [uploadExplanationsData, setUploadExplanationsData] = useState({})
  const [uploadsLoading, setUploadsLoading] = useState(false)
  const [userId, setUserId] = useState(() => {
    try {
      let id = window.localStorage.getItem('falconbroom_user_id')
      if (!id) {
        id = `user_${Math.random().toString(36).slice(2,9)}`
        window.localStorage.setItem('falconbroom_user_id', id)
      }
      return id
    } catch {
      return null
    }
  })
  const [consent, setConsent] = useState(() => {
    try { return JSON.parse(window.localStorage.getItem('falconbroom_consent') || 'null') } catch { return null }
  })
  const [consentHistory, setConsentHistory] = useState(null)
  const [showConsentHistory, setShowConsentHistory] = useState(false)
  const [analyticsId, setAnalyticsId] = useState(() => {
    try { return window.localStorage.getItem('falconbroom_analytics_id') || '' } catch { return '' }
  })
  const [authToken, setAuthToken] = useState(() => { try { return window.localStorage.getItem('falconbroom_access_token') || '' } catch { return '' } })
  const [accountUser, setAccountUser] = useState(null)
  const [signupUsername, setSignupUsername] = useState('')
  const [signupEmail, setSignupEmail] = useState('')
  const [signupPassword, setSignupPassword] = useState('')
  const [loginIdentity, setLoginIdentity] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [teamName, setTeamName] = useState('')
  const [teamMembers, setTeamMembers] = useState([])
  const [teamMemberObjects, setTeamMemberObjects] = useState([])
  const [pendingInvites, setPendingInvites] = useState([])
  const [sharedUploads, setSharedUploads] = useState([])
  const [wsConnected, setWsConnected] = useState(false)
  const [inviteToken, setInviteToken] = useState('')
  const [acceptUsername, setAcceptUsername] = useState('')
  const [acceptPasswordLocal, setAcceptPasswordLocal] = useState('')
  const [newMemberEmail, setNewMemberEmail] = useState('')
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteConfirmTextLocal, setDeleteConfirmTextLocal] = useState('')
  const [inspectionLoading, setInspectionLoading] = useState(false)
  const [inspectingPath, setInspectingPath] = useState(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [applyLoading, setApplyLoading] = useState(false)
  const [recipeGenerating, setRecipeGenerating] = useState(false)
  const [generatedPreview, setGeneratedPreview] = useState(null)
  const [prevRecipeText, setPrevRecipeText] = useState(null)
  const [rowsToShow, setRowsToShow] = useState(6)
  const [selectedColumns, setSelectedColumns] = useState(null) // null = all
  const [showColumnPicker, setShowColumnPicker] = useState(false)
  const [selectedCells, setSelectedCells] = useState({})
  const [tooltip, setTooltip] = useState(null)
  const [showFullPreview, setShowFullPreview] = useState(false)
  const [showRecipeJson, setShowRecipeJson] = useState(false)
  const [showConfirmModal, setShowConfirmModal] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [previewRows, setPreviewRows] = useState(6)
  const [showDedupeConfirm, setShowDedupeConfirm] = useState(false)
  const [dedupeConfirmText, setDedupeConfirmText] = useState('')
  const [candidateColumns, setCandidateColumns] = useState([])
  const [pendingGenerated, setPendingGenerated] = useState(null)
  const [lastGeneratedResponse, setLastGeneratedResponse] = useState(null)
  const [explanations, setExplanations] = useState(null)
  const [showExplanations, setShowExplanations] = useState(false)
  const [explainLoading, setExplainLoading] = useState(false)
  const [showCustomRevisions, setShowCustomRevisions] = useState(false)
  const [showDuplicatesModal, setShowDuplicatesModal] = useState(false)
  const [duplicateGroups, setDuplicateGroups] = useState([])
  const [regressionModel, setRegressionModel] = useState('linear')
  const [regressionFeatures, setRegressionFeatures] = useState('')
  const [regressionGroupBy, setRegressionGroupBy] = useState('')
  const [treatAsMissing, setTreatAsMissing] = useState('')
  const [theme, setTheme] = useState(() => window.localStorage.getItem("falconbroom-theme") || "dark")
  const [railCollapsed, setRailCollapsed] = useState(() => window.localStorage.getItem("falconbroom-rail-collapsed") === "true")
  const [activeTab, setActiveTab] = useState("source")

  const profileCount = profile ? Object.keys(profile).length : 0
  const suggestedCount = suggest ? suggest.length : 0
  const previewCount = preview ? preview.before?.length || 0 : 0
  const recipeSteps = useMemo(() => {
    try {
      const parsed = recipeText ? JSON.parse(recipeText) : null
      return parsed?.cleaning_steps?.length || 0
    } catch {
      return 0
    }
  }, [recipeText])

  const backgroundBusy = uploadsLoading || inspectionLoading || profileLoading || previewLoading || applyLoading

  const activeItem = NAV_ITEMS.find((item) => item.id === activeTab) || NAV_ITEMS[0]

  const themeClass = theme === "light" ? "theme-light" : "theme-dark"

  function persistTheme(nextTheme) {
    setTheme(nextTheme)
    window.localStorage.setItem("falconbroom-theme", nextTheme)
  }

  function persistRailCollapsed(nextValue) {
    setRailCollapsed(nextValue)
    window.localStorage.setItem("falconbroom-rail-collapsed", String(nextValue))
  }

  function persistUploadHistory(nextHistory) {
    const trimmed = nextHistory.slice(0, 8)
    setUploadHistory(trimmed)
    window.localStorage.setItem("falconbroom-upload-history", JSON.stringify(trimmed))
  }

  async function doInspectSource(nextPath, nextOffset = 0) {
    try {
      const res = await fetch(`${BACKEND}/inspect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: nextPath, offset: nextOffset, limit: INSPECT_PAGE_SIZE }),
      })
      if (!res.ok) {
        const text = await res.text()
        console.error('Inspect failed', res.status, text)
        addToast(`Inspect failed: ${res.status}`,'error',6000)
        return
      }
      const j = await res.json()
      const sanitized = sanitizeInspection(j.inspection)
      setSourceInspection(sanitized)
      setDiagnostics(sanitized?.diagnostics || null)
      // build a lightweight profile from the reconstructed inspection so
      // the UI (uploads tab) shows only data columns, not metadata/extraction cols
      try {
        const p = {}
        const cols = sanitized?.columns || []
        const diag = sanitized?.diagnostics || {}
        cols.forEach((c) => {
          try {
            const info = diag[c] || {}
            p[c] = { dtype: 'str', nulls: info.missing_count || 0, unique: info.unique_count || 0 }
          } catch (e) {
            p[c] = { dtype: 'str', nulls: 0, unique: 0 }
          }
        })
        setProfile(p)
      } catch (e) {
        // ignore profile build errors
      }
      setInspectOffset(nextOffset)
    // mark last refreshed on uploads list for UI
    setUploadsList((list) => {
      if (!list) return list
      return list.map((it) => {
        try {
          if (_normalizePathMatch(it.path || '', nextPath || '')) {
            return { ...it, last_refreshed: new Date().toISOString() }
          }
        } catch (e) {
          // ignore
        }
        return it
      })
    })
    } catch (err) {
      console.error('Inspect request failed', err)
      addToast('Inspect request failed: ' + (err.message || String(err)), 'error', 6000)
    }
  }

  async function doProfileForPath(nextPath) {
    setProfileLoading(true)
    try {
      const profileRes = await fetch(`${BACKEND}/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: nextPath }),
      })
      if (!profileRes.ok) {
        const text = await profileRes.text()
        console.error('Profile failed', profileRes.status, text)
        addToast(`Profile failed: ${profileRes.status}`,'error',6000)
        setProfileLoading(false)
        return
      }
      const profilePayload = await profileRes.json()
      setProfile(profilePayload.profile)
      await doInspectSource(nextPath, 0)
    } catch (err) {
      console.error('Profile request failed', err)
      addToast('Profile request failed: ' + (err.message || String(err)), 'error', 6000)
    } finally {
      setProfileLoading(false)
    }
  }

  useEffect(() => {
    async function fetchMe() {
      let token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (!token) {
        // try to refresh via httpOnly cookie
        try {
          const rres = await fetch(`${BACKEND}/refresh`, { method: 'POST', credentials: 'include' })
          if (rres.ok) {
            const jr = await rres.json()
            token = jr.access_token
            saveToken(token)
          }
        } catch (e) {
          // ignore
        }
      }
      if (!token) return
      try {
        const res = await authFetch(`${BACKEND}/me`, { headers: {} })
        if (!res.ok) {
          setAccountUser(null)
          return
        }
        const j = await res.json()
        setAccountUser(j)
        setTeamName(j.team_name || '')
        setTeamMembers(j.team_members || [])
      } catch (e) {
        // ignore
      }
    }
    fetchMe()
  }, [authToken])

  // unauthenticated view (rendered inside main return to keep hooks order stable)
  const unauthView = !accountUser ? (
    <div className={`auth-root-wrap`}>
      <div className="toasts-root" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone || 'info'}`}>
            <div className="toast-left"><span className="toast-icon" aria-hidden>{t.tone === 'success' ? '✔' : t.tone === 'error' ? '✖' : t.tone === 'warn' ? '⚠' : 'ℹ'}</span></div>
            <div className="toast-body">{t.message}</div>
            <button className="toast-close" aria-label="Dismiss" onClick={() => removeToast(t.id)}>✕</button>
            <div className="toast-progress" style={{animationDuration: `${t.ttl || 4200}ms`}} />
          </div>
        ))}
      </div>

      <div className="auth-root" style={{display:'flex',justifyContent:'center',alignItems:'center',minHeight:'60vh',padding:24}}>
        <div style={{maxWidth:720,display:'flex',gap:24,alignItems:'flex-start',width:'100%'}}>
          <div className="card" style={{flex:1}}>
            <div className="card-header"><h2>Welcome back</h2><p>Log in to access your team, uploads, and recipes.</p></div>
            <div style={{padding:12}}>
              <label>Username or email</label>
              <input value={loginIdentity} onChange={(e)=>setLoginIdentity(e.target.value)} placeholder="username or email" />
              <label style={{marginTop:8}}>Password</label>
              <input type="password" value={loginPassword} onChange={(e)=>setLoginPassword(e.target.value)} placeholder="password" />
              <div style={{marginTop:12,display:'flex',gap:8}}>
                <button className="primary" onClick={doLogin}>Log in</button>
                <button onClick={()=>{ setLoginIdentity(''); setLoginPassword('') }}>Clear</button>
              </div>
              <div style={{marginTop:12,color:'var(--muted)'}}>If you don't have an account, create one below.</div>
            </div>
          </div>
          <div className="card" style={{flex:1}}>
            <div className="card-header"><h2>Create account</h2><p>Sign up to save your work and invite teammates.</p></div>
            <div style={{padding:12}}>
              <label>Username</label>
              <input value={signupUsername} onChange={(e)=>setSignupUsername(e.target.value)} placeholder="username" />
              <label style={{marginTop:8}}>Email</label>
              <input value={signupEmail} onChange={(e)=>setSignupEmail(e.target.value)} placeholder="you@example.com" />
              <label style={{marginTop:8}}>Password</label>
              <input type="password" value={signupPassword} onChange={(e)=>setSignupPassword(e.target.value)} placeholder="password" />
              <div style={{marginTop:12,display:'flex',gap:8}}>
                <button className="primary" onClick={doSignup}>Create account</button>
                <button onClick={()=>{ setSignupUsername(''); setSignupEmail(''); setSignupPassword('') }}>Clear</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  ) : null
  async function fetchSharedUploads() {
    try {
      const res = await fetch(`${BACKEND}/uploads/shared`)
      if (!res.ok) return
      const j = await res.json()
      setSharedUploads(j.uploads || [])
    } catch (e) {
      // ignore
    }
  }

  async function inviteTeamMember() {
    if (!newMemberEmail || !newMemberEmail.includes('@')) { addToast('Enter a valid email', 'warn'); return }
    try {
      const res = await authFetch(`${BACKEND}/team/invite`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: newMemberEmail, team_name: teamName }) })
      if (!res.ok) { const t = await res.text(); addToast('Invite failed: ' + t, 'error'); return }
      addToast('Invite sent', 'success')
      setTeamMembers((s) => [...(s || []), newMemberEmail])
      setNewMemberEmail('')
    } catch (e) { addToast('Invite error: ' + e.message, 'error') }
  }

  async function fetchTeamInvites() {
    try {
      const res = await authFetch(`${BACKEND}/team/invites`, { method: 'GET' })
      if (!res.ok) return
      const j = await res.json()
      setPendingInvites(j.invites || [])
    } catch (e) {
      // ignore
    }
  }

  async function fetchTeamMembers() {
    try {
      const res = await authFetch(`${BACKEND}/team/members`, { method: 'GET' })
      if (!res.ok) return
      const j = await res.json()
      setTeamMemberObjects(j.members || [])
      setTeamName(j.team_name || teamName)
    } catch (e) {}
  }

  async function adminRevokeInvite(inviteId) {
    try {
      if (!window.confirm('Revoke this invite?')) return
      const res = await authFetch(`${BACKEND}/team/invites/${encodeURIComponent(inviteId)}`, { method: 'DELETE' })
      if (!res.ok) { addToast('Revoke failed', 'error'); return }
      addToast('Invite revoked', 'info')
      await fetchTeamInvites()
    } catch (e) { addToast('Revoke error: ' + e.message, 'error') }
  }

  async function changeMemberRole(email, role) {
    try {
      const res = await authFetch(`${BACKEND}/team/members`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'update_role', email, role }) })
      if (!res.ok) { addToast('Role update failed', 'error'); return }
      addToast('Role updated', 'success')
      await fetchTeamMembers()
    } catch (e) { addToast('Role update error: ' + e.message, 'error') }
  }

  async function transferOwnership(email) {
    try {
      if (!window.confirm(`Transfer ownership to ${email}? This will make them the team owner.`)) return
      const res = await authFetch(`${BACKEND}/team/owners`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'transfer', email }) })
      if (!res.ok) { const t = await res.text(); addToast('Transfer failed: ' + t, 'error'); return }
      addToast('Ownership transferred', 'success')
      await fetchTeamMembers()
      await fetchTeamInvites()
    } catch (e) { addToast('Transfer error: ' + e.message, 'error') }
  }

  async function acceptInvite() {
    if (!inviteToken) { addToast('Enter invite token', 'warn'); return }
    try {
      const res = await fetch(`${BACKEND}/team/accept`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: inviteToken, username: acceptUsername || undefined, password: acceptPasswordLocal || undefined }), credentials: 'include' })
      if (!res.ok) { const t = await res.text(); addToast('Accept failed: ' + t, 'error'); return }
      const j = await res.json()
      if (j.access_token) saveToken(j.access_token)
      addToast('Invite accepted', 'success')
      setInviteToken(''); setAcceptUsername(''); setAcceptPasswordLocal('')
      // refresh user info
      try { const mres = await authFetch(`${BACKEND}/me`, { headers: {} }); if (mres.ok) { const mj = await mres.json(); setAccountUser(mj); setTeamName(mj.team_name || ''); setTeamMembers(mj.team_members || []) } } catch(e){}
    } catch (e) { addToast('Accept error: ' + e.message, 'error') }
  }

  async function removeTeamMember(email) {
    try {
      // call server-side removal (owner endpoint) if authenticated
      const token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (token) {
        const res = await authFetch(`${BACKEND}/team/members`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'remove', email }) })
        if (!res.ok) { addToast('Remove failed', 'error'); return }
        addToast('Removed ' + email, 'info')
        await fetchTeamMembers()
        await fetchTeamInvites()
        return
      }
      const next = (teamMembers || []).filter(x => x !== email)
      setTeamMembers(next)
      await saveAccountUpdates()
      addToast('Removed ' + email, 'info')
    } catch (e) { addToast('Remove failed: ' + e.message, 'error') }
  }

  async function toggleShare(upload, share) {
    try {
      const res = await authFetch(`${BACKEND}/uploads/${encodeURIComponent(upload.name)}/share`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shared: !!share }) })
      if (!res.ok) { const t = await res.text(); addToast('Share failed: ' + t, 'error'); return }
      addToast((share ? 'Shared' : 'Unshared') + ' ' + upload.name, 'success')
      await fetchSharedUploads()
      await fetchUploads()
    } catch (e) { addToast('Share error: ' + e.message, 'error') }
  }

  // Open WebSocket when Team tab is active for live updates
  useEffect(() => {
    let ws = null
    if (activeTab === 'team') {
      fetchTeamInvites()
      fetchTeamMembers()
      try {
        const wsUrl = (BACKEND || '').replace(/^http/, 'ws') + '/ws/shared'
        ws = new WebSocket(wsUrl)
        ws.onopen = () => {
          setWsConnected(true)
          // request initial state via server-initiated init message
        }
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data)
            if (msg.type === 'init' && Array.isArray(msg.uploads)) {
              setSharedUploads(msg.uploads)
            } else if (msg.type === 'shared_changed' || msg.type === 'uploads_changed') {
              // refresh lists when server indicates a change
              fetchSharedUploads()
              fetchUploads()
            }
          } catch (e) {
            // ignore parse errors
          }
        }
        ws.onclose = () => { setWsConnected(false) }
        ws.onerror = () => { setWsConnected(false) }
      } catch (e) {
        setWsConnected(false)
      }
    }
    return () => { try { if (ws) ws.close() } catch (e) {} }
  }, [activeTab])

  function saveToken(access) {
    setAuthToken(access)
    try { window.localStorage.setItem('falconbroom_access_token', access) } catch {}
  }

  // use global authFetch from ./utils/authFetch

  async function doSignup() {
    try {
      const res = await fetch(`${BACKEND}/signup`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: signupUsername, email: signupEmail, password: signupPassword }) })
      if (!res.ok) {
        const txt = await res.text()
        addToast('Signup failed: ' + txt, 'error')
        return
      }
      addToast('Account created — please log in', 'success')
      setSignupUsername(''); setSignupEmail(''); setSignupPassword('')
    } catch (e) { addToast('Signup error: ' + e.message, 'error') }
  }

  async function doLogin() {
    try {
      const payload = loginIdentity.includes('@') ? { email: loginIdentity, password: loginPassword, persistent: true } : { username: loginIdentity, password: loginPassword, persistent: true }
      const res = await fetch(`${BACKEND}/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), credentials: 'include' })
      if (!res.ok) {
        addToast('Login failed', 'error')
        return
      }
      const j = await res.json()
      saveToken(j.access_token)
      setLoginIdentity(''); setLoginPassword('')
      addToast('Logged in', 'success')
    } catch (e) { addToast('Login error: ' + e.message, 'error') }
  }

  async function doLogout() {
    try {
      const token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (token) await authFetch(`${BACKEND}/logout`, { method: 'POST' })
    } catch (e) { /* ignore */ }
    setAccountUser(null); setAuthToken(''); try { window.localStorage.removeItem('falconbroom_access_token') } catch {}
    // refresh cookie cleared by server during logout
    addToast('Logged out', 'info')
  }

  async function saveAccountUpdates() {
    try {
      const token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (!token) { addToast('Not authenticated', 'error'); return }
      const body = { email: accountUser?.email || '', team_name: teamName, team_members: teamMembers }
      const res = await authFetch(`${BACKEND}/account`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!res.ok) { addToast('Update failed', 'error'); return }
      const j = await res.json()
      setAccountUser(j)
      addToast('Account updated', 'success')
    } catch (e) { addToast('Update error: ' + e.message, 'error') }
  }

  async function doAccountExport() {
    try {
      const token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (!token) { addToast('Not authenticated', 'error'); return }
      const res = await authFetch(`${BACKEND}/account/export`, { method: 'POST' })
      if (!res.ok) { addToast('Export request failed', 'error'); return }
      const j = await res.json()
      addToast('Export enqueued: ' + j.job_id, 'info')
    } catch (e) { addToast('Export error: ' + e.message, 'error') }
  }

  async function doAccountDelete() {
    try {
      const token = authToken || window.localStorage.getItem('falconbroom_access_token')
      if (!token) { addToast('Not authenticated', 'error'); return }
      const res = await authFetch(`${BACKEND}/account/delete`, { method: 'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify({ password: deletePassword, confirm_text: deleteConfirmTextLocal }) })
      if (!res.ok) { const t = await res.text(); addToast('Delete failed: ' + t, 'error'); return }
      addToast('Account deletion requested', 'info')
      doLogout()
    } catch (e) { addToast('Delete error: ' + e.message, 'error') }
  }

  async function doProfile() {
    await doProfileForPath(path)
  }

  async function uploadSelectedFile(file) {
    if (!file) return

    const form = new FormData()
    form.append("file", file)

    const res = await fetch(`${BACKEND}/upload`, {
      method: "POST",
      body: form,
    })

    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || "Upload failed")
    }

    const payload = await res.json()
    setPath(payload.path)
    // If upload was triggered from the Joins tab, set the appropriate path
    try {
      if (uploadTarget === 'left') setLeftPath(payload.path)
      else if (uploadTarget === 'right') setRightPath(payload.path)
    } catch (e) {}
    // reset upload target after use
    setUploadTarget(null)

    const historyItem = {
      name: payload.name || file.name,
      path: payload.path,
      size: payload.size || file.size,
      uploadedAt: new Date().toISOString(),
    }
    const deduped = [historyItem, ...uploadHistory.filter((item) => item.path !== historyItem.path)]
    persistUploadHistory(deduped)
    // refresh server-side uploads list
    fetchUploads()

    // show custom revisions box after upload
    setShowCustomRevisions(true)

    await doProfileForPath(payload.path)
  }

  async function fetchUploads() {
    setUploadsLoading(true)
    try {
      const res = await fetch(`${BACKEND}/uploads`)
      const j = await res.json()
      setUploadsList(j.uploads || [])
    } catch (e) {
      // fallback to local history
      setUploadsList(uploadHistory || [])
    } finally {
      setUploadsLoading(false)
    }
  }

  async function toggleUploadExplanations(item) {
    const key = item.path
    setUploadExplanationsOpen((s) => ({ ...(s || {}), [key]: !(s && s[key]) }))
    // if opening, fetch persisted explanations (server) or use in-memory
    if (!uploadExplanationsOpen[key]) {
      // try to use item.explanations_history if present
      const hist = item.explanations_history || []
      if (hist && hist.length > 0) {
        // enrich each entry by fetching recipe metadata to ensure it's approved
        const enriched = []
        for (const entry of hist) {
          try {
            const rid = entry.recipe_id
            let recipe_name = null
            let status = null
            try {
              const rres = await fetch(`${BACKEND}/recipes/${encodeURIComponent(rid)}`)
              if (rres.ok) {
                const rj = await rres.json()
                status = rj.status || null
                recipe_name = rj.name || null
              }
            } catch (e) {}
            // only include entries with approved recipes
            if (status === 'approved') {
              enriched.push({ recipe_id: rid, recipe_name, timestamp: entry.timestamp, explanations: entry.explanations })
            }
          } catch (e) {
            continue
          }
        }
        setUploadExplanationsData((s) => ({ ...(s || {}), [key]: enriched }))
      } else {
        setUploadExplanationsData((s) => ({ ...(s || {}), [key]: [] }))
      }
    }
  }

  React.useEffect(() => {
    fetchUploads()
  }, [])

  // When the generated recipe or the rowsToShow selection changes, refresh the generated preview
  React.useEffect(() => {
    if (!recipeFromText) return
    let cancelled = false
    ;(async () => {
      try {
        const nParam = typeof rowsToShow === 'number' ? rowsToShow : Number(rowsToShow)
        const previewQs = []
        if (nParam != null) previewQs.push(`n=${encodeURIComponent(nParam)}`)
        const previewUrl = `${BACKEND}/preview${previewQs.length ? `?${previewQs.join('&')}` : ''}`
        const res = await fetch(previewUrl, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(recipeFromText) })
        if (!res.ok) {
          console.warn('Generated preview refresh failed', await res.text())
          return
        }
        const pj = await res.json()
        if (!cancelled) setGeneratedPreview(sanitizePreview(pj.preview))
      } catch (e) {
        console.warn('Failed to refresh generated preview', e)
      }
    })()
    return () => { cancelled = true }
  }, [rowsToShow, recipeFromText])

  React.useEffect(() => {
    if (showCustomRevisions) {
      // small delay to ensure modal is mounted
      setTimeout(() => instructionRef.current?.focus(), 80)
    }
    function onKey(e){
      if(!showCustomRevisions) return
      if(e.key === 'Escape'){
        setShowCustomRevisions(false)
      }
      if((e.ctrlKey || e.metaKey) && e.key === 'Enter'){
        // accept generated recipe if present
        if(generatedPreview) acceptGenerated()
      }
    }
    window.addEventListener('keydown', onKey)
    return ()=> window.removeEventListener('keydown', onKey)
  }, [showCustomRevisions])

  // populate column options for left/right when paths change and are present in uploads
  React.useEffect(() => {
    let cancelled = false
    async function fetchLeftCols() {
      if (!leftPath) { setJoinLeftColsOptions([]); return }
      try {
        const presentPaths = (uploadsList || []).map((u) => u.path)
        if (!presentPaths.includes(leftPath)) { setJoinLeftColsOptions([]); return }
        const res = await fetch(`${BACKEND}/inspect`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: leftPath, offset: 0, limit: 1 }) })
        if (!res.ok) return
        const j = await res.json()
        if (cancelled) return
        const insp = j.inspection || j
        let cols = []
        if (insp && insp.columns && Array.isArray(insp.columns)) cols = insp.columns
        else if (insp && insp.rows && Array.isArray(insp.rows) && insp.rows.length > 0 && typeof insp.rows[0] === 'object') cols = Object.keys(insp.rows[0])
        setJoinLeftColsOptions(cols || [])
      } catch (e) { setJoinLeftColsOptions([]) }
    }
    fetchLeftCols()
    return () => { cancelled = true }
  }, [leftPath, uploadsList])

  React.useEffect(() => {
    let cancelled = false
    async function fetchRightCols() {
      if (!rightPath) { setJoinRightColsOptions([]); return }
      try {
        const presentPaths = (uploadsList || []).map((u) => u.path)
        if (!presentPaths.includes(rightPath)) { setJoinRightColsOptions([]); return }
        const res = await fetch(`${BACKEND}/inspect`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: rightPath, offset: 0, limit: 1 }) })
        if (!res.ok) return
        const j = await res.json()
        if (cancelled) return
        const insp = j.inspection || j
        let cols = []
        if (insp && insp.columns && Array.isArray(insp.columns)) cols = insp.columns
        else if (insp && insp.rows && Array.isArray(insp.rows) && insp.rows.length > 0 && typeof insp.rows[0] === 'object') cols = Object.keys(insp.rows[0])
        setJoinRightColsOptions(cols || [])
      } catch (e) { setJoinRightColsOptions([]) }
    }
    fetchRightCols()
    return () => { cancelled = true }
  }, [rightPath, uploadsList])

  // keep compositePairs in sync with selected arrays
  React.useEffect(()=>{
    const maxLen = Math.max((joinLeftOnArr||[]).length, (joinRightOnArr||[]).length)
    const next = []
    for(let i=0;i<maxLen;i++) next.push({ left: joinLeftOnArr[i] || '', right: joinRightOnArr[i] || '' })
    setCompositePairs(next)
  }, [joinLeftOnArr, joinRightOnArr])

  const pairWarnings = React.useMemo(()=>{
    const msgs = []
    if(!compositePairs || !compositePairs.length) return msgs
    const empty = compositePairs.filter(p=> !p.left || !p.right)
    if(empty.length) msgs.push(`${empty.length} pair(s) missing left or right value`)
    const lefts = compositePairs.map(p=>p.left).filter(Boolean)
    const rights = compositePairs.map(p=>p.right).filter(Boolean)
    const dupLefts = lefts.filter((v,i,a)=> a.indexOf(v)!==i)
    const dupRights = rights.filter((v,i,a)=> a.indexOf(v)!==i)
    if(dupLefts.length) msgs.push(`Duplicate left keys: ${[...new Set(dupLefts)].join(', ')}`)
    if(dupRights.length) msgs.push(`Duplicate right keys: ${[...new Set(dupRights)].join(', ')}`)
    return msgs
  }, [compositePairs])

  // Remove metadata keys (starting with `_`) from preview rows before rendering
  function _stripMeta(obj) {
    if (!obj || typeof obj !== 'object') return obj
    const out = {}
    Object.entries(obj).forEach(([k, v]) => {
      if (!k || typeof k !== 'string') return
      // skip internal underscore keys
      if (k.startsWith('_')) return
      // skip known metadata keys
      const metaKeys = ['container_name', 'sheet_name', 'slide_number', 'paragraph_index', 'table_index', 'column_index', 'cell_label', 'source_kind', 'source_name', 'source_path', 'unit_kind', 'row_index']
      if (metaKeys.includes(k)) return
      out[k] = v
    })
    return out
  }

  const META_KEYS = ['container_name','sheet_name','slide_number','paragraph_index','table_index','column_index','cell_label','source_kind','source_name','source_path','unit_kind','row_index']

  function sanitizePreview(p) {
    if (!p) return p
    const copy = { ...p }
    try {
      copy.before = (p.before || []).map((r) => _stripMeta(r))
      copy.after = (p.after || []).map((r) => _stripMeta(r))
    } catch (e) {
      // if unexpected shape, return original
      return p
    }
    return copy
  }

  function sanitizeInspection(ins) {
    if (!ins) return ins
    try {
      const metaKeys = ['container_name', 'sheet_name', 'slide_number', 'paragraph_index', 'table_index', 'column_index', 'cell_label', 'source_kind', 'source_name', 'source_path', 'unit_kind', 'row_index']
      // remove metadata columns from columns array
      const cols = (ins.columns || []).filter((c) => !metaKeys.includes(c))
      // strip metadata keys from each row
      const rows = (ins.rows || []).map((r) => {
        const out = {}
        Object.entries(r || {}).forEach(([k, v]) => {
          if (!k || typeof k !== 'string') return
          if (k.startsWith('_')) return
          if (metaKeys.includes(k)) return
          out[k] = v
        })
        return out
      })
      // prune diagnostics entries for metadata columns
      const diag = ins.diagnostics || {}
      const newDiag = Object.entries(diag).reduce((acc, [k, v]) => {
        if (!metaKeys.includes(k)) acc[k] = v
        return acc
      }, {})
      return { ...ins, columns: cols, rows, diagnostics: newDiag }
    } catch (e) {
      return ins
    }
  }

  // Remove cleaning steps that target metadata/internal columns
  function sanitizeRecipe(r) {
    if (!r) return r
    try {
      const metaKeys = ['container_name', 'sheet_name', 'slide_number', 'paragraph_index', 'table_index', 'column_index', 'cell_label', 'source_kind', 'source_name', 'source_path', 'unit_kind', 'row_index']
      const copy = JSON.parse(JSON.stringify(r))
      if (Array.isArray(copy.cleaning_steps)) {
        copy.cleaning_steps = copy.cleaning_steps.filter((s) => {
          try {
            const col = s && s.column
            if (!col || typeof col !== 'string') return true
            if (metaKeys.includes(col)) return false
            if (col.startsWith('_')) return false
            return true
          } catch (e) {
            return true
          }
        })
      }
      return copy
    } catch (e) {
      return r
    }
  }

  function _cellKey(rowIndex, col) { return `${rowIndex}|${col}` }
  function toggleCellSelection(rowIndex, col) {
    const k = _cellKey(rowIndex, col)
    setSelectedCells((s) => {
      const next = { ...(s || {}) }
      if (next[k]) delete next[k]
      else next[k] = true
      return next
    })
  }

  async function applySelectedChanges() {
    const patches = []
    if (!generatedPreview) {
      addToast('No generated preview to apply selected changes from', 'warn')
      return
    }
    const before = generatedPreview.before || []
    const after = generatedPreview.after || []
    Object.keys(selectedCells || {}).forEach((k) => {
      const [r, col] = k.split('|')
      const ri = Number(r)
      const newVal = (after[ri] || {})[col]
      patches.push({ row: ri, column: col, value: newVal })
    })
    if (patches.length === 0) {
      addToast('No cells selected', 'warn')
      return
    }
    try {
      const res = await fetch(`${BACKEND}/apply-patch`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, patches })
      })
      if (!res.ok) {
        let txt = await res.text()
        addToast('Apply selected failed: ' + txt, 'error')
        return
      }
      const j = await res.json()
      addToast(`Applied ${patches.length} selected changes`, 'success')
      // if server returned a patched file, add it to uploads list and open it
      if (j && (j.upload_path || j.patched_path)) {
        const usePath = j.upload_path || j.patched_path
        const parts = String(usePath).split(/\\|\//)
        const name = parts[parts.length-1]
        const newItem = { name, path: usePath, size: j.size || 0, modified_at: j.modified_at || new Date().toISOString() }
        setUploadsList((u) => {
          const existing = (u || []).filter(x => x.path !== newItem.path)
          return [newItem, ...existing]
        })
        // open and inspect the patched file
        try { await openUploadItem(newItem) } catch (err) { console.warn('Failed opening patched item', err) }
      }
      // clear selection and refresh preview/inspection
      setSelectedCells({})
      // try to refresh preview if possible
      try { await doPreview() } catch {}
    } catch (e) {
      addToast('Apply selected failed: ' + (e.message || e), 'error')
    }
  }

  // Removed automatic generation. Users must click "Generate JSON".

  function _normalizePathMatch(a, b) {
    if (!a || !b) return false
    const na = a.replace(/\\/g, '/').toLowerCase()
    const nb = b.replace(/\\/g, '/').toLowerCase()
    return na === nb || na.endsWith(nb) || nb.endsWith(na) || na.includes(nb) || nb.includes(na)
  }

  async function openUploadItem(item) {
    // item: {name, path, ...}
    setPath(item.path)
    // show custom revisions when opening an upload
    setShowCustomRevisions(true)
    // show full data preview for saved uploads
    setShowFullPreview(true)
    // hide raw recipe JSON by default (use instruction -> auto-generate)
    setShowRecipeJson(false)
    setInspectionLoading(true)
    setInspectingPath(item.path)
    // look for an existing inspection saved for this path
    try {
      const res = await fetch(`${BACKEND}/inspections`)
      const j = await res.json()
      const candidates = (j.inspections || []).filter((ins) => _normalizePathMatch(ins.path || '', item.path || ''))
      if (candidates.length > 0) {
        // pick the latest created
        const latest = candidates.sort((a,b)=> (a.created_at < b.created_at ? 1 : -1))[0]
        const got = await fetch(`${BACKEND}/inspections/${encodeURIComponent(latest.id)}`)
        const payload = await got.json()
        const inspection = payload.inspection || payload
        const sanitized = sanitizeInspection(inspection)
        setSourceInspection(sanitized)
        setDiagnostics(sanitized?.diagnostics || null)
        // build profile from inspection so uploads view matches source view
        try {
          const p = {}
          const cols = sanitized?.columns || []
          const diag = sanitized?.diagnostics || {}
          cols.forEach((c) => {
            try {
              const info = diag[c] || {}
              p[c] = { dtype: 'str', nulls: info.missing_count || 0, unique: info.unique_count || 0 }
            } catch (e) {
              p[c] = { dtype: 'str', nulls: 0, unique: 0 }
            }
          })
          setProfile(p)
        } catch (e) {
          // ignore
        }
        setInspectOffset(sanitized?.offset || 0)
        setInspectionLoading(false)
        setInspectingPath(null)
        return
      }
    } catch (e) {
      // ignore and fall back to inspect
    }

    // no saved inspection found — call POST /inspect to generate one
    try {
      await doInspectSource(item.path, 0)
      setInspectionLoading(false)
      setInspectingPath(null)
    } catch (e) {
      addToast('Inspect failed: ' + (e.message || e), 'error')
      setInspectionLoading(false)
      setInspectingPath(null)
    }
  }

  async function refreshInspection(item) {
    setInspectionLoading(true)
    setInspectingPath(item.path)
    try {
      await doInspectSource(item.path, 0)
      addToast('Refreshed inspection for ' + item.name, 'success')
    } catch (e) {
      addToast('Refresh failed: ' + (e.message || e), 'error')
    } finally {
      setInspectionLoading(false)
      setInspectingPath(null)
    }
  }

  async function fetchDuplicates(){
    try{
      const res = await fetch(`${BACKEND}/uploads/duplicates`)
      if(!res.ok){ addToast('Failed to check duplicates','error'); return }
      const j = await res.json()
      setDuplicateGroups(j.duplicates || [])
      if(!(j.duplicates || []).length) addToast('No duplicates found','info')
      else setShowDuplicatesModal(true)
    }catch(e){ addToast('Duplicate check failed: '+(e.message||e),'error') }
  }

  async function deleteUploadFromModal(path){
    try{
      const res = await fetch(`${BACKEND}/uploads/delete`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path}) })
      if(!res.ok){ const txt = await res.text(); addToast('Delete failed: '+txt,'error'); return }
      // remove from uploads list
      setUploadsList((u)=> (u||[]).filter(x=> x.path !== path))
      // remove from duplicate groups state
      setDuplicateGroups((groups)=> groups.map(g=> ({...g, paths: g.paths.filter(p=> p !== path)})).filter(g=> g.paths && g.paths.length>1))
      addToast('Deleted ' + path, 'success')
    }catch(e){ addToast('Delete failed: '+(e.message||e),'error') }
  }

  async function deleteOthersInGroup(group, keepPath){
    if(!group || !group.paths || group.paths.length < 2) return
    const confirmMsg = `Delete ${group.paths.length - 1} files in this group and keep ${keepPath}? This is permanent.`
    if(!window.confirm(confirmMsg)) return
    for(const p of group.paths){
      if(p === keepPath) continue
      // await deletion sequentially
      // eslint-disable-next-line no-await-in-loop
      await deleteUploadFromModal(p)
    }
    // refresh duplicate groups
    try{ await fetchDuplicates() } catch {}
  }

  async function onUploadInputChange(e) {
    const file = e.target.files?.[0]
    try {
      await uploadSelectedFile(file)
    } catch (err) {
      addToast(`Upload failed: ${err.message || err}`, "error")
    } finally {
      e.target.value = ""
    }
  }

  function openUploadPicker() {
    uploadInputRef.current?.click()
  }

  async function doSuggest() {
    const res = await fetch(`${BACKEND}/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    })
    const j = await res.json()
    setSuggest(j.suggestions)
    const rec = {
      sources: [{ path }],
      cleaning_steps: j.suggestions.map((s) => ({
        action: s.action,
        column: s.column,
        params: { strategy: s.strategy },
      })),
      outputs: [{ path: "output_preview.csv" }],
    }
    setRecipeText(JSON.stringify(rec, null, 2))
  }

  async function doCleaningSuggestions() {
    const res = await fetch(`${BACKEND}/cleaning-suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path || (uploadsList[0] && uploadsList[0].path) }),
    })
    const j = await res.json()
    setCleaningSuggestions(j.suggestions)
  }

  async function doRecipeFromText() {
    setRecipeGenerating(true)
    setPrevRecipeText(recipeText)
    try {
      // parse sentinel input into an array of values (numbers when numeric)
      const parseSentinels = (s) => {
        if (!s) return null
        return s.split(/\s*,\s*/).map((tok) => {
          if (tok === '') return tok
          const n = Number(tok)
          if (!Number.isNaN(n) && String(n) === tok) return n
          // also allow quoted strings, strip surrounding quotes
          const m = tok.match(/^['"](.*)['"]$/)
          if (m) return m[1]
          return tok
        })
      }
      const sentinelsArr = parseSentinels(treatAsMissing)

      const res = await fetch(`${BACKEND}/recipe-from-text`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instruction,
          source_path: path || (uploadsList[0] && uploadsList[0].path),
          output_path: "output_from_text.csv",
          regression_model: regressionModel,
          regression_features: regressionFeatures ? regressionFeatures.split(/\s*,\s*/) : null,
          regression_group_by: regressionGroupBy || null,
          treat_as_missing: sentinelsArr,
        }),
      })
      const j = await res.json()
      setLastGeneratedResponse(j)
      // embed parsed sentinels into impute steps for display if the user provided any
      const recipeObj = sanitizeRecipe(j.recipe || j)
      if (sentinelsArr && Array.isArray(sentinelsArr) && sentinelsArr.length > 0 && recipeObj && Array.isArray(recipeObj.cleaning_steps)) {
        recipeObj.cleaning_steps = recipeObj.cleaning_steps.map((step) => {
          try {
            if (step && step.action === 'impute') {
              const params = step.params ? { ...step.params } : {}
              // set the treat_as_missing sentinel array from user input
              params.treat_as_missing = sentinelsArr
              return { ...step, params }
            }
          } catch (e) {
            // swallow and return original step
          }
          return step
        })
      }
      // store only the generated recipe for the Generated recipe panel
      setRecipeFromText(recipeObj)
      // if multiple candidate columns, ask for confirmation before applying
        if (j.column_candidates && j.column_candidates.length > 1) {
        // filter out metadata columns (underscore-prefixed or known meta keys)
        try{
          const rawCols = j.column_candidates.map((c) => c[0])
          let filtered = rawCols.filter(col => col && !col.startsWith('_') && !META_KEYS.includes(col))
          // if filtering removed everything, fall back to raw list
          if(!filtered || filtered.length === 0) filtered = rawCols
          setCandidateColumns(filtered)
        }catch(e){
          setCandidateColumns(j.column_candidates.map((c) => c[0]))
        }
        setPendingGenerated(j)
        setShowConfirmModal(true)
      } else {
        const safeRecipe = sanitizeRecipe(j.recipe)
        const json = JSON.stringify(safeRecipe, null, 2)
        setRecipeText(json)
        // auto-show generated JSON so users see the recipe immediately
        setShowRecipeJson(true)
        setPendingGenerated(null)
      }
      // fetch generated preview sized to the current Generated preview Rows selector
      try {
        const nParam = typeof rowsToShow === 'number' ? rowsToShow : Number(rowsToShow)
        const previewQs = []
        if (nParam != null) previewQs.push(`n=${encodeURIComponent(nParam)}`)
        if (j && j.id) previewQs.push(`recipe_id=${encodeURIComponent(j.id)}`)
        const previewUrl = `${BACKEND}/preview${previewQs.length ? `?${previewQs.join('&')}` : ''}`
        const previewRes = await fetch(previewUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(j.recipe),
        })
        const pj = await previewRes.json()
        setGeneratedPreview(sanitizePreview(pj.preview))
      } catch (pe) {
        console.warn('Failed to fetch generated preview', pe)
        setGeneratedPreview(null)
      }
    } finally {
      setRecipeGenerating(false)
    }
  }

  function confirmCandidate(col) {
    if (!pendingGenerated) return
    const j = pendingGenerated
    // coerce recipe to use selected column where applicable
    try {
      const r = sanitizeRecipe(j.recipe)
      if (r && r.cleaning_steps) {
        r.cleaning_steps = r.cleaning_steps.map((s) => ({ ...s, column: s.column || col }))
      }
      const json = JSON.stringify(r, null, 2)
      setRecipeText(json)
      setShowRecipeJson(true)
      addToast(`Using column '${col}' for generated recipe`, 'info')
    } catch (e) {
      addToast('Failed to accept generated recipe: ' + e.message, 'error')
    }
    setShowConfirmModal(false)
    setPendingGenerated(null)
    setCandidateColumns([])
  }

  async function doJoinSuggestions() {
    // only allow suggestions for files that are present in uploads list
    const presentPaths = (uploadsList || []).map((u) => u.path)
    if (!presentPaths.includes(leftPath)) { addToast('Left path must be an uploaded file', 'error'); return }
    if (!presentPaths.includes(rightPath)) { addToast('Right path must be an uploaded file', 'error'); return }

    const res = await fetch(`${BACKEND}/join-suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ left_path: leftPath, right_path: rightPath }),
    })
    const j = await res.json()
    setJoinSuggestions(j.joins)

    // auto-fill join keys with top suggestion when available
    try {
      if (j.joins && j.joins.length > 0) {
        const top = j.joins[0]
        const lkeys = Array.isArray(top.left_on) ? top.left_on : []
        const rkeys = Array.isArray(top.right_on) ? top.right_on : []
        if (lkeys.length) { setJoinLeftOn(lkeys.join(',')); setJoinLeftOnArr(lkeys) }
        if (rkeys.length) { setJoinRightOn(rkeys.join(',')); setJoinRightOnArr(rkeys) }
      }
    } catch (e) {}

    // also fetch cleaning suggestions for both sides and merge into suggested step list
    try {
      const [ls, rs] = await Promise.all([
        fetch(`${BACKEND}/suggest`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: leftPath }) }).then(r => r.json()).catch(() => ({ suggestions: [] })),
        fetch(`${BACKEND}/suggest`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: rightPath }) }).then(r => r.json()).catch(() => ({ suggestions: [] })),
      ])
      // combine and set as `suggest` for Recipe preview card
      const combined = []
      if (ls && ls.suggestions) combined.push(...ls.suggestions.map(s => ({ ...s, source: 'left' })))
      if (rs && rs.suggestions) combined.push(...rs.suggestions.map(s => ({ ...s, source: 'right' })))
      setSuggest(combined)
    } catch (e) { console.warn('Failed to fetch cleaning suggestions', e) }
  }

  async function doJoinPreview() {
    setJoinPreviewLoading(true)
    try {
      // validate upload presence
      const presentPaths = (uploadsList || []).map((u) => u.path)
      if (!presentPaths.includes(leftPath)) { addToast('Left path must be an uploaded file', 'error'); setJoinPreviewLoading(false); return }
      if (!presentPaths.includes(rightPath)) { addToast('Right path must be an uploaded file', 'error'); setJoinPreviewLoading(false); return }

      // prefer explicit compositePairs ordering when present
      let left_on, right_on
      const validPairs = (compositePairs || []).filter(p=>p && p.left && p.right)
      if(validPairs.length) {
        left_on = validPairs.map(p=>p.left)
        right_on = validPairs.map(p=>p.right)
      } else {
        left_on = (joinLeftOnArr && joinLeftOnArr.length>0) ? joinLeftOnArr : (joinLeftOn ? joinLeftOn.split(',').map(s => s.trim()).filter(Boolean) : undefined)
        right_on = (joinRightOnArr && joinRightOnArr.length>0) ? joinRightOnArr : (joinRightOn ? joinRightOn.split(',').map(s => s.trim()).filter(Boolean) : undefined)
      }
      // if compositePairs exist but have incomplete pairs, alert user
      const hasAnyPairs = (compositePairs || []).length > 0
      const incomplete = (compositePairs || []).some(p=> !p.left || !p.right)
      if(hasAnyPairs && incomplete) { addToast('Cannot preview: some composite pairs are incomplete', 'error'); setJoinPreviewLoading(false); return }
      // parse rename mappings from textarea
      const mappings = []
      (mappingText || '').split('\n').map(l=>l.trim()).filter(Boolean).forEach(line=>{
        const m = line.match(/^(left|right)\s*:\s*(.+?)\s*->\s*(.+)$/i)
        if(m){ mappings.push({ side: m[1].toLowerCase(), from: m[2].trim(), to: m[3].trim() }) }
      })
      const conflict = { suffix_left: suffixLeft, suffix_right: suffixRight, prefer: preferResolve, rename_map: mappings }
      const payload = { left_path: leftPath, right_path: rightPath, left_on, right_on, join_type: joinType, sample: Number(joinSampleSize), conflict_resolution: conflict }
      const res = await fetch(`${BACKEND}/join-preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if(!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Join preview failed')
      }
      const j = await res.json()
      setJoinPreviewResult(j)
      // also fetch before-samples for left and right via /inspect to show before/after
      try{
        const [linsp, rins] = await Promise.all([
          fetch(`${BACKEND}/inspect`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path:leftPath, offset:0, limit: Number(joinSampleSize)})}).then(r=>r.json()).catch(()=>null),
          fetch(`${BACKEND}/inspect`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path:rightPath, offset:0, limit: Number(joinSampleSize)})}).then(r=>r.json()).catch(()=>null),
        ])
        setJoinLeftPreview(linsp && linsp.inspection && linsp.inspection.rows ? linsp.inspection.rows : (linsp && linsp.inspection? linsp.inspection : null))
        setJoinRightPreview(rins && rins.inspection && rins.inspection.rows ? rins.inspection.rows : (rins && rins.inspection? rins.inspection : null))
      }catch(e){console.warn('inspect failed', e); setJoinLeftPreview(null); setJoinRightPreview(null)}
    } catch (e) {
      addToast('Join preview failed: '+(e.message||e), 'error')
      setJoinPreviewResult(null)
    } finally {
      setJoinPreviewLoading(false)
    }
  }

  async function doJoinExport() {
    setJoinExporting(true)
    try {
      const presentPaths = (uploadsList || []).map((u) => u.path)
      if (!presentPaths.includes(leftPath)) { addToast('Left path must be an uploaded file', 'error'); setJoinExporting(false); return }
      if (!presentPaths.includes(rightPath)) { addToast('Right path must be an uploaded file', 'error'); setJoinExporting(false); return }
      let left_on, right_on
      const validPairs = (compositePairs || []).filter(p=>p && p.left && p.right)
      if(validPairs.length) {
        left_on = validPairs.map(p=>p.left)
        right_on = validPairs.map(p=>p.right)
      } else {
        left_on = (joinLeftOnArr && joinLeftOnArr.length>0) ? joinLeftOnArr : (joinLeftOn ? joinLeftOn.split(',').map(s=>s.trim()).filter(Boolean) : undefined)
        right_on = (joinRightOnArr && joinRightOnArr.length>0) ? joinRightOnArr : (joinRightOn ? joinRightOn.split(',').map(s=>s.trim()).filter(Boolean) : undefined)
      }
      const hasAnyPairs = (compositePairs || []).length > 0
      const incomplete = (compositePairs || []).some(p=> !p.left || !p.right)
      if(hasAnyPairs && incomplete) { addToast('Cannot export: some composite pairs are incomplete', 'error'); setJoinExporting(false); return }
      const mappings = []
      (mappingText || '').split('\n').map(l=>l.trim()).filter(Boolean).forEach(line=>{
        const m = line.match(/^(left|right)\s*:\s*(.+?)\s*->\s*(.+)$/i)
        if(m){ mappings.push({ side: m[1].toLowerCase(), from: m[2].trim(), to: m[3].trim() }) }
      })
      const conflict = { suffix_left: suffixLeft, suffix_right: suffixRight, prefer: preferResolve, rename_map: mappings }
      const payload = { left_path: leftPath, right_path: rightPath, left_on, right_on, join_type: joinType, export_format: exportFormat, filename: exportFilename, conflict_resolution: conflict }
      const res = await fetch(`${BACKEND}/join-export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
      })
      if(!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Join export failed')
      }
      const j = await res.json()
      addToast('Export created: ' + (j.export_path || ''), 'success')
      // surface download link to user
      setApplyRes((prev)=> ({...(prev||{}), last_export: j.export_path}))
    } catch (e) {
      addToast('Join export failed: '+(e.message||e), 'error')
    } finally {
      setJoinExporting(false)
    }
  }

  async function doPreview() {
    setPreviewLoading(true)
    try {
      let recipe = null
      if (recipeText && recipeText.trim()) {
        try {
          recipe = JSON.parse(recipeText)
        } catch (err) {
          // fallback to in-memory generated recipe if available
          if (recipeFromText) recipe = recipeFromText
          else throw new Error('Recipe JSON is invalid or incomplete')
        }
      } else if (recipeFromText) {
        recipe = recipeFromText
      } else {
        throw new Error('No recipe available to preview')
      }
      // include requested preview rows (n=0 means full dataset)
      const nParam = typeof previewRows === 'number' ? previewRows : Number(previewRows)
      const qs = []
      if (nParam != null) qs.push(`n=${encodeURIComponent(nParam)}`)
      if (recipeId) qs.push(`recipe_id=${encodeURIComponent(recipeId)}`)
      const url = `${BACKEND}/preview${qs.length ? `?${qs.join('&')}` : ''}`
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recipe),
      })
      const j = await res.json()
      setPreview(sanitizePreview(j.preview))
      if (j.schema_warnings) {
        // lightweight notification
        setApplyRes((prev) => ({ ...(prev || {}), schema_warnings: j.schema_warnings }))
        addToast(`Schema warnings: ${JSON.stringify(j.schema_warnings)}`, "warn")
      }
    } catch (e) {
      addToast("Invalid recipe JSON: " + e.message, "error")
    } finally {
      setPreviewLoading(false)
    }
  }

  async function loadExplanations() {
    // prefer last generated response if available
    try {
      setExplainLoading(true)
      if (lastGeneratedResponse && lastGeneratedResponse.explanations) {
        const ex = lastGeneratedResponse.explanations || []
        setExplanations(ex)
        // only auto-open when there are explanations
        if (ex && ex.length > 0) setShowExplanations(true)
        setExplainLoading(false)
        // attempt to log into uploads if recipe id present and uploads available
        try {
          const maybeId = lastGeneratedResponse.id || recipeId
          if (maybeId && ex && ex.length > 0) {
            // fetch saved recipe to find source path
            try {
              const rres = await fetch(`${BACKEND}/recipes/${encodeURIComponent(maybeId)}`)
              if (rres.ok) {
                const rj = await rres.json()
                const src = (rj.recipe && rj.recipe.sources && rj.recipe.sources[0] && rj.recipe.sources[0].path) || null
                  if (src) {
                  // persist on server and refresh uploads listing
                  try {
                    const pres = await fetch(`${BACKEND}/uploads/explanations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_path: src, recipe_id: maybeId, explanations: ex }) })
                    if (pres && pres.ok) await fetchUploads()
                  } catch (e) {}
                  setUploadsList((list) => {
                    if (!list) return list
                    return list.map((it) => {
                      try {
                        if (it.path === src) {
                          const hist = (it.explanations_history && Array.isArray(it.explanations_history)) ? it.explanations_history.slice() : []
                          hist.unshift({ recipe_id: maybeId, timestamp: new Date().toISOString(), explanations: ex })
                          return { ...it, explanations_history: hist }
                        }
                      } catch (e) {}
                      return it
                    })
                  })
                }
              }
            } catch (e) {}
          }
        } catch (e) {}
        return
      }
      if (!recipeId) {
        addToast('No saved recipe and no generated explanations available', 'warn')
        setExplainLoading(false)
        return
      }
      const qs = []
      const nParam = typeof rowsToShow === 'number' ? rowsToShow : Number(rowsToShow)
      if (nParam != null) qs.push(`sample_rows=${encodeURIComponent(nParam)}`)
      const url = `${BACKEND}/recipes/${encodeURIComponent(recipeId)}/explain${qs.length ? `?${qs.join('&')}` : ''}`
      const res = await fetch(url)
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Explain fetch failed')
      }
      const j = await res.json()
      const ex = j.explanations || null
      setExplanations(ex)
      if (ex && ex.length > 0) setShowExplanations(true)
      // if we have explanations and a recipe id, log into matching upload's explanations_history
      try {
        if (ex && ex.length > 0 && recipeId) {
          const rres = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}`)
          if (rres.ok) {
            const rj = await rres.json()
            const src = (rj.recipe && rj.recipe.sources && rj.recipe.sources[0] && rj.recipe.sources[0].path) || null
            if (src) {
              // persist on server and refresh uploads listing
              try {
                const pres = await fetch(`${BACKEND}/uploads/explanations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_path: src, recipe_id: recipeId, explanations: ex }) })
                if (pres && pres.ok) await fetchUploads()
              } catch (e) {}
              setUploadsList((list) => {
                if (!list) return list
                return list.map((it) => {
                  try {
                    if (it.path === src) {
                      const hist = (it.explanations_history && Array.isArray(it.explanations_history)) ? it.explanations_history.slice() : []
                      hist.unshift({ recipe_id: recipeId, timestamp: new Date().toISOString(), explanations: ex })
                      return { ...it, explanations_history: hist }
                    }
                  } catch (e) {}
                  return it
                })
              })
            }
          }
        }
      } catch (e) {}
    } catch (e) {
      addToast('Failed to load explanations: ' + (e.message || e), 'error')
      setExplanations(null)
      setShowExplanations(false)
    } finally {
      setExplainLoading(false)
    }
  }

  async function persistConsent(consents) {
    try {
      const payload = { user_id: userId, consents, user_agent: navigator.userAgent }
      const res = await fetch(`${BACKEND}/consent`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (res.ok) {
        // refresh consent history
        await fetchConsentHistory()
      }
    } catch (e) {
      console.warn('Failed to persist consent', e)
    }
  }

  async function fetchConsentHistory() {
    try {
      const res = await fetch(`${BACKEND}/consent?user_id=${encodeURIComponent(userId)}`)
      if (!res.ok) return
      const j = await res.json()
      setConsentHistory(j.consents || [])
    } catch (e) { console.warn('Failed to fetch consent history', e) }
  }

  function acceptAllConsents() {
    const cons = { essential: true, analytics: true, marketing: true, personalized_ads: true }
    setConsent(cons)
    try { window.localStorage.setItem('falconbroom_consent', JSON.stringify(cons)) } catch {}
    persistConsent(cons)
  }

  function rejectNonEssential() {
    const cons = { essential: true, analytics: false, marketing: false, personalized_ads: false }
    setConsent(cons)
    try { window.localStorage.setItem('falconbroom_consent', JSON.stringify(cons)) } catch {}
    persistConsent(cons)
  }

  // Dynamic analytics loader — loads/unloads analytics scripts based on consent
  function loadAnalyticsScript() {
    if (!consent || !consent.analytics) return
    if (!analyticsId) return
    if (document.getElementById('analytics-script')) return
    // Dynamic loader: analytics provider ID must be supplied in Settings.
    const s = document.createElement('script')
    s.id = 'analytics-script'
    s.async = true
    s.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(analyticsId)}`
    s.onload = () => {
      try {
        window.dataLayer = window.dataLayer || []
        function gtag(){window.dataLayer.push(arguments)}
        window.gtag = gtag
        gtag('js', new Date())
        gtag('config', analyticsId)
        addToast('Analytics enabled', 'info')
      } catch (e) { console.warn(e) }
    }
    document.head.appendChild(s)
  }

  function unloadAnalyticsScript() {
    const s = document.getElementById('analytics-script')
    if (s) s.remove()
    if (window.gtag) try { delete window.gtag } catch(e){}
    addToast('Analytics disabled', 'info')
  }

  // Initialize cookieconsent CMP when available
  React.useEffect(() => {
    // load/unload analytics based on consent state
    if (consent && consent.analytics) {
      loadAnalyticsScript()
    } else {
      unloadAnalyticsScript()
    }
  }, [consent])

  React.useEffect(() => {
    // Initialize Osano CookieConsent if present and not yet initialized
    try {
      if (window.cookieconsent && !window._falconbroom_cookieconsent_init) {
        window.cookieconsent.initialise({
          palette: { popup: { background: '#2f2f2f' }, button: { background: '#f1d600' } },
          theme: 'classic',
          position: 'bottom',
          content: {
            message: 'FalconBroom uses cookies to improve your experience.',
            dismiss: 'Accept all',
            deny: 'Reject non-essential',
            link: 'Manage',
            href: '#',
          },
          // on initialisation, sync state with our consent handlers
          onInitialise: function(status) {
            // status is 'allow' or 'deny'
            window._falconbroom_cookieconsent_init = true
            if (status === 'allow') acceptAllConsents()
            else rejectNonEssential()
          },
          onStatusChange: function(status) {
            if (status === 'allow') acceptAllConsents()
            else rejectNonEssential()
          },
          onRevokeChoice: function() {
            // user revoked consent; treat as revoke
            rejectNonEssential()
          }
        })
      }
    } catch (e) { console.warn('cookieconsent init failed', e) }
  }, [])

  // Persist analyticsId when changed
  React.useEffect(() => {
    try { window.localStorage.setItem('falconbroom_analytics_id', analyticsId || '') } catch {}
  }, [analyticsId])

  async function doApply() {
    setApplyLoading(true)
    try {
      let recipe = null
      if (recipeText && recipeText.trim()) {
        try {
          recipe = JSON.parse(recipeText)
        } catch (err) {
          if (recipeFromText) recipe = recipeFromText
          else throw new Error('Recipe JSON is invalid or incomplete')
        }
      } else if (recipeFromText) {
        recipe = recipeFromText
      } else {
        throw new Error('No recipe available to apply')
      }
      const res = await fetch(`${BACKEND}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recipe),
      })
      const j = await res.json()
      setApplyRes(j.result)
    } catch (e) {
      addToast("Invalid recipe JSON: " + e.message, "error")
    } finally {
      setApplyLoading(false)
    }
  }

  function acceptGenerated(){
    // accept current recipeText (already set) and clear preview
    setGeneratedPreview(null)
    setPrevRecipeText(null)
    setShowCustomRevisions(false)
    addToast('Accepted generated recipe', 'success')
  }

  async function saveRecipe(name) {
    try {
      // If recipeText is empty (e.g. user worked from generated preview),
      // fall back to the in-memory `recipeFromText` object. Provide a
      // clearer error if neither is present.
      let recipe = null
      if (recipeText && recipeText.trim()) {
        try {
          recipe = JSON.parse(recipeText)
        } catch (err) {
          throw new Error('Recipe JSON is invalid')
        }
      } else if (recipeFromText) {
        recipe = recipeFromText
      } else {
        throw new Error('No recipe to save')
      }

      const res = await fetch(`${BACKEND}/recipes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, recipe }),
      })
      let j = null
      if (res.ok) {
        try {
          j = await res.json()
        } catch (err) {
          // backend returned empty body; construct a minimal response
          j = { id: null }
        }
      } else {
        const txt = await res.text()
        throw new Error(txt || `Save failed: ${res.status}`)
      }
      setRecipeId(j.id)
      // fetch saved record to get status
      if (j.id) {
        const saved = await fetch(`${BACKEND}/recipes/${encodeURIComponent(j.id)}`)
        if (saved.ok) {
          const sdata = await saved.json()
          setRecipeStatus(sdata.status || "draft")
        }
      }
      addToast(`Saved recipe ${name}`, "success")
      // auto-fetch explanations after save to surface step-level rationale
      try {
        await loadExplanations()
      } catch (e) {
        // ignore: loadExplanations handles its own errors and toasts
      }
    } catch (e) {
      addToast("Failed to save recipe: " + e.message, "error")
    }
  }

  async function approveSavedRecipe() {
    // open confirmation modal
    if (!recipeId) {
      addToast("No saved recipe to approve", "warn")
      return
    }
    setShowApproveConfirm(true)
  }

  async function confirmApprove() {
    if (!recipeId) {
      addToast("No saved recipe to approve", "warn")
      setShowApproveConfirm(false)
      return
    }
    try {
      const res = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}/approve`, { method: "POST" })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(txt || 'Approve failed')
      }
      const j = await res.json()
      setRecipeStatus(j.status)
      setShowApproveConfirm(false)
      addToast(`Recipe ${recipeId} approved`, "success")
      // auto-refresh explanations after approval
      try {
        await loadExplanations()
      } catch (e) {
        // ignore; loadExplanations handles errors/toasts
      }
    } catch (e) {
      setShowApproveConfirm(false)
      addToast("Failed to approve: " + (e.message || e), "error")
    }
  }

  async function runSavedRecipe(format = "csv") {
    if (!recipeId) {
      addToast("Save recipe before running", "warn")
      return
    }
    if (recipeStatus !== 'approved') {
      addToast('Approve the recipe before running', 'warn')
      return
    }
    try {
      const res = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}/run?export_format=${encodeURIComponent(format)}`, { method: "POST" })
      const j = await res.json()
      // add to history UI
      setHistoryList((h) => [j.run, ...h])
      addToast(`Run started: ${j.run.id}`, "info")
    } catch (e) {
      addToast("Failed to run recipe: " + e.message, "error")
    }
  }

  // debug helper to surface client state and attempt run+download; useful when clicks produce no network traffic
  async function debugRunDownload(format = 'csv') {
    console.log('debugRunDownload invoked', { recipeId, format, recipeTextSnapshot: recipeText && recipeText.slice(0,200) })
    addToast(`Debug: recipeId=${recipeId || '<none>'}`, 'info', 6000)
    // if no recipeId, still show the current recipeText in console for inspection
    if (!recipeId) {
      console.warn('debugRunDownload: no recipeId; cannot run. Current recipeText (truncated):', recipeText && recipeText.slice(0,1000))
      return
    }
    // delegate to the real runner which also logs progress
    try {
      await runAndDownloadSavedRecipe(format)
    } catch (e) {
      console.error('debugRunDownload: runAndDownloadSavedRecipe threw', e)
      addToast('Debug run failed: ' + (e.message || e), 'error')
    }
  }

  const [runDownloadLoading, setRunDownloadLoading] = useState(false)

  async function runAndDownloadSavedRecipe(format = "csv") {
    console.log('runAndDownloadSavedRecipe invoked', { recipeId, format })
    if (!recipeId) {
      addToast('Save recipe before running', 'warn')
      console.warn('runAndDownloadSavedRecipe aborted: no recipeId')
      return
    }
    if (recipeStatus !== 'approved') {
      addToast('Approve the recipe before running', 'warn')
      console.warn('runAndDownloadSavedRecipe aborted: not approved')
      return
    }
    try {
      setRunDownloadLoading(true)
      addToast('Running recipe on server...', 'info')
      const res = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}/run?export_format=${encodeURIComponent(format)}`, { method: 'POST' })
      if (!res.ok) {
        let err = 'Run failed'
        try { const j = await res.json(); err = j.detail || j.message || JSON.stringify(j) } catch { err = await res.text() }
        addToast(err, 'error')
        setRunDownloadLoading(false)
        return
      }
      const j = await res.json()
      console.log('run response', j)
      const out = j.run && j.run.output_path
      if (!out) {
        addToast('Run completed but no output path found', 'warn')
        console.warn('Run completed but response had no output_path', j)
        setRunDownloadLoading(false)
        return
      }
      // Fetch the generated file and trigger a blob download (more reliable than opening a new tab)
      const downloadUrl = `${BACKEND}/download?path=${encodeURIComponent(out)}`
      console.log('Download URL', downloadUrl)
      try {
        const fileRes = await fetch(downloadUrl)
        if (!fileRes.ok) {
          let err = 'Download failed'
          try { const js = await fileRes.json(); err = js.detail || js.message || JSON.stringify(js) } catch { err = await fileRes.text() }
          addToast(err, 'error')
          setRunDownloadLoading(false)
          return
        }
        const blob = await fileRes.blob()
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        // derive filename from output path
        try {
          const parts = String(out).split(/\\|\//)
          a.download = parts[parts.length - 1] || `falconbroom_output_${Date.now()}`
        } catch {
          a.download = `falconbroom_output_${Date.now()}`
        }
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
        addToast('Download started', 'success')
      } catch (e) {
        addToast('Download failed: ' + (e.message || e), 'error')
      } finally {
        setRunDownloadLoading(false)
      }
    } catch (e) {
      console.error(e)
      addToast('Run & Download failed: ' + (e.message || e), 'error')
      setRunDownloadLoading(false)
    }
  }

  async function fetchHistory() {
    try {
      const res = await fetch(`${BACKEND}/history`)
      const j = await res.json()
      setHistoryList(j.history || [])
    } catch (e) {
      addToast("Failed to fetch history: " + e.message, "error")
    }
  }

  async function rollbackRun(runId) {
    try {
      const res = await fetch(`${BACKEND}/history/${encodeURIComponent(runId)}/rollback`, { method: "POST" })
      const j = await res.json()
      addToast(`Rollback created: ${j.rollback.rollback_path}`, "success")
      fetchHistory()
    } catch (e) {
      addToast("Rollback failed: " + e.message, "error")
    }
  }

  async function exportToSheets() {
    if (!recipeId) {
      addToast("Save recipe before exporting to Google Sheets", "warn")
      return
    }
    try {
      const res = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}/export_sheets`, { method: "POST" })
      if (res.ok) {
        const j = await res.json()
        addToast(`Export queued: ${j.export_id || 'queued'}`, "info")
      } else {
        let text = "Export failed"
        try {
          const j = await res.json()
          text = j.detail || j.message || JSON.stringify(j)
        } catch {
          text = await res.text()
        }
        addToast(text, "warn")
      }
    } catch (e) {
      addToast("Export failed: " + e.message, "error")
    }
  }

  async function deleteHistory(runId) {
    try {
      const res = await fetch(`${BACKEND}/history/${encodeURIComponent(runId)}`, { method: "DELETE" })
      if (res.ok) {
        const j = await res.json()
        addToast(`Deleted run ${j.deleted}`, 'success')
        fetchHistory()
      } else {
        let text = 'Delete failed'
        try { const j = await res.json(); text = j.detail || JSON.stringify(j) } catch { text = await res.text() }
        addToast(text, 'error')
      }
    } catch (e) {
      addToast('Delete failed: ' + e.message, 'error')
    }
  }

  async function dedupeHistory() {
    try {
      const res = await fetch(`${BACKEND}/history/dedupe`, { method: "POST" })
      const j = await res.json()
      addToast(`Removed ${j.removed.length} duplicate runs`, 'info')
      fetchHistory()
    } catch (e) {
      addToast('Dedupe failed: ' + e.message, 'error')
    }
  }

  function downloadPreviewCsv() {
    if (!generatedPreview || !generatedPreview.after) {
      addToast('No preview available to download. Run Preview or generate a recipe.', 'warn')
      return
    }
    try {
      const rows = generatedPreview.after || []
      if (rows.length === 0) {
        addToast('Preview is empty; nothing to download.', 'warn')
        return
      }
      const cols = selectedColumns && selectedColumns.length > 0 ? selectedColumns : Object.keys(rows[0])
      const escapeCell = (v) => {
        if (v === null || v === undefined) return ''
        const s = String(v)
        if (s.includes('"') || s.includes(',') || s.includes('\n')) return '"' + s.replace(/"/g, '""') + '"'
        return s
      }
      const header = cols.join(',')
      const csv = [header, ...rows.map(r => cols.map(c => escapeCell(r[c] ?? '')).join(','))].join('\n')
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `falconbroom_preview_${Date.now()}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      addToast('Downloaded CSV', 'success')
    } catch (e) {
      console.error(e)
      addToast('Failed to generate CSV: ' + (e.message || e), 'error')
    }
  }

  // poll history while any runs are running
  React.useEffect(() => {
    let timer = null
    const list = historyList || []
    const hasRunning = list.some((h) => h && h.status === "running")
    if (hasRunning) {
      timer = setInterval(fetchHistory, 3000)
    }
    return () => {
      if (timer) clearInterval(timer)
    }
  }, [historyList])

  // fetch history on mount
  React.useEffect(() => {
    fetchHistory()
  }, [])

  function renderTab() {
    if (activeTab === "overview") {
      return (
        <div className="tab-stack tab-panel">
          <Card
            eyebrow="Workspace"
            title="Analyst-friendly workflow"
            subtitle="Profile data, author a recipe, inspect join hints, then preview the result before writing anything out."
          >
            <div className="overview-grid">
              <Stat label="Columns profiled" value={formatCount(profileCount)} tone="blue" />
              <Stat label="Suggested steps" value={formatCount(suggestedCount)} tone="violet" />
              <Stat label="Preview rows" value={formatCount(previewCount)} tone="teal" />
              <Stat label="Recipe steps" value={formatCount(recipeSteps)} tone="amber" />
            </div>
            <div className="button-row button-row-tight">
              <button className="primary" onClick={openUploadPicker}>Upload file</button>
              <button onClick={doProfile}>Profile data</button>
              <button onClick={doSuggest}>Suggest recipe</button>
              <button onClick={doPreview}>Preview</button>
            </div>
          </Card>

          <div className="two-up">
            <Card eyebrow="Quick start" title="Open a dataset" subtitle="Load a CSV, then let the engine suggest the next move.">
              <label>Local CSV path</label>
              <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:/data/customers.csv" />
              <label>Plain-English instruction</label>
              <textarea
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                rows={4}
                placeholder="e.g. fill missing age values, lowercase email, and remove duplicate customers"
              />
            </Card>

            <Card eyebrow="Theme" title="Appearance" subtitle="Toggle between dark and light modes for different work sessions.">
              <div className="theme-toggle-panel">
                <div>
                  <strong>{theme === "light" ? "Light theme" : "Dark theme"}</strong>
                  <p>Switch the interface to match your environment and reading preference.</p>
                </div>
                <button
                  className="theme-toggle"
                  onClick={() => persistTheme(theme === "dark" ? "light" : "dark")}
                  aria-pressed={theme === "light"}
                >
                  {theme === "light" ? "Switch to dark" : "Switch to light"}
                </button>
              </div>
            </Card>
          </div>
        </div>
      )
    }

    if (activeTab === "source") {
      return (
        <div className="tab-stack tab-panel">
          <Card
            eyebrow="Source"
            title="Data file and task setup"
            subtitle="Choose a local file, write an instruction, and let the engine build a deterministic recipe."
          >
            <label>Local file path</label>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:/data/customers.csv" />
            <input
              ref={uploadInputRef}
              type="file"
              accept=".csv,.tsv,.txt,.md,.xlsx,.docx,.pptx,.gdoc,.gsheet,.gslides"
              onChange={onUploadInputChange}
              style={{ display: "none" }}
            />

            {/* Plain-English instruction moved to Custom Revisions - see that panel */}

            <div className="source-data-peek">
              <div className="source-data-peek-header">
                <h3>Raw data grid</h3>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <label style={{fontSize:'0.82rem',color:'var(--muted)'}}>Show as table</label>
                  <input type="checkbox" checked={showAsTable} onChange={(e)=>setShowAsTable(e.target.checked)} />
                </div>
                {sourceInspection ? (
                  <div className="source-data-peek-stats">
                    <span className="chip">Rows {sourceInspection.offset + 1}-{sourceInspection.offset + sourceInspection.returned_rows}</span>
                    <span className="chip">of {sourceInspection.row_count}</span>
                  </div>
                ) : null}
              </div>

              {sourceInspection ? (
                <>
                  <div className="table-wrap">
                    {showAsTable ? (
                      <table className="source-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            {sourceInspection.columns.map((col) => (
                              <th key={col}>{col}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {sourceInspection.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              <td>{sourceInspection.offset + rowIndex + 1}</td>
                              {sourceInspection.columns.map((col) => (
                                <td key={`${rowIndex}-${col}`}>{String(row[col] ?? "")}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <table className="source-table">
                        <thead>
                          <tr><th>#</th><th>Raw extracted text</th></tr>
                        </thead>
                        <tbody>
                          {(sourceInspection.raw_preview || sourceInspection.rows || []).map((r, i) => (
                            <tr key={i}>
                              <td>{sourceInspection.offset + i + 1}</td>
                              <td>{r.text ?? JSON.stringify(r)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>

                  <div className="button-row">
                    <button
                      onClick={() => doInspectSource(path, Math.max(0, inspectOffset - INSPECT_PAGE_SIZE))}
                      disabled={!sourceInspection.has_prev}
                    >
                      Previous 100 rows
                    </button>
                    <button
                      onClick={() => doInspectSource(path, inspectOffset + INSPECT_PAGE_SIZE)}
                      disabled={!sourceInspection.has_next}
                    >
                      Next 100 rows
                    </button>
                  </div>
                  {/* Diagnostics panel */}
                  {diagnostics && Object.keys(diagnostics).length > 0 && (
                    <div className="diagnostics-panel">
                      <h4>Data diagnostics</h4>
                      <div className="diagnostics-list">
                        {Object.entries(diagnostics).map(([col, info]) => (
                          <div key={col} className="diag-item">
                            <div className="diag-key">{col}</div>
                            <div className="diag-value">
                              <div>Missing: {info.missing_count ?? info.missing_count}</div>
                              {info.missing_positions_sample && info.missing_positions_sample.length > 0 && (
                                <div>Missing examples: {info.missing_positions_sample.slice(0,5).join(", ")}</div>
                              )}
                              <div>Unique values: {info.unique_count ?? info.unique_count}</div>
                              {info.mixed_type ? <div style={{color:'#f59e0b'}}>Mixed types detected</div> : null}
                              {info.constant ? <div style={{color:'#fb7185'}}>Constant/low-variance column</div> : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="empty-state">No data preview yet. Upload a file or click Profile data.</div>
              )}
            </div>

            <div className="button-row">
              <button className="primary" onClick={openUploadPicker}>Upload file</button>
              <button onClick={doProfile}>Profile data</button>
              <button onClick={() => doInspectSource(path, 0)}>Refresh data grid</button>
              <button onClick={doRecipeFromText}>Build recipe</button>
              <button onClick={doCleaningSuggestions}>Suggest columns to clean</button>
            </div>
          </Card>

          {/* Uploads moved to its own tab */}

          {/* Custom revisions modal rendered globally */}

          <Card eyebrow="Run history" title="Runs and lineage" subtitle="View previous runs, outputs, and create rollbacks.">
              <div className="button-row">
              <button onClick={fetchHistory}>Refresh history</button>
              <button onClick={() => setShowDedupeConfirm(true)}>Remove duplicates</button>
            </div>
            {historyList.length ? (
              <div className="history-list">
                {historyList.map((r, idx) => (
                  <div key={r && (r.id || r.run_id) ? (r.id || r.run_id) : `history-${idx}`} className="history-item">
                    <div style={{flex:'1 1 0'}}>
                      <div className="history-top">
                        <strong>{r.id}</strong>
                        <small style={{marginLeft:8}}>{r.recipe_id} • {r.status}</small>
                      </div>
                      <div className="history-body">
                        <div>Started: {r.started_at}</div>
                        {r.finished_at && <div>Finished: {r.finished_at}</div>}
                        {r.output_path && (
                          <div>Output: <small>{r.output_path}</small> <a href={`${BACKEND}/download?path=${encodeURIComponent(r.output_path)}`} target="_blank" rel="noreferrer">Download</a></div>
                        )}
                        {r.warnings && <div style={{color:'#f59e0b'}}>Warnings: {JSON.stringify(r.warnings)}</div>}
                      </div>
                    </div>
                    <div className="history-actions">
                      <button onClick={() => rollbackRun(r.id)}>Rollback</button>
                      <button onClick={() => { setDeleteTarget(r.id); setShowDeleteConfirm(true) }} style={{marginLeft:8,color:'#fff',background:'#ef4444',border:'none',padding:'6px 8px'}}>Delete</button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">No runs yet. Run a saved recipe to populate history.</div>
            )}
          </Card>

          <Card eyebrow="Recipe" title="Generated recipe" subtitle="The recipe produced from the analyst prompt or heuristic suggestions.">
            <JsonBlock value={recipeFromText} empty="No generated recipe yet." />
          </Card>

          <Card eyebrow="Apply" title="Last apply result" subtitle="The latest output path and summary from applying your recipe.">
            <JsonBlock value={applyRes} empty="Nothing applied yet." />
          </Card>
        </div>
      )
    }

    if (activeTab === "joins") {
      return (
        <div className="tab-stack tab-panel">
          <input
            ref={uploadInputRef}
            type="file"
            accept=".csv,.tsv,.txt,.xlsx"
            onChange={onUploadInputChange}
            style={{ display: "none" }}
          />
          <Card eyebrow="Joins" title="Join hints" subtitle="Find the most likely keys before blending datasets.">
            <div className="inline-grid">
              <div>
                <label>Left CSV path</label>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <input value={leftPath} onChange={(e) => setLeftPath(e.target.value)} placeholder="Left CSV path" style={{flex:1}} />
                  <button onClick={() => { setUploadTarget('left'); uploadInputRef.current?.click() }} title="Upload left file">Upload</button>
                </div>
              </div>
              <div>
                <label>Right CSV path</label>
                <div style={{display:'flex',gap:8,alignItems:'center'}}>
                  <input value={rightPath} onChange={(e) => setRightPath(e.target.value)} placeholder="Right CSV path" style={{flex:1}} />
                  <button onClick={() => { setUploadTarget('right'); uploadInputRef.current?.click() }} title="Upload right file">Upload</button>
                </div>
              </div>
            </div>
            <div className="button-row">
              <button className="primary" onClick={doJoinSuggestions}>Suggest joins</button>
            </div>
            <JsonBlock value={joinSuggestions} empty="No join suggestions yet." />
          </Card>

          <Card eyebrow="Recipe preview" title="Suggested step list" subtitle="What the current recipe suggestion engine produced.">
            <JsonBlock value={suggest} empty="No suggestion set yet." />
          </Card>
          <Card eyebrow="Join actions" title="Preview & export" subtitle="Preview the join and export results in multiple formats.">
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
              <div>
                <label>Left keys (select)</label>
                <div style={{display:'flex',flexDirection:'column',gap:6}}>
                  <input placeholder="Filter..." value={leftFilter} onChange={(e)=>setLeftFilter(e.target.value)} />
                  <div style={{maxHeight:160,overflow:'auto',border:'1px solid var(--muted)',padding:6}}>
                    {(joinLeftColsOptions||[]).filter(c=>!leftFilter || c.toLowerCase().includes(leftFilter.toLowerCase())).map(c=> (
                      <label key={c} style={{display:'block',marginBottom:4}}>
                        <input type="checkbox" checked={joinLeftOnArr.includes(c)} onChange={(e)=>{
                          const next = e.target.checked ? [...joinLeftOnArr, c] : joinLeftOnArr.filter(x=>x!==c)
                          setJoinLeftOnArr(next)
                          setJoinLeftOn(next.join(','))
                          // regenerate pairs when selections change
                          const maxLen = Math.max(next.length, joinRightOnArr.length)
                          const newPairs = []
                          for(let i=0;i<maxLen;i++) newPairs.push({ left: next[i] || '', right: joinRightOnArr[i] || '' })
                          setCompositePairs(newPairs)
                        }} /> {c}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <div>
                <label>Right keys (select)</label>
                <div style={{display:'flex',flexDirection:'column',gap:6}}>
                  <input placeholder="Filter..." value={rightFilter} onChange={(e)=>setRightFilter(e.target.value)} />
                  <div style={{maxHeight:160,overflow:'auto',border:'1px solid var(--muted)',padding:6}}>
                    {(joinRightColsOptions||[]).filter(c=>!rightFilter || c.toLowerCase().includes(rightFilter.toLowerCase())).map(c=> (
                      <label key={c} style={{display:'block',marginBottom:4}}>
                        <input type="checkbox" checked={joinRightOnArr.includes(c)} onChange={(e)=>{
                          const next = e.target.checked ? [...joinRightOnArr, c] : joinRightOnArr.filter(x=>x!==c)
                          setJoinRightOnArr(next)
                          setJoinRightOn(next.join(','))
                          const maxLen = Math.max(joinLeftOnArr.length, next.length)
                          const newPairs = []
                          for(let i=0;i<maxLen;i++) newPairs.push({ left: joinLeftOnArr[i] || '', right: next[i] || '' })
                          setCompositePairs(newPairs)
                        }} /> {c}
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              {/* Composite pairing UI */}
              <div style={{gridColumn:'1 / -1',marginTop:8}}>
                <label>Composite key mapping (pair left → right)</label>
                <div style={{display:'flex',flexDirection:'column',gap:6,marginTop:6}}>
                  {pairWarnings && pairWarnings.length ? (
                    <div style={{background:'#fff4e5',border:'1px solid #ffd8a8',padding:8,borderRadius:4}}>
                      {pairWarnings.map((m,mi)=> <div key={mi} style={{color:'#92400e'}}>{m}</div>)}
                    </div>
                  ) : null}
                  {compositePairs && compositePairs.length ? compositePairs.map((p, idx)=> (
                    <div key={idx} style={{display:'flex',gap:8,alignItems:'center'}}>
                      <div style={{display:'flex',flexDirection:'column'}}>
                        <button title="Move up" disabled={idx===0} onClick={()=>{
                          if(idx===0) return
                          const next = compositePairs.slice()
                          const item = next.splice(idx,1)[0]
                          next.splice(idx-1,0,item)
                          setCompositePairs(next)
                          const lefts = next.map(x=>x.left).filter(Boolean)
                          const rights = next.map(x=>x.right).filter(Boolean)
                          setJoinLeftOnArr(lefts); setJoinRightOnArr(rights)
                          setJoinLeftOn(lefts.join(',')); setJoinRightOn(rights.join(','))
                        }}>▲</button>
                        <button title="Move down" disabled={idx===compositePairs.length-1} onClick={()=>{
                          if(idx===compositePairs.length-1) return
                          const next = compositePairs.slice()
                          const item = next.splice(idx,1)[0]
                          next.splice(idx+1,0,item)
                          setCompositePairs(next)
                          const lefts = next.map(x=>x.left).filter(Boolean)
                          const rights = next.map(x=>x.right).filter(Boolean)
                          setJoinLeftOnArr(lefts); setJoinRightOnArr(rights)
                          setJoinLeftOn(lefts.join(',')); setJoinRightOn(rights.join(','))
                        }}>▼</button>
                      </div>
                      <select value={p.left} onChange={(e)=>{
                        const next = compositePairs.slice(); next[idx] = { ...next[idx], left: e.target.value }
                        setCompositePairs(next)
                        // propagate to arrays
                        const lefts = next.map(x=>x.left).filter(Boolean)
                        const rights = next.map(x=>x.right).filter(Boolean)
                        setJoinLeftOnArr(lefts); setJoinRightOnArr(rights)
                        setJoinLeftOn(lefts.join(',')); setJoinRightOn(rights.join(','))
                      }}>
                        <option value="">-- select left --</option>
                        {(joinLeftColsOptions||[]).map(c=> <option key={c} value={c}>{c}</option>)}
                      </select>
                      <div style={{flex:'0 0 24px',textAlign:'center'}}>→</div>
                      <select value={p.right} onChange={(e)=>{
                        const next = compositePairs.slice(); next[idx] = { ...next[idx], right: e.target.value }
                        setCompositePairs(next)
                        const lefts = next.map(x=>x.left).filter(Boolean)
                        const rights = next.map(x=>x.right).filter(Boolean)
                        setJoinLeftOnArr(lefts); setJoinRightOnArr(rights)
                        setJoinLeftOn(lefts.join(',')); setJoinRightOn(rights.join(','))
                      }}>
                        <option value="">-- select right --</option>
                        {(joinRightColsOptions||[]).map(c=> <option key={c} value={c}>{c}</option>)}
                      </select>
                      <button onClick={()=>{
                        const next = compositePairs.slice(); next.splice(idx,1)
                        setCompositePairs(next)
                        const lefts = next.map(x=>x.left).filter(Boolean)
                        const rights = next.map(x=>x.right).filter(Boolean)
                        setJoinLeftOnArr(lefts); setJoinRightOnArr(rights)
                        setJoinLeftOn(lefts.join(',')); setJoinRightOn(rights.join(','))
                      }}>Remove</button>
                    </div>
                  )) : <div className="empty-state">No key pairs yet — select keys above or add pairs.</div>}
                  <div>
                    <button onClick={()=>{
                      const next = (compositePairs || []).concat([{ left: '', right: '' }])
                      setCompositePairs(next)
                    }}>Add pair</button>
                  </div>
                </div>
              </div>
              <div>
                <label>Join type</label>
                <select value={joinType} onChange={(e)=>setJoinType(e.target.value)}>
                  <option value="inner">Inner</option>
                  <option value="left">Left</option>
                  <option value="right">Right</option>
                  <option value="outer">Full outer</option>
                  <option value="anti">Anti</option>
                </select>
              </div>
              <div>
                <label>Sample rows</label>
                <input type="number" value={joinSampleSize} onChange={(e)=>setJoinSampleSize(Number(e.target.value))} />
              </div>
            </div>
            <div className="button-row" style={{marginTop:8}}>
              <button className="primary" onClick={doJoinPreview} disabled={joinPreviewLoading}> {joinPreviewLoading ? 'Previewing…' : 'Preview Join'}</button>
              <button onClick={doJoinSuggestions}>Suggest keys</button>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8,marginTop:10}}>
              <div>
                <label>Suffix (left)</label>
                <input value={suffixLeft} onChange={(e)=>setSuffixLeft(e.target.value)} />
              </div>
              <div>
                <label>Suffix (right)</label>
                <input value={suffixRight} onChange={(e)=>setSuffixRight(e.target.value)} />
              </div>
              <div>
                <label>Conflict preference</label>
                <select value={preferResolve} onChange={(e)=>setPreferResolve(e.target.value)}>
                  <option value="left">Prefer left</option>
                  <option value="right">Prefer right (overwrite)</option>
                </select>
              </div>
              <div>
                <label>Rename mappings (one per line: side:left|right from-&gt;to)</label>
                <textarea value={mappingText} onChange={(e)=>setMappingText(e.target.value)} rows={3} placeholder="left:old_col-&gt;new_col" />
              </div>
            </div>
            {joinPreviewResult ? (
              <div style={{marginTop:12}}>
                <div style={{display:'flex',gap:12}}>
                  <div><strong>Left:</strong> {joinPreviewResult.stats && joinPreviewResult.stats.left_count}</div>
                  <div><strong>Right:</strong> {joinPreviewResult.stats && joinPreviewResult.stats.right_count}</div>
                  <div><strong>Joined:</strong> {joinPreviewResult.stats && joinPreviewResult.stats.joined_count}</div>
                </div>
                <div style={{marginTop:8,display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
                  <div>
                    <label>Before (left sample)</label>
                    <JsonBlock value={joinLeftPreview} empty="No sample" />
                  </div>
                  <div>
                    <label>Before (right sample)</label>
                    <JsonBlock value={joinRightPreview} empty="No sample" />
                  </div>
                </div>
                <div style={{marginTop:8}}>
                  <label>Preview rows</label>
                  <JsonBlock value={joinPreviewResult.preview} empty="No preview rows" />
                </div>
                <div style={{marginTop:8,display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
                  <div>
                    <label>Unmatched (left)</label>
                    <JsonBlock value={joinPreviewResult.unmatched_left_sample} empty="None" />
                  </div>
                  <div>
                    <label>Unmatched (right)</label>
                    <JsonBlock value={joinPreviewResult.unmatched_right_sample} empty="None" />
                  </div>
                </div>
              </div>
            ) : null}

            <hr />
            <div style={{display:'flex',gap:8,alignItems:'center'}}>
              <label style={{marginRight:8}}>Export format</label>
              <select value={exportFormat} onChange={(e)=>setExportFormat(e.target.value)}>
                <option value="csv">CSV</option>
                <option value="xlsx">XLSX</option>
                <option value="pandas">Pandas (pkl)</option>
                <option value="sql">SQLite</option>
              </select>
              <input value={exportFilename} onChange={(e)=>setExportFilename(e.target.value)} placeholder="filename" style={{marginLeft:8}} />
              <button className="primary" onClick={doJoinExport} disabled={joinExporting}>{joinExporting ? 'Exporting…' : 'Export Join'}</button>
            </div>
            {applyRes && applyRes.last_export ? (
              <div style={{marginTop:8}}>Download: <a href={`${BACKEND}/download?path=${encodeURIComponent(applyRes.last_export)}`} target="_blank" rel="noreferrer">{applyRes.last_export}</a></div>
            ) : null}
          </Card>
        </div>
      )
    }

    if (activeTab === "uploads") {
      return (
        <div className="tab-stack tab-panel">
          <Card eyebrow="Uploads" title="Saved uploads" subtitle="Uploaded files are persisted and reusable across app reloads.">
            <div style={{display:'flex',gap:8,marginBottom:8}}>
              <button onClick={fetchUploads}>Refresh uploads</button>
              <button onClick={fetchDuplicates}>Find duplicates</button>
            </div>
            {uploadsLoading ? (
              <div style={{padding:12,display:'flex',alignItems:'center',gap:8}}>
                <div>⏳</div>
                <div>Loading uploads…</div>
              </div>
            ) : uploadsList && uploadsList.length ? (
              <div className="upload-history">
                {uploadsList.map((item) => (
                  <div key={item.path} className="upload-item-row">
                    <div style={{flex:'1 1 0'}}>
                      <div className="upload-item-name">{item.name}</div>
                      <small style={{display:'block',color:'var(--muted)'}}>{item.path}</small>
                      {(item.last_refreshed || item.inspected_at || item.uploadedAt || item.modified_at) && (
                        <small style={{display:'block',color:'var(--muted)',marginTop:4}}>
                          Last refreshed: {new Date(item.last_refreshed || item.inspected_at || item.uploadedAt || item.modified_at).toLocaleString()}
                        </small>
                      )}
                    </div>
                    <div style={{display:'flex',gap:8}}>
                      <button
                        onClick={() => openUploadItem(item)}
                        disabled={uploadsLoading || (inspectionLoading && inspectingPath === item.path)}
                      >
                        {inspectionLoading && inspectingPath === item.path ? 'Opening…' : 'Open'}
                      </button>
                      <button
                        onClick={() => { setPath(item.path); doProfileForPath(item.path) }}
                        disabled={uploadsLoading}
                      >
                        Profile
                      </button>
                      <button
                        onClick={() => refreshInspection(item)}
                        disabled={uploadsLoading || (inspectionLoading && inspectingPath === item.path)}
                      >
                        {inspectionLoading && inspectingPath === item.path ? 'Refreshing…' : 'Refresh'}
                      </button>
                      <button
                        onClick={async ()=>{
                          try{
                            const res = await fetch(`${BACKEND}/uploads/delete`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path: item.path}) })
                            if(!res.ok){ const txt = await res.text(); addToast('Delete failed: '+txt,'error'); return }
                            setUploadsList((u)=> (u||[]).filter(x=> x.path !== item.path))
                            addToast('Deleted ' + item.name, 'success')
                          }catch(e){ addToast('Delete failed: '+(e.message||e),'error') }
                        }}
                      >
                        Delete
                      </button>
                      {item.explanations_history && item.explanations_history.length > 0 ? (
                        <button onClick={() => toggleUploadExplanations(item)} style={{marginLeft:8}}>
                          {uploadExplanationsOpen[item.path] ? 'Hide explanations' : `Explanations (${item.explanations_history.length})`}
                        </button>
                      ) : null}
                    </div>
                    <div style={{width:'100%'}}>
                      {uploadExplanationsOpen[item.path] && (
                        <div style={{marginTop:8,borderTop:'1px dashed rgba(255,255,255,0.04)',paddingTop:8}}>
                          {uploadExplanationsData[item.path] ? (
                            uploadExplanationsData[item.path].length === 0 ? (
                              <div className="empty-state">No approved-recipe explanations for this upload.</div>
                            ) : (
                              uploadExplanationsData[item.path].map((e, idx) => (
                                <div key={idx} style={{padding:'6px 0',borderBottom:'1px solid rgba(255,255,255,0.02)'}}>
                                  <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                                    <div><strong>{e.recipe_name || e.recipe_id}</strong> <small style={{color:'var(--muted)'}}>({e.recipe_id})</small></div>
                                    <div><small style={{color:'var(--muted)'}}>{new Date(e.timestamp).toLocaleString()}</small></div>
                                  </div>
                                  <div style={{marginTop:6}}>
                                    {e.explanations && e.explanations.length ? (
                                      <div style={{fontSize:'0.95rem',color:'var(--muted)'}}>{e.explanations[0].reason || JSON.stringify(e.explanations[0].step)}</div>
                                    ) : <div className="empty-state">No explanation details.</div>}
                                  </div>
                                </div>
                              ))
                            )
                          ) : (
                            <div style={{display:'flex',gap:8,alignItems:'center'}}><div>⏳</div><div>Loading explanations…</div></div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">No uploads yet. Use Upload CSV to add one.</div>
            )}
          </Card>
        </div>
      )
    }

    if (activeTab === "editor") {
      return (
        <div className="tab-stack tab-panel">
          <Card eyebrow="Moved" title="Recipe revision moved to Source" subtitle="Upload, instruct, edit recipe JSON, preview, and apply now live in the Source tab.">
            <div className="button-row">
              <button className="primary" onClick={() => setActiveTab("source")}>Open Source workflow</button>
            </div>
          </Card>
        </div>
      )
    }

    if (activeTab === "preview") {
      return (
        <div className="tab-stack tab-panel">
          <Card eyebrow="Result" title="Preview and apply output" subtitle="Use side-by-side previews to validate transformations before writing data.">
            <div className="button-row button-row-tight">
              <span className="chip">Backend: {BACKEND}</span>
              <span className="chip">Deterministic parser</span>
              <span className="chip">Tauri-ready</span>
            </div>
            {preview ? (
              <div className="preview-grid">
                <div>
                  <h3>Before</h3>
                  <pre className="json-block json-block-soft">{JSON.stringify(preview.before, null, 2)}</pre>
                </div>
                <div>
                  <h3>After</h3>
                  <pre className="json-block json-block-soft">{JSON.stringify(preview.after, null, 2)}</pre>
                </div>
              </div>
            ) : (
              <div className="empty-state">No preview yet. Build a recipe or click Preview.</div>
            )}
            <div className="button-row">
              <button className="primary" onClick={doPreview}>Refresh preview</button>
              <button onClick={doApply}>Apply</button>
            </div>
          </Card>

          <Card eyebrow="Apply" title="Apply result" subtitle="Confirm what was written to disk after the recipe runs.">
            <JsonBlock value={applyRes} empty="Nothing applied yet." />
          </Card>
        </div>
      )
    }

    if (activeTab === "team") {
      return (
        <div className="tab-stack tab-panel">
          <Card eyebrow="Team" title="Team collaboration" subtitle="Invite members and share files with your team">
            <div style={{display:'grid',gap:12}}>
              <div>
                <h4 style={{margin:0}}>{teamName || 'Your team'}</h4>
                <div style={{color:'var(--muted)'}}>Members</div>
                <div style={{marginTop:8}}>
                  {((teamMemberObjects && teamMemberObjects.length) || (teamMembers && teamMembers.length)) === 0 && <div className="empty-state">No team members yet.</div>}
                  {(teamMemberObjects && teamMemberObjects.length ? teamMemberObjects : (teamMembers || [])).map((m) => {
                    const email = m.email || m
                    const role = (m.role || (teamMemberObjects && m.role) || 'member')
                    return (
                      <div key={email} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                        <div>
                          <div>{m.username || email}</div>
                          <small style={{color:'var(--muted)'}}>Role: {role}</small>
                        </div>
                        <div style={{display:'flex',gap:8}}>
                          {accountUser && accountUser.team_name && <>
                            <select value={role} onChange={(e) => changeMemberRole(email, e.target.value)}>
                              <option value="member">member</option>
                              <option value="admin">admin</option>
                              <option value="guest">guest</option>
                            </select>
                            <button onClick={() => removeTeamMember(email)}>Remove</button>
                            {accountUser.team_owner && accountUser.email !== email && (
                              <button onClick={() => transferOwnership(email)}>Transfer ownership</button>
                            )}
                          </>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              <div>
                <h5>Invite member</h5>
                <div style={{display:'flex',gap:8}}>
                  <input value={newMemberEmail} onChange={(e)=>setNewMemberEmail(e.target.value)} placeholder="email@example.com" />
                  <button className="primary" onClick={inviteTeamMember}>Send invite</button>
                </div>
                <div style={{marginTop:12}}>
                  <h6>Accept invite (token)</h6>
                  <div style={{display:'flex',gap:8}}>
                    <input value={inviteToken} onChange={(e)=>setInviteToken(e.target.value)} placeholder="invite token" />
                    <input value={acceptUsername} onChange={(e)=>setAcceptUsername(e.target.value)} placeholder="username (if creating)" />
                    <input type="password" value={acceptPasswordLocal} onChange={(e)=>setAcceptPasswordLocal(e.target.value)} placeholder="password (if creating)" />
                    <button onClick={acceptInvite}>Accept</button>
                  </div>
                </div>
              </div>

              <div>
                <h5>Pending invites</h5>
                <div style={{marginTop:8}}>
                  {(!pendingInvites || pendingInvites.length === 0) && <div className="empty-state">No pending invites.</div>}
                  {(pendingInvites || []).map((inv) => (
                    <div key={inv.id} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                      <div>
                        <div>{inv.email} {inv.inviter_username ? <small style={{color:'var(--muted)', marginLeft:8}}>invited by {inv.inviter_username}</small> : null}</div>
                        <small style={{color:'var(--muted)'}}>
                          Role: {inv.role || 'member'} • Created: {inv.created_at ? new Date(inv.created_at).toLocaleString() : ''}
                          {inv.token_payload && inv.token_payload.exp ? (
                            <span> • Expires: {new Date(inv.token_payload.exp * 1000).toLocaleString()}</span>
                          ) : null}
                        </small>
                      </div>
                      <div style={{display:'flex',gap:8}}>
                        {accountUser && accountUser.team_name && inv.can_manage && <button onClick={() => adminRevokeInvite(inv.id)}>Revoke</button>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h5>Shared files</h5>
                <div style={{marginTop:8}}>
                  {sharedUploads.length === 0 && <div className="empty-state">No files shared with your team.</div>}
                  {sharedUploads.map((s) => (
                    <div key={s.path} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                      <div>
                        <div><strong>{s.name}</strong></div>
                        <small style={{color:'var(--muted)'}}>Shared by {s.shared_by || 'unknown'} • {s.shared_at ? new Date(s.shared_at).toLocaleString() : ''}</small>
                      </div>
                      <div style={{display:'flex',gap:8}}>
                        <button onClick={() => { if(window.confirm('Unshare this file?')) toggleShare({name: s.name}, false) }}>Unshare</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h5>All uploads</h5>
                <div style={{maxHeight:220, overflow:'auto', marginTop:8}}>
                  {(uploadsList || []).map((u) => {
                    const isShared = (sharedUploads || []).some(s => s.name === u.name)
                    return (
                      <div key={u.path} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 0',borderBottom:'1px solid rgba(255,255,255,0.03)'}}>
                        <div style={{flex:'1 1 0'}}>
                          <div><strong>{u.name}</strong></div>
                          <small style={{color:'var(--muted)'}}>{u.path}</small>
                        </div>
                        <div style={{display:'flex',gap:8}}>
                          <button onClick={() => toggleShare(u, !isShared)}>{isShared ? 'Unshare' : 'Share'}</button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          </Card>
        </div>
      )
    }

    if (activeTab === "settings") {
      return (
        <div className="tab-stack tab-panel">
          <Card eyebrow="Account" title="Settings" subtitle="Manage your account and privacy preferences">
            <div style={{display:'grid',gridTemplateColumns:'1fr',gap:12}}>
              <div>
                <h4>Account</h4>
                {!accountUser ? (
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
                    <div>
                      <h5>Create account</h5>
                      <label style={{display:'flex',flexDirection:'column',gap:6}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Username</span>
                        <input value={signupUsername} onChange={(e)=>setSignupUsername(e.target.value)} placeholder="username" />
                      </label>
                      <label style={{display:'flex',flexDirection:'column',gap:6,marginTop:8}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Email (optional)</span>
                        <input value={signupEmail} onChange={(e)=>setSignupEmail(e.target.value)} placeholder="you@example.com" />
                      </label>
                      <label style={{display:'flex',flexDirection:'column',gap:6,marginTop:8}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Password</span>
                        <input type="password" value={signupPassword} onChange={(e)=>setSignupPassword(e.target.value)} placeholder="choose a password" />
                      </label>
                      <div style={{marginTop:10}}>
                        <button className="primary" onClick={doSignup}>Create account</button>
                      </div>
                    </div>
                    <div>
                      <h5>Sign in</h5>
                      <label style={{display:'flex',flexDirection:'column',gap:6}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Username or email</span>
                        <input value={loginIdentity} onChange={(e)=>setLoginIdentity(e.target.value)} placeholder="username or email" />
                      </label>
                      <label style={{display:'flex',flexDirection:'column',gap:6,marginTop:8}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Password</span>
                        <input type="password" value={loginPassword} onChange={(e)=>setLoginPassword(e.target.value)} placeholder="password" />
                      </label>
                      <div style={{marginTop:10}}>
                        <button className="primary" onClick={doLogin}>Sign in</button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                      <div>
                        <strong>{accountUser.username}</strong>
                        <div style={{color:'var(--muted)'}}>Member since {new Date(accountUser.created_at).toLocaleDateString()}</div>
                      </div>
                      <div>
                        <button onClick={doLogout}>Logout</button>
                      </div>
                    </div>
                    <div style={{marginTop:12}}>
                      <label style={{display:'flex',flexDirection:'column',gap:6}}>
                        <span style={{fontSize:12,color:'var(--muted)'}}>Email (optional)</span>
                        <input value={accountUser.email||''} onChange={(e)=>setAccountUser({...accountUser,email:e.target.value})} placeholder="you@example.com" />
                      </label>
                      <div style={{marginTop:8}}>
                        <button className="primary" onClick={saveAccountUpdates}>Save account</button>
                        <button style={{marginLeft:8}} onClick={doAccountExport}>Export data</button>
                        <button style={{marginLeft:8}} onClick={doLogout}>Logout</button>
                      </div>
                      <div style={{marginTop:12,borderTop:'1px dashed rgba(255,255,255,0.04)',paddingTop:12}}>
                        <h5 style={{margin:0}}>Danger zone</h5>
                        <div style={{marginTop:8}}>
                          <label style={{display:'flex',flexDirection:'column',gap:6}}>
                            <span style={{fontSize:12,color:'var(--muted)'}}>Confirm password</span>
                            <input type="password" value={deletePassword} onChange={(e)=>setDeletePassword(e.target.value)} />
                          </label>
                          <label style={{display:'flex',flexDirection:'column',gap:6,marginTop:8}}>
                            <span style={{fontSize:12,color:'var(--muted)'}}>Type exactly: DELETE MY ACCOUNT</span>
                            <input value={deleteConfirmTextLocal} onChange={(e)=>setDeleteConfirmTextLocal(e.target.value)} />
                          </label>
                          <div style={{marginTop:8}}>
                            <button onClick={doAccountDelete} style={{background:'#a33',color:'#fff'}}>Delete account</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                <div style={{marginTop:18}}>
                  <h5>Team settings</h5>
                  <label style={{display:'flex',flexDirection:'column',gap:6}}>
                    <span style={{fontSize:12,color:'var(--muted)'}}>Team name</span>
                    <input value={teamName} onChange={(e)=>setTeamName(e.target.value)} placeholder="Your team" />
                  </label>
                  <div style={{marginTop:8}}>
                    <div style={{display:'flex',gap:8}}>
                      <input value={newMemberEmail} onChange={(e)=>setNewMemberEmail(e.target.value)} placeholder="invite member email" />
                      <button onClick={()=>{ if(newMemberEmail && !teamMembers.includes(newMemberEmail)){ setTeamMembers([...teamMembers,newMemberEmail]); setNewMemberEmail('') } }}>Add</button>
                    </div>
                    <div style={{marginTop:8}}>
                      {teamMembers.length === 0 ? <div className="empty-state">No team members</div> : (
                        teamMembers.map((m)=> (
                          <div key={m} style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 0'}}>
                            <div>{m}</div>
                            <button onClick={()=>setTeamMembers(teamMembers.filter(x=>x!==m))}>Remove</button>
                          </div>
                        ))
                      )}
                    </div>
                    <div style={{marginTop:10}}>
                      <button className="primary" onClick={saveAccountUpdates}>Save team</button>
                    </div>
                  </div>
                </div>
              </div>
              <div>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <h4 style={{margin:0}}>Consent history</h4>
                  <div>
                    <button onClick={async () => { if (!showConsentHistory) await fetchConsentHistory(); setShowConsentHistory(s => !s); }}>{showConsentHistory ? 'Hide' : 'Show'}</button>
                  </div>
                </div>
                {showConsentHistory && (
                  <div style={{maxHeight:320,overflow:'auto',marginTop:8}}>
                    {!consentHistory ? (
                      <div className="empty-state">No consent history loaded. <button onClick={()=>fetchConsentHistory()}>Load</button></div>
                    ) : consentHistory.length === 0 ? (
                      <div className="empty-state">No consent records</div>
                    ) : (
                      consentHistory.map((c) => (
                        <div key={c.id} style={{borderBottom:'1px solid rgba(255,255,255,0.03)',padding:'8px 0'}}>
                          <div style={{display:'flex',justifyContent:'space-between'}}>
                            <div><strong>{c.id}</strong></div>
                            <div><small style={{color:'var(--muted)'}}>{new Date(c.timestamp).toLocaleString()}</small></div>
                          </div>
                          <div style={{marginTop:6}}><pre style={{fontFamily:'monospace',margin:0}}>{JSON.stringify(c.consents,null,2)}</pre></div>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          </Card>
        </div>
      )
    }

    return (
      <div className="tab-stack tab-panel">
        <div className="insight-grid">
          <Card eyebrow="Profile" title="Column profile" subtitle="A quick summary of each field in the source file.">
            <JsonBlock value={profile} empty="No profile yet." />
          </Card>

          <Card eyebrow="Suggestions" title="Deterministic cleaning hints" subtitle="Rule-based recommendations derived from the data profile.">
            <JsonBlock value={cleaningSuggestions} empty="No cleaning suggestions yet." />
          </Card>
        </div>

        <Card eyebrow="Workflow" title="Current tab and controls" subtitle={`You are viewing ${activeItem.label.toLowerCase()}.`}>
          <div className="button-row">
            <button className="primary" onClick={doCleaningSuggestions}>Refresh suggestions</button>
            <button onClick={() => setActiveTab("source")}>Go to source</button>
            <button onClick={() => setActiveTab("preview")}>Go to preview</button>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className={`app-shell ${themeClass}`} data-theme={theme}>
      <header className="app-header">
        <div className="logo-wrap">
          <img src="/logo.svg" alt="FalconBroom logo" className="app-logo" />
          <h1 className="app-title">FalconBroom</h1>
        </div>
      </header>
      {/* CMP banner */}
      {!consent && (
        <div className="cmp-banner card" style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:12}}>
          <div>
            <strong>FalconBroom uses cookies</strong>
            <div style={{color:'var(--muted)'}}>We use essential cookies and optional analytics/marketing cookies to improve the product. You can manage preferences in Settings.</div>
          </div>
          <div style={{display:'flex',gap:8}}>
            <button className="primary" onClick={() => { acceptAllConsents() }}>Accept all</button>
            <button onClick={() => { rejectNonEssential() }}>Reject non-essential</button>
            <button onClick={() => setActiveTab('settings')}>Manage</button>
          </div>
        </div>
      )}
      
      {/* header styles moved to styles.css */}
      {unauthView}
      {/* Toasts container */}
      <div className="toasts-root" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone || 'info'}`}>
            <div className="toast-left">
              <span className="toast-icon" aria-hidden>
                {t.tone === 'success' ? '✔' : t.tone === 'error' ? '✖' : t.tone === 'warn' ? '⚠' : 'ℹ'}
              </span>
            </div>
            <div className="toast-body">{t.message}</div>
            <button className="toast-close" aria-label="Dismiss" onClick={() => removeToast(t.id)}>✕</button>
            <div className="toast-progress" style={{animationDuration: `${t.ttl || 4200}ms`}} />
          </div>
        ))}

        <button className="toast-archive-toggle" onClick={() => setShowToastPanel((s) => !s)} aria-expanded={showToastPanel} title="Show notifications history">
          {showToastPanel ? 'Hide notifications' : 'Notifications'}
        </button>

        {showToastPanel && (
          <div className="toast-panel" role="dialog" aria-label="Notifications history">
            <div className="toast-panel-header">
              <strong>Notifications</strong>
              <div style={{display:'flex',gap:8,alignItems:'center'}}>
                <button className="toast-archive-clear" onClick={() => setToastArchive([])}>Clear</button>
                <button onClick={() => setShowToastPanel(false)}>Close</button>
              </div>
            </div>
            <div className="toast-panel-list">
              {toastArchive.length === 0 && <div className="empty-state" style={{padding:12}}>No notifications yet.</div>}
              {toastArchive.map((a) => (
                <div key={a.id} className="toast-archive-item">
                  <div className="toast-left">
                    <span className="toast-icon" aria-hidden>{a.tone === 'success' ? '✔' : a.tone === 'error' ? '✖' : a.tone === 'warn' ? '⚠' : 'ℹ'}</span>
                  </div>
                  <div className="toast-archive-body">
                    <div className="toast-archive-msg">{a.message}</div>
                    <div className="toast-archive-ts">{new Date(a.ts).toLocaleString()}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {showApproveConfirm && (
          <div className="modal-overlay" onClick={() => setShowApproveConfirm(false)}>
            <div className="modal-panel card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Approve recipe">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div>
                  <h3>Confirm approve</h3>
                  <div style={{color:'var(--muted)'}}>Approving this recipe marks it as ready to run. This is required before executing runs.</div>
                </div>
                <div>
                  <button onClick={() => setShowApproveConfirm(false)}>Close</button>
                </div>
              </div>
              <div style={{marginTop:12}}>
                <p>Are you sure you want to approve recipe <strong>{recipeId || '(unsaved)'}</strong>?</p>
                <div style={{display:'flex',gap:8,marginTop:12}}>
                  <button className="primary" onClick={confirmApprove}>Approve</button>
                  <button className="close" onClick={() => setShowApproveConfirm(false)}>Cancel</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="app-bg app-bg-a" />
      <div className="app-bg app-bg-b" />
      <div className="app-bg app-bg-c" />

      <aside className="side-rail card">
        <div className="brand-block">
          <div className="brand-row">
            <span className="eyebrow">FalconBroom</span>
            <button
              className="rail-collapse"
              onClick={() => persistRailCollapsed(!railCollapsed)}
              aria-label={railCollapsed ? "Expand navigation rail" : "Collapse navigation rail"}
              title={railCollapsed ? "Expand rail" : "Collapse rail"}
            >
              {railCollapsed ? "⟩" : "⟨"}
            </button>
          </div>
          <h1>{railCollapsed ? "WB" : "Workspace"}</h1>
          <p className={railCollapsed ? "rail-copy rail-copy-hidden" : "rail-copy"}>Data cleaning, join orchestration, and preview-first recipe authoring.</p>
        </div>

        <nav className="side-nav" aria-label="Primary">
          {NAV_SECTIONS.map((sectionName, index) => {
            const sectionItems = NAV_ITEMS.filter((item) => item.section === sectionName)
            return (
              <div key={sectionName} className="nav-group">
                <div className={`nav-section-label ${railCollapsed ? "nav-section-label-collapsed" : ""}`}>
                  {!railCollapsed && <span>{sectionName}</span>}
                  {railCollapsed && index !== 0 && <span className="nav-divider" />}
                </div>
                <div className="nav-group-items">
                  {sectionItems.map((item) => (
                    <button
                      key={item.id}
                      className={`nav-item ${activeTab === item.id ? "active" : ""} ${railCollapsed ? "nav-item-collapsed" : ""}`.trim()}
                      onClick={() => setActiveTab(item.id)}
                    >
                      <span className="nav-topline">
                        <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                        <span className="nav-label">{item.label}</span>
                      </span>
                      {!railCollapsed && <small>{item.detail}</small>}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="rail-footer">
          <div className="rail-metric">
            <span>Theme</span>
            <strong>{theme === "light" ? "Light" : "Dark"}</strong>
          </div>
          <button className="theme-toggle" onClick={() => persistTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "light" ? "Switch to dark" : "Switch to light"}
          </button>
          <div className="rail-actions">
            <button onClick={openUploadPicker}>Upload file</button>
            <button onClick={doProfile}>Profile data</button>
            <button className="primary" onClick={doSuggest}>Suggest recipe</button>
          </div>
        </div>
      </aside>

      <main className="main-pane">
        <header className="top-bar card">
          <div>
            <span className="eyebrow">{activeItem.label}</span>
            <h2>{activeItem.detail}</h2>
          </div>
          <div className="top-bar-status">
            <span className="chip">Left rail navigation</span>
            <span className="chip">Tab: {activeItem.label}</span>
            {backgroundBusy && (
              <span className="chip" title="Background tasks running">Loading…</span>
            )}
          </div>
        </header>

        {renderTab()}

        {showCustomRevisions && (
          <div className="modal-overlay" onClick={() => setShowCustomRevisions(false)}>
            <div className="modal-panel card" style={{position:'relative'}} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Custom revisions">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12}}>
                <div>
                  <h3>Custom revisions</h3>
                  <div style={{color:'var(--muted)',fontSize:'0.9rem'}}>Refine transformation steps and instruction.</div>
                </div>
                <div>
                  <span style={{display:'inline-flex',alignItems:'center',gap:8}}>
                    {recipeGenerating ? <span className="chip">Generating…</span> : <span className="chip">Auto-generate</span>}
                    <button onClick={() => setShowCustomRevisions(false)} aria-label="Close custom revisions">✕</button>
                  </span>
                </div>
              </div>

              <div style={{marginTop:12}}>
                <label>Plain-English instruction</label>
                {sourceInspection ? (
                  <div style={{marginTop:8, marginBottom:8}}>
                    <div style={{fontSize:'0.9rem',color:'var(--muted)',marginBottom:6}}>
                      Raw data preview {showFullPreview ? '(full)' : '(first rows)'}
                      <button style={{marginLeft:12}} onClick={() => setShowFullPreview((s)=>!s)}>{showFullPreview ? 'Collapse' : 'Show full'}</button>
                    </div>
                    <div className="table-wrap" style={{maxHeight: showFullPreview ? 560 : 180, overflow:'auto'}}>
                      <table className="source-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            {(showFullPreview ? sourceInspection.columns : sourceInspection.columns.slice(0,8)).map((col) => (<th key={col}>{col}</th>))}
                          </tr>
                        </thead>
                        <tbody>
                          {(showFullPreview ? sourceInspection.rows : sourceInspection.rows.slice(0,5)).map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              <td>{sourceInspection.offset + rowIndex + 1}</td>
                              {(showFullPreview ? sourceInspection.columns : sourceInspection.columns.slice(0,8)).map((col) => (<td key={col}>{String(row[col] ?? '')}</td>))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {/* Condensed diagnostics summary placed between raw preview and instruction */}
                    {diagnostics && Object.keys(diagnostics).length > 0 && (
                      <div className="diagnostics-panel" style={{marginTop:8}}>
                        <h4>Data diagnostics summary</h4>
                        <div className="column-meta-grid">
                          {Object.entries(diagnostics)
                            .filter(([col, info]) => (info.missing_count || info.mixed_type || info.constant))
                            .slice(0, 12)
                            .map(([col, info]) => (
                              <div key={col} className="column-meta-item">
                                <strong>{col}</strong>
                                <small>Missing: {info.missing_count ?? '—'}</small>
                                <small>Unique: {info.unique_count ?? '—'}</small>
                                {info.mixed_type ? <small style={{color:'#f59e0b'}}>Mixed types</small> : null}
                                {info.constant ? <small style={{color:'#fb7185'}}>Constant</small> : null}
                              </div>
                            ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : null}
                <textarea
                  ref={instructionRef}
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  rows={3}
                  placeholder="e.g. fill missing age values, lowercase email, and remove duplicate customers"
                />
                <div style={{marginTop:8, display:'flex', gap:8, alignItems:'center', flexWrap:'wrap'}}>
                  <small style={{color:'var(--muted)'}}>Regression options:</small>
                  <label style={{fontSize:'0.85rem'}}>Model
                    <select value={regressionModel} onChange={(e)=>setRegressionModel(e.target.value)} style={{marginLeft:6}}>
                      <option value="linear">Linear</option>
                      <option value="ridge">Ridge</option>
                    </select>
                  </label>
                  <label style={{fontSize:'0.85rem'}}>Features
                    <input value={regressionFeatures} onChange={(e)=>setRegressionFeatures(e.target.value)} placeholder="HEIGHT, WEIGHT" style={{marginLeft:6}} />
                  </label>
                  <label style={{fontSize:'0.85rem'}}>Group by
                    <input value={regressionGroupBy} onChange={(e)=>setRegressionGroupBy(e.target.value)} placeholder="city" style={{marginLeft:6}} />
                  </label>
                  <label style={{fontSize:'0.85rem'}}>Treat as missing
                    <input value={treatAsMissing} onChange={(e)=>setTreatAsMissing(e.target.value)} placeholder="e.g. 0, NA, ''" style={{marginLeft:6}} />
                  </label>
                  <button onClick={() => setInstruction("Predict AGE from HEIGHT and WEIGHT using regression")}>Regression example</button>
                  <button onClick={() => setInstruction("Impute AGE from BIRTH_YEAR using mean")}>Mean impute example</button>
                </div>
                <div style={{marginTop:8}}>
                  <button className="primary" onClick={() => doRecipeFromText()} disabled={recipeGenerating || !instruction || instruction.trim().length===0}>
                    {recipeGenerating ? 'Generating…' : 'Generate JSON'}
                  </button>
                </div>
              </div>

              <div style={{marginTop:12}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <label>Recipe JSON</label>
                  <div style={{display:'flex',gap:8,alignItems:'center'}}>
                    <small style={{color:'var(--muted)'}}>Optional — generated from instruction</small>
                    <button onClick={() => setShowRecipeJson((s)=>!s)}>{showRecipeJson ? 'Hide JSON' : 'Show JSON'}</button>
                    <button onClick={() => loadExplanations()} style={{marginLeft:8}}>{explainLoading ? 'Loading…' : 'Show explanations'}</button>
                  </div>
                </div>
                {showRecipeJson ? (
                  <textarea value={recipeText} onChange={(e) => setRecipeText(e.target.value)} rows={10} className="editor" />
                ) : (
                  <div className="empty-state">Recipe JSON hidden. Click "Generate JSON" below the instruction to create a recipe JSON. Click "Show JSON" to edit manually.</div>
                )}
                {showExplanations && explanations ? (
                  <div style={{marginTop:12}}>
                    <div style={{display:'flex',alignItems:'center',gap:8}}>
                      <h4 style={{margin:0}}>Explanations</h4>
                      {explainLoading && <div style={{fontSize:'0.9rem',color:'var(--muted)'}}>Loading explanations…</div>}
                    </div>
                    {explanations.length === 0 && <div className="empty-state">No explanations available.</div>}
                    {explanations.map((exp, i) => (
                      <div key={i} style={{borderTop:'1px solid rgba(255,255,255,0.03)', paddingTop:8, marginTop:8}}>
                        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                          <div><strong>{exp.step?.action || exp.step?.action}</strong> <small style={{color:'var(--muted)'}}>confidence: {Number(exp.confidence).toFixed(2)}</small></div>
                          <div><button onClick={() => { setShowExplanations(false); setExplanations(null) }}>Close</button></div>
                        </div>
                        <div style={{marginTop:6}}><div style={{fontSize:'0.95rem'}}>{exp.reason}</div></div>
                        {exp.preview && (
                          <div style={{marginTop:8, display:'flex', gap:12}}>
                            <div><strong>Before</strong><pre style={{fontFamily:'monospace', margin:0}}>{JSON.stringify(exp.preview.before)}</pre></div>
                            <div><strong>After</strong><pre style={{fontFamily:'monospace', margin:0}}>{JSON.stringify(exp.preview.after)}</pre></div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : null}
                {showConfirmModal ? (
                  <div className="modal">
                    <div className="modal-content">
                      <h3>Confirm column</h3>
                      <p>Multiple columns match your instruction. Choose the correct column:</p>
                      <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
                        {candidateColumns.map((c) => (
                          <button key={c} onClick={() => confirmCandidate(c)}>{c}</button>
                        ))}
                      </div>
                      <div style={{marginTop:12}}>
                        <button className="close" onClick={() => setShowConfirmModal(false)}>Cancel</button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>

              {generatedPreview && (
                <div style={{marginTop:12}}>
                  <h4>Generated preview (sample)</h4>
                  {generatedPreview.warnings && generatedPreview.warnings.length > 0 && (
                    <div style={{marginBottom:8}}>
                      {generatedPreview.warnings.map((w, i) => (
                        <div key={i} style={{color:'#f59e0b', fontSize:'0.95rem'}}>
                          {w.message || (w.step === 'impute' ? `Imputed ${w.column}: ${w.rows_changed || 0} rows changed` : JSON.stringify(w))}
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{display:'flex',gap:12,alignItems:'center',marginBottom:8}}>
                    <div style={{display:'flex',gap:8,alignItems:'center'}}>
                      <label style={{fontSize:'0.9rem',color:'var(--muted)'}}>Rows</label>
                      <select value={rowsToShow} onChange={(e)=>setRowsToShow(Number(e.target.value))}>
                        <option value={6}>6</option>
                        <option value={12}>12</option>
                        <option value={20}>20</option>
                        <option value={0}>All</option>
                      </select>
                    </div>
                    <div style={{display:'flex',gap:8,alignItems:'center'}}>
                      <label style={{fontSize:'0.9rem',color:'var(--muted)'}}>Columns</label>
                      <button onClick={()=>setShowColumnPicker((s)=>!s)}>{showColumnPicker ? 'Hide' : 'Select'}</button>
                      <button onClick={()=>setSelectedColumns(null)}>All</button>
                    </div>
                  </div>

                  {showColumnPicker && (
                    <div style={{display:'flex',flexWrap:'wrap',gap:6,marginBottom:8}}>
                      {((generatedPreview.before[0] ? Object.keys(generatedPreview.before[0]) : [])).map((col)=> (
                        <label key={col} style={{display:'inline-flex',alignItems:'center',gap:6}}>
                          <input type="checkbox" checked={selectedColumns ? selectedColumns.includes(col) : true} onChange={(e)=>{
                            if(!selectedColumns){ setSelectedColumns(((generatedPreview.before[0]?Object.keys(generatedPreview.before[0]):[]).filter(c=>c!==col))) }
                            else{
                              if(e.target.checked) setSelectedColumns(selectedColumns.filter(c=>c!==col))
                              else setSelectedColumns([...selectedColumns, col])
                            }
                          }} />
                          <small style={{color:'var(--muted)'}}>{col}</small>
                        </label>
                      ))}
                    </div>
                  )}

                  <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,marginBottom:8}}>
                    <div style={{display:'flex',gap:8,alignItems:'center'}}>
                      <div style={{display:'inline-flex',alignItems:'center',gap:8}}>
                        <div style={{width:12,height:12,background:'linear-gradient(90deg, rgba(245,158,11,0.12), rgba(245,158,11,0.06))',borderBottom:'2px solid rgba(245,158,11,0.18)'}} />
                        <small style={{color:'var(--muted)'}}>Changed</small>
                      </div>
                      <div style={{display:'inline-flex',alignItems:'center',gap:8}}>
                        <div style={{width:12,height:12,background:'#f59e0b',opacity:0.18,border:'1px solid rgba(245,158,11,0.22)'}} />
                        <small style={{color:'var(--muted)'}}>Selected</small>
                      </div>
                    </div>
                    <div style={{display:'flex',gap:8}}>
                      <button onClick={() => setSelectedCells(((generatedPreview.before||[]).reduce((acc,_,i)=>{
                        ((generatedPreview.before[i] ? Object.keys(generatedPreview.before[i]) : [])).forEach((col)=>{
                          const isChanged = String((generatedPreview.before[i]||{})[col] ?? '') !== String(((generatedPreview.after[i]||{})[col] ?? ''))
                          if(isChanged) acc[`${i}|${col}`] = true
                        })
                        return acc
                      },{})))}>Select all changes</button>
                      <button onClick={() => setSelectedCells({})}>Clear selection</button>
                      <button onClick={applySelectedChanges} className="primary">Apply Selected</button>
                    </div>
                  </div>

                  <div className="preview-grid" style={{gridTemplateColumns:'1fr 1fr', gap:12}}>
                    <div>
                      <h5>Before</h5>
                      <div className="table-wrap" style={{maxHeight:320, overflow:'auto'}}>
                        <table className="source-table">
                          <thead>
                            <tr>
                              <th>#</th>
                              {(generatedPreview.before[0] ? ((selectedColumns?selectedColumns:Object.keys(generatedPreview.before[0])).slice(0,8)) : []).map((col) => (
                                <th key={col}>{col}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {(generatedPreview.before || []).slice(0, rowsToShow || undefined).map((row, i) => (
                              <tr key={i}>
                                <td>{i+1}</td>
                                {(generatedPreview.before[0] ? ((selectedColumns?selectedColumns:Object.keys(generatedPreview.before[0])).slice(0,8)) : []).map((col) => {
                                  const beforeVal = String((generatedPreview.before[i]||{})[col] ?? '')
                                  const afterVal = String(((generatedPreview.after[i]||{})[col] ?? ''))
                                  const isChanged = beforeVal !== afterVal
                                  const isSelected = !!selectedCells[_cellKey(i,col)]
                                  const cls = isChanged ? (isSelected ? 'cell-changed cell-selected' : 'cell-changed') : ''
                                  return (
                                    <td key={col} className={cls}
                                      onClick={() => { if(isChanged) toggleCellSelection(i,col) }}
                                      onMouseEnter={(e)=>{ if(isChanged) setTooltip({x:e.clientX,y:e.clientY, before:beforeVal, after:afterVal}) }}
                                      onMouseLeave={()=>setTooltip(null)}
                                    >{String(row[col] ?? '')}</td>
                                  )
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    <div>
                      <h5>After</h5>
                      <div className="table-wrap" style={{maxHeight:320, overflow:'auto'}}>
                        <table className="source-table">
                          <thead>
                            <tr>
                              <th>#</th>
                              {(generatedPreview.after[0] ? ((selectedColumns?selectedColumns:Object.keys(generatedPreview.after[0])).slice(0,8)) : []).map((col) => (
                                <th key={col}>{col}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {(generatedPreview.after || []).slice(0, rowsToShow || undefined).map((row, i) => (
                              <tr key={i}>
                                <td>{i+1}</td>
                                {(generatedPreview.after[0] ? ((selectedColumns?selectedColumns:Object.keys(generatedPreview.after[0])).slice(0,8)) : []).map((col) => {
                                  const beforeVal = String((generatedPreview.before[i]||{})[col] ?? '')
                                  const afterVal = String((generatedPreview.after[i]||{})[col] ?? '')
                                  const isChanged = beforeVal !== afterVal
                                  const isSelected = !!selectedCells[_cellKey(i,col)]
                                  const cls = isChanged ? (isSelected ? 'cell-changed cell-selected' : 'cell-changed') : ''
                                  return (
                                    <td key={col} className={cls}
                                      onClick={() => { if(isChanged) toggleCellSelection(i,col) }}
                                      onMouseEnter={(e)=>{ if(isChanged) setTooltip({x:e.clientX,y:e.clientY, before:beforeVal, after:afterVal}) }}
                                      onMouseLeave={()=>setTooltip(null)}
                                    >{String(row[col] ?? '')}</td>
                                  )
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>

                  {/* Schema diff */}
                  <div style={{marginTop:10}}>
                    <h5>Schema changes</h5>
                    <div className="schema-diff">
                      {(() => {
                        const beforeCols = generatedPreview.before[0] ? Object.keys(generatedPreview.before[0]) : []
                        const afterCols = generatedPreview.after[0] ? Object.keys(generatedPreview.after[0]) : []
                        const added = afterCols.filter(c=>!beforeCols.includes(c))
                        const removed = beforeCols.filter(c=>!afterCols.includes(c))
                        return (
                          <div>
                            {added.length>0 && <div style={{color:'#10b981'}}>Added: {added.join(', ')}</div>}
                            {removed.length>0 && <div style={{color:'#ef4444'}}>Removed: {removed.join(', ')}</div>}
                            {added.length===0 && removed.length===0 && <div style={{color:'var(--muted)'}}>No schema changes detected</div>}
                          </div>
                        )
                      })()}
                    </div>
                  </div>

                  <div style={{marginTop:8,display:'flex',gap:8}}>
                    <button className="primary" onClick={() => { acceptGenerated(); }}>Accept generated</button>
                    <button onClick={() => { setRecipeText(prevRecipeText || ''); setGeneratedPreview(null); setSelectedCells({}); addToast('Rejected generated recipe', 'info') }}>Reject</button>
                  </div>
                </div>
              )}

              {/* Tooltip popup */}
              {tooltip && (
                <div className="tooltip-popup" style={{left: tooltip.x + 8, top: tooltip.y + 8}}>
                  <div><strong>Before</strong>: <span style={{fontFamily:'monospace'}}>{tooltip.before}</span></div>
                  <div><strong>After</strong>: <span style={{fontFamily:'monospace'}}>{tooltip.after}</span></div>
                </div>
              )}

              <div style={{marginTop:10}} className="button-row">
                <label style={{display:'inline-flex',alignItems:'center',gap:8}}>
                  <span style={{fontSize:'0.85rem',color:'var(--muted)'}}>Preview rows</span>
                  <select value={previewRows} onChange={(e) => setPreviewRows(Number(e.target.value))}>
                    <option value={6}>6</option>
                    <option value={20}>20</option>
                    <option value={0}>All</option>
                  </select>
                </label>
                <button className="primary" onClick={() => saveRecipe(recipeNameRef.current?.value || `recipe_${Date.now()}`)}>Save</button>
                <button onClick={doPreview} disabled={previewLoading}>Preview</button>
                <button onClick={doApply} disabled={applyLoading}>Apply</button>
                <button
                  onClick={approveSavedRecipe}
                  disabled={!(applyRes && (applyRes.written || applyRes.written === 0)) || !recipeId}
                  style={{ background: '#0ea5a4', color: '#fff', border: 'none', padding: '6px 10px', borderRadius: 4 }}
                >
                  Approve
                </button>
                <label style={{display:'inline-flex',alignItems:'center',gap:8}}>
                  <span>Run as</span>
                  <select defaultValue="csv" ref={runFormatRef}>
                    <option value="csv">CSV</option>
                    <option value="xlsx">XLSX</option>
                  </select>
                </label>
                <button onClick={() => runSavedRecipe(runFormatRef.current?.value || 'csv')} disabled={!recipeId || recipeStatus !== 'approved'}>Run</button>
                <button onClick={() => runAndDownloadSavedRecipe(runFormatRef.current?.value || 'csv')} disabled={!recipeId || recipeStatus !== 'approved'}>{runDownloadLoading ? 'Running…' : 'Run & Download'}</button>
                <button title="Debug: show logs and attempt run+download" onClick={() => debugRunDownload(runFormatRef.current?.value || 'csv')}>Debug Run & Download</button>
                <button onClick={exportToSheets} disabled={!recipeId}>Export to Google Sheets</button>
                <button onClick={() => downloadPreviewCsv()} disabled={!generatedPreview}>Download CSV</button>
              </div>
            </div>
          </div>
        )}
        {showDuplicatesModal && (
          <div className="modal-overlay" onClick={() => setShowDuplicatesModal(false)}>
            <div className="modal-panel card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Duplicate uploads">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div>
                  <h3>Duplicate files</h3>
                  <div style={{color:'var(--muted)'}}>Review duplicate groups and delete unwanted copies.</div>
                </div>
                <div>
                  <button onClick={() => setShowDuplicatesModal(false)}>Close</button>
                </div>
              </div>
              <div style={{marginTop:12}}>
                {duplicateGroups.length === 0 && <div className="empty-state">No duplicates found.</div>}
                {duplicateGroups.map((g, gi) => (
                  <div key={g.hash || gi} style={{borderTop:'1px solid rgba(255,255,255,0.03)', paddingTop:10, marginTop:10}}>
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                      <div><strong>Group</strong> <small style={{color:'var(--muted)'}}>size: {g.size}</small></div>
                      <div style={{display:'flex',gap:8,alignItems:'center'}}>
                        <small style={{color:'var(--muted)'}}>{g.hash}</small>
                        <button onClick={()=>{ if(window.confirm('Keep first file and delete others in this group?')) deleteOthersInGroup(g, g.paths[0]) }}>Keep first / Delete others</button>
                      </div>
                    </div>
                    <div style={{marginTop:8,display:'flex',flexDirection:'column',gap:8}}>
                      {g.paths.map((p) => (
                        <div key={p} style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8}}>
                          <div style={{flex:'1 1 0'}}><small style={{color:'var(--muted)'}}>{p}</small></div>
                          <div style={{display:'flex',gap:8}}>
                            <button onClick={()=>{ navigator.clipboard?.writeText(p); addToast('Copied path to clipboard','info') }}>Copy</button>
                            <button onClick={()=>{ deleteUploadFromModal(p) }}>Delete</button>
                            <button onClick={()=>{ if(window.confirm('Keep this file and delete other duplicates in group?')) deleteOthersInGroup(g, p) }}>Keep</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
        {showDeleteConfirm && (
          <div className="modal-overlay" onClick={() => setShowDeleteConfirm(false)}>
            <div className="modal-panel card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Confirm delete">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div>
                  <h3>Confirm delete</h3>
                  <div style={{color:'var(--muted)'}}>Are you sure you want to delete this run record? This action cannot be undone.</div>
                </div>
                <div>
                  <button onClick={() => setShowDeleteConfirm(false)}>Cancel</button>
                </div>
              </div>
              <div style={{marginTop:12}}>
                <div style={{marginBottom:8}}>
                  <label style={{display:'block', fontSize:'0.95rem'}}>Type "DELETE" to confirm:</label>
                  <input value={deleteConfirmText} onChange={(e) => setDeleteConfirmText(e.target.value)} placeholder="DELETE" />
                </div>
                <div style={{display:'flex', gap:8}}>
                  <button
                    disabled={deleteConfirmText.trim().toUpperCase() !== 'DELETE'}
                    onClick={async () => { await deleteHistory(deleteTarget); setShowDeleteConfirm(false); setDeleteTarget(null); setDeleteConfirmText('') }}
                    style={{background:'#ef4444', color:'#fff', border:'none', padding:'8px 12px'}}
                  >
                    Delete
                  </button>
                  <button onClick={() => { setShowDeleteConfirm(false); setDeleteTarget(null); setDeleteConfirmText('') }}>Cancel</button>
                </div>
              </div>
            </div>
          </div>
        )}

        {showDedupeConfirm && (
          <div className="modal-overlay" onClick={() => setShowDedupeConfirm(false)}>
            <div className="modal-panel card" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Confirm dedupe">
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <div>
                  <h3>Remove duplicate runs</h3>
                  <div style={{color:'var(--muted)'}}>This will remove duplicate run records (keeps the most recent per output). Proceed?</div>
                </div>
                <div>
                  <button onClick={() => setShowDedupeConfirm(false)}>Close</button>
                </div>
              </div>
              <div style={{marginTop:12}}>
                <div style={{marginBottom:8}}>
                  <label style={{display:'block', fontSize:'0.95rem'}}>Type "REMOVE DUPLICATES" to confirm:</label>
                  <input value={dedupeConfirmText} onChange={(e) => setDedupeConfirmText(e.target.value)} placeholder="REMOVE DUPLICATES" />
                </div>
                <div style={{display:'flex', gap:8}}>
                  <button
                    disabled={dedupeConfirmText.trim().toUpperCase() !== 'REMOVE DUPLICATES'}
                    onClick={async () => { await dedupeHistory(); setShowDedupeConfirm(false); setDedupeConfirmText('') }}
                    style={{background:'#ef4444', color:'#fff', border:'none', padding:'8px 12px'}}
                  >
                    Remove duplicates
                  </button>
                  <button onClick={() => setShowDedupeConfirm(false)}>Cancel</button>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
