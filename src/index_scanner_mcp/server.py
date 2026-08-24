"""
MCP Server: Index Scanner
Scans project source code for database index definitions
(MongoDB, SQL, Elasticsearch, etc.)
"""

import os
import re
import json
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# Import modular components
from index_scanner_mcp.scanner_engine import ScannerEngine
from index_scanner_mcp.script_generator import ScriptGenerator
from index_scanner_mcp.report_generator import ReportGenerator

mcp = FastMCP("index-scanner")

# Instantiate modular components
_scanner_engine = ScannerEngine()
_script_generator = ScriptGenerator()
_report_generator = ReportGenerator()

# --- Index patterns ---

JAVA_INDEX_PATTERNS = [
    # MongoDB Spring Data annotations
    (r'@Indexed\b(?:\(([^)]*)\))?', 'MongoDB @Indexed'),
    (r'@CompoundIndex\b(?:\(([^)]*)\))?', 'MongoDB @CompoundIndex'),
    (r'@CompoundIndexes\b', 'MongoDB @CompoundIndexes'),
    (r'@TextIndexed\b', 'MongoDB @TextIndexed'),
    (r'@GeoSpatialIndexed\b', 'MongoDB @GeoSpatialIndexed'),
    (r'@HashIndexed\b', 'MongoDB @HashIndexed'),
    (r'@WildcardIndexed\b', 'MongoDB @WildcardIndexed'),
    # Programmatic MongoDB index creation
    (r'\.createIndex\s*\(([^)]*)\)', 'MongoDB createIndex()'),
    (r'\.ensureIndex\s*\(([^)]*)\)', 'MongoDB ensureIndex()'),
    (r'Index\s*\(\s*\)\s*\.on\s*\(([^)]*)\)', 'MongoDB Index().on()'),
    (r'new\s+Index\b', 'MongoDB new Index'),
    (r'IndexOperations', 'MongoDB IndexOperations'),
    # JPA / Hibernate
    (r'@Index\b(?:\(([^)]*)\))?', 'JPA @Index'),
    (r'@Table\b[^)]*indexes\s*=', 'JPA @Table indexes'),
    # SQL in code
    (r'CREATE\s+(?:UNIQUE\s+)?INDEX\b', 'SQL CREATE INDEX'),
    # Elasticsearch
    (r'@Setting\b.*index', 'Elasticsearch @Setting'),
    (r'@Document\b.*index', 'Elasticsearch @Document index'),
    (r'CreateIndexRequest', 'Elasticsearch CreateIndexRequest'),
]

PYTHON_INDEX_PATTERNS = [
    (r'create_index\s*\(([^)]*)\)', 'MongoDB create_index()'),
    (r'ensure_index\s*\(([^)]*)\)', 'MongoDB ensure_index()'),
    (r'create_indexes\s*\(', 'MongoDB create_indexes()'),
    (r'IndexModel\s*\(', 'MongoDB IndexModel'),
    (r'CREATE\s+(?:UNIQUE\s+)?INDEX\b', 'SQL CREATE INDEX'),
    (r'db\..*\.createIndex\s*\(', 'MongoDB shell createIndex'),
    (r'index\s*=\s*True', 'ORM index=True'),
    (r'Index\s*\(', 'Index definition'),
]

CONFIG_INDEX_PATTERNS = [
    (r'index[._-]', 'Index config reference'),
    (r'CREATE\s+(?:UNIQUE\s+)?INDEX\b', 'SQL CREATE INDEX'),
    (r'ensureIndex', 'MongoDB ensureIndex'),
    (r'createIndex', 'MongoDB createIndex'),
]

SCAN_EXTENSIONS = {
    '.java': JAVA_INDEX_PATTERNS,
    '.kt': JAVA_INDEX_PATTERNS,
    '.py': PYTHON_INDEX_PATTERNS,
    '.sql': CONFIG_INDEX_PATTERNS,
    '.xml': CONFIG_INDEX_PATTERNS,
    '.yaml': CONFIG_INDEX_PATTERNS,
    '.yml': CONFIG_INDEX_PATTERNS,
    '.json': CONFIG_INDEX_PATTERNS,
    '.properties': CONFIG_INDEX_PATTERNS,
    '.conf': CONFIG_INDEX_PATTERNS,
    '.js': PYTHON_INDEX_PATTERNS,
    '.ts': PYTHON_INDEX_PATTERNS,
}

SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.idea', '.vscode', 'target', 'build', 'dist', '.gradle', 'venv', '.env'}


def scan_file(filepath: str) -> list[dict]:
    """Scan a single file for index definitions."""
    ext = Path(filepath).suffix.lower()
    patterns = SCAN_EXTENSIONS.get(ext)
    if not patterns:
        return []

    results = []
    try:
        with open(filepath, 'r', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('#'):
            continue
        for pattern, index_type in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                # Grab surrounding context (2 lines before/after)
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 2)
                context = ''.join(lines[start:end]).strip()

                results.append({
                    'file': filepath,
                    'line': line_num,
                    'type': index_type,
                    'match': stripped,
                    'details': match.group(1) if match.lastindex else None,
                    'context': context,
                })
                break  # one match per line is enough

    return results


