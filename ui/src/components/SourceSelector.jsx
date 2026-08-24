import { useState, useCallback } from 'react'
import { API_BASE } from '../config'

export default function SourceSelector({ source, onChange }) {
  const [loadingBranches, setLoadingBranches] = useState(false)
  const [branches, setBranches] = useState([])
  const [branchError, setBranchError] = useState('')
  const [showToken, setShowToken] = useState(false)

  const mode = source.mode || 'local'

  const update = (patch) => {
    onChange({ ...source, ...patch })
  }

  const fetchBranches = useCallback(async () => {
    if (!source.repo_url?.trim()) {
      setBranchError('Enter a repo URL first')
      return
    }
    setLoadingBranches(true)
    setBranchError('')
    setBranches([])
    try {
      const res = await fetch(`${API_BASE}/api/git/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: source.repo_url,
          git_api_token: source.git_api_token || '',
          git_username: source.git_username || '',
        }),
      })
      const json = await res.json()
      if (!res.ok) throw new Error(json.error || 'Failed to fetch branches')
      setBranches(json.branches || [])
    } catch (e) {
      setBranchError(e.message)
    } finally {
      setLoadingBranches(false)
    }
  }, [source.repo_url, source.git_api_token, source.git_username])

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
        <label style={{ fontSize: '0.85rem', color: 'var(--text-muted)', fontWeight: 500 }}>Source:</label>
        <div className="tab-nav" style={{ marginBottom: 0, flex: 'none' }}>
          <button
            className={`tab-btn ${mode === 'local' ? 'active' : ''}`}
            onClick={() => update({ mode: 'local' })}
            style={{ fontSize: '0.8rem', padding: '6px 16px' }}
          >
            📁 Local Path
          </button>
          <button
            className={`tab-btn ${mode === 'git' ? 'active' : ''}`}
            onClick={() => update({ mode: 'git' })}
            style={{ fontSize: '0.8rem', padding: '6px 16px' }}
          >
            🌿 Git / Bitbucket
          </button>
        </div>
      </div>

      {mode === 'local' ? (
        <div>
          <label style={{ display: 'block', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 6, fontWeight: 500 }}>
            Project Path
          </label>
          <input
            type="text"
            value={source.path || ''}
            onChange={(e) => update({ path: e.target.value })}
            placeholder="/path/to/your/project"
            className="path-input"
          />
        </div>
      ) : (
        <div>
          <div className="form-row">
            <div className="form-group" style={{ flex: 2 }}>
              <label>Repository URL</label>
              <input
                type="text"
                value={source.repo_url || ''}
                onChange={(e) => update({ repo_url: e.target.value })}
                placeholder="git@bitbucket.org:your-org/repo-name.git"
                style={{ fontFamily: 'JetBrains Mono, monospace' }}
              />
            </div>
            <div className="form-group">
              <label>Username (for Bitbucket)</label>
              <input
                type="text"
                value={source.git_username || ''}
                onChange={(e) => update({ git_username: e.target.value })}
                placeholder="bitbucket username (not email)"
              />
            </div>
            <div className="form-group">
              <label>API Token</label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showToken ? 'text' : 'password'}
                  value={source.git_api_token || ''}
                  onChange={(e) => update({ git_api_token: e.target.value })}
                  placeholder="Bitbucket / GitHub API token"
                  style={{ paddingRight: 36 }}
                />
                <button
                  onClick={() => setShowToken(!showToken)}
                  style={{
                    position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                    background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.85rem',
                    color: 'var(--text-muted)',
                  }}
                  title={showToken ? 'Hide' : 'Show'}
                >
                  {showToken ? '🙈' : '👁️'}
                </button>
              </div>
            </div>
          </div>

          <div className="form-row" style={{ alignItems: 'flex-end' }}>
            <div className="form-group" style={{ flex: 2 }}>
              <label>Branch</label>
              {branches.length > 0 ? (
                <select
                  value={source.branch || ''}
                  onChange={(e) => update({ branch: e.target.value })}
                >
                  <option value="">-- default branch --</option>
                  {branches.map(b => (
                    <option key={b} value={b}>{b}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={source.branch || ''}
                  onChange={(e) => update({ branch: e.target.value })}
                  placeholder="main, develop, feature/xyz..."
                />
              )}
            </div>
            <button
              className="btn btn-secondary"
              onClick={fetchBranches}
              disabled={loadingBranches}
              style={{ fontSize: '0.8rem', padding: '8px 14px', whiteSpace: 'nowrap' }}
            >
              {loadingBranches ? '⏳' : '🌿'} Fetch Branches
            </button>
          </div>

          {branchError && <div className="error-msg" style={{ marginTop: 8 }}>{branchError}</div>}
        </div>
      )}

      {/* Config/Helm repo (optional) */}
      <div style={{ marginTop: 16 }}>
        <button
          onClick={() => update({ _showConfigRepo: !source._showConfigRepo })}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: '0.85rem', padding: 0,
          }}
        >
          {source._showConfigRepo ? '▼' : '▶'} Config / Helm Repo (optional — for DB name detection)
        </button>

        {source._showConfigRepo && (
          <div style={{ marginTop: 8, padding: 12, background: 'var(--surface-hover)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 8 }}>
              If your DB configs live in a separate repo (Helm values, Groovy pipelines, etc.), add it here.
              {mode === 'git' && ' Uses same credentials as the main source by default.'}
            </p>
            <div className="form-row">
              {mode === 'local' ? (
                <div className="form-group" style={{ flex: 2 }}>
                  <label>Config Repo Local Path</label>
                  <input type="text" value={source.config_repo_path || ''}
                    onChange={(e) => update({ config_repo_path: e.target.value })}
                    placeholder="/path/to/config-repo or helm-charts" />
                </div>
              ) : (
                <>
                  <div className="form-group" style={{ flex: 2 }}>
                    <label>Config Repo URL</label>
                    <input type="text" value={source.config_repo_url || ''}
                      onChange={(e) => update({ config_repo_url: e.target.value })}
                      placeholder="https://bitbucket.org/org/helm-charts.git"
                      style={{ fontFamily: 'JetBrains Mono, monospace' }} />
                  </div>
                  <div className="form-group">
                    <label>Branch</label>
                    <input type="text" value={source.config_repo_branch || ''}
                      onChange={(e) => update({ config_repo_branch: e.target.value })}
                      placeholder="main" />
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
