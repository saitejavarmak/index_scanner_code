"""Index Scanner UI — Flask API serving the React frontend and scanner engine."""

import sys
import os
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

# Add the src directory to the path so we can import the scanner modules
_src_path = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
if os.path.isdir(_src_path):
    sys.path.insert(0, _src_path)

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from index_scanner_mcp.scanner_engine import ScannerEngine
from index_scanner_mcp.script_generator import ScriptGenerator
from index_scanner_mcp.report_generator import ReportGenerator

# Service-name prefixes stripped when deriving a Bitbucket repo name from a
# service-catalog service name. Customize for your org's naming convention,
# e.g. SERVICE_NAME_PREFIXES = ('svc_', 'app-').
SERVICE_NAME_PREFIXES = ()

# Static files directory (built React app)
STATIC_DIR = os.environ.get('STATIC_DIR', os.path.join(os.path.dirname(__file__), '..', 'dist'))

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')
CORS(app)

_engine = ScannerEngine()
_script_gen = ScriptGenerator()
_report_gen = ReportGenerator()


# ---- Serve React SPA ----

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'service': 'index-scanner-ui'})


@app.route('/')
def serve_index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.errorhandler(404)
def fallback(e):
    """Serve index.html for client-side routing (non-API routes only)."""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'path': request.path}), 404
    index_path = os.path.join(STATIC_DIR, 'index.html')
    if os.path.isfile(index_path):
        return send_from_directory(STATIC_DIR, 'index.html')
    return jsonify({'error': 'Not found'}), 404


# ---- Git helpers ----

_clone_cache: dict[str, str] = {}  # (repo_url, branch) -> temp_dir


def _resolve_project_path(data: dict) -> tuple[str, str | None, str | None]:
    """Resolve the project path from request data.

    Supports:
    - source=local: uses 'path' directly
    - source=git: clones repo_url at branch into a temp dir

    Returns (resolved_path, error_message, temp_dir_to_cleanup).
    """
    source = data.get('source', 'local')

    if source == 'local':
        path = data.get('path', '')
        if not path:
            return '', 'path is required', None
        target = Path(path).resolve()
        if not target.exists():
            return '', f'Path not found: {path}', None
        return str(target), None, None

    elif source == 'git':
        repo_url = data.get('repo_url', '').strip()
        branch = data.get('branch', '').strip() or None
        api_token = data.get('git_api_token', '').strip()
        username = data.get('git_username', '').strip()

        # Fall back to default credentials from environment if not provided
        if not api_token:
            api_token = os.environ.get('DEFAULT_GIT_API_TOKEN', '')
        if not username:
            username = os.environ.get('DEFAULT_GIT_USERNAME', '')

        if not repo_url:
            return '', 'repo_url is required for git source', None

        # Convert SSH URL to HTTPS when credentials are provided
        # git@bitbucket.org:org/repo.git → https://bitbucket.org/org/repo.git
        clone_url = repo_url
        if api_token and clone_url.startswith('git@'):
            # SSH format: git@bitbucket.org:org/repo.git
            ssh_match = None
            import re as _re
            ssh_match = _re.match(r'git@([^:]+):(.+)', clone_url)
            if ssh_match:
                host = ssh_match.group(1)
                path_part = ssh_match.group(2)
                clone_url = f'https://{host}/{path_part}'

        # Build git auth: Bitbucket needs username:token in URL (Basic auth)
        # GitHub/GitLab Bearer also supported as fallback
        auth_args = []
        if api_token:
            if username:
                # Bitbucket: username + API token as password in URL
                from urllib.parse import quote
                encoded_user = quote(username, safe='')
                encoded_token = quote(api_token, safe='')
                if clone_url.startswith('https://'):
                    clone_url = clone_url.replace(
                        'https://',
                        f'https://{encoded_user}:{encoded_token}@',
                        1
                    )
            else:
                # GitHub/GitLab: Bearer token via header
                auth_args = ['-c', f'http.extraHeader=Authorization: Bearer {api_token}']

        cache_key = f"{repo_url}|{branch or 'default'}"
        if cache_key in _clone_cache:
            cached = _clone_cache[cache_key]
            if os.path.isdir(cached):
                try:
                    pull_cmd = ['git'] + auth_args + ['pull', '--ff-only']
                    subprocess.run(
                        pull_cmd,
                        cwd=cached, capture_output=True, timeout=60
                    )
                except Exception:
                    pass
                return cached, None, None
            else:
                del _clone_cache[cache_key]

        tmp_dir = tempfile.mkdtemp(prefix='idx-scanner-')
        cmd = ['git'] + auth_args + ['clone', '--depth', '1']
        if branch:
            cmd += ['--branch', branch]
        cmd += [clone_url, tmp_dir]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                err = result.stderr.strip()
                err = err.replace(api_token, '***') if api_token else err
                err = err.replace(username, '***') if username else err
                return '', f'Git clone failed: {err}', None
        except subprocess.TimeoutExpired:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return '', 'Git clone timed out (120s)', None
        except Exception as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return '', f'Git clone error: {str(e)}', None

        _clone_cache[cache_key] = tmp_dir
        return tmp_dir, None, tmp_dir

    else:
        return '', f'Unknown source type: {source}', None


