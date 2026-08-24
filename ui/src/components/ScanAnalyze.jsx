import { useState, useCallback } from 'react'
import { API_BASE } from '../config'

export default function ScanAnalyze({ getSourcePayload }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [section, setSection] = useState('all')

  // Create indexes state
  const [uri, setUri] = useState('')
  const [selected, setSelected] = useState(new Set())
  const [creating, setCreating] = useState(false)
  const [createResults, setCreateResults] = useState(null)
  const [showCreate, setShowCreate] = useState(false)

  // Multi-tenant DB selection
  const [liveDbs, setLiveDbs] = useState([])
  const [selectedDbs, setSelectedDbs] = useState(new Set())
  const [loadingDbs, setLoadingDbs] = useState(false)
  const [dbMode, setDbMode] = useState('detected') // 'detected' or 'pick'

  const handleScan = async () => {
    setLoading(true); setError(''); setData(null); setCreateResults(null); setSelected(new Set())
    try {
      const res = await fetch(`${API_BASE}/api/scan`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(getSourcePayload()),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Scan failed')
      setData(json)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  const fetchDatabases = useCallback(async () => {
    if (!uri.trim()) { setError('Enter MongoDB URI first'); return }
    setLoadingDbs(true)
    try {
      const res = await fetch(`${API_BASE}/api/list-databases`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uri: uri.trim() }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error)
      setLiveDbs(json.databases || [])
      // Auto-select detected databases
      const detected = new Set(data?.database_names || [])
      setSelectedDbs(new Set(json.databases.filter(d => detected.has(d))))
      setDbMode('pick')
    } catch (e) { setError(e.message) }
    finally { setLoadingDbs(false) }
  }, [uri, data])

  const summary = data?.summary || {}
  const indexes = data?.indexes || []
  const suggestions = data?.suggestions || []
  const detectedDbs = data?.database_names || []

  // Build flat list for create
  const allItems = []
  indexes.forEach((idx, i) => allItems.push({ ...idx, _id: `idx-${i}`, _type: 'defined', _db: idx.database }))
  suggestions.forEach((s, i) => allItems.push({ ...s, _id: `sug-${i}`, _type: 'suggested', _db: s.database }))

  const q = filter.toLowerCase()
  const filteredIndexes = q ? indexes.filter(idx =>
    idx.collection?.toLowerCase().includes(q) || idx.database?.toLowerCase()?.includes(q) ||
    JSON.stringify(idx.fields).toLowerCase().includes(q) || idx.source?.file?.toLowerCase().includes(q)
  ) : indexes
  const filteredSuggestions = q ? suggestions.filter(s =>
    s.collection?.toLowerCase().includes(q) || s.database?.toLowerCase()?.includes(q) ||
    JSON.stringify(s.fields).toLowerCase().includes(q) || s.rationale?.toLowerCase().includes(q)
  ) : suggestions

  const showIndexes = section === 'all' || section === 'indexes'
  const showSuggestions = section === 'all' || section === 'suggestions'

  const toggleSelect = (id) => setSelected(prev => {
    const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next
  })
  const selectAll = () => {
    if (selected.size === allItems.length) setSelected(new Set())
    else setSelected(new Set(allItems.map(i => i._id)))
  }
  const toggleDb = (db) => setSelectedDbs(prev => {
    const next = new Set(prev); next.has(db) ? next.delete(db) : next.add(db); return next
  })

  const handleCreate = async () => {
    if (!uri.trim()) { setError('Enter MongoDB URI'); return }
    if (selected.size === 0) { setError('Select at least one index'); return }

    // Determine target databases
    let targetDbs
    if (dbMode === 'pick' && selectedDbs.size > 0) {
      targetDbs = [...selectedDbs]
    } else {
      // Use detected databases from code
      const dbSet = new Set()
      for (const item of allItems) {
        if (selected.has(item._id) && item._db) dbSet.add(item._db)
      }
      if (dbSet.size === 0) { setError('No database name detected. Use "Pick from Server" to select target databases.'); return }
      targetDbs = [...dbSet]
    }

    // Group selected indexes by their detected DB (for correct mapping)
    // But if using multi-tenant mode, apply all selected indexes to all selected DBs
    const selectedIndexes = allItems.filter(i => selected.has(i._id)).map(i => ({
      collection: i.collection,
      fields: i.fields,
      unique: i.options?.unique || false,
      sparse: i.options?.sparse || false,
    }))

    setCreating(true); setError(''); setCreateResults(null)
    try {
      const res = await fetch(`${API_BASE}/api/create-indexes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uri: uri.trim(), db_names: targetDbs, indexes: selectedIndexes }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Create failed')
      setCreateResults(json)
    } catch (e) { setError(e.message) }
    finally { setCreating(false) }
  }

  const cr = createResults?.summary || {}

  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 className="section-title" style={{ margin: 0 }}>Scan & Analyze</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          {data && <button className={`btn ${showCreate ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => setShowCreate(!showCreate)} style={{ fontSize: '0.85rem' }}>
            🚀 {showCreate ? 'Hide Create' : 'Create Indexes'}</button>}
          <button className="btn btn-primary" onClick={handleScan} disabled={loading}>
            {loading ? '⏳ Scanning...' : '🔍 Scan Project'}</button>
        </div>
      </div>

      {error && <div className="error-msg">{error}</div>}
      {loading && <div className="loading"><div className="spinner" /><p>Scanning project...</p></div>}

      {data && !loading && (
        <>
          <div className="stats-grid">
            <div className="stat-card"><div className="stat-value">{summary.total_indexes || 0}</div><div className="stat-label">Indexes Found</div></div>
            <div className="stat-card"><div className="stat-value">{summary.total_suggestions || 0}</div><div className="stat-label">Suggestions</div></div>
            <div className="stat-card"><div className="stat-value">{summary.files_scanned || 0}</div><div className="stat-label">Files Scanned</div></div>
            <div className="stat-card"><div className="stat-value">{detectedDbs.length}</div><div className="stat-label">Databases</div></div>
          </div>

          {detectedDbs.length > 0 && (
            <div style={{ marginBottom: 16, padding: '10px 14px', background: 'var(--surface-hover)', borderRadius: 'var(--radius)', fontSize: '0.85rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Detected Databases: </span>
              {detectedDbs.map(db => <span key={db} className="field-tag">{db}</span>)}
              {data.config_repo_databases?.length > 0 && (
                <>
                  <span style={{ color: 'var(--text-muted)', marginLeft: 12 }}>From config repo: </span>
                  {data.config_repo_databases.map(db => (
                    <span key={`cfg-${db}`} className="field-tag" style={{ background: 'rgba(116,185,255,0.15)', color: 'var(--info)' }}>{db}</span>
                  ))}
                </>
              )}
            </div>
          )}

          {/* Create panel */}
          {showCreate && (
            <div style={{ background: 'var(--surface-hover)', borderRadius: 'var(--radius)', padding: 16, marginBottom: 16, border: '1px solid var(--primary)' }}>
              <h3 style={{ fontSize: '0.95rem', marginBottom: 12, color: 'var(--text)' }}>🚀 Create Indexes on MongoDB</h3>
              <div className="form-row" style={{ marginBottom: 12 }}>
                <div className="form-group" style={{ flex: 2 }}>
                  <label>MongoDB URI</label>
                  <input type="password" value={uri} onChange={(e) => setUri(e.target.value)}
                    placeholder="mongodb://user:pass@host:27017" style={{ fontFamily: 'JetBrains Mono, monospace' }} />
                </div>
              </div>

              {/* Database selection */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                  <label style={{ fontSize: '0.85rem', color: 'var(--text-muted)', fontWeight: 500 }}>Target Databases:</label>
                  <div className="tab-nav" style={{ marginBottom: 0, flex: 'none' }}>
                    <button className={`tab-btn ${dbMode === 'detected' ? 'active' : ''}`}
                      onClick={() => setDbMode('detected')} style={{ fontSize: '0.75rem', padding: '4px 12px' }}>
                      Auto (from code)
                    </button>
                    <button className={`tab-btn ${dbMode === 'pick' ? 'active' : ''}`}
                      onClick={() => { setDbMode('pick'); if (liveDbs.length === 0) fetchDatabases() }}
                      style={{ fontSize: '0.75rem', padding: '4px 12px' }}>
                      Pick from Server
                    </button>
                  </div>
                  {dbMode === 'pick' && (
                    <button className="btn btn-secondary" onClick={fetchDatabases} disabled={loadingDbs}
                      style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
                      {loadingDbs ? '⏳' : '🔄'} Refresh
                    </button>
                  )}
                </div>

                {dbMode === 'detected' && detectedDbs.length > 0 && (
                  <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
                    Will create on: {detectedDbs.map(db => <span key={db} className="field-tag">{db}</span>)}
                  </div>
                )}
                {dbMode === 'detected' && detectedDbs.length === 0 && (
                  <div style={{ fontSize: '0.85rem', color: 'var(--warning)' }}>
                    ⚠️ No databases detected from code. Use "Pick from Server" to select target databases.
                  </div>
                )}

                {dbMode === 'pick' && liveDbs.length > 0 && (
                  <div style={{ maxHeight: 150, overflowY: 'auto', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {liveDbs.map(db => (
                      <label key={db} style={{
                        display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer',
                        padding: '3px 10px', borderRadius: 4, fontSize: '0.8rem',
                        background: selectedDbs.has(db) ? 'rgba(108,92,231,0.2)' : 'var(--bg)',
                        border: `1px solid ${selectedDbs.has(db) ? 'var(--primary)' : 'var(--border)'}`,
                        color: selectedDbs.has(db) ? 'var(--primary)' : 'var(--text-muted)',
                      }}>
                        <input type="checkbox" checked={selectedDbs.has(db)} onChange={() => toggleDb(db)}
                          style={{ accentColor: 'var(--primary)', width: 12, height: 12 }} />
                        {db}
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <button className="btn btn-secondary" onClick={selectAll} style={{ fontSize: '0.8rem', padding: '6px 14px' }}>
                    {selected.size === allItems.length ? '☐ Deselect All' : '☑ Select All'}</button>
                  <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    {selected.size} indexes × {dbMode === 'pick' ? selectedDbs.size : detectedDbs.length} database(s)
                  </span>
                </div>
                <button className="btn btn-primary" onClick={handleCreate} disabled={creating || selected.size === 0}>
                  {creating ? '⏳ Creating...' : `🚀 Create`}</button>
              </div>

              {createResults && (
                <div style={{ marginTop: 12, padding: '10px 14px', background: 'var(--bg)', borderRadius: 'var(--radius)', fontSize: '0.85rem' }}>
                  <span style={{ color: 'var(--success)' }}>✅ {cr.created || 0} created</span>
                  {cr.already_existed > 0 && <span style={{ color: 'var(--info)', marginLeft: 12 }}>ℹ️ {cr.already_existed} existed</span>}
                  {cr.errors > 0 && <span style={{ color: 'var(--danger)', marginLeft: 12 }}>❌ {cr.errors} errors</span>}
                  <span style={{ color: 'var(--text-muted)', marginLeft: 12 }}>across {cr.databases || 0} database(s)</span>
                </div>
              )}
            </div>
          )}

          {/* Filter + section toggle */}
          <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center' }}>
            <input type="text" className="search-input" style={{ marginBottom: 0, flex: 1 }}
              placeholder="Filter by collection, field, database..." value={filter} onChange={(e) => setFilter(e.target.value)} />
            <div className="tab-nav" style={{ marginBottom: 0, flex: 'none' }}>
              {[{ id: 'all', label: 'All' }, { id: 'indexes', label: `Indexes (${filteredIndexes.length})` },
                { id: 'suggestions', label: `Suggestions (${filteredSuggestions.length})` }].map(s => (
                <button key={s.id} className={`tab-btn ${section === s.id ? 'active' : ''}`}
                  onClick={() => setSection(s.id)} style={{ fontSize: '0.8rem', padding: '6px 12px' }}>{s.label}</button>
              ))}
            </div>
          </div>

          {showIndexes && filteredIndexes.length > 0 && (
            <>
              <h3 className="section-title">📋 Defined Indexes ({filteredIndexes.length})</h3>
              {filteredIndexes.map((idx, i) => {
                const itemId = `idx-${indexes.indexOf(idx)}`
                return (
                  <div className="index-card" key={i} style={{ borderLeft: '3px solid var(--info)', cursor: showCreate ? 'pointer' : 'default', opacity: showCreate && !selected.has(itemId) ? 0.6 : 1 }}
                    onClick={showCreate ? () => toggleSelect(itemId) : undefined}>
                    <div className="index-card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {showCreate && <input type="checkbox" checked={selected.has(itemId)} onChange={() => toggleSelect(itemId)}
                          onClick={e => e.stopPropagation()} style={{ accentColor: 'var(--primary)', width: 16, height: 16 }} />}
                        <span className="collection-name">
                          <span style={{ color: 'var(--text-muted)' }}>{idx.database || '?'}.</span>{idx.collection}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <span className="badge badge-type">{idx.options?.index_type || 'standard'}</span>
                        {idx.options?.unique && <span className="badge badge-high">unique</span>}
                      </div>
                    </div>
                    <div className="fields-list">
                      {Object.entries(idx.fields || {}).map(([f, d]) => <span className="field-tag" key={f}>{f}: {d}</span>)}
                    </div>
                    {idx.source?.file && <div className="source-info">📄 {idx.source.file}:{idx.source.line}{idx.source.annotation && ` — ${idx.source.annotation}`}</div>}
                  </div>
                )
              })}
            </>
          )}

          {showSuggestions && filteredSuggestions.length > 0 && (
            <>
              <h3 className="section-title" style={{ marginTop: showIndexes && filteredIndexes.length > 0 ? 24 : 0 }}>
                💡 Suggested Indexes ({filteredSuggestions.length})</h3>
              {filteredSuggestions.map((s, i) => {
                const itemId = `sug-${suggestions.indexOf(s)}`
                return (
                  <div className="index-card" key={i} style={{ borderLeft: '3px solid var(--warning)', cursor: showCreate ? 'pointer' : 'default', opacity: showCreate && !selected.has(itemId) ? 0.6 : 1 }}
                    onClick={showCreate ? () => toggleSelect(itemId) : undefined}>
                    <div className="index-card-header">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        {showCreate && <input type="checkbox" checked={selected.has(itemId)} onChange={() => toggleSelect(itemId)}
                          onClick={e => e.stopPropagation()} style={{ accentColor: 'var(--primary)', width: 16, height: 16 }} />}
                        <span className="collection-name">
                          <span style={{ color: 'var(--text-muted)' }}>{s.database || '?'}.</span>{s.collection}
                        </span>
                      </div>
                      <span className={`badge badge-${s.priority}`}>{s.priority}</span>
                    </div>
                    <div className="fields-list">
                      {Object.entries(s.fields || {}).map(([f, d]) => <span className="field-tag" key={f}>{f}: {d}</span>)}
                    </div>
                    {s.rationale && <div className="rationale">💬 {s.rationale}</div>}
                    {s.sample_locations?.length > 0 && <div className="source-info">📄 {s.sample_locations.join(', ')}</div>}
                  </div>
                )
              })}
            </>
          )}

          {filteredIndexes.length === 0 && filteredSuggestions.length === 0 && (
            <div className="empty-state"><div className="icon">{filter ? '🔍' : '📭'}</div>
              <p>{filter ? `No results matching "${filter}"` : 'No indexes or suggestions found.'}</p></div>
          )}
        </>
      )}

      {!data && !loading && !error && (
        <div className="empty-state"><div className="icon">🗂️</div><p>Enter a project path and click Scan to discover indexes and get suggestions.</p></div>
      )}
    </div>
  )
}
