import React, {useMemo, useRef, useState} from "react"

const BACKEND = "http://127.0.0.1:3008"
const NAV_ITEMS = [
  { section: "Start", id: "source", label: "Source", detail: "Profile and prompt to recipe", icon: "⟡" },
  { section: "Build", id: "joins", label: "Joins", detail: "Match and merge hints", icon: "⧉" },
  { section: "Review", id: "preview", label: "Preview", detail: "Compare before and after", icon: "↔" },
]

const NAV_SECTIONS = ["Start", "Build", "Review"]

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
  const runFormatRef = useRef(null)
  const INSPECT_PAGE_SIZE = 100
  const [path, setPath] = useState("")
  const [instruction, setInstruction] = useState("")
  const [leftPath, setLeftPath] = useState("")
  const [rightPath, setRightPath] = useState("")
  const [profile, setProfile] = useState(null)
  const [suggest, setSuggest] = useState(null)
  const [cleaningSuggestions, setCleaningSuggestions] = useState(null)
  const [joinSuggestions, setJoinSuggestions] = useState(null)
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
    const res = await fetch(`${BACKEND}/inspect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: nextPath, offset: nextOffset, limit: INSPECT_PAGE_SIZE }),
    })
    const j = await res.json()
    setSourceInspection(j.inspection)
    setDiagnostics(j.inspection?.diagnostics || null)
    setInspectOffset(nextOffset)
  }

  async function doProfileForPath(nextPath) {
    const profileRes = await fetch(`${BACKEND}/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: nextPath }),
    })
    const profilePayload = await profileRes.json()
    setProfile(profilePayload.profile)
    await doInspectSource(nextPath, 0)
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

    const historyItem = {
      name: payload.name || file.name,
      path: payload.path,
      size: payload.size || file.size,
      uploadedAt: new Date().toISOString(),
    }
    const deduped = [historyItem, ...uploadHistory.filter((item) => item.path !== historyItem.path)]
    persistUploadHistory(deduped)

    await doProfileForPath(payload.path)
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
      body: JSON.stringify({ path }),
    })
    const j = await res.json()
    setCleaningSuggestions(j.suggestions)
  }

  async function doRecipeFromText() {
    const res = await fetch(`${BACKEND}/recipe-from-text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instruction, source_path: path, output_path: "output_from_text.csv" }),
    })
    const j = await res.json()
    setRecipeFromText(j)
    setRecipeText(JSON.stringify(j.recipe, null, 2))
  }

  async function doJoinSuggestions() {
    const res = await fetch(`${BACKEND}/join-suggestions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ left_path: leftPath, right_path: rightPath }),
    })
    const j = await res.json()
    setJoinSuggestions(j.joins)
  }

  async function doPreview() {
    try {
      const recipe = JSON.parse(recipeText)
      const url = `${BACKEND}/preview${recipeId ? `?recipe_id=${encodeURIComponent(recipeId)}` : ""}`
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recipe),
      })
      const j = await res.json()
      setPreview(j.preview)
      if (j.schema_warnings) {
        // lightweight notification
        setApplyRes((prev) => ({ ...(prev || {}), schema_warnings: j.schema_warnings }))
        addToast(`Schema warnings: ${JSON.stringify(j.schema_warnings)}`, "warn")
      }
    } catch (e) {
      addToast("Invalid recipe JSON: " + e.message, "error")
    }
  }

  async function doApply() {
    try {
      const recipe = JSON.parse(recipeText)
      const res = await fetch(`${BACKEND}/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recipe),
      })
      const j = await res.json()
      setApplyRes(j.result)
    } catch (e) {
      addToast("Invalid recipe JSON: " + e.message, "error")
    }
  }

  async function saveRecipe(name) {
    try {
      const recipe = JSON.parse(recipeText)
      const res = await fetch(`${BACKEND}/recipes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, recipe }),
      })
      const j = await res.json()
      setRecipeId(j.id)
      // fetch saved record to get status
      const saved = await fetch(`${BACKEND}/recipes/${encodeURIComponent(j.id)}`)
      const sdata = await saved.json()
      setRecipeStatus(sdata.status || "draft")
      addToast(`Saved recipe ${name}`, "success")
    } catch (e) {
      addToast("Failed to save recipe: " + e.message, "error")
    }
  }

  async function approveSavedRecipe() {
    if (!recipeId) {
      addToast("No saved recipe to approve", "warn")
      return
    }
    try {
      const res = await fetch(`${BACKEND}/recipes/${encodeURIComponent(recipeId)}/approve`, { method: "POST" })
      const j = await res.json()
      setRecipeStatus(j.status)
      addToast(`Recipe ${recipeId} approved`, "success")
    } catch (e) {
      addToast("Failed to approve: " + e.message, "error")
    }
  }

  async function runSavedRecipe(format = "csv") {
    if (!recipeId) {
      addToast("Save recipe before running", "warn")
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

  // poll history while any runs are running
  React.useEffect(() => {
    let timer = null
    const hasRunning = historyList.some((h) => h.status === "running")
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

            <label>Plain-English instruction</label>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              rows={5}
              placeholder="e.g. fill missing age values, lowercase email, and remove duplicate customers"
            />

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

          <Card eyebrow="Uploads" title="Saved uploads" subtitle="Uploaded files are persisted and reusable across app reloads.">
            {uploadHistory.length ? (
              <div className="upload-history">
                {uploadHistory.map((item) => (
                  <button
                    key={item.path}
                    className="upload-item"
                    onClick={() => setPath(item.path)}
                    title={item.path}
                  >
                    <span className="upload-item-name">{item.name}</span>
                    <small>{item.path}</small>
                  </button>
                ))}
              </div>
            ) : (
              <div className="empty-state">No uploads yet. Use Upload CSV to add one.</div>
            )}
          </Card>

          <Card
            eyebrow="Custom revisions"
            title="Recipe editor"
            subtitle="Refine transformation steps in-place, then preview or apply without leaving the Source tab."
          >
            <textarea value={recipeText} onChange={(e) => setRecipeText(e.target.value)} rows={14} className="editor" />
            <div className="editor-meta">
              <label>Recipe name</label>
              <input placeholder="My recipe name" ref={recipeNameRef} />
              <div className="recipe-meta-line">
                <strong>Saved ID:</strong> {recipeId || "(not saved)"} &nbsp; <strong>Status:</strong> {recipeStatus || "(draft)"}
              </div>
            </div>
            <div className="button-row">
              <button className="primary" onClick={doPreview}>Preview</button>
              <button onClick={doApply}>Apply</button>
              <button onClick={() => saveRecipe(recipeNameRef.current?.value || `recipe_${Date.now()}`)}>Save</button>
              <button onClick={approveSavedRecipe} disabled={!recipeId}>Approve</button>
              <label style={{display:'inline-flex',alignItems:'center',gap:8}}>
                <span>Run as</span>
                <select defaultValue="csv" ref={runFormatRef}>
                  <option value="csv">CSV</option>
                  <option value="xlsx">XLSX</option>
                </select>
              </label>
              <button onClick={() => runSavedRecipe(runFormatRef.current?.value || 'csv')} disabled={!recipeId}>Run</button>
              <button onClick={exportToSheets} disabled={!recipeId}>Export to Google Sheets</button>
            </div>
          </Card>

          <Card eyebrow="Run history" title="Runs and lineage" subtitle="View previous runs, outputs, and create rollbacks.">
            <div className="button-row">
              <button onClick={fetchHistory}>Refresh history</button>
            </div>
            {historyList.length ? (
              <div className="history-list">
                {historyList.map((r) => (
                  <div key={r.id} className="history-item">
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
          <Card eyebrow="Joins" title="Join hints" subtitle="Find the most likely keys before blending datasets.">
            <div className="inline-grid">
              <div>
                <label>Left CSV path</label>
                <input value={leftPath} onChange={(e) => setLeftPath(e.target.value)} placeholder="Left CSV path" />
              </div>
              <div>
                <label>Right CSV path</label>
                <input value={rightPath} onChange={(e) => setRightPath(e.target.value)} placeholder="Right CSV path" />
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
          </div>
        </header>

        {renderTab()}
      </main>
    </div>
  )
}