def scan_directory(directory: str) -> list[dict]:
    """Recursively scan a directory for index definitions."""
    all_results = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext in SCAN_EXTENSIONS:
                filepath = os.path.join(root, fname)
                all_results.extend(scan_file(filepath))
    return all_results


# --- MCP Tools ---

@mcp.tool()
def scan_indexes(path: str) -> str:
    """
    Scan a project directory or file for database index definitions.
    Detects MongoDB, JPA/Hibernate, SQL, and Elasticsearch indexes
    from annotations, programmatic calls, and config files.

    Args:
        path: Absolute or relative path to a project directory or file to scan.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    if target.is_file():
        results = scan_file(str(target))
    else:
        results = scan_directory(str(target))

    if not results:
        return json.dumps({
            "message": "No database index definitions found.",
            "path": str(target),
            "files_scanned": sum(1 for _ in target.rglob('*') if _.is_file()) if target.is_dir() else 1,
        }, indent=2)

    # Group by type
    by_type = {}
    for r in results:
        t = r['type']
        by_type.setdefault(t, []).append(r)

    return json.dumps({
        "path": str(target),
        "total_indexes_found": len(results),
        "by_type": {k: len(v) for k, v in by_type.items()},
        "indexes": results,
    }, indent=2)


@mcp.tool()
def scan_multiple_projects(paths: list[str]) -> str:
    """
    Scan multiple project directories for database index definitions
    and return a combined report.

    Args:
        paths: List of project directory paths to scan.
    """
    combined = {}
    for p in paths:
        target = Path(p).resolve()
        if not target.exists():
            combined[p] = {"error": f"Path not found: {p}"}
            continue

        results = scan_directory(str(target)) if target.is_dir() else scan_file(str(target))
        by_type = {}
        for r in results:
            by_type.setdefault(r['type'], []).append(r)

        combined[p] = {
            "total_indexes_found": len(results),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "indexes": results,
        }

    total = sum(v.get("total_indexes_found", 0) for v in combined.values() if isinstance(v, dict))
    return json.dumps({
        "total_across_all_projects": total,
        "projects": combined,
    }, indent=2)


@mcp.tool()
def search_indexes(path: str, query: str) -> str:
    """
    Search for specific index patterns in a project.
    Filters results by a keyword (e.g. 'mongo', 'compound', 'unique', a field name).

    Args:
        path: Project directory or file path to scan.
        query: Keyword to filter index results (case-insensitive).
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    results = scan_directory(str(target)) if target.is_dir() else scan_file(str(target))
    q = query.lower()
    filtered = [
        r for r in results
        if q in r['type'].lower()
        or q in r['match'].lower()
        or (r['details'] and q in r['details'].lower())
        or q in r['context'].lower()
    ]

    return json.dumps({
        "path": str(target),
        "query": query,
        "matches": len(filtered),
        "indexes": filtered,
    }, indent=2)


