import { useState } from 'react'
import { API_BASE } from '../config'

export default function Export({ getSourcePayload }) {
  const [format, setFormat] = useState('mongo_shell')
  const [dbName, setDbName] = useState('')
  const [includeSuggestions, setIncludeSuggestions] = useState(true)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)

  const handleExport = async () => {
    setLoading(true)
    setError('')
    setData(null)
    try {
      const body = { ...getSourcePayload(), format, include_suggestions: includeSuggestions }
      if (dbName.trim()) body.db_name = dbName.trim()
      const res = await fetch(`${API_BASE}/api/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const json = await res.json()
      if (json.error && !json.script) throw new Error(json.error)
      setData(json)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleCopy = async () => {
    if (data?.script) {
      await navigator.clipboard.writeText(data.script)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const handleDownload = () => {
    if (!data?.script) return
    const ext = format === 'mongo_shell' ? 'js' : format === 'pymongo' ? 'py' : 'sql'
    const blob = new Blob([data.script], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `create_indexes.${ext}`
    a.click()
    URL.revokeObjectURL(url)
  }

  const detectedDbs = data?.detected_databases || []

  return (
    <div className="panel">
      <h2 className="section-title">Export Index Script</h2>

      <div className="form-row">
        <div className="form-group">
          <label htmlFor="format-select">Script Format</label>
          <select id="format-select" value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="mongo_shell">MongoDB Shell (JavaScript)</option>
            <option value="pymongo">PyMongo (Python)</option>
            <option value="sql">PostgreSQL SQL</option>
          </select>
        </div>
        <div className="form-group">
          <label htmlFor="db-name">{format === 'sql' ? 'Schema Name Override (optional)' : 'Database Name Override (optional)'}</label>
          <input
            id="db-name"
            type="text"
            value={dbName}
            onChange={(e) => setDbName(e.target.value)}
            placeholder={format === 'sql' ? 'public' : 'auto-detected from source code'}
          />
        </div>
        <button className="btn btn-primary" onClick={handleExport} disabled={loading}>
          {loading ? '⏳ Generating...' : '📦 Generate Script'}
        </button>
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, cursor: 'pointer', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
        <input
          type="checkbox"
          checked={includeSuggestions}
          onChange={(e) => setIncludeSuggestions(e.target.checked)}
          style={{ accentColor: 'var(--primary)' }}
        />
        Include suggested indexes (from query pattern analysis)
      </label>

      {error && <div className="error-msg">{error}</div>}

      {loading && (
        <div className="loading">
          <div className="spinner" />
          <p>Generating export script...</p>
        </div>
      )}

      {data && !loading && (
        <>
          {detectedDbs.length > 0 && (
            <div style={{ marginBottom: 12, padding: '10px 14px', background: 'var(--surface-hover)', borderRadius: 'var(--radius)', fontSize: '0.85rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Databases detected from source: </span>
              {detectedDbs.map((db) => (
                <span
                  key={db}
                  className="field-tag"
                  style={{ cursor: 'pointer' }}
                  onClick={() => setDbName(db)}
                  title="Click to use as override"
                >
                  {db}
                </span>
              ))}
            </div>
          )}

          {data.script ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <span className="source-info" style={{ margin: 0 }}>
                  {data.total_in_script || data.indexes_count} index(es) in script
                  {data.suggestions_count > 0 && ` (incl. ${data.suggestions_count} suggestions)`}
                  {' • '}{format === 'mongo_shell' ? 'MongoDB Shell' : format === 'pymongo' ? 'PyMongo' : 'PostgreSQL SQL'}
                </span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-secondary" onClick={handleCopy}>
                    {copied ? '✅ Copied!' : '📋 Copy'}
                  </button>
                  <button className="btn btn-secondary" onClick={handleDownload}>
                    💾 Download
                  </button>
                </div>
              </div>
              <div className="code-block">{data.script}</div>
            </>
          ) : (
            <div className="empty-state">
              <div className="icon">📭</div>
              <p>No indexes found to export.</p>
            </div>
          )}
        </>
      )}

      {!data && !loading && !error && (
        <div className="empty-state">
          <div className="icon">📦</div>
          <p>Generate executable scripts from discovered indexes and suggestions.</p>
        </div>
      )}
    </div>
  )
}