@app.route('/api/git/branches', methods=['POST'])
def list_branches():
    """List remote branches for a git repo URL."""
    data = request.json or {}
    repo_url = data.get('repo_url', '').strip()
    api_token = data.get('git_api_token', '').strip()
    username = data.get('git_username', '').strip()

    # Fall back to default credentials from environment if not provided
    if not api_token:
        api_token = os.environ.get('DEFAULT_GIT_API_TOKEN', '')
    if not username:
        username = os.environ.get('DEFAULT_GIT_USERNAME', '')

    if not repo_url:
        return jsonify({'error': 'repo_url is required'}), 400

    # Convert SSH URL to HTTPS when credentials are provided
    auth_args = []
    ls_url = repo_url
    if api_token and ls_url.startswith('git@'):
        import re as _re
        ssh_match = _re.match(r'git@([^:]+):(.+)', ls_url)
        if ssh_match:
            host = ssh_match.group(1)
            path_part = ssh_match.group(2)
            ls_url = f'https://{host}/{path_part}'

    if api_token:
        if username:
            from urllib.parse import quote
            encoded_user = quote(username, safe='')
            encoded_token = quote(api_token, safe='')
            if ls_url.startswith('https://'):
                ls_url = ls_url.replace(
                    'https://',
                    f'https://{encoded_user}:{encoded_token}@',
                    1
                )
        else:
            auth_args = ['-c', f'http.extraHeader=Authorization: Bearer {api_token}']

    try:
        result = subprocess.run(
            ['git'] + auth_args + ['ls-remote', '--heads', ls_url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            err = err.replace(api_token, '***') if api_token else err
            err = err.replace(username, '***') if username else err
            return jsonify({'error': f'Failed to list branches: {err}'}), 400

        branches = []
        for line in result.stdout.strip().split('\n'):
            if line and 'refs/heads/' in line:
                branch = line.split('refs/heads/')[-1]
                branches.append(branch)

        return jsonify({'branches': sorted(branches)})
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Timed out listing branches'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _scan_config_repo_for_db_names(config_path: str) -> list[str]:
    """Scan a config/Helm repo for MongoDB database names.

    Looks in Groovy pipelines, Helm values, Jenkinsfiles, .env, properties,
    and YAML files for database name patterns.
    """
    import re

    db_names = []
    seen = set()

    # Patterns to find DB names in config files
    patterns = [
        # Groovy/Jenkins: def dbName = "included_${t}", database = "xxx"
        re.compile(r'(?:dbName|database|db)\s*=\s*["\']([^"\'$]+)["\']'),
        # Helm values: database: candidates, mongodb.database: xxx
        re.compile(r'(?:database|mongodb\.database|mongo\.db|MONGO_DB|DB_NAME)\s*[:=]\s*["\']?([a-z][a-z0-9_-]+)["\']?'),
        # MongoDB URI in any file
        re.compile(r'mongodb(?:\+srv)?://[^/]+/([a-z][a-z0-9_-]+)'),
        # Groovy string patterns like "included_${t}" -> extract "included" as a DB pattern
        re.compile(r'["\']([a-z][a-z0-9_]+)_\$\{'),
        # spring.data.mongodb.database
        re.compile(r'spring\.data\.mongodb\.database\s*[:=]\s*["\']?([a-z][a-z0-9_-]+)["\']?'),
        # getDatabase("name"), getSiblingDB("name")
        re.compile(r'(?:getDatabase|getSiblingDB|getDB)\s*\(\s*["\']([^"\'$]+)["\']'),
    ]

    config_extensions = {'.groovy', '.yaml', '.yml', '.properties', '.env', '.conf', '.toml', '.json', '.tf'}
    config_filenames = {'Jenkinsfile', 'Dockerfile', '.env', '.env.example'}

    skip = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'target', 'build', 'dist'}

    blocklist = {'admin', 'local', 'config', 'test', 'true', 'false', 'null', 'localhost', 'example', 'default'}

    for root, dirs, files in os.walk(config_path):
        dirs[:] = [d for d in dirs if d not in skip]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in config_extensions and fname not in config_filenames:
                continue
            filepath = os.path.join(root, fname)
            try:
                with open(filepath, encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                for pattern in patterns:
                    for m in pattern.finditer(content):
                        name = m.group(1).strip().strip("'\"")
                        if name and name not in seen and name not in blocklist and len(name) >= 2 and not name.startswith('$'):
                            seen.add(name)
                            db_names.append(name)
            except (OSError, UnicodeDecodeError):
                pass

    return db_names


@app.route('/api/scan', methods=['POST'])
def scan():
    """Scan a project directory for index definitions.

    Optionally accepts config_repo_* fields to scan a separate config/Helm
    repo for database names that get merged into the results.
    """
    data = request.json or {}
    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    result = _engine.scan_project(resolved_path)

    # Optionally scan a config/Helm repo for additional database names
    config_repo_url = data.get('config_repo_url', '').strip()
    config_repo_path = data.get('config_repo_path', '').strip()
    config_db_names = []

    if config_repo_url or config_repo_path:
        if config_repo_url:
            # Clone the config repo
            config_data = {
                'source': 'git',
                'repo_url': config_repo_url,
                'branch': data.get('config_repo_branch', '').strip() or None,
                'git_api_token': data.get('config_repo_token', data.get('git_api_token', '')).strip(),
                'git_username': data.get('config_repo_username', data.get('git_username', '')).strip(),
            }
            cfg_path, cfg_err, _ = _resolve_project_path(config_data)
            if not cfg_err:
                config_db_names = _scan_config_repo_for_db_names(cfg_path)
        elif config_repo_path:
            cfg = Path(config_repo_path).resolve()
            if cfg.is_dir():
                config_db_names = _scan_config_repo_for_db_names(str(cfg))

        # Merge config repo DB names into the scan result
        if config_db_names:
            existing = set(result.database_names)
            for db in config_db_names:
                if db not in existing:
                    result.database_names.append(db)
                    existing.add(db)
            # Re-assign DB names to indexes/suggestions that don't have one
            if result.database_names:
                primary_db = result.database_names[0]
                for idx in result.indexes:
                    if idx.database is None:
                        idx.database = primary_db
                for sug in result.suggestions:
                    if sug.database is None:
                        sug.database = primary_db

    report = _report_gen.generate_report(result)
    if config_db_names:
        report['config_repo_databases'] = config_db_names
    return jsonify(report)


@app.route('/api/suggest', methods=['POST'])
def suggest():
    """Get index suggestions for a project."""
    data = request.json or {}
    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    result = _engine.scan_project(resolved_path)
    report = _report_gen.generate_report(result)
    return jsonify({
        'suggestions': report.get('suggestions', []),
        'summary': report.get('summary', {}),
    })


@app.route('/api/search', methods=['POST'])
def search():
    """Search for specific index patterns."""
    data = request.json or {}
    query = data.get('query', '')
    if not query:
        return jsonify({'error': 'query is required'}), 400

    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    result = _engine.scan_project(resolved_path)
    report = _report_gen.generate_report(result)

    q = query.lower()
    filtered_indexes = []
    for idx in report.get('indexes', []):
        collection = idx.get('collection', '')
        fields = json.dumps(idx.get('fields', {}))
        idx_type = idx.get('index_type', '')
        if q in collection.lower() or q in fields.lower() or q in idx_type.lower():
            filtered_indexes.append(idx)

    filtered_suggestions = []
    for s in report.get('suggestions', []):
        collection = s.get('collection', '')
        fields = json.dumps(s.get('fields', {}))
        rationale = s.get('rationale', '')
        if q in collection.lower() or q in fields.lower() or q in rationale.lower():
            filtered_suggestions.append(s)

    return jsonify({
        'query': query,
        'indexes': filtered_indexes,
        'suggestions': filtered_suggestions,
    })


@app.route('/api/export', methods=['POST'])
def export():
    """Generate an executable script from scanned indexes AND suggestions."""
    data = request.json or {}
    fmt = data.get('format', 'mongo_shell')
    db_name = data.get('db_name')
    include_suggestions = data.get('include_suggestions', True)

    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    result = _engine.scan_project(resolved_path)

    # Use detected database names if user didn't provide one
    detected_dbs = result.database_names or []

    # Combine actual indexes + suggestions (converted to IndexDefinition)
    all_indexes = list(result.indexes)

    if include_suggestions and result.suggestions:
        from index_scanner_mcp.models import IndexDefinition as IdxDef
        # Assign detected db name to suggestions too
        primary_db = db_name or (detected_dbs[0] if detected_dbs else None)
        for s in result.suggestions:
            all_indexes.append(IdxDef(
                collection=s.collection,
                fields=s.fields,
                index_type='suggested',
                database=primary_db,
            ))

    if not all_indexes:
        return jsonify({'error': 'No indexes found to export', 'script': ''}), 200

    # If user provided db_name, use it; otherwise let the script generator
    # use the database names already assigned to each IndexDefinition
    effective_db = db_name if db_name else None

    if fmt == 'mongo_shell':
        script = _script_gen.generate_mongo_shell(all_indexes, db_name=effective_db)
    elif fmt == 'pymongo':
        script = _script_gen.generate_pymongo(all_indexes, db_name=effective_db)
    elif fmt in ('sql', 'postgresql'):
        script = _script_gen.generate_postgresql_sql(all_indexes, db_name=effective_db)
    else:
        return jsonify({'error': f'Invalid format: {fmt}'}), 400

    return jsonify({
        'script': script,
        'format': fmt,
        'detected_databases': detected_dbs,
        'indexes_count': len(result.indexes),
        'suggestions_count': len(result.suggestions) if include_suggestions else 0,
        'total_in_script': len(all_indexes),
    })


@app.route('/api/compare-script', methods=['POST'])
def compare_script():
    """Compare a tenant index script file against code-scanned indexes."""
    import re as _re

    data = request.json or {}
    script_path = data.get('script_path', '')
    script_content = data.get('script_content', '')
    script_source = data.get('script_source', 'local')  # 'local', 'paste', or 'git'
    script_repo_url = data.get('script_repo_url', '').strip()
    script_branch = data.get('script_branch', '').strip()
    script_file_path = data.get('script_file_path', '').strip()  # path within repo
    script_git_token = data.get('script_git_token', '').strip()
    script_git_username = data.get('script_git_username', '').strip()

    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    # Resolve script content based on source
    if script_source == 'git':
        if not script_repo_url:
            return jsonify({'error': 'script_repo_url is required for git script source'}), 400
        if not script_file_path:
            return jsonify({'error': 'script_file_path is required (path to file within repo)'}), 400

        # Clone the script repo to a temp dir
        script_data = {
            'source': 'git',
            'repo_url': script_repo_url,
            'branch': script_branch or None,
            'git_api_token': script_git_token,
            'git_username': script_git_username,
        }
        script_repo_path, script_err, _ = _resolve_project_path(script_data)
        if script_err:
            return jsonify({'error': f'Script repo: {script_err}'}), 400

        # Read the file from the cloned repo
        full_script_path = os.path.join(script_repo_path, script_file_path)
        if not os.path.isfile(full_script_path):
            return jsonify({'error': f'File not found in repo: {script_file_path}'}), 404
        with open(full_script_path, 'r', errors='ignore') as f:
            script_content = f.read()

    elif script_source == 'paste':
        if not script_content:
            return jsonify({'error': 'script_content is required'}), 400

    else:  # local
        if not script_path and not script_content:
            return jsonify({'error': 'script_path or script_content is required'}), 400

    # Read the script file
    if script_path and not script_content:
        sp = Path(script_path).resolve()
        if not sp.exists():
            return jsonify({'error': f'Script file not found: {script_path}'}), 404
        with open(sp, 'r', errors='ignore') as f:
            script_content = f.read()

    # Parse indexes from the script file
    script_indexes = []

    # Mongo shell: db.collection.createIndex({...})
    shell_pattern = _re.compile(
        r'db\.(\w+)\.createIndex\s*\(\s*(\{[^}]+\})',
        _re.MULTILINE
    )
    for m in shell_pattern.finditer(script_content):
        coll = m.group(1)
        fields_str = m.group(2)
        fields = _parse_fields_from_json(fields_str)
        if fields:
            script_indexes.append({
                'collection': coll,
                'fields': fields,
                'raw': m.group(0).strip(),
            })

    # PyMongo: db["collection"].create_index([("field", 1)])
    pymongo_pattern = _re.compile(
        r'db\[[\"\'](\w+)[\"\']\]\.create_index\s*\(\s*\[([^\]]+)\]',
        _re.MULTILINE
    )
    for m in pymongo_pattern.finditer(script_content):
        coll = m.group(1)
        tuples_str = m.group(2)
        fields = _parse_pymongo_fields(tuples_str)
        if fields:
            script_indexes.append({
                'collection': coll,
                'fields': fields,
                'raw': m.group(0).strip(),
            })

    # Also try: collection.createIndex pattern (without db. prefix)
    generic_pattern = _re.compile(
        r'(\w+)\.createIndex\s*\(\s*(\{[^}]+\})',
        _re.MULTILINE
    )
    seen_raws = {si['raw'] for si in script_indexes}
    for m in generic_pattern.finditer(script_content):
        raw = m.group(0).strip()
        if raw in seen_raws:
            continue
        coll = m.group(1)
        if coll in ('db', 'collection', 'coll'):
            continue
        fields_str = m.group(2)
        fields = _parse_fields_from_json(fields_str)
        if fields:
            script_indexes.append({
                'collection': coll,
                'fields': fields,
                'raw': raw,
            })

    # Scan the project
    result = _engine.scan_project(resolved_path)
    report = _report_gen.generate_report(result)
    code_indexes = report.get('indexes', [])
    code_suggestions = report.get('suggestions', [])

    # Normalize for comparison
    def norm(fields_dict):
        return frozenset((k, _norm_dir(v)) for k, v in fields_dict.items())

    def _norm_dir(v):
        try:
            return int(v)
        except (ValueError, TypeError):
            return v

    # Build lookup sets
    script_set = {}
    for si in script_indexes:
        key = (si['collection'], norm(si['fields']))
        script_set[key] = si

    code_set = {}
    for ci in code_indexes:
        key = (ci['collection'], norm(ci['fields']))
        code_set[key] = ci

    suggestion_set = {}
    for s in code_suggestions:
        key = (s['collection'], norm(s['fields']))
        suggestion_set[key] = s

    # All code-side indexes (defined + suggested)
    all_code = {}
    all_code.update(code_set)
    all_code.update(suggestion_set)

    # Gaps
    in_script_not_in_code = []
    for key, si in script_set.items():
        if key not in all_code:
            in_script_not_in_code.append({
                **si,
                'status': 'extra_in_script',
            })

    in_code_not_in_script = []
    for key, ci in code_set.items():
        if key not in script_set:
            in_code_not_in_script.append({
                **ci,
                'status': 'missing_from_script',
                'source_type': 'defined',
            })

    suggestions_not_in_script = []
    for key, s in suggestion_set.items():
        if key not in script_set:
            suggestions_not_in_script.append({
                **s,
                'status': 'missing_from_script',
                'source_type': 'suggested',
            })

    in_both = []
    for key, si in script_set.items():
        if key in all_code:
            in_both.append({
                **si,
                'status': 'covered',
            })

    return jsonify({
        'script_indexes_count': len(script_indexes),
        'script_indexes': script_indexes,
        'code_indexes_count': len(code_indexes),
        'code_suggestions_count': len(code_suggestions),
        'gaps': {
            'in_script_not_in_code': in_script_not_in_code,
            'in_code_not_in_script': in_code_not_in_script,
            'suggestions_not_in_script': suggestions_not_in_script,
            'covered': in_both,
        },
        'summary': {
            'script_total': len(script_indexes),
            'code_total': len(code_indexes) + len(code_suggestions),
            'covered': len(in_both),
            'extra_in_script': len(in_script_not_in_code),
            'missing_from_script': len(in_code_not_in_script) + len(suggestions_not_in_script),
        },
    })


def _parse_fields_from_json(fields_str):
    """Parse a JS-style {field: 1, field2: -1} into a dict."""
    import re as _re
    fields = {}
    # Match "field": 1 or field: 1 patterns
    for m in _re.finditer(r'["\']?(\w+)["\']?\s*:\s*(-?\d+|"[^"]*")', fields_str):
        name = m.group(1)
        val = m.group(2).strip('"')
        try:
            fields[name] = int(val)
        except ValueError:
            fields[name] = val
    return fields


def _parse_pymongo_fields(tuples_str):
    """Parse pymongo-style ("field", ASCENDING) tuples into a dict."""
    import re as _re
    fields = {}
    for m in _re.finditer(r'["\'](\w+)["\']\s*,\s*(-?\d+|ASCENDING|DESCENDING|TEXT|HASHED)', tuples_str):
        name = m.group(1)
        val = m.group(2)
        direction_map = {'ASCENDING': 1, 'DESCENDING': -1, 'TEXT': 'text', 'HASHED': 'hashed'}
        if val in direction_map:
            fields[name] = direction_map[val]
        else:
            try:
                fields[name] = int(val)
            except ValueError:
                fields[name] = val
    return fields


@app.route('/api/compare', methods=['POST'])
def compare():
    """Compare code-scanned indexes against a live database instance.

    Supports both MongoDB and PostgreSQL. Auto-detects from the URI:
    - mongodb:// or mongodb+srv:// → uses pymongo
    - postgresql:// or postgres:// → uses psycopg2
    """
    data = request.json or {}
    uri = data.get('uri', '')
    extra_db_names = [d.strip() for d in data.get('db_names', '').split(',') if d.strip()]
    db_type = data.get('db_type', '')  # optional: 'mongodb' or 'postgresql'

    if not uri:
        return jsonify({'error': 'Database URI is required'}), 400

    # Auto-detect database type from URI
    if not db_type:
        if uri.startswith('mongodb://') or uri.startswith('mongodb+srv://'):
            db_type = 'mongodb'
        elif uri.startswith('postgresql://') or uri.startswith('postgres://') or uri.startswith('jdbc:postgresql://'):
            db_type = 'postgresql'
        else:
            return jsonify({'error': 'Cannot detect database type from URI. Use mongodb:// or postgresql:// prefix, or specify db_type.'}), 400

    resolved_path, err, _ = _resolve_project_path(data)
    if err:
        return jsonify({'error': err}), 400

    if db_type == 'postgresql':
        return _compare_postgresql(uri, resolved_path, extra_db_names)
    else:
        return _compare_mongodb(uri, resolved_path, extra_db_names, data)


def _compare_postgresql(uri, resolved_path, extra_schemas):
    """Compare code-scanned indexes against a live PostgreSQL instance."""
    # Strip jdbc: prefix if present
    if uri.startswith('jdbc:'):
        uri = uri[5:]

    # --- Scan the project ---
    result = _engine.scan_project(resolved_path)
    report = _report_gen.generate_report(result)
    suggestions = report.get('suggestions', [])
    code_indexes = report.get('indexes', [])

    # --- Connect to PostgreSQL ---
    try:
        import psycopg2
    except ImportError:
        return jsonify({'error': 'psycopg2 is not installed. Install with: pip install psycopg2-binary'}), 500

    try:
        conn = psycopg2.connect(uri)
        conn.autocommit = True
        cur = conn.cursor()
    except Exception as e:
        return jsonify({'error': f'Failed to connect to PostgreSQL: {str(e)}'}), 400

    try:
        # Query pg_indexes for all user-defined indexes
        schemas_to_check = extra_schemas if extra_schemas else ['public']
        schema_filter = ','.join(f"'{s}'" for s in schemas_to_check)

        cur.execute(f"""
            SELECT schemaname, tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
            {f"AND schemaname IN ({schema_filter})" if extra_schemas else ""}
        """)

        live_indexes = []
        for row in cur.fetchall():
            schemaname, tablename, indexname, indexdef = row
            # Parse columns from indexdef
            fields = _parse_pg_index_columns(indexdef)
            is_unique = 'UNIQUE' in indexdef.upper()
            live_indexes.append({
                'database': schemaname,
                'collection': tablename,
                'name': indexname,
                'fields': fields,
                'unique': is_unique,
                'sparse': False,
                'indexdef': indexdef,
            })

        # Get list of schemas checked
        cur.execute("""
            SELECT DISTINCT schemaname FROM pg_indexes
            WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        """)
        schemas_checked = [row[0] for row in cur.fetchall()]

        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'Error querying PostgreSQL indexes: {str(e)}'}), 500

    # --- Compare ---
    def normalize_fields(fields_dict):
        return frozenset((k.lower(), v) for k, v in fields_dict.items())

    # Build lookup by table
    live_lookup_by_table = {}
    for li in live_indexes:
        table = li['collection']
        live_lookup_by_table.setdefault(table, set())
        live_lookup_by_table[table].add(normalize_fields(li['fields']))

    # Compare suggestions
    missing = []
    existing = []
    for s in suggestions:
        table = s.get('collection', '')
        fields = s.get('fields', {})
        norm = normalize_fields(fields)
        table_indexes = live_lookup_by_table.get(table, set())

        found = norm in table_indexes or any(
            _pg_fields_covered(norm, live_fs) for live_fs in table_indexes
        )
        entry = {**s, 'status': 'exists' if found else 'missing'}
        (existing if found else missing).append(entry)

    # Compare code-defined indexes
    code_missing = []
    code_existing = []
    for ci in code_indexes:
        table = ci.get('collection', '')
        fields = ci.get('fields', {})
        norm = normalize_fields(fields)
        table_indexes = live_lookup_by_table.get(table, set())

        found = norm in table_indexes or any(
            _pg_fields_covered(norm, live_fs) for live_fs in table_indexes
        )
        entry = {**ci, 'status': 'exists' if found else 'missing'}
        (code_existing if found else code_missing).append(entry)

    return jsonify({
        'db_type': 'postgresql',
        'databases_checked': schemas_checked,
        'databases_detected_from_code': [],
        'tenant_patterns': {},
        'live_indexes_count': len(live_indexes),
        'live_indexes': live_indexes,
        'suggestions_total': len(suggestions),
        'suggestions_missing': missing,
        'suggestions_existing': existing,
        'code_indexes_total': len(code_indexes),
        'code_indexes_missing': code_missing,
        'code_indexes_existing': code_existing,
        'esr_analysis': [],
        'esr_violations_count': 0,
        'summary': {
            'missing_suggestions': len(missing),
            'existing_suggestions': len(existing),
            'missing_code_indexes': len(code_missing),
            'existing_code_indexes': len(code_existing),
            'esr_violations': 0,
        },
    })