@mcp.tool()
def export_index_report(path: str, output_file: str = "index_report.json") -> str:
    """
    Scan a project and export a structured JSON report of all index definitions.

    Args:
        path: Project directory to scan.
        output_file: Output file path for the report (default: index_report.json).
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    results = scan_directory(str(target)) if target.is_dir() else scan_file(str(target))

    by_type = {}
    for r in results:
        by_type.setdefault(r['type'], []).append(r)

    by_file = {}
    for r in results:
        by_file.setdefault(r['file'], []).append(r)

    report = {
        "project": str(target),
        "summary": {
            "total_indexes": len(results),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "files_with_indexes": len(by_file),
        },
        "by_file": {k: v for k, v in by_file.items()},
        "all_indexes": results,
    }

    out = Path(output_file).resolve()
    with open(out, 'w') as f:
        json.dump(report, f, indent=2)

    return json.dumps({
        "message": f"Report exported to {out}",
        "summary": report["summary"],
    }, indent=2)


# --- Query pattern analysis for index suggestions ---

# Patterns that extract field names from query constructions
QUERY_FIELD_PATTERNS_JAVA = [
    # new BasicDBObject("fieldName", value)
    (r'new\s+BasicDBObject\s*\(\s*"([^"]+)"\s*,', 'filter'),
    # new BasicDBObject(AppConstants.FIELD, value) — we capture the constant name
    (r'new\s+BasicDBObject\s*\(\s*AppConstants\.(\w+)\s*,', 'filter_constant'),
    # new Document("fieldName", value)
    (r'new\s+Document\s*\(\s*"([^"]+)"\s*,', 'filter'),
    # .append("fieldName", value)
    (r'\.append\s*\(\s*"([^"]+)"\s*,', 'filter'),
    # queryMap.put("fieldName", value) or put(AppConstants.X, ...)
    (r'(?:queryMap|query|filterMap|filter|searchQuery|findQuery)\s*\.put\s*\(\s*"([^"]+)"\s*,', 'filter'),
    (r'(?:queryMap|query|filterMap|filter|searchQuery|findQuery)\s*\.put\s*\(\s*AppConstants\.(\w+)\s*,', 'filter_constant'),
    # Filters.eq("field", val), Filters.gt, Filters.in, etc.
    (r'Filters\.\w+\s*\(\s*"([^"]+)"', 'filter'),
    # sort: new BasicDBObject("field", 1/-1) in sort context
    (r'\.sort\s*\(\s*new\s+BasicDBObject\s*\(\s*"([^"]+)"', 'sort'),
    # Sorts.ascending/descending("field")
    (r'Sorts\.\w+\s*\(\s*"([^"]+)"', 'sort'),
    # Aggregation: "$match", "$sort", "$group"
    (r'\$match', 'agg_match_marker'),
    (r'\$sort', 'agg_sort_marker'),
    (r'\$group', 'agg_group_marker'),
]

QUERY_FIELD_PATTERNS_PYTHON = [
    # collection.find({"field": value})
    (r'["\'](\w+)["\']\s*:', 'filter'),
    # create_index("field") or create_index([("field", 1)])
    (r'create_index\s*\(\s*["\'](\w+)["\']', 'existing_index'),
    (r'create_index\s*\(\s*\[\s*\(\s*["\'](\w+)["\']', 'existing_index'),
    # $match, $sort
    (r'\$match', 'agg_match_marker'),
    (r'\$sort', 'agg_sort_marker'),
]

# Patterns to detect DB operations (what kind of query)
OPERATION_PATTERNS_JAVA = [
    (r'\.find\s*\(', 'find'),
    (r'\.findOne\s*\(', 'find'),
    (r'\.aggregate\s*\(', 'aggregate'),
    (r'\.updateOne\s*\(', 'update'),
    (r'\.updateMany\s*\(', 'update'),
    (r'\.UpdateOne\s*\(', 'update'),
    (r'\.deleteMany\s*\(', 'delete'),
    (r'\.deleteOne\s*\(', 'delete'),
    (r'\.countDocuments\s*\(', 'count'),
    (r'\.count\s*\(', 'count'),
    (r'\.distinct\s*\(', 'distinct'),
    (r'\.sort\s*\(', 'sort'),
]

OPERATION_PATTERNS_PYTHON = [
    (r'\.find\s*\(', 'find'),
    (r'\.find_one\s*\(', 'find'),
    (r'\.aggregate\s*\(', 'aggregate'),
    (r'\.update_one\s*\(', 'update'),
    (r'\.update_many\s*\(', 'update'),
    (r'\.delete_many\s*\(', 'delete'),
    (r'\.delete_one\s*\(', 'delete'),
    (r'\.count_documents\s*\(', 'count'),
    (r'\.distinct\s*\(', 'distinct'),
    (r'\.sort\s*\(', 'sort'),
]

# Collection name patterns
COLLECTION_PATTERNS = [
    # getCollection("name") or get_collection("name")
    (r'getCollection\s*\(\s*"([^"]+)"', 'direct'),
    (r'getCollection\s*\(\s*AppConstants\.(\w+)', 'constant'),
    (r'get_collection\s*\(\s*["\'](\w+)["\']', 'direct'),
    # db["collection"] or db.collection
    (r'db\s*\[\s*["\'](\w+)["\']\s*\]', 'direct'),
    (r'db\.(\w+)\.(?:find|aggregate|update|delete|insert|count)', 'direct'),
    # MongoCollection variable assignments
    (r'MongoCollection.*=.*getCollection\s*\(\s*"([^"]+)"', 'direct'),
    (r'MongoCollection.*=.*getCollection\s*\(\s*AppConstants\.(\w+)', 'constant'),
]

# Fields to ignore (MongoDB operators, common non-field keys)
IGNORE_FIELDS = {
    '$ne', '$gt', '$gte', '$lt', '$lte', '$in', '$nin', '$exists', '$regex',
    '$and', '$or', '$not', '$nor', '$set', '$unset', '$inc', '$push', '$pull',
    '$addToSet', '$match', '$sort', '$group', '$project', '$lookup', '$unwind',
    '$limit', '$skip', '$count', '$options', '$elemMatch', '$size', '$type',
    '$all', '$text', '$search', '$meta', '$slice', '$each', '$position',
    '_id', '$eq', '$mod', '$where', '$geoNear', '$geoWithin',
    '$nearSphere', '$near', '$maxDistance', '$minDistance',
}


def analyze_query_patterns(filepath: str) -> list[dict]:
    """Analyze a source file for MongoDB query patterns and extract queried fields."""
    ext = Path(filepath).suffix.lower()

    if ext in ('.java', '.kt'):
        field_patterns = QUERY_FIELD_PATTERNS_JAVA
        op_patterns = OPERATION_PATTERNS_JAVA
    elif ext in ('.py', '.js', '.ts'):
        field_patterns = QUERY_FIELD_PATTERNS_PYTHON
        op_patterns = OPERATION_PATTERNS_PYTHON
    else:
        return []

    try:
        with open(filepath, 'r', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return []

    content = ''.join(lines)
    findings = []

    # Detect collections used in this file
    collections_in_file = []
    for pattern, ptype in COLLECTION_PATTERNS:
        for m in re.finditer(pattern, content):
            coll_name = m.group(1)
            collections_in_file.append({
                'name': coll_name,
                'type': ptype,
                'pos': m.start(),
            })

    # Detect operations and their locations
    operations = []
    for pattern, op_type in op_patterns:
        for m in re.finditer(pattern, content):
            line_num = content[:m.start()].count('\n') + 1
            operations.append({
                'type': op_type,
                'line': line_num,
                'pos': m.start(),
            })

    # Scan for queried fields line by line
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('#') or stripped.startswith('*'):
            continue

        for pattern, field_type in field_patterns:
            for m in re.finditer(pattern, line):
                field_name = m.group(1) if m.lastindex else None
                if not field_name:
                    continue
                # Skip MongoDB operators and markers
                if field_name.startswith('$') or field_name in IGNORE_FIELDS:
                    continue
                if field_type in ('agg_match_marker', 'agg_sort_marker', 'agg_group_marker'):
                    continue
                if field_type == 'existing_index':
                    continue

                # Find nearest operation context
                nearest_op = None
                for op in operations:
                    if op['line'] <= line_num and (nearest_op is None or op['line'] > nearest_op['line']):
                        # Only consider operations within ~50 lines
                        if line_num - op['line'] < 50:
                            nearest_op = op

                # Find nearest collection
                char_pos = sum(len(lines[i]) for i in range(line_num - 1))
                nearest_coll = None
                for coll in collections_in_file:
                    if coll['pos'] <= char_pos and (nearest_coll is None or coll['pos'] > nearest_coll['pos']):
                        nearest_coll = coll

                # Context lines
                start = max(0, line_num - 3)
                end = min(len(lines), line_num + 2)
                context = ''.join(lines[start:end]).strip()

                findings.append({
                    'file': filepath,
                    'line': line_num,
                    'field': field_name,
                    'usage': field_type.replace('_constant', ''),
                    'operation': nearest_op['type'] if nearest_op else 'unknown',
                    'collection': nearest_coll['name'] if nearest_coll else 'unknown',
                    'collection_type': nearest_coll['type'] if nearest_coll else 'unknown',
                    'context': context,
                })

    return findings


def analyze_directory(directory: str) -> list[dict]:
    """Recursively analyze a directory for query patterns."""
    all_findings = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext in ('.java', '.kt', '.py', '.js', '.ts'):
                filepath = os.path.join(root, fname)
                all_findings.extend(analyze_query_patterns(filepath))
    return all_findings


def generate_suggestions(findings: list[dict]) -> list[dict]:
    """Generate index suggestions from query pattern findings."""
    # Group by collection + field
    field_usage = {}  # key: (collection, field) -> list of usages
    for f in findings:
        key = (f['collection'], f['field'])
        field_usage.setdefault(key, []).append(f)

    # Group by collection + operation to find compound index candidates
    collection_ops = {}  # key: (collection, file, operation, ~line_range) -> fields
    for f in findings:
        # Group fields that appear within 10 lines of each other in the same operation
        bucket_line = (f['line'] // 10) * 10
        key = (f['collection'], f['file'], f['operation'], bucket_line)
        collection_ops.setdefault(key, []).append(f)

    suggestions = []
    seen = set()

    # Single field index suggestions
    for (collection, field), usages in field_usage.items():
        if field.startswith('$') or field in IGNORE_FIELDS:
            continue

        operations = set(u['operation'] for u in usages)
        files = set(u['file'] for u in usages)
        total_refs = len(usages)

        # Determine priority
        priority = 'low'
        if total_refs >= 5:
            priority = 'high'
        elif total_refs >= 2:
            priority = 'medium'
        if 'sort' in operations:
            priority = 'high'  # sort fields almost always need indexes

        rationale_parts = []
        rationale_parts.append(f"Field '{field}' used {total_refs} time(s)")
        rationale_parts.append(f"in operations: {', '.join(sorted(operations))}")
        rationale_parts.append(f"across {len(files)} file(s)")

        sample_locations = []
        for u in usages[:3]:
            sample_locations.append(f"{Path(u['file']).name}:{u['line']}")

        suggestion_key = (collection, field)
        if suggestion_key not in seen:
            seen.add(suggestion_key)
            suggestions.append({
                'type': 'single_field',
                'collection': collection,
                'field': field,
                'suggested_index': {field: 1},
                'priority': priority,
                'rationale': '; '.join(rationale_parts),
                'operations': sorted(operations),
                'reference_count': total_refs,
                'sample_locations': sample_locations,
            })

    # Compound index suggestions
    for (collection, file, operation, bucket), fields_list in collection_ops.items():
        unique_fields = list(dict.fromkeys(f['field'] for f in fields_list
                                            if not f['field'].startswith('$') and f['field'] not in IGNORE_FIELDS))
        if len(unique_fields) >= 2:
            # Sort: filter fields first, then sort fields
            filter_fields = [f['field'] for f in fields_list if f['usage'] == 'filter'
                            and f['field'] not in IGNORE_FIELDS and not f['field'].startswith('$')]
            sort_fields = [f['field'] for f in fields_list if f['usage'] == 'sort'
                          and f['field'] not in IGNORE_FIELDS and not f['field'].startswith('$')]

            # Deduplicate while preserving order
            ordered = list(dict.fromkeys(filter_fields + sort_fields))
            if len(ordered) < 2:
                ordered = list(dict.fromkeys(unique_fields))
            if len(ordered) < 2:
                continue

            compound_key = (collection, tuple(sorted(ordered[:5])))
            if compound_key in seen:
                continue
            seen.add(compound_key)

            index_spec = {f: 1 for f in ordered[:5]}
            sample_locations = [f"{Path(fl['file']).name}:{fl['line']}" for fl in fields_list[:3]]

            suggestions.append({
                'type': 'compound',
                'collection': collection,
                'fields': ordered[:5],
                'suggested_index': index_spec,
                'priority': 'medium' if len(ordered) <= 3 else 'high',
                'rationale': f"Fields {ordered[:5]} queried together in {operation} operation at {Path(file).name}",
                'operations': [operation],
                'sample_locations': sample_locations,
            })

    # Sort by priority
    priority_order = {'high': 0, 'medium': 1, 'low': 2}
    suggestions.sort(key=lambda s: (priority_order.get(s['priority'], 3), -s.get('reference_count', 0)))

    return suggestions


@mcp.tool()
def suggest_indexes(path: str) -> str:
    """
    Analyze source code for database query patterns and suggest indexes
    that should exist based on how the database is being queried.

    Scans for MongoDB query patterns like .find(), .aggregate(), .updateOne(),
    filter fields, sort fields, and suggests single-field and compound indexes.

    Args:
        path: Absolute or relative path to a project directory or file to scan.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    if target.is_file():
        findings = analyze_query_patterns(str(target))
    else:
        findings = analyze_directory(str(target))

    if not findings:
        return json.dumps({
            "message": "No database query patterns found to suggest indexes for.",
            "path": str(target),
        }, indent=2)

    suggestions = generate_suggestions(findings)

    # Summary stats
    collections = set(f['collection'] for f in findings)
    fields_queried = set(f['field'] for f in findings)
    files_analyzed = set(f['file'] for f in findings)

    return json.dumps({
        "path": str(target),
        "analysis_summary": {
            "total_query_patterns_found": len(findings),
            "unique_collections_referenced": len(collections),
            "unique_fields_queried": len(fields_queried),
            "files_with_query_patterns": len(files_analyzed),
        },
        "suggestions_count": len(suggestions),
        "suggestions": suggestions,
        "queried_fields_detail": [
            {
                "file": f['file'],
                "line": f['line'],
                "field": f['field'],
                "operation": f['operation'],
                "collection": f['collection'],
            }
            for f in findings
        ],
    }, indent=2)


