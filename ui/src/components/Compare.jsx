import { useState } from 'react'
import { API_BASE } from '../config'

export default function Compare({ getSourcePayload }) {
  const [uri, setUri] = useState('')
  const [extraDbs, setExtraDbs] = useState('')
  const [dbType, setDbType] = useState('auto')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeSection, setActiveSection] = useState('missing')
  const [creating, setCreating] = useState({}) // track per-index creation state
  const [createResults, setCreateResults] = useState({}) // per-index results

  const detectDbType = (inputUri) => {
    if (inputUri.startsWith('mongodb://') || inputUri.startsWith('mongodb+srv://')) return 'mongodb'
    if (inputUri.startsWith('postgresql://') || inputUri.startsWith('postgres://') || inputUri.startsWith('jdbc:postgresql://')) return 'postgresql'
    return 'unknown'
  }

  const detectedType = detectDbType(uri)
  const effectiveDbType = dbType === 'auto' ? detectedType : dbType

  const handleCompare = async () => {
    if (!uri.trim()) { setError('Please enter a database URI'); return }
    setLoading(true); setError(''); setData(null); setCreating({}); setCreateResults({})
    try {
      const body = { ...getSourcePayload(), uri: uri.trim() }
      if (extraDbs.trim()) body.db_names = extraDbs.trim()
      if (dbType !== 'auto') body.db_type = dbType
      const res = await fetch(`${API_BASE}/api/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Compare failed')
      setData(json)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  const createIndex = async (item, key) => {
    const dbName = item.database || data?.databases_checked?.[0] || ''
    if (!dbName) { setCreateResults(p => ({ ...p, [key]: { status: 'error', message: 'No database name available' } })); return }
    if (!uri.trim()) { setCreateResults(p => ({ ...p, [key]: { status: 'error', message: 'No database URI' } })); return }

    setCreating(p => ({ ...p, [key]: true }))
    try {
      const res = await fetch(`${API_BASE}/api/create-indexes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          uri: uri.trim(),
          db_name: dbName,
          indexes: [{ collection: item.collection, fields: item.fields, unique: item.unique || false, sparse: item.sparse || false }],
        }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Create failed')
      const r = json.results?.[0] || { status: 'error', message: 'Unknown' }
      setCreateResults(p => ({ ...p, [key]: r }))
    } catch (e) {
      setCreateResults(p => ({ ...p, [key]: { status: 'error', message: e.message } }))
    } finally {
      setCreating(p => ({ ...p, [key]: false }))
    }
  }

  const createAllMissing = async (items) => {
    for (let i = 0; i < items.length; i++) {
      const key = `missing-${i}`
      if (createResults[key]?.status === 'created') continue
      await createIndex(items[i], key)
    }
  }

  const summary = data?.summary || {}
  const missingItems = [...(data?.suggestions_missing || []), ...(data?.code_indexes_missing || [])]
  const sections = [
    { id: 'missing', label: `🚨 Missing (${missingItems.length})` },
    { id: 'existing', label: `✅ Covered (${summary.existing_suggestions || 0})` },
    { id: 'esr', label: `📐 ESR (${summary.esr_violations || 0} issues)` },
    { id: 'live', label: `🗄️ Live (${data?.live_indexes_count || 0})` },
    { id: 'code', label: `📝 Code` },
  ]

  return (
    <div className="panel">
      <h2 className="section-title">Compare Against Live Database</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: 16 }}>
        Connect to your database instance to see which suggested indexes are missing vs already created.
      </p>

      <div className="form-row">
        <div className="form-group" style={{ flex: 2 }}>
          <label htmlFor="db-uri">Database URI</label>
          <input id="db-uri" type="password" value={uri} onChange={(e) => setUri(e.target.value)}
            placeholder="mongodb://user:pass@host:27017 or postgresql://user:pass@host:5432/db" style={{ fontFamily: 'JetBrains Mono, monospace' }} />
          {uri && detectedType !== 'unknown' && (
            <span style={{ fontSize: '0.75rem', color: 'var(--info)', marginTop: 4, display: 'block' }}>
              Detected: {detectedType === 'mongodb' ? '🍃 MongoDB' : '🐘 PostgreSQL'}
            </span>
          )}
        </div>
        <div className="form-group">
          <label htmlFor="db-type-select">Database Type</label>
          <select id="db-type-select" value={dbType} onChange={(e) => setDbType(e.target.value)}>
            <option value="auto">Auto-detect from URI</option>
            <option value="mongodb">MongoDB</option>
            <option value="postgresql">PostgreSQL</option>
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="compare-db">
            {effectiveDbType === 'postgresql' ? 'Extra Schemas (comma-separated, optional)' : 'Extra Databases (comma-separated, optional)'}
          </label>
          <input id="compare-db" type="text" value={extraDbs} onChange={(e) => setExtraDbs(e.target.value)}
            placeholder={effectiveDbType === 'postgresql' ? 'public, myschema' : 'auto-detected from code'} />
        </div>
        <button className="btn btn-primary" onClick={handleCompare} disabled={loading} style={{ whiteSpace: 'nowrap' }}>
          {loading ? '⏳ Comparing...' : '⚡ Compare'}
        </button>
      </div>

      {error && <div className="error-msg">{error}</div>}
      {loading && <div className="loading"><div className="spinner" /><p>Connecting to database and comparing indexes...</p></div>}

      {data && !loading && (
        <>
          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--danger)' }}>{missingItems.length}</div>
              <div className="stat-label">Missing</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--success)' }}>{summary.existing_suggestions || 0}</div>
              <div className="stat-label">Covered</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--info)' }}>{data.live_indexes_count || 0}</div>
              <div className="stat-label">Live Indexes</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: summary.esr_violations ? 'var(--warning)' : 'var(--success)' }}>{summary.esr_violations || 0}</div>
              <div className="stat-label">ESR Violations</div>
            </div>
          </div>

          {data.databases_checked?.length > 0 && (
            <div style={{ marginBottom: 16, padding: '10px 14px', background: 'var(--surface-hover)', borderRadius: 'var(--radius)', fontSize: '0.85rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Databases checked ({data.databases_checked.length}): </span>
              {data.databases_checked.length <= 10
                ? data.databases_checked.map((db) => (<span key={db} className="field-tag">{db}</span>))
                : <>
                    {data.databases_checked.slice(0, 5).map((db) => (<span key={db} className="field-tag">{db}</span>))}
                    <span style={{ color: 'var(--text-muted)' }}> ...and {data.databases_checked.length - 5} more</span>
                  </>
              }
              {data.tenant_patterns && Object.keys(data.tenant_patterns).length > 0 && (
                <div style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  Tenant matching: {Object.entries(data.tenant_patterns).map(([pattern, dbs]) => (
                    <span key={pattern} style={{ marginRight: 12 }}>
                      <span style={{ color: 'var(--info)' }}>{pattern}</span> → {dbs.length} db(s)
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="tab-nav" style={{ marginBottom: 16 }}>
            {sections.map((s) => (
              <button key={s.id} className={`tab-btn ${activeSection === s.id ? 'active' : ''}`}
                onClick={() => setActiveSection(s.id)} style={{ fontSize: '0.8rem' }}>{s.label}</button>
            ))}
          </div>

          {activeSection === 'missing' && (
            <MissingSection
              items={missingItems}
              creating={creating}
              createResults={createResults}
              onCreateOne={createIndex}
              onCreateAll={() => createAllMissing(missingItems)}
              databases={data.databases_checked || []}
            />
          )}

          {activeSection === 'existing' && (
            <IndexList items={data.suggestions_existing || []} emptyIcon="📭" emptyText="No suggestions matched existing indexes."
              statusColor="var(--success)" statusLabel="EXISTS" showPriority showRationale />
          )}

          {activeSection === 'esr' && <EsrAnalysis items={data?.esr_analysis || []} />}
          {activeSection === 'live' && <LiveIndexList items={data.live_indexes || []} />}

          {activeSection === 'code' && (
            <>
              {(data.code_indexes_existing?.length > 0) && (
                <>
                  <h3 className="section-title" style={{ color: 'var(--success)' }}>Exists in DB ({data.code_indexes_existing.length})</h3>
                  <IndexList items={data.code_indexes_existing} statusColor="var(--success)" statusLabel="EXISTS" />
                </>
              )}
              {(!data.code_indexes_missing?.length && !data.code_indexes_existing?.length) && (
                <div className="empty-state"><div className="icon">📭</div><p>No code-defined indexes found.</p></div>
              )}
            </>
          )}
        </>
      )}

      {!data && !loading && !error && (
        <div className="empty-state"><div className="icon">⚡</div><p>Enter your database URI to compare code suggestions against live indexes.</p></div>
      )}
    </div>
  )
}


function MissingSection({ items, creating, createResults, onCreateOne, onCreateAll, databases }) {
  if (!items.length) {
    return <div className="empty-state"><div className="icon">🎉</div><p>All indexes already exist in your database!</p></div>
  }

  const allCreated = items.every((_, i) => createResults[`missing-${i}`]?.status === 'created')
  const createdCount = items.filter((_, i) => createResults[`missing-${i}`]?.status === 'created').length
  const anyCreating = Object.values(creating).some(Boolean)

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          {createdCount > 0 && `${createdCount}/${items.length} created`}
        </span>
        {!allCreated && (
          <button className="btn btn-primary" onClick={onCreateAll} disabled={anyCreating}
            style={{ fontSize: '0.85rem', padding: '8px 16px' }}>
            {anyCreating ? '⏳ Creating...' : `🚀 Create All Missing (${items.length - createdCount})`}
          </button>
        )}
      </div>

      {items.map((item, i) => {
        const key = `missing-${i}`
        const result = createResults[key]
        const isCreating = creating[key]
        const dbName = item.database || databases[0] || '?'

        return (
          <div className="index-card" key={key} style={{
            borderLeft: `3px solid ${result?.status === 'created' ? 'var(--success)' : 'var(--danger)'}`,
            opacity: result?.status === 'created' ? 0.7 : 1,
          }}>
            <div className="index-card-header">
              <span className="collection-name">
                <span style={{ color: 'var(--text-muted)' }}>{dbName}.</span>
                {item.collection}
              </span>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {result?.status === 'created' ? (
                  <span className="badge" style={{ background: 'rgba(0,184,148,0.2)', color: 'var(--success)' }}>✅ CREATED</span>
                ) : result?.status === 'exists' ? (
                  <span className="badge" style={{ background: 'rgba(116,185,255,0.2)', color: 'var(--info)' }}>ℹ️ EXISTS</span>
                ) : result?.status === 'error' ? (
                  <span className="badge badge-high" title={result.message}>❌ ERROR</span>
                ) : (
                  <>
                    <span className="badge" style={{ background: 'rgba(225,112,85,0.2)', color: 'var(--danger)' }}>MISSING</span>
                    <button
                      className="btn btn-primary"
                      onClick={() => onCreateOne(item, key)}
                      disabled={isCreating}
                      style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                    >
                      {isCreating ? '⏳' : '🚀'} Create
                    </button>
                  </>
                )}
                {item.priority && <span className={`badge badge-${item.priority}`}>{item.priority}</span>}
              </div>
            </div>
            <div className="fields-list">
              {Object.entries(item.fields || {}).map(([field, dir]) => (
                <span className="field-tag" key={field}>{field}: {dir}</span>
              ))}
            </div>
            {item.rationale && <div className="rationale">💬 {item.rationale}</div>}
            {item.source?.file && <div className="source-info">📄 {item.source.file}:{item.source.line}</div>}
            {result?.status === 'error' && (
              <div className="error-msg" style={{ marginTop: 6, padding: '6px 10px', fontSize: '0.8rem' }}>{result.message}</div>
            )}
            {result?.status === 'created' && result.index_name && (
              <div className="source-info" style={{ color: 'var(--success)' }}>Index name: {result.index_name}</div>
            )}
          </div>
        )
      })}
    </>
  )
}

function IndexList({ items, emptyIcon, emptyText, showPriority, showRationale, statusColor, statusLabel }) {
  if (!items.length) {
    return <div className="empty-state"><div className="icon">{emptyIcon || '📭'}</div><p>{emptyText || 'No items.'}</p></div>
  }
  return items.map((item, i) => (
    <div className="index-card" key={i} style={{ borderLeft: `3px solid ${statusColor}` }}>
      <div className="index-card-header">
        <span className="collection-name">
          {item.database && <span style={{ color: 'var(--text-muted)' }}>{item.database}.</span>}
          {item.collection}
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          <span className="badge" style={{ background: `${statusColor}22`, color: statusColor }}>{statusLabel}</span>
          {showPriority && item.priority && <span className={`badge badge-${item.priority}`}>{item.priority}</span>}
        </div>
      </div>
      <div className="fields-list">
        {Object.entries(item.fields || {}).map(([field, dir]) => (
          <span className="field-tag" key={field}>{field}: {dir}</span>
        ))}
      </div>
      {showRationale && item.rationale && <div className="rationale">💬 {item.rationale}</div>}
      {item.source?.file && <div className="source-info">📄 {item.source.file}:{item.source.line}</div>}
    </div>
  ))
}

function LiveIndexList({ items }) {
  if (!items.length) {
    return <div className="empty-state"><div className="icon">🗄️</div><p>No indexes found in the database.</p></div>
  }
  const grouped = {}
  for (const idx of items) {
    const key = `${idx.database}.${idx.collection}`
    grouped[key] = grouped[key] || []
    grouped[key].push(idx)
  }
  return Object.entries(grouped).map(([key, indexes]) => (
    <div key={key} style={{ marginBottom: 16 }}>
      <h4 style={{ color: 'var(--info)', fontFamily: 'JetBrains Mono, monospace', fontSize: '0.9rem', marginBottom: 8 }}>
        {key} ({indexes.length})
      </h4>
      {indexes.map((idx, i) => (
        <div className="index-card" key={i} style={{ borderLeft: '3px solid var(--info)' }}>
          <div className="index-card-header">
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.85rem', color: 'var(--text-muted)' }}>{idx.name}</span>
            <div style={{ display: 'flex', gap: 6 }}>
              {idx.unique && <span className="badge badge-high">unique</span>}
              {idx.sparse && <span className="badge badge-medium">sparse</span>}
            </div>
          </div>
          <div className="fields-list">
            {Object.entries(idx.fields || {}).map(([field, dir]) => (
              <span className="field-tag" key={field}>{field}: {dir}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  ))
}

const ESR_COLORS = { E: '#00b894', S: '#6c5ce7', R: '#fdcb6e', '?': '#636e72' }
const ESR_LABELS = { E: 'Equality', S: 'Sort', R: 'Range', '?': 'Unknown' }

function EsrAnalysis({ items }) {
  if (!items.length) {
    return <div className="empty-state"><div className="icon">📐</div><p>No compound indexes to analyze for ESR compliance.</p></div>
  }
  const violations = items.filter(e => !e.is_esr_compliant)
  const compliant = items.filter(e => e.is_esr_compliant)
  return (
    <>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: 16 }}>
        ESR Rule: <span style={{ color: ESR_COLORS.E }}>Equality</span> → <span style={{ color: ESR_COLORS.S }}>Sort</span> → <span style={{ color: ESR_COLORS.R }}>Range</span>
      </p>
      {violations.length > 0 && (
        <>
          <h3 className="section-title" style={{ color: 'var(--warning)' }}>⚠️ ESR Violations ({violations.length})</h3>
          {violations.map((e, i) => <EsrCard key={`v-${i}`} item={e} />)}
        </>
      )}
      {compliant.length > 0 && (
        <>
          <h3 className="section-title" style={{ color: 'var(--success)', marginTop: violations.length ? 20 : 0 }}>✅ ESR Compliant ({compliant.length})</h3>
          {compliant.map((e, i) => <EsrCard key={`c-${i}`} item={e} />)}
        </>
      )}
    </>
  )
}

function EsrCard({ item }) {
  const isViolation = !item.is_esr_compliant
  return (
    <div className="index-card" style={{ borderLeft: `3px solid ${isViolation ? 'var(--warning)' : 'var(--success)'}` }}>
      <div className="index-card-header">
        <span className="collection-name">
          <span style={{ color: 'var(--text-muted)' }}>{item.database}.</span>{item.collection}
        </span>
        <span className="badge" style={{
          background: isViolation ? 'rgba(253,203,110,0.2)' : 'rgba(0,184,148,0.2)',
          color: isViolation ? 'var(--warning)' : 'var(--success)',
        }}>{isViolation ? '⚠️ VIOLATION' : '✅ OK'}</span>
      </div>
      <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 4 }}>{item.index_name}</div>
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Current: </span>
        {item.current_fields.map((f, i) => {
          const cls = item.field_classes[i]
          return (
            <span key={f} className="field-tag" style={{ background: `${ESR_COLORS[cls]}22`, color: ESR_COLORS[cls], border: `1px solid ${ESR_COLORS[cls]}44` }}>
              {f} <span style={{ opacity: 0.7 }}>({ESR_LABELS[cls]?.[0] || '?'})</span>
            </span>
          )
        })}
        <span style={{ marginLeft: 8, fontSize: '0.8rem', fontFamily: 'JetBrains Mono, monospace', color: 'var(--text-muted)' }}>[{item.current_order}]</span>
      </div>
      {isViolation && item.optimal_esr_order && (
        <div>
          <span style={{ fontSize: '0.8rem', color: 'var(--success)' }}>Suggested: </span>
          {item.optimal_esr_order.map((f) => {
            const origIdx = item.current_fields.indexOf(f)
            const cls = origIdx >= 0 ? item.field_classes[origIdx] : '?'
            return (
              <span key={f} className="field-tag" style={{ background: `${ESR_COLORS[cls]}22`, color: ESR_COLORS[cls], border: `1px solid ${ESR_COLORS[cls]}44` }}>
                {f} <span style={{ opacity: 0.7 }}>({ESR_LABELS[cls]?.[0] || '?'})</span>
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
