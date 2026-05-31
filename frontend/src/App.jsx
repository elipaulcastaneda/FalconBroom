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
  const [inspectOffset, setInspectOffset] = useState(0)
  const [preview, setPreview] = useState(null)
  const [applyRes, setApplyRes] = useState(null)
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
      alert(`Upload failed: ${err.message || err}`)
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
      const res = await fetch(`${BACKEND}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recipe),
      })
      const j = await res.json()
      setPreview(j.preview)
    } catch (e) {
      alert("Invalid recipe JSON: " + e.message)
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
      alert("Invalid recipe JSON: " + e.message)
    }
  }

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
            <div className="button-row">
              <button className="primary" onClick={doPreview}>Preview</button>
              <button onClick={doApply}>Apply</button>
            </div>
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