@mcp.tool()
def suggest_indexes_report(path: str, output_file: str = "index_suggestions_report.json") -> str:
    """
    Analyze source code for query patterns and export a JSON report
    with index suggestions based on how the database is being queried.

    Args:
        path: Project directory to scan.
        output_file: Output file path for the report (default: index_suggestions_report.json).
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    findings = analyze_directory(str(target)) if target.is_dir() else analyze_query_patterns(str(target))
    suggestions = generate_suggestions(findings)

    collections = set(f['collection'] for f in findings)
    fields_queried = set(f['field'] for f in findings)

    report = {
        "project": str(target),
        "analysis_summary": {
            "total_query_patterns_found": len(findings),
            "unique_collections_referenced": len(collections),
            "unique_fields_queried": len(fields_queried),
        },
        "suggestions": suggestions,
        "all_query_patterns": [
            {
                "file": f['file'],
                "line": f['line'],
                "field": f['field'],
                "operation": f['operation'],
                "collection": f['collection'],
                "context": f['context'],
            }
            for f in findings
        ],
    }

    out = Path(output_file).resolve()
    with open(out, 'w') as f:
        json.dump(report, f, indent=2)

    return json.dumps({
        "message": f"Suggestions report exported to {out}",
        "summary": report["analysis_summary"],
        "suggestions_count": len(suggestions),
        "top_suggestions": suggestions[:10],
    }, indent=2)


# --- New modular MCP tool ---

@mcp.tool()
def scan_and_export(path: str, format: str = "mongo_shell", db_name: str = None) -> str:
    """
    Scan a project for MongoDB index definitions and generate an executable script.

    Uses the modular ScannerEngine to discover indexes from annotations and query
    patterns, then generates a script in the requested format along with a JSON report.

    Args:
        path: Project directory path to scan.
        format: Output format - "mongo_shell" or "pymongo".
        db_name: Optional database name for the generated script.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({"error": f"Path not found: {path}"})

    if not target.is_dir():
        return json.dumps({"error": f"Path is not a directory: {path}"})

    # Validate format parameter
    if format not in ("mongo_shell", "pymongo"):
        return json.dumps({"error": f"Invalid format: {format}. Must be 'mongo_shell' or 'pymongo'."})

    # Scan using the modular ScannerEngine
    scan_result = _scanner_engine.scan_project(str(target))

    # Check for scan errors that indicate a total failure
    if scan_result.errors and not scan_result.indexes and not scan_result.suggestions:
        return json.dumps({
            "error": "Scan failed",
            "errors": scan_result.errors,
            "path": str(target),
        }, indent=2)

    # Generate script using ScriptGenerator
    if scan_result.indexes:
        if format == "mongo_shell":
            script = _script_generator.generate_mongo_shell(scan_result.indexes, db_name=db_name)
        else:
            script = _script_generator.generate_pymongo(scan_result.indexes, db_name=db_name)
    else:
        script = ""

    # Generate report using ReportGenerator
    report = _report_generator.generate_report(scan_result)

    return json.dumps({
        "script": script,
        "report": report,
        "indexes_found": len(scan_result.indexes),
        "suggestions": len(scan_result.suggestions),
        "format": format,
    }, indent=2)


