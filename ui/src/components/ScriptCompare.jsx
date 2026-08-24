import { useState, useCallback } from 'react'
import { API_BASE } from '../config'

export default function ScriptCompare({ getSourcePayload, sourceCredentials }) {
  // Script source mode
  const [scriptMode, setScriptMode] = useState('path') // 'path', 'paste', 'git'

  // Local file
  const [scriptPath, setScriptPath] = useState('')

  // Paste
  const [scriptContent, setScriptContent] = useState('')

  // Git
  const [scriptRepoUrl, setScriptRepoUrl] = useState('')
  const [scriptBranch, setScriptBranch] = useState('')
  const [scriptFilePath, setScriptFilePath] = useState('')
  const [scriptToken, setScriptToken] = useState('')
  const [scriptUsername, setScriptUsername] = useState('')
  const [useSameCreds, setUseSameCreds] = useState(true)
  const [branches, setBranches] = useState([])
  const [loadingBranches, setLoadingBranches] = useState(false)

  // Effective credentials (from source or overridden)
  const effectiveUsername = useSameCreds ? (sourceCredentials?.username || '') : scriptUsername
  const effectiveToken = useSameCreds ? (sourceCredentials?.token || '') : scriptToken
  const hasSourceCreds = !!(sourceCredentials?.username || sourceCredentials?.token)

  // Results
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeSection, setActiveSection] = useState('missing')

  const fetchBranches = useCallback(async () => {
    if (!scriptRepoUrl.trim()) return
    setLoadingBranches(true)
    setBranches([])
    try {
      const res = await fetch(`${API_BASE}/api/git/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: scriptRepoUrl,
          git_api_token: effectiveToken,
          git_username: effectiveUsername,
        }),
      })
      const json = await res.json()
      if (res.ok) setBranches(json.branches || [])
    } catch (_) {}
    setLoadingBranches(false)
  }, [scriptRepoUrl, effectiveToken, effectiveUsername])

  const handleCompare = async () => {
    setLoading(true)
    setError('')
    setData(null)
    try {
      const body = { ...getSourcePayload(), script_source: scriptMode }

      if (scriptMode === 'path') {
        if (!scriptPath.trim()) throw new Error('Enter the script file path')
        body.script_path = scriptPath.trim()
      } else if (scriptMode === 'paste') {
        if (!scriptContent.trim()) throw new Error('Paste the script content')
        body.script_content = scriptContent
      } else if (scriptMode === 'git') {
        if (!scriptRepoUrl.trim()) throw new Error('Enter the script repo URL')
        if (!scriptFilePath.trim()) throw new Error('Enter the file path within the repo')
        body.script_repo_url = scriptRepoUrl.trim()
        body.script_branch = scriptBranch.trim()
        body.script_file_path = scriptFilePath.trim()
        body.script_git_token = effectiveToken
        body.script_git_username = effectiveUsername
      }

      const res = await fetch(`${API_BASE}/api/compare-script`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Compare failed')
      setData(json)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const summary = data?.summary || {}
  const gaps = data?.gaps || {}

  const sections = [
    { id: 'missing', label: `🚨 Missing from Script (${summary.missing_from_script || 0})` },
    { id: 'extra', label: `📌 Extra in Script (${summary.extra_in_script || 0})` },
    { id: 'covered', label: `✅ Covered (${summary.covered || 0})` },
    { id: 'script', label: `📜 Script Indexes (${summary.script_total || 0})` },
  ]

  return (
    <div className="panel">
      <h2 className="section-title">Compare Tenant Script vs Code</h2>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: 16 }}>
        Compare your tenant onboarding index script against indexes found in source code to find gaps.
      </p>

      {/* Script source toggle */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: '0.85rem', color: 'var(--text-muted)', fontWeight: 500, marginBottom: 8, display: 'block' }}>
          Script Source:
        </label>
        <div className="tab-nav" style={{ marginBottom: 0, maxWidth: 420 }}>
          {[
            { id: 'path', label: '📁 Local File' },
            { id: 'git', label: '🌿 Git Repo' },
            { id: 'paste', label: '📋 Paste' },
          ].map(m => (
            <button
              key={m.id}
              className={`tab-btn ${scriptMode === m.id ? 'active' : ''}`}
              onClick={() => setScriptMode(m.id)}
              style={{ fontSize: '0.8rem' }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Local file path */}
      {scriptMode === 'path' && (
        <div className="form-row">
          <div className="form-group" style={{ flex: 2 }}>
            <label>Tenant Index Script File</label>
            <input
              type="text"
              value={scriptPath}
              onChange={(e) => setScriptPath(e.target.value)}
              placeholder="/path/to/tenant_indexes.js"
              style={{ fontFamily: 'JetBrains Mono, monospace' }}
            />
          </div>
          <button className="btn btn-primary" onClick={handleCompare} disabled={loading} style={{ whiteSpace: 'nowrap' }}>
            {loading ? '⏳ Comparing...' : '🔄 Compare'}
          </button>
        </div>
      )}

      {/* Git repo */}
      {scriptMode === 'git' && (
        <div style={{ background: 'var(--surface-hover)', borderRadius: 'var(--radius)', padding: 16, marginBottom: 16 }}>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>Script Repo URL</label>
              <input
                type="text"
                value={scriptRepoUrl}
                onChange={(e) => setScriptRepoUrl(e.target.value)}
                placeholder="https://bitbucket.org/org/db-scripts.git"
                style={{ fontFamily: 'JetBrains Mono, monospace' }}
              />
            </div>
          </div>

          <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '8px 0 12px', cursor: 'pointer', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            <input
              type="checkbox"
              checked={useSameCreds}
              onChange={(e) => setUseSameCreds(e.target.checked)}
              style={{ accentColor: 'var(--primary)' }}
            />
            Use same credentials as project source
            {useSameCreds && sourceCredentials?.username && (
              <span className="field-tag" style={{ marginLeft: 4 }}>{sourceCredentials.username}</span>
            )}
            {useSameCreds && !hasSourceCreds && (
              <span style={{ color: 'var(--warning)', fontSize: '0.8rem' }}>(no credentials set in source)</span>
            )}
          </label>

          {!useSameCreds && (
            <div className="form-row">
              <div className="form-group">
                <label>Username</label>
                <input
                  type="text"
                  value={scriptUsername}
                  onChange={(e) => setScriptUsername(e.target.value)}
                  placeholder="bitbucket username"
                />
              </div>
              <div className="form-group">
                <label>API Token</label>
                <input
                  type="password"
                  value={scriptToken}
                  onChange={(e) => setScriptToken(e.target.value)}
                  placeholder="API token"
                />
              </div>
            </div>
          )}
          <div className="form-row" style={{ alignItems: 'flex-end' }}>
            <div className="form-group">
              <label>Branch</label>
              {branches.length > 0 ? (
                <select value={scriptBranch} onChange={(e) => setScriptBranch(e.target.value)}>
                  <option value="">-- default --</option>
                  {branches.map(b => <option key={b} value={b}>{b}</option>)}
                </select>
              ) : (
                <input
                  type="text"
                  value={scriptBranch}
                  onChange={(e) => setScriptBranch(e.target.value)}
                  placeholder="main"
                />
              )}
            </div>
            <button
              className="btn btn-secondary"
              onClick={fetchBranches}
              disabled={loadingBranches}
              style={{ fontSize: '0.8rem', padding: '8px 12px', whiteSpace: 'nowrap' }}
            >
              {loadingBranches ? '⏳' : '🌿'} Branches
            </button>
            <div className="form-group" style={{ flex: 2 }}>
              <label>File Path in Repo</label>
              <input
                type="text"
                value={scriptFilePath}
                onChange={(e) => setScriptFilePath(e.target.value)}
                placeholder="scripts/create_indexes.js"
                style={{ fontFamily: 'JetBrains Mono, monospace' }}
              />
            </div>
          </div>
          <button className="btn btn-primary" onClick={handleCompare} disabled={loading} style={{ marginTop: 12 }}>
            {loading ? '⏳ Comparing...' : '🔄 Compare'}
          </button>
        </div>
      )}

      {/* Paste */}
      {scriptMode === 'paste' && (
        <>
          <div className="form-group" style={{ marginBottom: 12 }}>
            <label>Paste Script Content</label>
            <textarea
              value={scriptContent}
              onChange={(e) => setScriptContent(e.target.value)}
              placeholder={'db.users.createIndex({"email": 1}, {"unique": true});\ndb.orders.createIndex({"userId": 1, "createdAt": -1});'}
              style={{
                width: '100%', minHeight: 150, padding: '12px 16px',
                background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius)', color: 'var(--text)',
                fontFamily: 'JetBrains Mono, monospace', fontSize: '0.85rem', resize: 'vertical',
              }}
            />
          </div>
          <button className="btn btn-primary" onClick={handleCompare} disabled={loading} style={{ marginBottom: 16 }}>
            {loading ? '⏳ Comparing...' : '🔄 Compare'}
          </button>
        </>
      )}

      {error && <div className="error-msg">{error}</div>}

      {loading && (
        <div className="loading">
          <div className="spinner" />
          <p>{scriptMode === 'git' ? 'Cloning repo and comparing...' : 'Parsing script and comparing against code...'}</p>
        </div>
      )}

      {data && !loading && (
        <>
          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--danger)' }}>{summary.missing_from_script || 0}</div>
              <div className="stat-label">Missing from Script</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--warning)' }}>{summary.extra_in_script || 0}</div>
              <div className="stat-label">Extra in Script</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--success)' }}>{summary.covered || 0}</div>
              <div className="stat-label">Covered</div>
            </div>
            <div className="stat-card">
              <div className="stat-value" style={{ color: 'var(--info)' }}>{summary.script_total || 0}</div>
              <div className="stat-label">Script Total</div>
            </div>
          </div>

          <div className="tab-nav" style={{ marginBottom: 16 }}>
            {sections.map(s => (
              <button
                key={s.id}
                className={`tab-btn ${activeSection === s.id ? 'active' : ''}`}
                onClick={() => setActiveSection(s.id)}
                style={{ fontSize: '0.8rem' }}
              >
                {s.label}
              </button>
            ))}
          </div>

          {activeSection === 'missing' && (
            <>
              {(gaps.in_code_not_in_script?.length > 0) && (
                <>
                  <h3 className="section-title" style={{ color: 'var(--danger)' }}>
                    Code-Defined Indexes Missing from Script ({gaps.in_code_not_in_script.length})
                  </h3>
                  {gaps.in_code_not_in_script.map((item, i) => (
                    <IndexCard key={`cm-${i}`} item={item} borderColor="var(--danger)" label="MISSING" />
                  ))}
                </>
              )}
              {(gaps.suggestions_not_in_script?.length > 0) && (
                <>
                  <h3 className="section-title" style={{ color: 'var(--warning)', marginTop: 20 }}>
                    Suggested Indexes Missing from Script ({gaps.suggestions_not_in_script.length})
                  </h3>
                  {gaps.suggestions_not_in_script.map((item, i) => (
                    <IndexCard key={`sm-${i}`} item={item} borderColor="var(--warning)" label="SUGGESTED" showRationale />
                  ))}
                </>
              )}
              {(!gaps.in_code_not_in_script?.length && !gaps.suggestions_not_in_script?.length) && (
                <div className="empty-state">
                  <div className="icon">🎉</div>
                  <p>Your tenant script covers all code-defined and suggested indexes!</p>
                </div>
              )}
            </>
          )}

          {activeSection === 'extra' && (
            (gaps.in_script_not_in_code?.length > 0) ? (
              gaps.in_script_not_in_code.map((item, i) => (
                <IndexCard key={`ex-${i}`} item={item} borderColor="var(--warning)" label="EXTRA" showRaw />
              ))
            ) : (
              <div className="empty-state"><div className="icon">✅</div><p>No extra indexes in the script.</p></div>
            )
          )}

          {activeSection === 'covered' && (
            (gaps.covered?.length > 0) ? (
              gaps.covered.map((item, i) => (
                <IndexCard key={`cov-${i}`} item={item} borderColor="var(--success)" label="COVERED" />
              ))
            ) : (
              <div className="empty-state"><div className="icon">📭</div><p>No overlapping indexes found.</p></div>
            )
          )}

          {activeSection === 'script' && (
            (data.script_indexes?.length > 0) ? (
              data.script_indexes.map((item, i) => (
                <IndexCard key={`si-${i}`} item={item} borderColor="var(--info)" label="SCRIPT" showRaw />
              ))
            ) : (
              <div className="empty-state"><div className="icon">📭</div><p>No indexes parsed from the script.</p></div>
            )
          )}
        </>
      )}

      {!data && !loading && !error && (
        <div className="empty-state">
          <div className="icon">🔄</div>
          <p>Provide your tenant index script to compare against code-scanned indexes.</p>
        </div>
      )}
    </div>
  )
}


function IndexCard({ item, borderColor, label, showRationale, showRaw }) {
  return (
    <div className="index-card" style={{ borderLeft: `3px solid ${borderColor}` }}>
      <div className="index-card-header">
        <span className="collection-name">{item.collection}</span>
        <div style={{ display: 'flex', gap: 6 }}>
          <span className="badge" style={{ background: `${borderColor}22`, color: borderColor }}>
            {label}
          </span>
          {item.priority && <span className={`badge badge-${item.priority}`}>{item.priority}</span>}
          {item.source_type && <span className="badge badge-type">{item.source_type}</span>}
        </div>
      </div>
      <div className="fields-list">
        {Object.entries(item.fields || {}).map(([field, dir]) => (
          <span className="field-tag" key={field}>{field}: {dir}</span>
        ))}
      </div>
      {showRationale && item.rationale && <div className="rationale">💬 {item.rationale}</div>}
      {showRaw && item.raw && (
        <div className="source-info" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '0.8rem' }}>{item.raw}</div>
      )}
      {item.source?.file && <div className="source-info">📄 {item.source.file}:{item.source.line}</div>}
      {item.operations?.length > 0 && <div className="source-info">Operations: {item.operations.join(', ')}</div>}
    </div>
  )
}
