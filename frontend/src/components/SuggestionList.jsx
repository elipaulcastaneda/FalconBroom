import React, { useMemo, useState } from 'react'

const CATEGORIES = [
  { key: 'fix', label: 'Fixes', badge: 'Fix', hint: 'Changes values in place' },
  { key: 'move', label: 'Move / swap', badge: 'Move values', hint: 'Moves values between columns' },
  { key: 'remove', label: 'Remove', badge: 'Removes data', hint: 'Removes a column, duplicates or values' },
  { key: 'drop_rows', label: 'Drop rows', badge: 'Removes rows', hint: 'Removes whole rows from the output' },
  { key: 'review', label: 'Review', badge: 'Review only', hint: 'Needs your judgement; no automatic step' },
]
const ORDER = Object.fromEntries(CATEGORIES.map((c, i) => [c.key, i]))
const META = Object.fromEntries(CATEGORIES.map((c) => [c.key, c]))
const WHOLE_DATASET = 'Whole dataset'

function categoryOf(s) {
  if (s && META[s.category]) return s.category
  return Array.isArray(s?.json_steps) && s.json_steps.length ? 'fix' : 'review'
}

function columnOf(s) {
  if (s?.column) return s.column
  const stepColumn = s?.json_steps?.[0]?.column
  if (typeof stepColumn === 'string' && stepColumn) return stepColumn
  const quoted = /'([^']+)'/.exec(s?.text || '')
  return quoted ? quoted[1] : WHOLE_DATASET
}

function isReady(s) {
  return Array.isArray(s?.json_steps) && s.json_steps.length > 0
}

function isAdded(s, selectedKeys) {
  return isReady(s) && s.json_steps.every((step) => selectedKeys.has(JSON.stringify(step)))
}

export default function SuggestionList({ suggestions, selectedSteps, onUse, onUndo }) {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')

  const selectedKeys = useMemo(() => new Set((selectedSteps || []).map((step) => JSON.stringify(step))), [selectedSteps])

  const items = useMemo(
    () => (suggestions || []).map((s) => (typeof s === 'string' ? { text: s } : s)).filter((s) => s && s.text),
    [suggestions],
  )

  const counts = useMemo(() => {
    const result = { all: items.length }
    items.forEach((s) => { const key = categoryOf(s); result[key] = (result[key] || 0) + 1 })
    return result
  }, [items])

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const byColumn = new Map()
    items.forEach((s) => {
      const category = categoryOf(s)
      if (filter !== 'all' && category !== filter) return
      const column = columnOf(s)
      if (needle && !(column.toLowerCase().includes(needle) || s.text.toLowerCase().includes(needle))) return
      if (!byColumn.has(column)) byColumn.set(column, [])
      byColumn.get(column).push({ ...s, __category: category })
    })
    const list = [...byColumn.entries()].map(([column, entries]) => ({
      column,
      entries: entries.sort((a, b) => ORDER[a.__category] - ORDER[b.__category] || (b.confidence || 0) - (a.confidence || 0)),
    }))
    // Whole-dataset items first, then columns with the most actionable suggestions.
    return list.sort((a, b) => {
      if (a.column === WHOLE_DATASET) return -1
      if (b.column === WHOLE_DATASET) return 1
      const ready = (g) => g.entries.filter(isReady).length
      return ready(b) - ready(a) || a.column.localeCompare(b.column)
    })
  }, [items, filter, query])

  if (!items.length) return null

  const readyCount = items.filter(isReady).length
  const tabs = [{ key: 'all', label: 'All' }, ...CATEGORIES.filter((c) => counts[c.key])]

  return (
    <div className="sg-root">
      <div className="sg-header">
        <h4>Suggestions</h4>
        <span className="sg-summary">
          {items.length} total · {readyCount} can be applied · {items.length - readyCount} need review
        </span>
      </div>

      <div className="sg-toolbar">
        <div className="sg-tabs" role="tablist" aria-label="Filter suggestions">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={filter === tab.key}
              className={`sg-tab${filter === tab.key ? ' is-active' : ''}`}
              onClick={() => setFilter(tab.key)}
            >
              {tab.label} <span className="sg-count">{counts[tab.key] || 0}</span>
            </button>
          ))}
        </div>
        <input
          className="sg-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by column or text"
          aria-label="Filter suggestions by column or text"
        />
      </div>

      {groups.length === 0 && <div className="sg-empty">No suggestions match this filter.</div>}

      {groups.map((group, index) => {
        const fixes = group.entries.filter((s) => s.__category === 'fix' && !isAdded(s, selectedKeys))
        return (
          <details key={group.column} className="sg-group" open={Boolean(query) || filter !== 'all' || index < 3}>
            <summary className="sg-group-head">
              <span className="sg-column">{group.column}</span>
              <span className="sg-count">{group.entries.length}</span>
              {fixes.length > 1 && (
                <button
                  type="button"
                  className="sg-add-all secondary"
                  onClick={(e) => { e.preventDefault(); fixes.forEach((s) => onUse(s)) }}
                >
                  Add {fixes.length} fixes
                </button>
              )}
            </summary>
            <ul className="sg-items">
              {group.entries.map((s, i) => {
                const meta = META[s.__category]
                const added = isAdded(s, selectedKeys)
                return (
                  <li key={`${group.column}-${i}`} className={`sg-item sg-${s.__category}${added ? ' is-added' : ''}`}>
                    <span className={`sg-badge sg-badge-${s.__category}`} title={meta.hint}>{meta.badge}</span>
                    <span className="sg-text">{s.text}</span>
                    {isReady(s) ? (
                      added ? (
                        <>
                          <span className="sg-note">Added</span>
                          <button
                            type="button"
                            className="secondary"
                            title="Remove this suggestion from the instruction box and the selected steps"
                            onClick={() => onUndo && onUndo(s)}
                          >
                            Undo
                          </button>
                        </>
                      ) : (
                        <button type="button" onClick={() => onUse(s)}>Use</button>
                      )
                    ) : (
                      <span className="sg-note">Review</span>
                    )}
                  </li>
                )
              })}
            </ul>
          </details>
        )
      })}
    </div>
  )
}