# --- PostgreSQL Guardrails MCP Tools ---

from index_scanner_mcp.pg.config_loader import ConfigLoader
from index_scanner_mcp.pg.migration_scanner import MigrationScanner
from index_scanner_mcp.pg.schema_analyzer import SchemaAnalyzer
from index_scanner_mcp.pg.index_analyzer import IndexAnalyzer
from index_scanner_mcp.pg.performance_scanner import PerformanceScanner
from index_scanner_mcp.pg.application_code_scanner import ApplicationCodeScanner
from index_scanner_mcp.pg.json_report_generator import JSONReportGenerator
from index_scanner_mcp.pg.team_scanner import TeamScanner, TeamScanResult
from index_scanner_mcp.pg.service_catalog import ServiceCatalog


def _serialize_violations(violations: list) -> list[dict]:
    """Convert a list of Violation objects to JSON-serializable dicts."""
    results = []
    for v in violations:
        entry = {
            "rule_id": v.rule_id,
            "category": v.category.value,
            "severity": v.severity.value,
            "action": v.action.value,
            "file_path": v.file_path,
            "line_number": v.line_number,
            "description": v.description,
            "remediation": v.remediation,
        }
        if v.auto_fix_sql:
            entry["auto_fix_sql"] = v.auto_fix_sql
        if v.explain_output:
            entry["explain_output"] = v.explain_output
        results.append(entry)
    return results