def _parse_pg_index_columns(indexdef: str) -> dict:
    """Parse column names and directions from a PostgreSQL indexdef string.

    Example: 'CREATE INDEX idx_name ON public.table USING btree (col1, col2 DESC)'
    Returns: {'col1': 1, 'col2': -1}
    """
    import re
    fields = {}
    # Extract the columns part between parentheses
    m = re.search(r'\((.+)\)$', indexdef.strip().rstrip(';'))
    if m:
        cols_str = m.group(1)
        for col in cols_str.split(','):
            col = col.strip()
            if not col:
                continue
            parts = col.split()
            col_name = parts[0].strip('"')
            direction = 1
            if len(parts) > 1 and parts[-1].upper() == 'DESC':
                direction = -1
            fields[col_name] = direction
    return fields


def _pg_fields_covered(suggested_fs, live_fs):
    """Check if suggested PostgreSQL fields are covered by a live index (prefix match)."""
    sugg_names = {f[0] for f in suggested_fs}
    live_names = {f[0] for f in live_fs}
    return sugg_names.issubset(live_names)


def _compare_mongodb(uri, resolved_path, extra_db_names, data):
    """Compare code-scanned indexes against a live MongoDB instance."""

    # --- Scan the project ---
    result = _engine.scan_project(resolved_path)
    report = _report_gen.generate_report(result)
    suggestions = report.get('suggestions', [])
    code_indexes = report.get('indexes', [])
    detected_dbs = report.get('database_names', [])

    # Merge detected DBs with any extra ones the user provided
    all_db_names = list(dict.fromkeys(detected_dbs + extra_db_names))

    # --- Connect to MongoDB ---
    from pymongo import MongoClient
    from pymongo.errors import OperationFailure

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        client.admin.command('ping')
    except Exception as e:
        return jsonify({'error': f'Failed to connect to MongoDB: {str(e)}'}), 400

    try:
        live_indexes = []

        # List all databases on the server
        all_server_dbs = [
            n for n in client.list_database_names()
            if n not in ('admin', 'local', 'config')
        ]

        # Expand code-detected DB names to match tenant-prefixed databases
        # e.g. code says "candidates" -> match "abc_candidates", "xyz_candidates" etc.
        expanded_db_names = set()
        tenant_patterns = {}  # code_db -> list of matched live dbs

        if all_db_names:
            for code_db in all_db_names:
                # Exact match
                if code_db in all_server_dbs:
                    expanded_db_names.add(code_db)
                    tenant_patterns.setdefault(code_db, []).append(code_db)
                # Tenant-prefixed match: *_codeName
                for live_db in all_server_dbs:
                    if live_db.endswith(f'_{code_db}') or live_db.endswith(f'-{code_db}'):
                        expanded_db_names.add(live_db)
                        tenant_patterns.setdefault(code_db, []).append(live_db)
        else:
            # No DBs detected — use all server databases
            expanded_db_names = set(all_server_dbs)

        # Use expanded list for scanning
        dbs_to_check = sorted(expanded_db_names) if expanded_db_names else all_server_dbs

        for dbn in dbs_to_check:
            db = client[dbn]
            try:
                coll_names = db.list_collection_names()
            except OperationFailure:
                continue
            for coll_name in coll_names:
                try:
                    for idx_name, idx_info in db[coll_name].index_information().items():
                        fields = {}
                        for field, direction in idx_info.get('key', []):
                            fields[field] = direction

                        # Skip default _id index
                        if fields == {'_id': 1}:
                            continue

                        live_indexes.append({
                            'database': dbn,
                            'collection': coll_name,
                            'name': idx_name,
                            'fields': fields,
                            'unique': idx_info.get('unique', False),
                            'sparse': idx_info.get('sparse', False),
                        })
                except OperationFailure:
                    continue

        client.close()
    except Exception as e:
        return jsonify({'error': f'Error fetching indexes: {str(e)}'}), 500

    # --- Compare ---
    def normalize_fields(fields_dict):
        return frozenset((k, int(v) if isinstance(v, (int, float)) else v)
                         for k, v in fields_dict.items())

    # Build lookup: (database, collection) -> set of field combos
    live_lookup = {}
    for li in live_indexes:
        key = (li['database'], li['collection'])
        live_lookup.setdefault(key, set())
        live_lookup[key].add(normalize_fields(li['fields']))

    # Also build collection-only lookup (for indexes without db info)
    live_lookup_by_coll = {}
    for li in live_indexes:
        coll = li['collection']
        live_lookup_by_coll.setdefault(coll, set())
        live_lookup_by_coll[coll].add(normalize_fields(li['fields']))

    # Build set of collections that actually exist in the live DB
    # and check document counts to filter out tiny collections
    live_collections = set()  # all collection names that exist
    small_collections = set()  # collections with < 1000 documents
    MIN_DOCS_FOR_INDEX = 50

    for dbn in dbs_to_check:
        db = client[dbn]
        try:
            for coll_name in db.list_collection_names():
                live_collections.add(coll_name)
                # Check doc count — skip if too small to benefit from indexing
                try:
                    count = db[coll_name].estimated_document_count()
                    if count < MIN_DOCS_FOR_INDEX:
                        small_collections.add(coll_name)
                except Exception:
                    pass
        except Exception:
            pass

    def _check_exists(coll, db, fields_dict):
        """Check if an index exists in live DB, matching by (db, coll) or just coll."""
        norm = normalize_fields(fields_dict)
        # Try exact (db, coll) match first
        if db:
            exact = live_lookup.get((db, coll), set())
            if norm in exact:
                return True
            # Check prefix/superset coverage
            for live_fs in exact:
                if _is_covered(norm, live_fs):
                    return True
        # Fall back to collection-only match (across all DBs)
        coll_indexes = live_lookup_by_coll.get(coll, set())
        if norm in coll_indexes:
            return True
        for live_fs in coll_indexes:
            if _is_covered(norm, live_fs):
                return True
        return False

    def _is_covered(suggested_fs, live_fs):
        """Check if suggested fields are covered by a live index (prefix match)."""
        sugg_names = {f[0] for f in suggested_fs}
        live_names = {f[0] for f in live_fs}
        if sugg_names.issubset(live_names):
            live_ordered = [f[0] for f in sorted(live_fs)]
            sugg_ordered = [f[0] for f in sorted(suggested_fs)]
            return all(f in live_ordered[:len(sugg_ordered)+2] for f in sugg_ordered)
        return False

    # Compare suggestions
    missing = []
    existing = []
    skipped = []
    for s in suggestions:
        coll = s.get('collection', '')

        # Skip collections that don't exist in the live DB
        if coll not in live_collections:
            skipped.append({**s, 'status': 'skipped', 'reason': 'collection_not_found'})
            continue

        # Skip small collections (< 1000 docs) — not worth indexing
        if coll in small_collections:
            skipped.append({**s, 'status': 'skipped', 'reason': 'small_collection'})
            continue

        found = _check_exists(coll, None, s.get('fields', {}))
        entry = {**s, 'status': 'exists' if found else 'missing'}
        (existing if found else missing).append(entry)

    # Compare code-defined indexes
    code_missing = []
    code_existing = []
    for ci in code_indexes:
        coll = ci.get('collection', '')
        db = ci.get('database', '')

        # Skip collections that don't exist in the live DB
        if coll not in live_collections:
            continue

        # Skip small collections
        if coll in small_collections:
            continue

        found = _check_exists(coll, db, ci.get('fields', {}))
        entry = {**ci, 'status': 'exists' if found else 'missing'}
        (code_existing if found else code_missing).append(entry)

    # --- ESR Analysis on live compound indexes ---
    # Build a map of field usage types from the code scan
    # so we can evaluate if live compound indexes follow ESR
    field_type_map = {}  # (collection, field) -> set of usage types
    for s in suggestions:
        coll = s.get('collection', '')
        for field_name in s.get('fields', {}).keys():
            field_type_map.setdefault((coll, field_name), set())
    # Also gather from the raw scan result if available
    if hasattr(result, 'suggestions'):
        for sug in result.suggestions:
            for field_name in sug.fields.keys():
                field_type_map.setdefault((sug.collection, field_name), set())

    # Analyze query patterns to get field usage types
    from index_scanner_mcp.query_analyzer import QueryPatternAnalyzer
    _qa = QueryPatternAnalyzer()
    try:
        import os as _os
        for root, dirs, files in _os.walk(resolved_path):
            dirs[:] = [d for d in dirs if d not in {'node_modules', '.git', 'target', 'build', '__pycache__', 'venv', '.env'}]
            for fname in files:
                if fname.endswith('.java'):
                    fpath = _os.path.join(root, fname)
                    try:
                        with open(fpath, encoding='utf-8') as fh:
                            content = fh.read()
                        from index_scanner_mcp.constant_resolver import ConstantResolver
                        usages = _qa.extract_query_fields(content, _engine.constant_resolver.constants.get(
                            _os.path.splitext(_os.path.basename(fpath))[0], {}
                        ) if hasattr(_engine, 'constant_resolver') else {}, fpath)
                        for u in usages:
                            key = (u.collection, u.field)
                            field_type_map.setdefault(key, set()).add(u.usage_type)
                    except Exception:
                        pass
    except Exception:
        pass

    esr_analysis = []
    for li in live_indexes:
        fields_list = list(li['fields'].keys())
        if len(fields_list) < 2:
            continue  # ESR only applies to compound indexes

        coll = li['collection']

        # Classify each field in the live index
        field_classes = []
        for f in fields_list:
            types = field_type_map.get((coll, f), set())
            if 'sort' in types:
                field_classes.append('S')
            elif 'filter_range' in types:
                field_classes.append('R')
            elif types:  # filter_equality or filter
                field_classes.append('E')
            else:
                field_classes.append('?')  # unknown usage

        # Check ESR compliance: E* S* R* pattern
        current_order = ''.join(field_classes)
        is_esr = True
        phase = 'E'
        phase_order = {'E': 0, 'S': 1, 'R': 2, '?': 3}
        for cls in field_classes:
            if cls == '?':
                continue
            if phase_order.get(cls, 3) < phase_order.get(phase, 0):
                is_esr = False
                break
            phase = cls

        # Build optimal ESR order
        e_fields = [f for f, c in zip(fields_list, field_classes) if c == 'E']
        s_fields = [f for f, c in zip(fields_list, field_classes) if c == 'S']
        r_fields = [f for f, c in zip(fields_list, field_classes) if c == 'R']
        u_fields = [f for f, c in zip(fields_list, field_classes) if c == '?']
        optimal_order = e_fields + s_fields + r_fields + u_fields

        esr_analysis.append({
            'database': li['database'],
            'collection': coll,
            'index_name': li['name'],
            'current_fields': fields_list,
            'field_classes': field_classes,
            'current_order': current_order,
            'is_esr_compliant': is_esr,
            'optimal_esr_order': optimal_order,
            'recommendation': None if is_esr else f"Reorder to ESR: {' → '.join(f'{f}({c})' for f, c in zip(optimal_order, sorted(field_classes, key=lambda x: phase_order.get(x, 3))))}",
        })

    esr_violations = [e for e in esr_analysis if not e['is_esr_compliant']]

    return jsonify({
        'db_type': 'mongodb',
        'databases_checked': dbs_to_check,
        'databases_detected_from_code': detected_dbs,
        'tenant_patterns': {k: sorted(set(v)) for k, v in tenant_patterns.items()} if tenant_patterns else {},
        'live_indexes_count': len(live_indexes),
        'live_indexes': live_indexes,
        'suggestions_total': len(suggestions),
        'suggestions_missing': missing,
        'suggestions_existing': existing,
        'code_indexes_total': len(code_indexes),
        'code_indexes_missing': code_missing,
        'code_indexes_existing': code_existing,
        'esr_analysis': esr_analysis,
        'esr_violations_count': len(esr_violations),
        'summary': {
            'missing_suggestions': len(missing),
            'existing_suggestions': len(existing),
            'missing_code_indexes': len(code_missing),
            'existing_code_indexes': len(code_existing),
            'esr_violations': len(esr_violations),
        },
    })


