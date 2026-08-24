import { useState } from 'react'
import './App.css'
import SourceSelector from './components/SourceSelector'
import ScanAnalyze from './components/ScanAnalyze'
import Compare from './components/Compare'
import ScriptCompare from './components/ScriptCompare'
import Export from './components/Export'

function App() {
  const [activeTab, setActiveTab] = useState('scan')
  const [source, setSource] = useState({ mode: 'local', path: '' })

  const tabs = [
    { id: 'scan', label: '🔍 Scan & Analyze' },
    { id: 'compare', label: '⚡ Compare DB' },
    { id: 'script', label: '🔄 Compare Script' },
    { id: 'export', label: '📦 Export Script' },
  ]

  // Build API payload from source config
  const getSourcePayload = () => {
    const base = source.mode === 'git' ? {
      source: 'git',
      repo_url: source.repo_url || '',
      branch: source.branch || '',
      git_api_token: source.git_api_token || '',
      git_username: source.git_username || '',
    } : { source: 'local', path: source.path || '' }

    // Add config repo if provided
    if (source.config_repo_url) base.config_repo_url = source.config_repo_url
    if (source.config_repo_path) base.config_repo_path = source.config_repo_path
    if (source.config_repo_branch) base.config_repo_branch = source.config_repo_branch

    return base
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🗄️ Index Scanner</h1>
        <p className="subtitle">Scan codebases for MongoDB & PostgreSQL index definitions</p>
      </header>

      <SourceSelector source={source} onChange={setSource} />

      <nav className="tab-nav" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <main className="tab-content" role="tabpanel">
        {activeTab === 'scan' && <ScanAnalyze getSourcePayload={getSourcePayload} />}
        {activeTab === 'compare' && <Compare getSourcePayload={getSourcePayload} />}
        {activeTab === 'script' && <ScriptCompare getSourcePayload={getSourcePayload} sourceCredentials={{ username: source.git_username || '', token: source.git_api_token || '' }} />}
        {activeTab === 'export' && <Export getSourcePayload={getSourcePayload} />}
      </main>
    </div>
  )
}

export default App