@mcp.tool()
def pg_scan_migrations(path: str, config_path: str | None = None) -> str:
    """
    Scan SQL migration files for risky or blocked DDL operations.

    Detects destructive operations (DROP TABLE/COLUMN/DATABASE, TRUNCATE),
    blocked maintenance operations (VACUUM FULL, CLUSTER, REINDEX SYSTEM),
    non-concurrent index creation, missing rollback scripts, and multiple
    DDL statements in a single migration.

    Args:
        path: Absolute or relative path to a directory containing SQL migration files, or a single SQL file.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a directory containing SQL migration files, or a single .sql file.",
        }, indent=2)

    config = ConfigLoader().load(config_path)
    scanner = MigrationScanner(config)

    if target.is_file():
        violations = scanner.scan_file(str(target))
    else:
        violations = scanner.scan_directory(str(target))

    return json.dumps({
        "path": str(target),
        "tool": "pg_scan_migrations",
        "total_violations": len(violations),
        "violations": _serialize_violations(violations),
    }, indent=2)


@mcp.tool()
def pg_scan_schema(path: str, config_path: str | None = None) -> str:
    """
    Scan SQL schema definitions for design anti-patterns and constraint gaps.

    Detects missing primary keys, wide composite PKs, missing foreign keys,
    circular references, problematic data types (JSON vs JSONB, TIMESTAMP
    WITHOUT TIME ZONE, SERIAL vs IDENTITY, NUMERIC without precision),
    nullable business-critical columns, and NOT NULL without DEFAULT.

    Args:
        path: Absolute or relative path to a SQL file or directory containing schema definitions.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a SQL file or directory containing schema definitions.",
        }, indent=2)

    config = ConfigLoader().load(config_path)
    analyzer = SchemaAnalyzer(config)

    if target.is_file():
        violations = analyzer.analyze_file(str(target))
    else:
        # Scan all .sql files in the directory
        violations = []
        for sql_file in sorted(target.rglob("*.sql")):
            if sql_file.is_file():
                violations.extend(analyzer.analyze_file(str(sql_file)))

    return json.dumps({
        "path": str(target),
        "tool": "pg_scan_schema",
        "total_violations": len(violations),
        "violations": _serialize_violations(violations),
    }, indent=2)


@mcp.tool()
def pg_scan_indexes(path: str, config_path: str | None = None) -> str:
    """
    Scan index definitions for duplicates, overlaps, naming issues, and missing FK indexes.

    Detects foreign keys without corresponding indexes, duplicate indexes,
    overlapping/prefix indexes, overly wide composite indexes, wrong column
    order, and naming convention violations.

    Args:
        path: Absolute or relative path to a SQL file or directory containing index definitions.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a SQL file or directory containing index definitions.",
        }, indent=2)

    config = ConfigLoader().load(config_path)
    analyzer = IndexAnalyzer(config)

    if target.is_file():
        violations = analyzer.analyze_file(str(target))
    else:
        violations = []
        for sql_file in sorted(target.rglob("*.sql")):
            if sql_file.is_file():
                violations.extend(analyzer.analyze_file(str(sql_file)))

    return json.dumps({
        "path": str(target),
        "tool": "pg_scan_indexes",
        "total_violations": len(violations),
        "violations": _serialize_violations(violations),
    }, indent=2)


@mcp.tool()
def pg_scan_performance(path: str, config_path: str | None = None) -> str:
    """
    Scan SQL queries for performance anti-patterns.

    Detects SELECT *, DELETE/UPDATE without WHERE, SELECT without WHERE,
    LIKE with leading wildcard, ORDER BY RANDOM(), large OFFSET values,
    Cartesian joins (JOIN without ON), and functions applied to indexed
    columns in WHERE clauses.

    Args:
        path: Absolute or relative path to a SQL file or directory containing queries to analyze.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a SQL file or directory containing SQL queries.",
        }, indent=2)

    config = ConfigLoader().load(config_path)
    scanner = PerformanceScanner(config)

    if target.is_file():
        violations = scanner.scan_file(str(target))
    else:
        violations = []
        for sql_file in sorted(target.rglob("*.sql")):
            if sql_file.is_file():
                violations.extend(scanner.scan_file(str(sql_file)))

    return json.dumps({
        "path": str(target),
        "tool": "pg_scan_performance",
        "total_violations": len(violations),
        "violations": _serialize_violations(violations),
    }, indent=2)