@app.route('/api/list-databases', methods=['POST'])
def list_databases():
    """List databases on a MongoDB instance, optionally filtered by a pattern.

    If 'pattern' is provided (e.g. 'candidates'), returns databases matching
    *_candidates or candidates pattern for multi-tenant selection.
    """
    from pymongo import MongoClient

    data = request.json or {}
    uri = data.get('uri', '')
    pattern = data.get('pattern', '').strip()

    if not uri:
        return jsonify({'error': 'MongoDB URI is required'}), 400

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        all_dbs = [n for n in client.list_database_names() if n not in ('admin', 'local', 'config')]
        client.close()
    except Exception as e:
        return jsonify({'error': f'Failed to connect: {str(e)}'}), 400

    if pattern:
        # Match databases ending with the pattern (e.g. *_candidates)
        # or exactly matching the pattern
        matched = [d for d in all_dbs if d == pattern or d.endswith(f'_{pattern}') or d.endswith(pattern)]
        return jsonify({'databases': sorted(all_dbs), 'matched': sorted(matched), 'pattern': pattern})

    return jsonify({'databases': sorted(all_dbs)})


@app.route('/api/create-indexes', methods=['POST'])
def create_indexes():
    """Create indexes on one or more MongoDB databases.

    Supports multi-tenant: pass 'db_names' as a list to create the same
    indexes across multiple databases (e.g. all *_candidates databases).
    """
    from pymongo import MongoClient, ASCENDING, DESCENDING
    from pymongo.errors import OperationFailure

    data = request.json or {}
    uri = data.get('uri', '')
    db_name = data.get('db_name', '')
    db_names = data.get('db_names', [])
    indexes = data.get('indexes', [])

    if not uri:
        return jsonify({'error': 'MongoDB URI is required'}), 400
    if not db_name and not db_names:
        return jsonify({'error': 'Database name is required'}), 400
    if not indexes:
        return jsonify({'error': 'No indexes provided'}), 400

    # Support single db_name or list of db_names
    target_dbs = db_names if db_names else [db_name]

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
    except Exception as e:
        return jsonify({'error': f'Failed to connect to MongoDB: {str(e)}'}), 400

    all_results = []

    for dbn in target_dbs:
        db = client[dbn]
        for idx in indexes:
            collection = idx.get('collection', '')
            fields = idx.get('fields', {})
            unique = idx.get('unique', False)
            sparse = idx.get('sparse', False)
            expire = idx.get('expire_after_seconds')

            if not collection or not fields:
                all_results.append({
                    'database': dbn, 'collection': collection, 'fields': fields,
                    'status': 'error', 'message': 'Missing collection or fields',
                })
                continue

            key_list = []
            for field_name, direction in fields.items():
                if isinstance(direction, str):
                    key_list.append((field_name, direction))
                elif direction == -1:
                    key_list.append((field_name, DESCENDING))
                else:
                    key_list.append((field_name, ASCENDING))

            kwargs = {}
            if unique: kwargs['unique'] = True
            if sparse: kwargs['sparse'] = True
            if expire is not None: kwargs['expireAfterSeconds'] = expire

            try:
                index_name = db[collection].create_index(key_list, **kwargs)
                all_results.append({
                    'database': dbn, 'collection': collection, 'fields': fields,
                    'status': 'created', 'index_name': index_name,
                    'message': f'Index "{index_name}" created on {dbn}',
                })
            except OperationFailure as e:
                if 'already exists' in str(e).lower() or 'IndexOptionsConflict' in str(e):
                    all_results.append({
                        'database': dbn, 'collection': collection, 'fields': fields,
                        'status': 'exists', 'message': f'Already exists on {dbn}',
                    })
                else:
                    all_results.append({
                        'database': dbn, 'collection': collection, 'fields': fields,
                        'status': 'error', 'message': str(e),
                    })
            except Exception as e:
                all_results.append({
                    'database': dbn, 'collection': collection, 'fields': fields,
                    'status': 'error', 'message': str(e),
                })

    client.close()

    created = sum(1 for r in all_results if r['status'] == 'created')
    existed = sum(1 for r in all_results if r['status'] == 'exists')
    errors = sum(1 for r in all_results if r['status'] == 'error')

    return jsonify({
        'results': all_results,
        'databases_targeted': target_dbs,
        'summary': {
            'total': len(all_results),
            'created': created,
            'already_existed': existed,
            'errors': errors,
            'databases': len(target_dbs),
        },
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)


# ---------------------------------------------------------------------------
# Team Scan Endpoint — scan all services for a team using service catalog,
# helm charts, and build properties from Bitbucket
# ---------------------------------------------------------------------------

# Default service catalog path (relative to project root)
# Default service catalog path. This file is org-specific and NOT shipped —
# create it from service_catalog/service_catalog.csv.example. Override per
# request with the "catalog_path" field.
_SERVICE_CATALOG_PATH = os.environ.get(
    'SERVICE_CATALOG_PATH',
    os.path.join(
        os.path.dirname(__file__), '..', '..', 'service_catalog',
        'service_catalog.csv',
    ),
)


@app.route('/api/scan-team', methods=['POST'])
def scan_team():
    """Scan all services for a team: discover repos, read helm for DB creds,
    scan code for indexes needed.

    Request JSON:
        {
            "team_name": "analytics",
            "catalog_path": "/path/to/catalog.csv"  (optional, uses default)
            "auth_token": "..."  (optional, for HTTPS Bitbucket auth)
        }

    Response JSON:
        {
            "team_name": "analytics",
            "services": [...],
            "helm_context": {...},
            "scan_results": [...],
            "errors": [...]
        }
    """
    data = request.get_json(force=True)
    team_name = data.get('team_name', '').strip()

    if not team_name:
        return jsonify({'error': 'team_name is required'}), 400

    catalog_path = data.get('catalog_path', _SERVICE_CATALOG_PATH)
    auth_token = data.get('auth_token')

    errors = []
    results = {
        'team_name': team_name,
        'services': [],
        'helm_context': None,
        'scan_results': [],
        'errors': [],
    }

    from index_scanner_mcp.pg.service_catalog import ServiceCatalog
    from index_scanner_mcp.pg.repo_cloner import RepoCloner
    from index_scanner_mcp.pg.helm_context_loader import HelmContextLoader

    # 1. Load service catalog and filter by team
    try:
        catalog = ServiceCatalog(catalog_path)
        team_services = catalog.filter_by_team(team_name)
    except FileNotFoundError:
        return jsonify({'error': f'Service catalog not found: {catalog_path}'}), 404
    except Exception as e:
        return jsonify({'error': f'Failed to load catalog: {e}'}), 500

    if not team_services:
        return jsonify({'error': f'No services found for team: {team_name}'}), 404

    results['services'] = [
        {
            'service_name': s.service_name,
            'namespace': s.namespace,
            'language': s.language,
            'db_service': s.db_service,
            'uri_location': s.uri_location,
            'has_postgres': ServiceCatalog.has_postgres(s.db_service),
            'has_mongodb': ServiceCatalog.has_mongodb(s.db_service),
        }
        for s in team_services
    ]

    # 2. Clone helm charts / buildproperties for the team
    cloner = RepoCloner(auth_token=auth_token)

    helm_path = cloner.clone_helm_charts(team_name)
    if not helm_path:
        errors.append(f"Could not clone helm charts or buildproperties for team '{team_name}'")

    # 3. Load helm context (DB connections from values files)
    helm_context_data = None
    if helm_path:
        catalog_entries = [
            (s.service_name, s.uri_location)
            for s in team_services
            if s.uri_location and 'service not found' not in s.uri_location.lower()
        ]

        loader = HelmContextLoader(
            team_name=team_name,
            helm_repo_base=str(Path(helm_path).parent),
            catalog_entries=catalog_entries,
        )
        helm_context = loader.load()

        helm_context_data = {
            'team_name': helm_context.team_name,
            'helm_repo_path': helm_context.helm_repo_path,
            'total_services_with_values': len(helm_context.services),
            'postgres_connections': [
                {
                    'host': db.host,
                    'port': db.port,
                    'db_name': db.db_name,
                    'connection_string': db.connection_string[:80] + '...' if len(db.connection_string) > 80 else db.connection_string,
                    'env_var': db.env_var_name,
                    'source_file': db.source_file,
                }
                for db in helm_context.postgres_databases
            ],
            'mongo_connections': [
                {
                    'host': db.host,
                    'port': db.port,
                    'db_name': db.db_name,
                    'connection_string': db.connection_string[:80] + '...' if len(db.connection_string) > 80 else db.connection_string,
                    'env_var': db.env_var_name,
                    'source_file': db.source_file,
                }
                for db in helm_context.mongo_databases
            ],
            'services_context': {
                svc_name: {
                    'values_file': svc_ctx.values_file,
                    'databases': [
                        {'type': db.db_type, 'host': db.host, 'db_name': db.db_name}
                        for db in svc_ctx.databases
                    ],
                }
                for svc_name, svc_ctx in helm_context.services.items()
            },
            'errors': helm_context.errors,
        }
        errors.extend(helm_context.errors)

    results['helm_context'] = helm_context_data

    # 4. Scan service repos for index patterns (existing MongoDB scanner)
    scan_results = []
    services_with_repos = [
        s for s in team_services
        if s.uri_location
        and 'service not found' not in s.uri_location.lower()
        and (ServiceCatalog.has_mongodb(s.db_service) or ServiceCatalog.has_postgres(s.db_service))
    ]

    for service in services_with_repos[:10]:  # Limit to 10 to avoid timeout
        svc_name = service.service_name
        # Derive the likely Bitbucket repo name from service name
        # Strip org-specific service-name prefixes (see SERVICE_NAME_PREFIXES)
        repo_name = svc_name
        for prefix in SERVICE_NAME_PREFIXES:
            if repo_name.startswith(prefix):
                repo_name = repo_name[len(prefix):]
                break

        repo_path = cloner.clone_service_repo(repo_name)
        if not repo_path:
            # Try with the original service name
            repo_path = cloner.clone_service_repo(svc_name)

        if not repo_path:
            errors.append(f"Could not clone repo for service '{svc_name}' (tried: {repo_name}, {svc_name})")
            continue

        # Scan with the existing MongoDB scanner engine
        try:
            scan_result = _engine.scan_project(repo_path)
            scan_results.append({
                'service_name': svc_name,
                'repo_name': repo_name,
                'db_type': service.db_service,
                'indexes_found': len(scan_result.indexes),
                'suggestions': len(scan_result.suggestions),
                'files_scanned': scan_result.files_scanned,
                'indexes': [
                    {
                        'collection': idx.collection,
                        'fields': idx.fields,
                        'index_type': idx.index_type,
                        'source_file': idx.source_file,
                    }
                    for idx in scan_result.indexes[:20]  # Limit output
                ],
                'top_suggestions': [
                    {
                        'collection': sug.collection,
                        'fields': sug.suggested_fields,
                        'reason': sug.reason,
                        'priority': sug.priority,
                    }
                    for sug in scan_result.suggestions[:10]
                ],
                'errors': scan_result.errors,
            })
        except Exception as e:
            errors.append(f"Scan failed for '{svc_name}': {e}")
            scan_results.append({
                'service_name': svc_name,
                'repo_name': repo_name,
                'error': str(e),
            })

    results['scan_results'] = scan_results
    results['errors'] = errors

    # 5. Cleanup cloned repos (async-safe — runs in background after response)
    # For now leave cached; they'll be reused on next scan.
    # cloner.cleanup_all()

    return jsonify(results)


@app.route('/api/teams', methods=['GET'])
def list_teams():
    """List all unique teams from the service catalog."""
    from index_scanner_mcp.pg.service_catalog import ServiceCatalog
    try:
        catalog = ServiceCatalog(_SERVICE_CATALOG_PATH)
        entries = catalog.load()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    teams = sorted(set(e.team for e in entries if e.team.strip()))
    return jsonify({'teams': teams, 'total': len(teams)})


@app.route('/api/team-services/<team_name>', methods=['GET'])
def get_team_services(team_name):
    """Get all services for a specific team."""
    from index_scanner_mcp.pg.service_catalog import ServiceCatalog
    try:
        catalog = ServiceCatalog(_SERVICE_CATALOG_PATH)
        services = catalog.filter_by_team(team_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({
        'team_name': team_name,
        'total': len(services),
        'services': [
            {
                'service_name': s.service_name,
                'namespace': s.namespace,
                'language': s.language,
                'db_service': s.db_service,
                'uri_location': s.uri_location,
                'has_postgres': ServiceCatalog.has_postgres(s.db_service),
                'has_mongodb': ServiceCatalog.has_mongodb(s.db_service),
            }
            for s in services
        ],
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