@mcp.tool()
def pg_scan_application_code(path: str, config_path: str | None = None) -> str:
    """
    Scan Java source code for unsafe database access patterns.

    Detects Statement/createStatement() usage (SQL injection risk),
    execute/executeQuery/executeUpdate on Statement objects, string
    concatenation in SQL construction, SELECT * in string literals,
    and hardcoded DELETE/UPDATE without parameterized conditions.

    Args:
        path: Absolute or relative path to a Java file or directory containing Java source code.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a Java file or directory containing Java source code.",
        }, indent=2)

    config = ConfigLoader().load(config_path)
    scanner = ApplicationCodeScanner(config)

    if target.is_file():
        violations = scanner.scan_file(str(target))
    else:
        violations = scanner.scan_directory(str(target))

    return json.dumps({
        "path": str(target),
        "tool": "pg_scan_application_code",
        "total_violations": len(violations),
        "violations": _serialize_violations(violations),
    }, indent=2)


@mcp.tool()
def pg_full_scan(path: str, config_path: str | None = None) -> str:
    """
    Run all PostgreSQL guardrail analyzers and return a combined report.

    Executes migration scanning, schema analysis, index analysis,
    performance scanning, and application code scanning in sequence.
    Produces a unified report with gate decision (pass/fail).

    Args:
        path: Absolute or relative path to a project directory to scan.
        config_path: Optional path to a .guardrails.yml configuration file. If not provided, defaults are used.
    """
    target = Path(path).resolve()
    if not target.exists():
        return json.dumps({
            "error": f"Path not found: {path}",
            "guidance": "Provide an absolute or relative path to a project directory containing SQL and/or Java files.",
        }, indent=2)

    try:
        from index_scanner_mcp.pg.engine import PostgresGuardrailEngine

        config = ConfigLoader().load(config_path)
        engine = PostgresGuardrailEngine(config)
        result = engine.run_analysis(str(target))

        report_generator = JSONReportGenerator()
        report_json = report_generator.generate(result)

        return report_json
    except ImportError:
        # Fallback: run each scanner individually if engine is not yet available
        config = ConfigLoader().load(config_path)

        all_violations = []
        errors = []
        migration_files = 0
        java_files = 0
        total_files = 0

        # Scan migrations
        try:
            migration_scanner = MigrationScanner(config)
            if target.is_dir():
                for sql_file in sorted(target.rglob("*.sql")):
                    if sql_file.is_file():
                        migration_files += 1
                        all_violations.extend(migration_scanner.scan_file(str(sql_file)))
            else:
                migration_files = 1
                all_violations.extend(migration_scanner.scan_file(str(target)))
        except Exception as e:
            errors.append(f"Migration scan error: {str(e)}")

        # Scan schema
        try:
            schema_analyzer = SchemaAnalyzer(config)
            if target.is_dir():
                for sql_file in sorted(target.rglob("*.sql")):
                    if sql_file.is_file():
                        all_violations.extend(schema_analyzer.analyze_file(str(sql_file)))
            else:
                all_violations.extend(schema_analyzer.analyze_file(str(target)))
        except Exception as e:
            errors.append(f"Schema scan error: {str(e)}")

        # Scan indexes
        try:
            index_analyzer = IndexAnalyzer(config)
            if target.is_dir():
                for sql_file in sorted(target.rglob("*.sql")):
                    if sql_file.is_file():
                        all_violations.extend(index_analyzer.analyze_file(str(sql_file)))
            else:
                all_violations.extend(index_analyzer.analyze_file(str(target)))
        except Exception as e:
            errors.append(f"Index scan error: {str(e)}")

        # Scan performance
        try:
            performance_scanner = PerformanceScanner(config)
            if target.is_dir():
                for sql_file in sorted(target.rglob("*.sql")):
                    if sql_file.is_file():
                        total_files += 1
                        all_violations.extend(performance_scanner.scan_file(str(sql_file)))
            else:
                total_files = 1
                all_violations.extend(performance_scanner.scan_file(str(target)))
        except Exception as e:
            errors.append(f"Performance scan error: {str(e)}")

        # Scan application code
        try:
            app_scanner = ApplicationCodeScanner(config)
            if target.is_dir():
                for java_file in sorted(target.rglob("*.java")):
                    if java_file.is_file():
                        java_files += 1
                        all_violations.extend(app_scanner.scan_file(str(java_file)))
            else:
                if str(target).endswith(".java"):
                    java_files = 1
                    all_violations.extend(app_scanner.scan_file(str(target)))
        except Exception as e:
            errors.append(f"Application code scan error: {str(e)}")

        # Compute gate decision
        from index_scanner_mcp.pg.models import Action as PgAction
        blocking = [v for v in all_violations if v.action == PgAction.BLOCK_PR]
        from index_scanner_mcp.pg.models import Severity as PgSeverity
        critical_count = sum(1 for v in all_violations if v.severity == PgSeverity.CRITICAL)
        high_count = sum(1 for v in all_violations if v.severity == PgSeverity.HIGH)
        medium_count = sum(1 for v in all_violations if v.severity == PgSeverity.MEDIUM)

        return json.dumps({
            "project_path": str(target),
            "tool": "pg_full_scan",
            "gate_decision": {
                "passed": len(blocking) == 0,
                "total_violations": len(all_violations),
                "critical_count": critical_count,
                "high_count": high_count,
                "medium_count": medium_count,
            },
            "summary": {
                "files_scanned": total_files + migration_files + java_files,
                "migration_files_scanned": migration_files,
                "java_files_scanned": java_files,
            },
            "violations": _serialize_violations(all_violations),
            "errors": errors,
        }, indent=2)


@mcp.tool()
def scan_team(team_name: str, catalog_path: str, config_path: str | None = None) -> str:
    """
    Auto-discover repositories for a team from the service catalog and run
    the appropriate database guardrails on each service.

    Reads the service catalog CSV, filters to services owned by *team_name*,
    and runs PostgreSQL guardrails on PostgreSQL services and the MongoDB
    scanner on MongoDB services.  Returns an aggregated report with
    per-service violations and a combined gate decision.

    Args:
        team_name:    Name of the team to scan (matched case-insensitively
                      against the catalog ``Team`` column).
        catalog_path: Absolute or relative path to the service catalog CSV file.
        config_path:  Optional path to a ``.guardrails.yml`` configuration file.
                      If not provided, built-in defaults are used.
    """
    catalog_target = Path(catalog_path).resolve()
    if not catalog_target.exists():
        return json.dumps({
            "error": f"Catalog path not found: {catalog_path}",
            "guidance": (
                "Provide an absolute or relative path to the service catalog "
                "CSV file.  The CSV must have columns: Namespace, Team, "
                "ServiceName, Team Size, Team Members, Language, "
                "URI location if present, Sub Team, DB Service."
            ),
        }, indent=2)

    config = ConfigLoader().load(config_path)

    try:
        scanner = TeamScanner(team_name, str(catalog_target), config)
        result: TeamScanResult = scanner.scan()
    except Exception as exc:
        return json.dumps({
            "error": f"Team scan failed: {exc}",
            "team_name": team_name,
            "catalog_path": str(catalog_target),
        }, indent=2)

    # Build per-service violations dict (service_name → serialized violations)
    violations_by_service: dict[str, list[dict]] = {
        svc: _serialize_violations(viols)
        for svc, viols in result.violations_by_service.items()
    }

    # Gate decision fields
    gate = result.gate_decision
    gate_dict: dict | None = None
    if gate is not None:
        gate_dict = {
            "passed": gate.passed,
            "total_violations": gate.total_violations,
            "critical_count": gate.critical_count,
            "high_count": gate.high_count,
            "medium_count": gate.medium_count,
            "blocking_violations": _serialize_violations(gate.blocking_violations),
        }

    return json.dumps({
        "team_name": result.team_name,
        "total_services": result.total_services,
        "scanned_services": result.scanned_services,
        "skipped_services": result.skipped_services,
        "postgres_services": result.postgres_services,
        "mongodb_services": result.mongodb_services,
        "all_violations": _serialize_violations(result.all_violations),
        "violations_by_service": violations_by_service,
        "gate_decision": gate_dict,
        "errors": result.errors,
    }, indent=2)


@mcp.tool()
def list_team_services(team_name: str, catalog_path: str) -> str:
    """
    Return all services registered for a team in the service catalog, along
    with their database types and repository locations.

    Reads the service catalog CSV and filters it to services owned by
    *team_name*.  Each entry in the returned list includes the service name,
    namespace, sub-team, programming language, DB service type, URI/repo
    location, and convenience booleans indicating whether the service uses
    PostgreSQL or MongoDB.

    Args:
        team_name:    Name of the team (matched case-insensitively against
                      the catalog ``Team`` column).
        catalog_path: Absolute or relative path to the service catalog CSV file.
    """
    catalog_target = Path(catalog_path).resolve()
    if not catalog_target.exists():
        return json.dumps({
            "error": f"Catalog path not found: {catalog_path}",
            "guidance": (
                "Provide an absolute or relative path to the service catalog "
                "CSV file.  The CSV must have columns: Namespace, Team, "
                "ServiceName, Team Size, Team Members, Language, "
                "URI location if present, Sub Team, DB Service."
            ),
        }, indent=2)

    try:
        catalog = ServiceCatalog(str(catalog_target))
        services = catalog.filter_by_team(team_name)
    except Exception as exc:
        return json.dumps({
            "error": f"Failed to load service catalog: {exc}",
            "team_name": team_name,
            "catalog_path": str(catalog_target),
        }, indent=2)

    service_list = [
        {
            "service_name": svc.service_name,
            "namespace": svc.namespace,
            "sub_team": svc.sub_team,
            "language": svc.language,
            "db_service": svc.db_service,
            "uri_location": svc.uri_location,
            "has_postgres": ServiceCatalog.has_postgres(svc.db_service),
            "has_mongodb": ServiceCatalog.has_mongodb(svc.db_service),
        }
        for svc in services
    ]

    return json.dumps({
        "team_name": team_name,
        "total_services": len(service_list),
        "services": service_list,
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
